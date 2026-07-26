--[[
  Azimuth Sync — Lightroom Classic plugin (SDK 13 surface).
  Info.lua: plugin identity + Library menu entry.
]]

return {
  LrSdkVersion = 13.0,
  LrSdkMinimumVersion = 10.0,
  LrToolkitIdentifier = "com.azimuth.sync",
  LrPluginName = "Azimuth Sync",
  LrPluginInfoUrl = "https://github.com/seankennethdoherty/azimuth-photo",
  LrLibraryMenuItems = {
    {
      title = "Azimuth Sync",
      file = "AzimuthSyncDialog.lua",
    },
  },
  LrInitPlugin = "AzimuthSyncInit.lua",
  LrShutdownPlugin = "AzimuthSyncShutdown.lua",
  VERSION = { major = 1, minor = 0, revision = 0 },
}
