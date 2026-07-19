--[[
  Pure-Lua core: echo ledger, delta diffing, batching.
  No Lightroom SDK imports — unit-testable with stock lua5.4.
]]

local Core = {}

local function key(filepath, family)
  return tostring(filepath or "") .. "\0" .. tostring(family or "")
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

return Core
