--[[
  Pure-Lua core: echo ledger, delta diffing, batching.
  No Lightroom SDK imports — unit-testable with stock lua5.4.
]]

local Core = {}

local function key(filepath, family)
  return tostring(filepath or "") .. "\0" .. tostring(family or "")
end

local function encode_scalar(value)
  local t = type(value)
  if t == "nil" then
    return "n"
  elseif t == "boolean" then
    return value and "b1" or "b0"
  elseif t == "number" then
    return "d" .. tostring(value)
  end
  local text = tostring(value or "")
  text = text:gsub("\\", "\\\\"):gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t"):gsub("\0", "\\0")
  return "s" .. text
end

local function decode_scalar(token)
  if token == nil or token == "n" then
    return nil
  end
  local prefix = token:sub(1, 1)
  if prefix == "b" then
    return token == "b1"
  elseif prefix == "d" then
    return tonumber(token:sub(2))
  elseif prefix == "s" then
    local text = token:sub(2)
    text = text:gsub("\\0", "\0"):gsub("\\t", "\t"):gsub("\\r", "\r"):gsub("\\n", "\n"):gsub("\\\\", "\\")
    return text
  end
  return token
end

function Core.new_ledger()
  return { applied = {} }
end

--- Record that we wrote ``value`` at ``clock`` into LR (or accepted from LR).
function Core.ledger_remember(ledger, filepath, family, value, clock)
  ledger.applied[key(filepath, family)] = {
    value = value,
    clock = tonumber(clock) or 0,
  }
end

function Core.ledger_get(ledger, filepath, family)
  return ledger.applied[key(filepath, family)]
end

--- True when an observed LR value matches the last write we applied (echo).
function Core.is_echo(ledger, filepath, family, value)
  local entry = Core.ledger_get(ledger, filepath, family)
  if not entry then
    return false
  end
  return entry.value == value
end

--- Filter outbound observations: drop echoes, keep real LR edits.
function Core.filter_outbound(ledger, observations)
  local outbound = {}
  for _, obs in ipairs(observations or {}) do
    if not Core.is_echo(ledger, obs.filepath, obs.family, obs.value) then
      outbound[#outbound + 1] = obs
    end
  end
  return outbound
end

--- Decide whether an inbound elo_stars projection may overwrite LR stars.
--- Only when current LR stars equal the last projection we wrote (or unset).
function Core.may_apply_elo_stars(ledger, filepath, current_lr_stars, projected)
  local entry = Core.ledger_get(ledger, filepath, "elo_stars")
  if not entry then
    -- No prior projection: only write when the user has no stars set.
    return (tonumber(current_lr_stars) or 0) == 0
  end
  return (tonumber(current_lr_stars) or 0) == (tonumber(entry.value) or 0)
      and (tonumber(projected) or 0) ~= (tonumber(entry.value) or 0)
end

--- Persistable snapshot of the echo ledger (survives LR/plugin restart).
function Core.ledger_serialize(ledger)
  local rows = {}
  for k, entry in pairs((ledger and ledger.applied) or {}) do
    local filepath, family = k:match("^(.-)\0(.*)$")
    if filepath and family then
      rows[#rows + 1] = {
        filepath = filepath,
        family = family,
        value = entry.value,
        clock = tonumber(entry.clock) or 0,
      }
    end
  end
  table.sort(rows, function(a, b)
    if a.filepath == b.filepath then
      return a.family < b.family
    end
    return a.filepath < b.filepath
  end)
  local lines = {}
  for _, row in ipairs(rows) do
    lines[#lines + 1] = table.concat({
      encode_scalar(row.filepath),
      encode_scalar(row.family),
      encode_scalar(row.value),
      encode_scalar(row.clock),
    }, "\t")
  end
  return table.concat(lines, "\n")
end

function Core.ledger_deserialize(text)
  local ledger = Core.new_ledger()
  if type(text) ~= "string" or #text == 0 then
    return ledger
  end
  for line in (text .. "\n"):gmatch("(.-)\n") do
    if #line > 0 then
      local parts = {}
      local start = 1
      while true do
        local stop = line:find("\t", start, true)
        if not stop then
          parts[#parts + 1] = line:sub(start)
          break
        end
        parts[#parts + 1] = line:sub(start, stop - 1)
        start = stop + 1
      end
      if #parts >= 4 then
        local filepath = decode_scalar(parts[1])
        local family = decode_scalar(parts[2])
        local value = decode_scalar(parts[3])
        local clock = decode_scalar(parts[4])
        if filepath and family then
          Core.ledger_remember(ledger, filepath, family, value, clock)
        end
      end
    end
  end
  return ledger
end

--- Remember only server-confirmed applied entries (never pending/unmatched).
function Core.ledger_remember_confirmed(ledger, confirmed_entries)
  for _, entry in ipairs(confirmed_entries or {}) do
    if entry and entry.filepath and entry.family then
      Core.ledger_remember(ledger, entry.filepath, entry.family, entry.value, entry.ts or entry.clock)
    end
  end
end

--- Split a list into batches of at most ``size`` (for withWriteAccessDo).
function Core.batches(items, size)
  size = math.max(1, tonumber(size) or 25)
  local out = {}
  local batch = {}
  for _, item in ipairs(items or {}) do
    batch[#batch + 1] = item
    if #batch >= size then
      out[#out + 1] = batch
      batch = {}
    end
  end
  if #batch > 0 then
    out[#out + 1] = batch
  end
  return out
end

--- Merge inbound deltas: keep newest clock per (filepath, family).
function Core.merge_inbound(items)
  local best = {}
  local order = {}
  for _, item in ipairs(items or {}) do
    local k = key(item.filepath, item.family)
    local prev = best[k]
    local ts = tonumber(item.ts) or 0
    if not prev or ts >= (tonumber(prev.ts) or 0) then
      if not prev then
        order[#order + 1] = k
      end
      best[k] = item
    end
  end
  local merged = {}
  for _, k in ipairs(order) do
    merged[#merged + 1] = best[k]
  end
  return merged
end

--- Map LR pick/reject flags to Azimuth vocabulary.
function Core.lr_flag_to_azimuth(pick_status)
  -- Lightroom: nil/0 unflagged, 1 pick, -1 reject (SDK photo:getRawMetadata("pickStatus"))
  local status = tonumber(pick_status) or 0
  if status == 1 then
    return "picked"
  elseif status == -1 then
    return "rejected"
  end
  return "unflagged"
end

function Core.azimuth_flag_to_lr(flag)
  if flag == "picked" then
    return 1
  elseif flag == "rejected" then
    return -1
  end
  return 0
end

--- Diff catalog snapshot against previous scan → outbound observations.
function Core.diff_catalog(previous, current)
  previous = previous or {}
  local observations = {}
  for filepath, state in pairs(current or {}) do
    local prior = previous[filepath]
    if not prior or prior.flag ~= state.flag then
      observations[#observations + 1] = {
        filepath = filepath,
        family = "flag",
        value = state.flag,
        observed_at = state.observed_at,
      }
    end
    if not prior or prior.rating ~= state.rating then
      local rating = tonumber(state.rating) or 0
      -- User stars only (lr_rating). elo_stars writes are ledger-echoed out.
      observations[#observations + 1] = {
        filepath = filepath,
        family = "lr_rating",
        value = rating,
        observed_at = state.observed_at,
      }
    end
  end
  return observations
end

-- ── Morning collection (pure-core) ──────────────────────────────────────────

local function _date_label(epoch)
  -- YYYY-MM-DD in local time; epoch defaults to now.
  local t = tonumber(epoch) or os.time()
  return os.date("%Y-%m-%d", t)
end

--- Collection title: "From Azimuth — <N> picks · <date>"
function Core.morning_collection_title(pick_count, epoch)
  local n = math.max(0, tonumber(pick_count) or 0)
  local noun = (n == 1) and "pick" or "picks"
  return string.format("From Azimuth — %d %s · %s", n, noun, _date_label(epoch))
end

--- Picks present in ``current`` that were not in ``previous_session`` (last LR session).
--- ``current`` / ``previous_session`` are filepath → { flag = ... } maps (or flag strings).
function Core.new_picks_since(previous_session, current)
  previous_session = previous_session or {}
  local fresh = {}
  for filepath, state in pairs(current or {}) do
    local flag = type(state) == "table" and state.flag or state
    if flag == "picked" then
      local prior = previous_session[filepath]
      local prior_flag = type(prior) == "table" and prior.flag or prior
      if prior_flag ~= "picked" then
        fresh[#fresh + 1] = filepath
      end
    end
  end
  table.sort(fresh)
  return fresh
end

--- Decide which dated morning collections to remove.
--- ``collections`` is a list of { name=, photo_count=, date= "YYYY-MM-DD", empty= bool }.
--- Empty collections whose date is older than today are aged out.
function Core.aged_empty_collections(collections, today_epoch)
  local today = _date_label(today_epoch)
  local remove = {}
  for _, coll in ipairs(collections or {}) do
    local count = tonumber(coll.photo_count)
    local empty = coll.empty
    if empty == nil then
      empty = (count or 0) == 0
    end
    local date = tostring(coll.date or "")
    if empty and date ~= "" and date < today then
      remove[#remove + 1] = coll
    end
  end
  return remove
end

--- Parse "From Azimuth — N picks · YYYY-MM-DD" → { count, date } or nil.
function Core.parse_morning_collection_name(name)
  local count, date = tostring(name or ""):match("^From Azimuth — (%d+) picks? · (%d%d%d%d%-%d%d%-%d%d)$")
  if not count then
    return nil
  end
  return { count = tonumber(count), date = date }
end

-- ── Best-of-shoot collections + contextual whisper (pure-core) ──────────────

-- Collection set name the plugin maintains (child collections live under it).
Core.BEST_OF_SET_NAME = "Azimuth / Best of"

-- Mirror of server BEST_OF_SHOOT_MIN_SIZE — suppress collections for tiny shoots.
Core.BEST_OF_SHOOT_MIN_SIZE = 5

--- "Best of <shoot>" child collection title.
function Core.best_of_collection_title(shoot_title)
  local title = tostring(shoot_title or ""):gsub("^%s+", ""):gsub("%s+$", "")
  if title == "" then
    title = "Shoot"
  end
  return "Best of " .. title
end

--- Parse "Best of <title>" → title or nil.
function Core.parse_best_of_collection_name(name)
  local title = tostring(name or ""):match("^Best of (.+)$")
  if not title or title == "" then
    return nil
  end
  return title
end

--- Whether a shoot is large enough for an automatic Best-of collection.
function Core.shoot_qualifies_for_best_of(shoot_size, min_size)
  local size = tonumber(shoot_size) or 0
  local floor = tonumber(min_size) or Core.BEST_OF_SHOOT_MIN_SIZE
  return size >= floor
end

--- Membership diff: filepath lists → { add = {...}, remove = {...} } (sorted).
function Core.diff_collection_membership(desired, current)
  local want = {}
  for _, path in ipairs(desired or {}) do
    if path and path ~= "" then
      want[path] = true
    end
  end
  local have = {}
  for _, path in ipairs(current or {}) do
    if path and path ~= "" then
      have[path] = true
    end
  end
  local add, remove = {}, {}
  for path, _ in pairs(want) do
    if not have[path] then
      add[#add + 1] = path
    end
  end
  for path, _ in pairs(have) do
    if not want[path] then
      remove[#remove + 1] = path
    end
  end
  table.sort(add)
  table.sort(remove)
  return { add = add, remove = remove }
end

--- Build desired Best-of child collections from server shoot_context.best_of_shoots.
--- Drops shoots below min_size (defense in depth if server sends them).
function Core.best_of_targets(best_of_shoots, min_size)
  local floor = tonumber(min_size) or Core.BEST_OF_SHOOT_MIN_SIZE
  local targets = {}
  for _, shoot in ipairs(best_of_shoots or {}) do
    local size = tonumber(shoot.shoot_size) or #(shoot.filepaths or {})
    if Core.shoot_qualifies_for_best_of(size, floor) then
      local filepaths = {}
      for _, path in ipairs(shoot.filepaths or {}) do
        if path and path ~= "" then
          filepaths[#filepaths + 1] = path
        end
      end
      table.sort(filepaths)
      if #filepaths > 0 then
        targets[#targets + 1] = {
          shoot_key = shoot.shoot_key,
          shoot_title = shoot.shoot_title or "Shoot",
          shoot_size = size,
          name = Core.best_of_collection_title(shoot.shoot_title),
          filepaths = filepaths,
        }
      end
    end
  end
  table.sort(targets, function(a, b)
    return a.name < b.name
  end)
  return targets
end

--- Plan collection create/update/delete from desired targets + existing child names.
--- ``existing`` is { name = { filepaths = {...} }, ... } for children under the Best-of set.
--- Returns { upsert = { {name, filepaths, add, remove} }, delete = { name, ... } }.
function Core.plan_best_of_collections(targets, existing)
  existing = existing or {}
  local desired_names = {}
  local upsert = {}
  for _, target in ipairs(targets or {}) do
    desired_names[target.name] = true
    local current = (existing[target.name] and existing[target.name].filepaths) or {}
    local diff = Core.diff_collection_membership(target.filepaths, current)
    upsert[#upsert + 1] = {
      name = target.name,
      shoot_key = target.shoot_key,
      shoot_title = target.shoot_title,
      filepaths = target.filepaths,
      add = diff.add,
      remove = diff.remove,
      create = existing[target.name] == nil,
    }
  end
  local delete = {}
  for name, _ in pairs(existing) do
    if not desired_names[name] then
      delete[#delete + 1] = name
    end
  end
  table.sort(delete)
  return { upsert = upsert, delete = delete }
end

--- Context-aware star whisper: global band + optional shoot rank.
--- Example: "Top 30% of your ranked photos · #4 in this shoot"
function Core.compose_star_whisper(global_whisper, rank_in_shoot)
  local base = tostring(global_whisper or "")
  if base == "" then
    return nil
  end
  local rank = tonumber(rank_in_shoot)
  if rank and rank > 0 then
    return string.format("%s · #%d in this shoot", base, math.floor(rank))
  end
  return base
end

--- Lookup rank_in_shoot for a filepath from shoot_context.photos (or nil).
function Core.rank_in_shoot_for(photos, filepath)
  if not filepath or filepath == "" then
    return nil
  end
  for _, row in ipairs(photos or {}) do
    if row.filepath == filepath then
      return tonumber(row.rank_in_shoot)
    end
  end
  return nil
end

return Core
