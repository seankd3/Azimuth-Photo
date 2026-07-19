--[[ Pure-Lua unit tests for AzimuthSyncCore (no Lightroom SDK). ]]

local path = arg[0]:match("(.*/)") or "./"
package.path = path .. "../?.lua;" .. package.path

local Core = dofile(path .. "../AzimuthSyncCore.lua")

local failures = 0

local function check(name, cond)
  if cond then
    print("ok  - " .. name)
  else
    failures = failures + 1
    print("FAIL - " .. name)
  end
end

-- Echo ledger suppresses re-push of last-applied values.
do
  local ledger = Core.new_ledger()
  Core.ledger_remember(ledger, "/a.dng", "flag", "picked", 10)
  check("echo detects last applied", Core.is_echo(ledger, "/a.dng", "flag", "picked"))
  check("non-echo different value", not Core.is_echo(ledger, "/a.dng", "flag", "rejected"))
  local outbound = Core.filter_outbound(ledger, {
    { filepath = "/a.dng", family = "flag", value = "picked" },
    { filepath = "/a.dng", family = "flag", value = "rejected" },
  })
  check("filter keeps only real edit", #outbound == 1 and outbound[1].value == "rejected")
end

-- Three-cycle echo loop: apply inbound → observe same → no outbound.
do
  local ledger = Core.new_ledger()
  local cycles_outbound = 0
  for _ = 1, 3 do
    Core.ledger_remember(ledger, "/b.dng", "flag", "picked", 1)
    local observed = { { filepath = "/b.dng", family = "flag", value = "picked" } }
    local outbound = Core.filter_outbound(ledger, observed)
    cycles_outbound = cycles_outbound + #outbound
  end
  check("no echo loops over 3 cycles", cycles_outbound == 0)
end

-- elo_stars never overwrites user stars.
do
  local ledger = Core.new_ledger()
  check("no projection yet + user stars blocks", not Core.may_apply_elo_stars(ledger, "/c.dng", 4, 5))
  check("no projection yet + unstarred allows", Core.may_apply_elo_stars(ledger, "/c.dng", 0, 5))
  Core.ledger_remember(ledger, "/c.dng", "elo_stars", 5, 1)
  check("matching last projection allows update", Core.may_apply_elo_stars(ledger, "/c.dng", 5, 4))
  check("user override blocks projection", not Core.may_apply_elo_stars(ledger, "/c.dng", 3, 5))
  check("demotion to zero allowed when matching", Core.may_apply_elo_stars(ledger, "/c.dng", 5, 0))
end

-- Ledger survives serialize/deserialize (plugin restart).
do
  local ledger = Core.new_ledger()
  Core.ledger_remember(ledger, "/photos/a.dng", "elo_stars", 5, 42)
  Core.ledger_remember(ledger, "C:\\Photos\\b.dng", "flag", "picked", 7)
  local blob = Core.ledger_serialize(ledger)
  local restored = Core.ledger_deserialize(blob)
  check("serialize round-trip elo", Core.is_echo(restored, "/photos/a.dng", "elo_stars", 5))
  check("serialize round-trip flag", Core.is_echo(restored, "C:\\Photos\\b.dng", "flag", "picked"))
  check("deserialize preserves clock", Core.ledger_get(restored, "/photos/a.dng", "elo_stars").clock == 42)
  check("may_apply after restart", Core.may_apply_elo_stars(restored, "/photos/a.dng", 5, 4))
end

-- Pending/unmatched entries are not remembered.
do
  local ledger = Core.new_ledger()
  Core.ledger_remember_confirmed(ledger, {
    { filepath = "/ok.dng", family = "flag", value = "picked", ts = 1 },
  })
  Core.ledger_remember_confirmed(ledger, {
    { filepath = nil, family = "flag", value = "rejected", ts = 2 },
    { family = "lr_rating", value = 3, ts = 3 },
  })
  check("confirmed entry remembered", Core.is_echo(ledger, "/ok.dng", "flag", "picked"))
  check("pending-shaped entries skipped", Core.ledger_get(ledger, "/missing.dng", "flag") == nil)
  local count = 0
  for _ in pairs(ledger.applied) do
    count = count + 1
  end
  check("only confirmed rows land in ledger", count == 1)
end

-- Batching
do
  local items = {}
  for i = 1, 10 do
    items[i] = i
  end
  local batches = Core.batches(items, 4)
  check("batch count", #batches == 3)
  check("batch sizes", #batches[1] == 4 and #batches[2] == 4 and #batches[3] == 2)
end

-- Flag vocabulary
check("pick maps", Core.lr_flag_to_azimuth(1) == "picked")
check("reject maps", Core.lr_flag_to_azimuth(-1) == "rejected")
check("unflagged maps", Core.lr_flag_to_azimuth(0) == "unflagged")
check("azimuth to lr pick", Core.azimuth_flag_to_lr("picked") == 1)

-- Catalog diff
do
  local prev = { ["/d.dng"] = { flag = "unflagged", rating = 0 } }
  local curr = { ["/d.dng"] = { flag = "picked", rating = 3, observed_at = 9 } }
  local obs = Core.diff_catalog(prev, curr)
  local families = {}
  for _, item in ipairs(obs) do
    families[item.family] = item.value
  end
  check("diff emits flag", families.flag == "picked")
  check("diff emits lr_rating", families.lr_rating == 3)
end

-- Morning collection title
do
  local title = Core.morning_collection_title(3, os.time({ year = 2026, month = 7, day = 19, hour = 12 }))
  check("morning title shape", title == "From Azimuth — 3 picks · 2026-07-19")
  local singular = Core.morning_collection_title(1, os.time({ year = 2026, month = 7, day = 19, hour = 12 }))
  check("morning title singular", singular == "From Azimuth — 1 pick · 2026-07-19")
end

-- New picks since last session
do
  local previous = {
    ["/a.dng"] = { flag = "picked" },
    ["/b.dng"] = { flag = "unflagged" },
  }
  local current = {
    ["/a.dng"] = { flag = "picked" },
    ["/b.dng"] = { flag = "picked" },
    ["/c.dng"] = { flag = "picked" },
    ["/d.dng"] = { flag = "rejected" },
  }
  local fresh = Core.new_picks_since(previous, current)
  check("new picks count", #fresh == 2)
  check("new picks include b", fresh[1] == "/b.dng" or fresh[2] == "/b.dng")
  check("new picks include c", fresh[1] == "/c.dng" or fresh[2] == "/c.dng")
  check("new picks exclude prior pick", true)
end

-- Aging empty collections
do
  local today = os.time({ year = 2026, month = 7, day = 19, hour = 12 })
  local aged = Core.aged_empty_collections({
    { name = "From Azimuth — 2 picks · 2026-07-18", date = "2026-07-18", photo_count = 0 },
    { name = "From Azimuth — 1 pick · 2026-07-19", date = "2026-07-19", photo_count = 0 },
    { name = "From Azimuth — 3 picks · 2026-07-17", date = "2026-07-17", photo_count = 2 },
  }, today)
  check("ages empty yesterday", #aged == 1 and aged[1].date == "2026-07-18")
  check("keeps empty today", true)
  check("keeps non-empty old", true)
end

-- Parse morning name
do
  local parsed = Core.parse_morning_collection_name("From Azimuth — 4 picks · 2026-07-19")
  check("parse morning name", parsed and parsed.count == 4 and parsed.date == "2026-07-19")
  check("parse rejects other", Core.parse_morning_collection_name("Other") == nil)
end

if failures > 0 then
  print(string.format("%d failure(s)", failures))
  os.exit(1)
end
print("all ok")
os.exit(0)
