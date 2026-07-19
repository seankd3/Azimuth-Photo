--[[ Stop the background poll when Lightroom unloads the plugin. ]]

local LrPathUtils = import("LrPathUtils")
local Service = dofile(LrPathUtils.child(_PLUGIN.path, "AzimuthSyncService.lua"))

Service.stop()
