-- Lightroom plugin Lua — declare LR SDK globals; do not ignore them wholesale.
std = "lua54"
max_line_length = false

globals = {
    -- Adobe Lightroom SDK entry points (plugin host injects these)
    "import",
    "_PLUGIN",
}
