--[[
  Background poll + catalog observe service (LrHttp / LrTasks / LrCatalog).
  Targets Lightroom Classic SDK 13 API surface.
]]

local LrTasks = import("LrTasks")
local LrHttp = import("LrHttp")
local LrApplication = import("LrApplication")
local LrPathUtils = import("LrPathUtils")
local LrPrefs = import("LrPrefs")

-- One instance across Init / Dialog / Shutdown (each dofile would otherwise fork state).
if _G.AzimuthSyncService then
  return _G.AzimuthSyncService
end

local Core = dofile(LrPathUtils.child(_PLUGIN.path, "AzimuthSyncCore.lua"))

local POLL_SECONDS = 10
local WRITE_BATCH = 25

local function prefs()
  return LrPrefs.prefsForPlugin()
end

local function load_persisted_ledger()
  local stored = prefs().echoLedger
  if type(stored) == "string" and #stored > 0 then
    return Core.ledger_deserialize(stored)
  end
  return Core.new_ledger()
end

local function persist_ledger(ledger)
  prefs().echoLedger = Core.ledger_serialize(ledger)
end

local Service = {
  running = false,
  ledger = load_persisted_ledger(),
  last_scan = {},
  clock = 0,
  status = {
    connected = false,
    synced = 0,
    pending = 0,
    last_error = nil,
    satellite_url = nil,
  },
}

local function read_bundled_satellite_url()
  -- One-click connect writes satellite_url.json beside Info.lua.
  local path = LrPathUtils.child(_PLUGIN.path, "satellite_url.json")
  local fh = io.open(path, "r")
  if not fh then
    return nil
  end
  local body = fh:read("*a")
  fh:close()
  if type(body) ~= "string" or #body == 0 then
    return nil
  end
  local url = body:match('"satelliteUrl"%s*:%s*"([^"]+)"')
  if url and #url > 0 then
    return url:gsub("/+$", "")
  end
  return nil
end

local function satellite_url()
  local p = prefs()
  local url = p.satelliteUrl
  if type(url) == "string" and #url > 0 then
    return url:gsub("/+$", "")
  end
  local bundled = read_bundled_satellite_url()
  if bundled then
    return bundled
  end
  return "http://127.0.0.1:8000"
end

local function json_encode(value)
  -- Minimal encoder for our closed payload shapes (tables/arrays/strings/numbers/bools).
  local t = type(value)
  if t == "nil" then
    return "null"
  elseif t == "boolean" then
    return value and "true" or "false"
  elseif t == "number" then
    return tostring(value)
  elseif t == "string" then
    return '"' .. value:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\n", "\\n"):gsub("\r", "\\r") .. '"'
  elseif t == "table" then
    local is_array = #value > 0 or next(value) == nil
    if is_array then
      for k, _ in pairs(value) do
        if type(k) ~= "number" then
          is_array = false
          break
        end
      end
    end
    if is_array then
      local parts = {}
      for i, v in ipairs(value) do
        parts[i] = json_encode(v)
      end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local parts = {}
    for k, v in pairs(value) do
      parts[#parts + 1] = json_encode(tostring(k)) .. ":" .. json_encode(v)
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end

local function json_decode(text)
  if type(text) ~= "string" or #text == 0 then
    return nil
  end
  local ok, LrJson = pcall(import, "LrJson")
  if ok and LrJson and LrJson.decode then
    local success, value = pcall(LrJson.decode, text)
    if success then
      return value
    end
  end
  Service.status.last_error = "JSON decode failed (LrJson required)"
  return nil
end

local function http_json(method, path, body)
  local url = satellite_url() .. path
  Service.status.satellite_url = satellite_url()
  local headers = { { field = "Content-Type", value = "application/json" } }
  local result, hdrs
  if method == "GET" then
    result, hdrs = LrHttp.get(url, headers)
  else
    result, hdrs = LrHttp.post(url, json_encode(body or {}), headers, method)
  end
  if not result then
    Service.status.connected = false
    Service.status.last_error = "no response from satellite"
    return nil
  end
  local status = 0
  if type(hdrs) == "table" then
    status = tonumber(hdrs.status) or tonumber(hdrs.statusCode) or 0
  end
  if status >= 400 then
    Service.status.connected = false
    Service.status.last_error = "HTTP " .. tostring(status)
    return nil
  end
  Service.status.connected = true
  Service.status.last_error = nil
  return json_decode(result)
end

local function photo_filepath(photo)
  local path = photo:getRawMetadata("path")
  if type(path) == "string" and #path > 0 then
    return path
  end
  return nil
end

local function scan_catalog()
  local catalog = LrApplication.activeCatalog()
  local snapshot = {}
  local photos = catalog:getAllPhotos()
  local now = os.time()
  for _, photo in ipairs(photos) do
    local filepath = photo_filepath(photo)
    if filepath then
      local pick = photo:getRawMetadata("pickStatus")
      local rating = photo:getRawMetadata("rating") or 0
      snapshot[filepath] = {
        flag = Core.lr_flag_to_azimuth(pick),
        rating = tonumber(rating) or 0,
        observed_at = now,
        photo = photo,
      }
    end
  end
  return snapshot
end

local function push_outbound(observations)
  if not observations or #observations == 0 then
    return
  end
  local filtered = Core.filter_outbound(Service.ledger, observations)
  Service.status.pending = #filtered
  for _, batch in ipairs(Core.batches(filtered, WRITE_BATCH)) do
    local items = {}
    for _, obs in ipairs(batch) do
      items[#items + 1] = {
        filepath = obs.filepath,
        family = obs.family,
        value = obs.value,
        observed_at = obs.observed_at,
      }
    end
    local response = http_json("POST", "/api/lr/deltas", { items = items })
    if response then
      Service.status.pending = tonumber(response.pending_count) or 0
      Service.status.synced = Service.status.synced + #(response.entries or {})
      -- Only remember server-confirmed applies — pending/unmatched stay out of the ledger.
      Core.ledger_remember_confirmed(Service.ledger, response.entries)
      persist_ledger(Service.ledger)
    end
  end
end

local maintain_morning_collection, maintain_best_of_collections

local function apply_inbound(items, shoot_context)
  if not items or #items == 0 then
    return
  end
  local catalog = LrApplication.activeCatalog()
  local snapshot = scan_catalog()
  local merged = Core.merge_inbound(items)
  local morning_picks = {}
  local photos = (shoot_context and shoot_context.photos) or {}
  for _, batch in ipairs(Core.batches(merged, WRITE_BATCH)) do
    catalog:withWriteAccessDo("Azimuth Sync", function()
      for _, item in ipairs(batch) do
        local state = snapshot[item.filepath]
        if state and state.photo then
          local photo = state.photo
          if item.family == "flag" then
            if not Core.is_echo(Service.ledger, item.filepath, "flag", item.value) then
              photo:setRawMetadata("pickStatus", Core.azimuth_flag_to_lr(item.value))
              Core.ledger_remember(Service.ledger, item.filepath, "flag", item.value, item.ts)
              persist_ledger(Service.ledger)
              Service.status.synced = Service.status.synced + 1
              if item.value == "picked" then
                morning_picks[#morning_picks + 1] = { filepath = item.filepath, photo = photo }
              end
            end
          elseif item.family == "elo_stars" then
            local current = tonumber(photo:getRawMetadata("rating")) or 0
            if Core.may_apply_elo_stars(Service.ledger, item.filepath, current, item.value) then
              photo:setRawMetadata("rating", tonumber(item.value) or 0)
              Core.ledger_remember(Service.ledger, item.filepath, "elo_stars", item.value, item.ts)
              persist_ledger(Service.ledger)
              Service.status.synced = Service.status.synced + 1
              -- Context-aware whisper (global band + shoot rank) for status/debug.
              local stars = tonumber(item.value) or 0
              local band = nil
              if stars >= 5 then
                band = "Top 2% of your ranked photos"
              elseif stars == 4 then
                band = "Top 10% of your ranked photos"
              elseif stars == 3 then
                band = "Top 30% of your ranked photos"
              end
              Service.status.last_whisper = Core.compose_star_whisper(
                band,
                Core.rank_in_shoot_for(photos, item.filepath)
              )
            end
          end
        end
      end
    end)
  end
  if #morning_picks > 0 then
    maintain_morning_collection(morning_picks)
  end
end

--- Dated "From Azimuth — N picks · date" collection for picks since last LR session.
local function save_session_pick_baseline(snapshot)
  local baseline = {}
  for filepath, state in pairs(snapshot or {}) do
    if state.flag == "picked" then
      baseline[filepath] = "picked"
    end
  end
  prefs().morningBaseline = baseline
end

local function list_morning_collections(catalog)
  local found = {}
  local children = catalog:getChildCollections() or {}
  for _, coll in ipairs(children) do
    local name = coll:getName()
    local parsed = Core.parse_morning_collection_name(name)
    if parsed then
      local photos = coll:getPhotos() or {}
      found[#found + 1] = {
        collection = coll,
        name = name,
        date = parsed.date,
        photo_count = #photos,
        empty = #photos == 0,
      }
    end
  end
  return found
end

maintain_morning_collection = function(new_pick_photos)
  local catalog = LrApplication.activeCatalog()
  local today = os.date("%Y-%m-%d")
  catalog:withWriteAccessDo("Azimuth morning collection", function()
    -- Age out empty collections from prior days.
    local existing = list_morning_collections(catalog)
    for _, aged in ipairs(Core.aged_empty_collections(existing, os.time())) do
      if aged.collection then
        aged.collection:delete()
      end
    end
    -- Find or create today's collection; append new picks.
    local today_coll = nil
    for _, entry in ipairs(list_morning_collections(catalog)) do
      if entry.date == today then
        today_coll = entry.collection
        break
      end
    end
    local photos = {}
    for _, item in ipairs(new_pick_photos or {}) do
      if item.photo then
        photos[#photos + 1] = item.photo
      end
    end
    if #photos == 0 then
      return
    end
    if not today_coll then
      local title = Core.morning_collection_title(#photos, os.time())
      today_coll = catalog:createCollection(title, nil, false)
    end
    if today_coll and today_coll.addPhotos then
      today_coll:addPhotos(photos)
      -- Refresh title count from membership.
      local members = today_coll:getPhotos() or photos
      local title = Core.morning_collection_title(#members, os.time())
      if today_coll.setName then
        today_coll:setName(title)
      end
    end
  end)
end

local function ensure_best_of_set(catalog)
  local sets = catalog:getChildCollectionSets() or {}
  for _, set in ipairs(sets) do
    if set:getName() == Core.BEST_OF_SET_NAME then
      return set
    end
  end
  if catalog.createCollectionSet then
    return catalog:createCollectionSet(Core.BEST_OF_SET_NAME, nil, true)
  end
  return nil
end

local function list_best_of_children(parent_set)
  local existing = {}
  if not parent_set or not parent_set.getChildCollections then
    return existing
  end
  for _, coll in ipairs(parent_set:getChildCollections() or {}) do
    local name = coll:getName()
    if Core.parse_best_of_collection_name(name) then
      local paths = {}
      for _, photo in ipairs(coll:getPhotos() or {}) do
        local path = photo_filepath(photo)
        if path then
          paths[#paths + 1] = path
        end
      end
      existing[name] = { collection = coll, filepaths = paths }
    end
  end
  return existing
end

local function photos_for_paths(snapshot, filepaths)
  local photos = {}
  for _, path in ipairs(filepaths or {}) do
    local state = snapshot[path]
    if state and state.photo then
      photos[#photos + 1] = state.photo
    end
  end
  return photos
end

--- Idempotent "Azimuth / Best of" set: one child collection per qualifying shoot.
maintain_best_of_collections = function(shoot_context)
  local context = shoot_context or {}
  local targets = Core.best_of_targets(context.best_of_shoots or {})
  local catalog = LrApplication.activeCatalog()
  local snapshot = scan_catalog()
  -- Collection sets are not usable as parents until the write gate returns
  -- (LR SDK). Ensure the set in its own gate, then mutate children.
  local parent = nil
  catalog:withWriteAccessDo("Azimuth best-of set", function()
    parent = ensure_best_of_set(catalog)
  end)
  if not parent then
    -- Re-resolve after commit in case createCollectionSet deferred the object.
    local sets = catalog:getChildCollectionSets() or {}
    for _, set in ipairs(sets) do
      if set:getName() == Core.BEST_OF_SET_NAME then
        parent = set
        break
      end
    end
  end
  if not parent then
    return
  end
  catalog:withWriteAccessDo("Azimuth best-of collections", function()
    local existing = list_best_of_children(parent)
    local plan = Core.plan_best_of_collections(targets, existing)
    for _, name in ipairs(plan.delete) do
      local entry = existing[name]
      if entry and entry.collection then
        entry.collection:delete()
      end
    end
    for _, item in ipairs(plan.upsert) do
      local coll = existing[item.name] and existing[item.name].collection or nil
      if not coll then
        coll = catalog:createCollection(item.name, parent, true)
      end
      if coll then
        local remove_photos = photos_for_paths(snapshot, item.remove)
        if #remove_photos > 0 and coll.removePhotos then
          coll:removePhotos(remove_photos)
        end
        local add_photos = photos_for_paths(snapshot, item.add)
        if #add_photos > 0 and coll.addPhotos then
          coll:addPhotos(add_photos)
        end
      end
    end
  end)
end

local function poll_once()
  local inbound = http_json("GET", "/api/lr/deltas?since=" .. tostring(Service.clock))
  if inbound and inbound.items then
    apply_inbound(inbound.items, inbound.shoot_context)
    if inbound.clock then
      Service.clock = math.max(Service.clock, tonumber(inbound.clock) or 0)
    end
  end
  if inbound and inbound.shoot_context then
    maintain_best_of_collections(inbound.shoot_context)
  end

  local current = scan_catalog()
  local observations = Core.diff_catalog(Service.last_scan, current)
  -- First scan seeds the baseline without flooding the satellite.
  if next(Service.last_scan) ~= nil then
    push_outbound(observations)
  else
    for filepath, state in pairs(current) do
      Core.ledger_remember(Service.ledger, filepath, "flag", state.flag, state.observed_at)
      if (tonumber(state.rating) or 0) > 0 then
        Core.ledger_remember(Service.ledger, filepath, "lr_rating", state.rating, state.observed_at)
      end
    end
    -- Remember which picks were already present when this LR session opened.
    if not prefs().morningBaseline then
      save_session_pick_baseline(current)
    end
    -- Age empty morning collections even when no new picks arrive.
    maintain_morning_collection({})
    persist_ledger(Service.ledger)
  end
  Service.last_scan = current
end

function Service.start()
  if Service.running then
    return
  end
  Service.running = true
  LrTasks.startAsyncTask(function()
    while Service.running do
      local ok, err = pcall(poll_once)
      if not ok then
        Service.status.last_error = tostring(err)
        Service.status.connected = false
      end
      LrTasks.sleep(POLL_SECONDS)
    end
  end)
end

function Service.stop()
  Service.running = false
end

function Service.get_status()
  Service.status.satellite_url = satellite_url()
  return Service.status
end

function Service.set_satellite_url(url)
  prefs().satelliteUrl = tostring(url or ""):gsub("/+$", "")
  Service.status.satellite_url = prefs().satelliteUrl
end

-- Export observed RAW→render via LrExportSession hooks when available.
function Service.report_export(source_path, export_path)
  if not source_path or not export_path then
    return
  end
  http_json("POST", "/api/lr/exports", {
    source_filepath = source_path,
    export_filepath = export_path,
  })
end

_G.AzimuthSyncService = Service
return Service
