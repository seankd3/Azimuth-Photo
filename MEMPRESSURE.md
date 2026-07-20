# MEMPRESSURE.md — swap-aware pressure + idle model shed

Lane: `impl-mempressure` (worktree off `develop`). Does not touch prod.

## Signal chosen

**Periodic poll of cgroup memory**, not `MEMORY_PRESSURE_WATCH` PSI.

Priority:

1. **cgroup** — `memory.current` + `memory.swap.current` under the process cgroup
   (`/proc/self/cgroup` → `/sys/fs/cgroup/...`). This is what systemd actually
   accounts (live photoarchive: ~2.3G current + ~3.1G swap ≈ 5.4G combined).
2. **proc** — `VmRSS` + `VmSwap` from `/proc/self/status` if cgroup files are
   missing.
3. **RSS-only** — last resort.

PSI (`MEMORY_PRESSURE_WATCH`) remains unused: a poll of cgroup current is
enough for bulk gating, needs no fd watch loop, and matches the same numbers
`systemctl show` reports. Revisit PSI only if we need event-driven wakeups.

## Thresholds + rationale

Defaults are derived from cgroup limits with **2 GiB headroom**:

| Knob | Default on this host | Source |
|------|----------------------|--------|
| soft | **6 GiB** | `MemoryHigh` (8G) − 2G |
| hard | **10 GiB** | `MemoryMax` (12G) − 2G |
| resume | soft − 512 MiB (hysteresis) | existing formula |

Env overrides still win: `PHOTOARCHIVE_MEMORY_SOFT_BYTES`,
`PHOTOARCHIVE_MEMORY_HARD_BYTES`, `PHOTOARCHIVE_MEMORY_RESUME_BYTES`.

**Why not the old 5/7 GiB RSS numbers?** Those gated **RSS only**, so a process
with ~1.1G RSS and ~3G swap looked “within watermarks” while the box was
deep in swap. The gate now compares the **combined** figure to soft/hard.

**Conservative by construction:** false pauses of bulk work are worse than
slightly late shedding. Soft at 6G leaves normal browse (~1G RSS + modest
swap) far below the line. Soft only trips when combined residency is already
approaching MemoryHigh.

Idle model TTL: `PHOTOARCHIVE_MODEL_IDLE_TTL_SECONDS` (default **120s**).
Manual pause sheds as soon as the interactive pin window ends (no wait for
TTL).

## Before / after

| Situation | Before | After |
|-----------|--------|-------|
| RSS 1.1G, swap 3G, combined ~4–5G | Health “within watermarks”; no pause/shed | Health shows combined + swap; still under soft (6G) — no false pause |
| RSS 1.1G, swap pushes combined ≥ soft | Blind / green | Soft pause + buffer release + unpinned model unload |
| Worker paused, model still in VRAM | Pause called unload, but pin/pressure paths could fight | Pause/idle unload; **pin blocks** mid-search; shed resumes when pin expires |
| Interactive search in flight | `unload_all` could yank the model | `unload` / `unload_all(force=False)` skip pin-while-hot |

## Never unload mid-search (proof)

1. Interactive search `acquire(..., interactive=True)` sets pin-while-hot
   (`PHOTOARCHIVE_MODEL_PIN_SECONDS`, default 30s).
2. `ModelPool.unload` / `unload_all(force=False)` refuse pinned residents.
3. `memory_pressure.request_model_unload()` and
   `embedding_worker._unload_model()` honor that (shutdown uses `force=True`).
4. Tests:
   - `test_pressure_unload_skips_pinned_search_model`
   - `test_pressure_unload_all_skips_pinned`
   - `EmbeddingIdleUnloadTests.test_pause_unload_blocked_while_search_pinned`

Cold-reload on the next search is acceptable; a stall mid-search is not.

## Risk notes

- Soft/hard are higher than the old RSS thresholds in absolute GiB, but the
  metric is stricter (includes swap). Net: fewer false greens, still calm
  under normal browse.
- If cgroup is unreadable (odd container), we fall back to proc RSS+swap;
  watermarks fall back to 6G/10G constants.
- Pin window is time-based: a search longer than the pin TTL could become
  unloadable under hard pressure. Interactive acquires refresh the pin;
  extend `PHOTOARCHIVE_MODEL_PIN_SECONDS` if long searches need a longer hold.
- Embed-cache mmap residency is **out of scope** for this lane.
