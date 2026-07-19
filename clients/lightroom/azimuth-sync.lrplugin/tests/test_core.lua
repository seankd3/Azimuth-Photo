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

if failures > 0 then
  print(string.format("%d failure(s)", failures))
  os.exit(1)
end
print("all ok")
os.exit(0)
