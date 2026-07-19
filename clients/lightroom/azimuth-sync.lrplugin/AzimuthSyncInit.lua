--[[ Start the background Azimuth sync task when the plugin loads. ]]

local LrPathUtils = import("LrPathUtils")
local Service = dofile(LrPathUtils.child(_PLUGIN.path, "AzimuthSyncService.lua"))

Service.start()
