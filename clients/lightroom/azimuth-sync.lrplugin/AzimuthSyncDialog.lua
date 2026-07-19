--[[
  One status dialog: connection, synced/pending counts, satellite URL, last error.
]]

local LrDialogs = import("LrDialogs")
local LrView = import("LrView")
local LrBinding = import("LrBinding")
local LrFunctionContext = import("LrFunctionContext")
local LrPathUtils = import("LrPathUtils")

local Service = dofile(LrPathUtils.child(_PLUGIN.path, "AzimuthSyncService.lua"))

LrFunctionContext.postAsyncTaskWithContext("Azimuth Sync status", function(context)
  local f = LrView.osFactory()
  local props = LrBinding.makePropertyTable(context)
  local status = Service.get_status()
  props.satelliteUrl = status.satellite_url or "http://127.0.0.1:8000"
  props.summary = string.format(
    "Connected: %s\nSynced: %d\nPending: %d\nLast error: %s",
    status.connected and "yes" or "no",
    tonumber(status.synced) or 0,
    tonumber(status.pending) or 0,
    status.last_error or "(none)"
  )

  local contents = f:column({
    bind_to_object = props,
    spacing = f:control_spacing(),
    f:static_text({ title = "Azimuth Sync", font = "<system/bold>" }),
    f:static_text({ title = "Satellite URL" }),
    f:edit_field({ value = LrView.bind("satelliteUrl"), width_in_chars = 40 }),
    f:static_text({ title = LrView.bind("summary"), height_in_lines = 6, width_in_chars = 44 }),
  })

  local result = LrDialogs.presentModalDialog({
    title = "Azimuth Sync",
    contents = contents,
    actionVerb = "Save",
  })
  if result == "ok" then
    Service.set_satellite_url(props.satelliteUrl)
  end
end)
