# Azimuth — The Dev Log Story (narrative spine)

Source of truth: `research/full_log.txt` (1,562 commits, 2023-08-11 → 2026-07-20).
Commits/month: 2023-08 (8), 2023-09 (1), 2024-08 (3), 2026-02 (2), 2026-04 (222), 2026-05 (161), 2026-06 (32), 2026-07 (1133).

Thesis line: *A weekend "which photo is better?" toy became an AI-native Lightroom — a physically-modeled darkroom, a three-device photo platform, and finally a codebase built out by a fleet of AI agents. Every chapter is a lesson about speed, honesty, or data you can't afford to lose.*

Key running doctrines (recur across eras): **fastest photo app ever** (profile-first, never widen a budget to hide slowness); **the app never lies about state** (error-honesty); **the laptop never waits on the server** (local-first); **your originals are sacred** (collision-proof before deletion, hash-gated).

---

## Ch 0 — PhotoRanker: "which one is better?" (Aug 2023 – Aug 2024) · 12 commits
- **Commits:** f60ab3c16 *Initial commit* → 82e2887d7 *Basic funtion* (sic) → … → 4586172aa *Added Controller Support*.
- **What it is:** a 228-line Python/tkinter desktop app. Show two photos side by side, click the better one, update Elo. `Image.MAX_IMAGE_PIXELS=None`, DNG via rawpy, a threaded preload `Queue`, and pairing biased toward the current top-10 so the best photos keep fighting.
- **The dream:** a photographer with tens of thousands of frames can't rank them on a 1–5 star scale honestly. But you *can* always answer "this one or that one?" Elo turns thousands of tiny binary judgments into one global order.
- **Trade-offs / seeds:** K-factor 32 when ratings are close, 16 when far (commit's own `update_elo_rank`). Everything keyed by *filename* in one `elo_ratings.json`. Blacklisting + Xbox controller support (2024) — built for long couch culling sessions.
- **Then:** silence. 18 months. The idea was right; the shell was a toy.
- **Capture:** G1 — run `Main.py @ d97f2aae5` on the XPS, tkinter two-up comparison window.

## Ch 1 — The Web Rebirth (Feb 2026) · 2 commits
- **Commits:** db3b4ad3a *Add web-based PhotoRanker with mosaic ranking system*; 60cb19f39 *Polish mosaic grid compare and rankings page*.
- **What changed:** the same Elo idea, reborn in the browser. Not just two-up — a **mosaic**: a grid of candidates, pick the standout, everything else takes a small loss. More judgments per click.
- **The dream:** leave the desktop toy behind; make it live where the library lives.
- **Capture:** M1 — `60cb19f39`, mosaic compare + rankings page.

## Ch 2 — The Mind Awakens (Apr 21, 2026) · one astonishing day (~16 commits)
- **Arc in a day:** CLIP active learning → unified bottom bar → AI panel + *effective Elo pairing* + AI-ranked sort → **Library page** (justified grid, AI search, ViT-L/14) → **Qwen3-VL-Embedding-8B** (SOTA search) → **rename PhotoRanker → Azimuth Photo** (fa1594a8f) → lightbox, Find Similar, duplicate detection, EXIF, k-means auto-collections, folder browse, batch export → **Qwen3-VL-2B int4** (c0d7f5441, "fits GPU alongside other apps").
- **The dream:** the computer should *understand* the pictures. Not tags you typed — embeddings. Search "golden hour over water" and it just works. Rank one photo and its look-alikes move with it.
- **Trade-offs:** 8B was state-of-the-art but hogged the GPU; dropped to 2B-int4 so it could live beside Sean's other resident models on one 8GB card. The first appearance of a theme that never leaves: **sharing one small GPU is a design constraint, not an afterthought.**
- **Capture:** M2 — `c0d7f5441`, the newborn Azimuth Photo Library + AI panel.

## Ch 3 — The Need for Speed (Apr 22–25, 2026) · the doctrine is born
- **Benchmark cascade (real commit subjects):**
  - 6b2f46443 — thumbnail serving **30–50× faster** via in-memory path index (stop `os.stat`-ing the HDD on every request).
  - 3ea37e76b — composite indexes + folders cache: sorting/filtering **5–20× faster**.
  - bf6a3e2ed — shared embedding cache: search/similar/duplicates near-instant.
  - 73e… pipeline embedding CPU/GPU stages; remove sleeps; parallel frontend init; sessionStorage prefetch.
  - b15e1f4b1 → 601846879 — diverse mode: subsample 20k→200, then full-matrix greedy **farthest-point** with no pool limit.
- **UX in the same breath:** Lightroom-style loupe + filmstrip (12db7d6dd), transform-based zoom/pan (e5527b519), design tokens + focus-visible + a11y + toast queue + skeletons + 3-level undo (the Apr-25 wave ending b28564460).
- **Elo propagation matures:** rank one photo, its embedding-neighbors move too — cubic nonlinear falloff, 20→100 neighbors, undoable, with a "+N propagated" badge (93e12896e, 7dfcb7a17).
- **The dream / doctrine:** *the fastest photo app ever made.* Profile first; never widen a timing budget to mask slowness. This is where that becomes law.
- **Capture:** M3 — `b28564460`, the mature-April library + loupe.

## Ch 4 — The Great Modularization (May 17–18, 2026) · ~130 commits in 48h
- **What happened:** a near-fanatical refactor — "refactor: drain X facades" over and over. App shell extracted, thumbnail cache engine split into a package, frontend split into modules, every route facade drained, every controller extracted (loupe, compare, mosaic, date-scrubber, pregeneration…). A codebase organization map (7286970ed).
- **The dream:** Sean's rule — *super modular, no giant monolith files.* You can't build a darkroom on top of a 10k-line `app.py`.
- **Trade-off / lesson:** the least glamorous chapter is the one that made every later chapter possible. Also: 105d8d250 wraps schema setup in transactions — a boring perf win that quietly matters at 139k rows.
- **Also May:** governed deep-search cache, background-work status panel, offline-source embeddings stay searchable.
- **Capture:** M4 — `149d8ba55`, post-refactor library with the background-work panel (visually near M3 + a work banner — decide after seeing M3).

## Ch 5 — The Companion (Jun 2026) · Android is born · 32 commits
- **Commits:** 59240aab8 *self-hosted Android companion* → f9a9d27a9 *Build Android Azimuth Photo app* → a native Kotlin/Compose burst (Jun 20–21): ZoomableImageView (pinch/swipe), fullscreen PhotoViewer, A/B CompareView, flag/EXIF/share/save, grid flag badges, haptics, edge-feedback, e2e + no-phone smoke gates.
- **The product vision (625a0d49f):** three faces, one library. **mobile = Google Photos; desktop = Lightroom Classic.** The fleet (XPS UI · omarchy hub · Pixel client) becomes the architecture.
- **Also:** persistent collections foundation, collection picker.
- **Capture:** (Android build is heavy — likely narrate + use later Android shots; primary web captures continue.)

## Ch 6 — "The One" Redesign (Jul 6–8, 2026) · /d desktop + /m mobile
- **UI architecture charter (11efdb0dd, 85ce7421a):** two experience bars — mobile=Google Photos, desktop=LR Classic.
- **Shipped:** `/d` desktop "one" shell with real writes (9b444ff57); `/m` mobile one-token reskin (48ea67daf, 68b3cae3c); **Stacks** primitive (variant/burst/cross-source, schema v12); **Safe trash** (v13); **Share links** (public galleries behind revocable tokens, passwords, analytics); **Smart collections** (query-backed, live counts, frozen share snapshots); **Search v2** (VLM caption worker + understanding index + RRF retrieval fusion, v15). Legacy UI deleted (c080da2d3).
- **The GPU war (real commits):** the 4-bit 7B caption VLM vs the resident voice daemon on one 8GB card — 8cf59a6d2/d03ec12cd/68c3b2ad8/499… settle on **Qwen2.5-VL-3B**, cap vision input at 1280px ("uncapped previews demand >9GiB of attention memory on an 8GB card"). Honest engineering against a hard limit.
- **Capture:** M5 — `9b444ff57`/Jul-8, the `/d` desktop hero. (Committed Jul-8 screenshots exist: library-grid, loupe, loupe-lights-out, refine-mosaic, mobile-library — period-accurate for this era.)

## Ch 7 — The Darkroom (Jul 9–11, 2026) · the crown of engineering · dev1→dev7
This is the technical peak. Azimuth Photo stops being a *manager* and becomes an *editor* aiming to beat Lightroom, darktable, Affinity — pillar by pillar (master plan 5a71428cb).

- **Develop core (8d8e97cfb, 874b8a705):** schema v21, rawpy linear base + SSD LRU cache + pregen; WebGL2 live pipeline (RGBA16F), Lightroom-ordered panels, scrubby sliders, interactive tone curve, HSL/B&W, histogram w/ clipping, crop, before/after, autosave + history. **The "twin" contract:** every op exists twice — numpy (export truth) and GLSL (live preview) — pinned to a shared `PARITY_TABLE`. *53 ops exact, pixel deltas <0.4/255.*
- **Lossy-DNG decode (e68b0973e):** LibRaw can't unpack JPEG-XL / DNG 1.7. Decode the LinearRaw SubIFD pyramid via tifffile+imagecodecs with full DNG color math (black/white levels, AsShotNeutral WB, ForwardMatrix→XYZ D50, Bradford→linear sRGB, BaselineExposure). **2048px in 0.4s.**
- **Color science pixel-matched to Lightroom (83a4f6346, dev v1.5–1.7):** camera-space WB matrix (M·diag·M⁻¹, dual-illuminant, iterative CCT), base profile curve+sat fit against Sean's real LR exports ("B&W pair near-indistinguishable"). Per-camera profiles fit from RAW-vs-LR pairs (R5/R7/RP). **467 tests green.**
- **Masking (e77da3532, df38deecb):** Adobe-schema corrections rendered in *both* twins — brush/linear/radial/luminance-range/color-range (JS↔numpy match 2e-6), R8 atlas + 16-correction shader loop, rubylith. **AI masks:** U2Net subject seg + heuristic sky. **HDR merge** (found 6 real brackets; phase-correlation align; radiance-weighted→EXR). **Panorama** (OpenCV stitch). **73 real LR presets** imported. **.lrcat import** (picks/ratings/collections; a safe Lua-table parser for Adobe develop settings).
- **THE FILM ENGINE (cc6071721, §26) — the jewel:** *physically-modeled.* Spectral layer exposure; **halation modeled at the exposure stage** — CineStill 800T's red glow *emerges from the physics* (no-remjet params, verified on a real floodlight frame); H&D curve LUTs; DIR coupler matrix; density-dependent clumped per-layer grain; per-channel speed-point print calibration that neutralizes the orange mask. 8 stocks datasheet-anchored, then **tuned against 53 real San Marcos lab scans** (Portra 400 / Gold 200 / Fuji 400 / HP5). JS/GL twin mirrors `film.py` exactly (7b6333f40).
- **DNG camera color pipeline (§30, 8c7dbdc6e, 631…):** Adobe-identical defaults harvested from the DNG corpus's embedded profiles — ForwardMatrix interpolation, ProPhoto, HueSatMap/LookTable, the exact ACR3 default tone curve; GL twin.
- **Waves dev3–dev7 (582→611 tests):** color-grading wheels, NR, defringe, distortion auto-crop, clone/heal, **RAW-edit version stacks**, technical quality scorer (face-region sharpness/clip/blur), transform/upright (homography twins + Hough auto-level), **Develop for JPEG/TIFF too** ("the whole 47k library is now editable"), virtual copies + snapshots + history rail, **catalog time machine** (gzip-verified DB snapshots). Film panel goes **LIVE** on canvas at dev7 (385db0b7c).
- **Also:** geodata (GPS backfill, Google Timeline import), keywords/IPTC.
- **Trade-off/lesson:** the twin discipline is the whole game — a fast lie (GL) and a slow truth (numpy) that must agree to <0.4/255, enforced by goldens. That's how you get a *live* editor that still exports correctly.
- **Capture:** M6 — `874b8a705` develop editor; M7 — `385db0b7c` film panel live (CineStill halation on canvas).

## Ch 8 — The Field & The Fleet coordination (Jul 11–12) · one library everywhere
- **FIELD_SPEC (a0a2fb190, c616fa297):** satellite mode + hub sync contract — catalog mirror, thumb tiers, predictive prefetch, **oplog convergence**. Watched folders. Windows Tauri shell folded into the repo (eb75b50ad). mDNS hub discovery, pair codes, device tokens, standalone mode + first-run wizard.
- **The fleet becomes literal:** lane branches (wt-lane-a/b/c/d) merge in — the multi-agent build structure surfaces in the git graph.
- **Lesson:** the XPS is a *satellite* that must never wait on the omarchy *hub*; sync is an oplog that converges, not a request that blocks (local-first doctrine, made concrete).

## Ch 9 — Azimuth (Jul 12) · the rebrand
- c5fc4579a / 6c3b13f85: **Azimuth Photo identity established** across the wordmark, PWA manifest, Android launcher, and Tauri product name. Bundle IDs moved to `app.azimuthphoto.*` (5eb678c3c); the later consolidation completed the operational identifiers and storage layout.
- **Distribution:** Docker image + compose + **Unraid template** (a911715ce), INSTALL.md for NAS hosts. Full **Google-Photos-replacement Android client** (d37e68792: timeline, auto-backup, archive browse, free-up-space). DISTRIBUTION_SPEC (installers, first-run wizard, pairing/mDNS, guided Tailscale).
- **Lesson (from a real commit):** d4ea3fa30 — "commit the phone client source (was untracked on every machine — git is the backup)." The OneDrive path isn't a backup; git is.

## Ch 10 — Works to Shippable (Jul 13) · release hardening begins
- RELEASE_POLISH program (b429b774a). Version handshake, release pipeline, update notice. **Backup-before-migrate** — protected snapshot before any schema upgrade (a0c42f9a9). Catalog recovery drills.
- **Security:** contain media within source roots, reject path-like sync filenames, bound bulk work. **P0-3 (4a35e5728):** an empty online scan could mark the whole library missing — data-loss guard added.
- Taxonomy import routing (4 destinations), Contrast2012 bounded S-curve (0e738de31, replaces a clipping linear stretch). Bug-tracker waves (23 fixed / day).

## Ch 11 — The Quality Program (Jul 15–16) · the loop-until-dry marathon
- **The org model becomes explicit (c1b5b263e, c141db869):** session-lane charter — CEO/CTO/managers/subagents, "one session owns one lane," lane is canonical not title. Hundreds of `qfix-*`, `ux*`, `errhonesty` merges. This is the AI-fleet building the app in the open, with **cross-model review** (Grok adversarial passes confirm regressions — 8e70df4dc).
- **The doctrine that names this era — error-honesty:** the app never lies about state. Toasts only after commit; honest empty/loading/preparing states; worker wait states read as active; "photos exist on screen immediately, never a void" (placeholder-cards spec, ux11/ux14). **Four Laws** ONE_SURFACE spec for hub/desktop/mobile/android unity (450aa41c9).
- **Benchmarks (real commit subjects):** boot **65s→~5s** (59758bc2e: quick_check only after unclean shutdown); search **2.1s→0.7s cold** (e7c7d55ae: targeted IN-query, not 87k-set materialization); People/map **instant at catalog scale** (60ef05985); `/api/stats` **<200ms on 139k** (633d97173); Develop-open **483→30ms** (b310546c7: on-demand Adobe profile store); suggestions tag-pair join **10.5s → in-memory** (f4aa36f85).
- **Owner auth (41802a141…e340a84ca):** default-deny on every non-public route; closes 3 hardening criticals including a verified `publish_hook` **RCE** (made server-side-only). Mint the owner key once during setup.
- **Data safety, the near-miss (d6de3529a):** the backup manifest counted trashed/missing/mirror rows as "backed up" — **Free-up-space could delete the phone's last copy.** Fixed. Virtual-copy families now trash/restore atomically (no silent edit loss). Backup-gated destructive migrations.
- **Windows QA (f2e9be7e4… 1cbb60da2):** first Windows runner — **95 platform failures → zero**, parity proven on real GPU hardware. `%-d` is glibc-only and crashed Windows; pre-1970 dates threw `OSError`; portable everything.

## Ch 12 — Reconciliation, Incidents & the Final Waves (Jul 16–20)
- **Main/develop reconciliation (46da61f01):** 87 prod-side commits absorbed + 3 explicit ports.
- **The Holland incident (P1-D, 3d5…, sync-honesty):** satellite sync status must never lie — pause is a state, unknown reads as "recovering." **Import incident P0s (de86a13c1):** byte-verified finalize, locked placement, byte-proof "known." **OOM watermarks (2dd1f33cc):** RSS pressure pauses bulk work, models yield, decode working-set bounded.
- **The Backup Incident, Jul 19 (5c07f6344):** *"nightly 04:00 cross-instance collision left prod with zero sealed snapshots."* Root cause: shared tmp names across instances. Fix: per-process tmp names + orphaned-tmp sweep + **weekly restore drill proven against a real 150k-image snapshot** (7f6857065). The scariest kind of bug — the backup that isn't there when you need it.
- **The darktable payoff (dt-gfilter, dt-noise, highlight recon):** guided-filter masking (classic + EIGF), opposed-channel **highlight reconstruction** during raw decode, measured camera **noise profiles** driving NR defaults — the adopt-list from the earlier darktable study finally lands.
- **LR Bridge (lrbridge, lrux):** cull anywhere, edit in Lightroom, rank in Azimuth. `elo_stars` projection at delta-read time; a Lua LR-Classic plugin; one-click Connect installs it; "morning collection" of Azimuth picks; "stars are global, sets are local."
- **Client auto-update (53426f21d):** the hub carries its clients — hub-served bundles, satellite self-update with atomic flip + two-failed-boots rollback.
- **In-app Cloud Backup (94b3cf15f):** universal rclone vault. **System Health panel.** **ModelPool (2e0a60a74):** LRU VRAM/RAM time-sharing by declared cost, pin-while-hot, single-flight, pressure shed.
- **Test/lint industrialization:** pytest-xdist parallel suite + tiered gates (4d2c62e5d); ruff+eslint+luacheck **zero-warning baseline**; CI polls origin/develop every 5m and ntfy's on gate transitions.
- **The final overnight throughput/local-first waves (gxsat, gxseq, gxlat, gxclient, hddgov, snappy, wavefix):** server runs full-tilt, interactivity protected by **isolation not throttling**; laptop **local-first adaptive thumb budget** (auto from free disk, newest-first, fill during browse); **HDD governor** — every read pays maximum value; SWR thumb service worker; pregen candidate selection by cache anti-join (O(pending), not O(walked)).
- **Ends** at b9a701bac — a one-line test fix. Still going.
- **Capture:** M8 — `49823da21` staged import canvas; M9 — current Azimuth (grid, loupe, develop, film, map/people, mobile).

---

## Benchmarks table (for a "measured wins" panel)
| Win | Before | After | Commit |
|---|---|---|---|
| Thumbnail serving | disk stat/req | **30–50× faster** (in-mem path index) | 6b2f46443 |
| Sort/filter | — | **5–20× faster** (composite indexes) | 3ea37e76b |
| Cold boot | 65 s | **~5 s** (quick_check only after unclean shutdown) | 59758bc2e |
| Cold search | 2.1 s | **0.7 s** (targeted IN-query) | e7c7d55ae |
| Develop open | 483 ms | **30 ms** (on-demand Adobe profile store) | b310546c7 |
| `/api/stats` @139k | — | **<200 ms warm** | 633d97173 |
| Suggestions join | 10.5 s SQL | **in-memory** | f4aa36f85 |
| Sync-status poll | — | **<3 ms** (memoized) | b54d7e7aa |
| Lossy-DNG 2048px decode | (LibRaw fails) | **0.4 s** (LinearRaw pyramid) | e68b0973e |
| Twin parity | — | **53 ops exact, <0.4/255** | 874b8a705 |
| Windows QA | 95 fails | **0** | 1cbb60da2 |
| Boot import trim | — | **−215 ms** (cv2/numpy off boot path) | f829454d9 |
| Develop-open profile copies | — | **−53%** | a272b5afa |

## Incidents & near-misses (for a "war stories" panel)
1. **Free-up-space could delete your last copy** (d6de3529a) — manifest counted trashed/missing/mirror as backed up.
2. **Prod had zero valid backups** (5c07f6344) — 04:00 cross-instance tmp collision; fixed + weekly restore drill on real 150k snapshot.
3. **publish_hook RCE** (e340a84ca) — shell hook was client-settable; made server-side-only + owner auth.
4. **Empty scan wiped the library** (4a35e5728, P0-3) — online scan of an offline source marked everything missing.
5. **Caption VLM OOM on an 8GB card** (8cf59a6d2) — whole-model GPU placement; clean OOM recoverable; settle on 3B + 1280px cap.
6. **Prod warm-loop SIGKILL** (memory: 07-16) — never bulk-drive on-demand generation against prod; budgeted pregen only.
7. **Windows `%-d` / pre-1970 dates** — glibc-isms crashed the Windows runner.

## Capture manifest
| ID | Commit | Era | Target views |
|---|---|---|---|
| G1 | d97f2aae5 | 2023 PhotoRanker | tkinter two-up compare |
| M1 | 60cb19f39 | Feb 2026 web | mosaic compare, rankings |
| M2 | c0d7f5441 | Apr-21 Azimuth Photo born | Library grid, AI search panel |
| M3 | b28564460 | Apr-25 mature | Library, loupe |
| M4 | 149d8ba55 | May-18 post-refactor | Library + background-work panel |
| M5 | 9b444ff57 | Jul-08 "/d" one | desktop grid, refine mosaic, loupe |
| M6 | 874b8a705 | Jul-10 Develop | WebGL2 editor, tone curve |
| M7 | 385db0b7c | Jul-10 Film | Film panel live (halation) |
| M8 | 49823da21 | Jul-15 Import | staged import canvas |
| M9 | HEAD | Now | grid, loupe, develop, film, map/people, /m mobile |
