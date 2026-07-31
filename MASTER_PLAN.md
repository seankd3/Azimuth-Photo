# Azimuth Photo Master Plan

The owner document for **what Sean asked for and whether it is done**.

Section 1 is the authority: Sean's own words, verbatim, dated, from his prompts.
Nothing in it is paraphrased, summarised, or improved. When a plan and a quote
disagree, the quote wins. Section 2 is the derived work queue. Section 3 says how
to keep this file honest.

Product doctrine lives elsewhere and is not duplicated here:
[product vision](docs/product-vision.md) ·
[roadmap](docs/product-roadmap.md) ·
[perf budgets](docs/PERF_BUDGETS.md) ·
[agent rules](AGENTS.md).

## Status vocabulary

- `live` — checked against the current tree or disk today; it is there.
- `partial` — checked; some of it exists, the quoted ask is not fully met.
- `open` — checked; not done.
- `unverified` — recorded from the transcript, **nobody has checked it**. This is
  not a soft "probably done". It means unexamined.

Statements below were mined from 53 session transcripts (2026-06-05 → 2026-07-31).
Only Azimuth statements are included; statements about other projects, and Sean's
general working preferences (TLDR length, delegation, screenshots), live in his
global `CLAUDE.md`.

---

## 1. Sean's direct statements

### 1.1 What the product is

- `unverified` "I want to build this into a product not just for myself but for any photographer out there" — 07-06
- `open` "The photographer like you" — 07-31 · **the second user.** Shoots RAW, runs Lightroom Classic, owns a NAS, wants out of Adobe. Generalize only where that person differs from Sean — different camera, different drive layout, different OS. Not the self-hoster escaping Google Photos; that is Immich's audience and chasing it pulls Azimuth onto their ground.
- `open` "Import, browse (rank/cull/sort/search), edit, export (Once edits are finished they should be exported into the edits folder), share (links/website/galleries n shit)" — 07-31 · **the core loop, five named stages.** Export is not the end: finished edits are written into `Edits/`, which makes export the act that *files* an image rather than merely emitting it. Share then draws from `Edits/`. No single stage wins a tie — the seams between them are the product, so work that improves one stage by worsening a handoff is a regression.
- `live` "lets always just work off prod, this is a private app" — 07-09 · **reaffirmed 07-31** when asked whether going public ends prod-first development: it does not. Azimuth is a public product that in practice has one user, and prod-first iteration stays.
- `live` "lets keep it all open source and free." — 07-06 · AGPL-3.0 free/open-source license with no product paywall in settings or routes; greps for stripe/paypal/require_license found only false-positive stripEnd identifiers.
- `partial` "all of the custom solutions im building for me, ideally id like to be built in such an elegantly generalized way that they can benefit anyone who wants to use this software." — 07-19 · `web/core/runtime_paths.py:72`, `web/settings.py:36`, `web/settings.py:90` — Portable XDG/AppData paths and empty publish defaults show generalization; camera Adobe profile pack is still a small set (Canon-heavy plus a few others) so not every custom solution is generalized for anyone.
- `partial` "we are building this for me, but in a way that anyone can use it" — 07-21 · `web/features/pages/routes.py:128`, `web/features/pages/routes.py:149`, `web/settings.py:105` — Anyone can hit /setup on a fresh install, but public-release hardening (device token default, commercial face models) is incomplete.
- `runtime` "I want it to feel similar to lightroom classic yet much stronger and better" — 07-06 · `web/static/js/desktop/develop/panels.js:253`, `web/templates/desktop.html:37`, `web/static/js/desktop/state.js:10` — LRC-like Develop panels and Library/Map/Develop views exist in code, but "feel similar yet much stronger" is a subjective UX bar; settle with a timed side-by-side LRC task walkthrough (cull→develop→export)…
- `runtime` "I really want to eat lightrooms lunch." — 07-10 · `web/static/js/desktop/develop/panels.js:253`, `web/static/js/desktop/lenses.js:16`, `web/features/stacks/builders.py:21` — Competitive aspiration cannot be proven from source; settle with a comparative speed/feature preference bake-off vs Lightroom Classic on the same catalog.
- `partial` "I want full 100% feature parity with lightroom classic, with all the same editing tools and locations, aswell as all the other features I have and a really brillant way to unify RAW editing workflow with exported edits, and linking them in a good, way." — 07-10 · `web/static/js/desktop/develop/panels.js:253`, `web/features/stacks/builders.py:322`, `web/features/sync/export_relation.py:1` — Develop has LRC-ordered tools, virtual copies, and RAW↔export version stacks; missing 100% parity — greps found no color_label, tether, /api/print, or Book module; VALID_LENSES has no print/book/slideshow;…
- `partial` "rather than bolting on lots of differnt yet similar features Id like an elegant feature set that has a few powerful multi use features rather than tons of differnt yet similar ones." — 07-09 · `web/static/js/desktop/scope_data.js:12`, `web/features/stacks/builders.py:21`, `web/static/js/desktop/lenses.js:16` — Multi-use primitives exist (shared scope loader, one stacks system with four kinds, shared lens shell), but the desktop surface still has many specialized modules so "few elegant multi-use features" is only…
- `runtime` "I want this to all feel like magic." — 07-15 · `web/static/js/desktop/state.js:10`, `web/templates/desktop.html:37` — "Feel like magic" is a subjective quality bar; settle with timed first-interaction tasks plus error/blank-state counts on a warm library (not static review).
- `runtime` "everything about the app needs to be best in class" — 07-15 · `web/static/js/desktop/develop/panels.js:253`, `web/static/js/desktop/lenses.js:16` — "Best in class" cannot be settled from source; settle with explicit quality-bar benchmarks per surface (library, develop, search, mobile) against named peers.

### 1.2 Speed

- `open` "Speed is the bar" — 07-31 · **the release gate, above feature parity.** A missing feature is acceptable; a slow one is not. Nothing ships that misses its latency budget, even if scope must be cut to make it. Directly bounds "100% Lightroom parity" (1.1): parity never justifies shipping something slow.
- `runtime` "the data should all be there somewhere we really want to focus on extreame preformace too
- `runtime` "I want every part of this app hyper optimized like VLC levels, it should be wayyyyy faster than anything else, most software is horribly inefficent." — 07-16 · `web/test_perf_budgets.py:30`, `web/perf/baseline.json`, `scripts/bench.py:50` — "VLC levels / way faster than anything else" is comparative and subjective; settle with a same-machine browse/open latency bakeoff against peer apps using the standing fixture metrics.
- `runtime` "no bloat, no slop, just extreamly good engineering to make this app the fastest photo app ever made." — 07-16 · `web/perf/standing.py:1`, `scripts/perfbench.py:1`, `web/test_perf_budgets.py:30` — "Fastest photo app ever / no bloat" cannot be proven from source; settle with end-to-end wall-clock and RSS on a large catalog versus named competitors, not internal budgets alone.
- `runtime` "I want this running as fast and as snappy on any system as its physically possible." — 07-16 · `web/core/host_profile.py:37`, `web/settings.py:345`, `web/thumbnails/demosaic_pool.py:35` — "As fast as physically possible on any system" is an unbounded ceiling; settle by measuring interactive p50/p95 and resource use on 8GB, 16GB, and 64GB hosts after host_profile tuning.
- `partial` "as you work overnight I want you to continously idenitify the limiting factor of large work, find the slowest part of the app the bottleneck and agressively optomize it, until something else is the bottle neck, and then do the same for it. repeat this process over and over and over" — 07-20 · `scripts/perfbench.py:1`, `scripts/bench.py:1`, `web/perf/baseline.json` — Bottleneck measurement tooling exists (perfbench, standing bench, baseline); missing is any continuous overnight identify→optimize→repeat automation (no perf cron/timer/CI loop found under scripts/,…
- `partial` "I want to squeeze every possible drop of preformance out of any and all hardware we have acess too, and not be silly with how we do it, such to not waste cycles, reads writes etc" — 07-20 · `web/core/host_profile.py:75`, `web/core/hdd_governor.py:1`, `web/core/work_coordination.py:1` — Host-adaptive CPU/RAM/VRAM budgets, single-flight bulk HDD reads, and GPU ownership exist; "every possible drop of all hardware" is not demonstrated and remains unbounded.
- `partial` "SSd space, ram, cpu, gpu, all of it keep track of benchmarks over time and log them in the commits as you go, I want to be able to really track progress and watch as the app gets faster and faster" — 07-20 · `web/perf/baseline.json`, `scripts/bench.py:18`, `scripts/perfbench.py:37` — One committed baseline snapshot and tools that tag git SHA exist; time-series history is written outside the checkout (runtime state dir / AZIMUTH_PERFBENCH_HISTORY), not logged into commits as continuous…
- `partial` "dont be afraid to use lots of my ram to accelerate the xps app, I have 64gb, and I want the app to be super super snappy as much as possible" — 07-21 · `desktop/src-tauri/src/engine.rs:58`, `web/features/sync/satellite.py:76`, `web/settings.py:315` — XPS standalone is satellite-class and auto-sizes thumb RAM cache to min(25% RAM, 8GB) with settings allowing up to 64GB; default does not aggressively spend most of 64GB.

### 1.3 Laptop and server split

- `live` "Im currently in california and my home server is in austin, so we should have some elegant way to work import cull and edit work locally, some sort of local cache system or something that keeps everything very preformant." — 07-11 · `web/features/sync/satellite.py:76`, `web/features/sync/satellite.py:87`, `web/features/sync/mirror.py:76` — Satellite/standalone keeps a local catalog, imports, and SSD caches while syncing to a hub; request-path thumbs/develop do not block on the hub.
- `partial` "I want to be able to work on my latop, impor photos quickly and edit off my laptops fast SSDs while also backing up to the omarchy server and freeing up space on my laptop." — 07-16 · `web/features/imports/staging.py:94`, `web/features/imports/staging.py:517`, `web/features/sync/sync_worker.py:192` — Local import plus background hub upload exist; freeing laptop space is a manual Free up space job, not automatic after backup.
- `runtime` "the server needs to run at max speed as much as possible, its doing the grunt work, my xps will be the nice pretty ui thats super fast and responsive." — 07-19 · `web/core/background.py:538`, `web/core/background.py:540`, `web/core/background.py:549` — Hub-vs-satellite grunt split is coded (hub pregen/AI, satellite skips bulk workers); whether the server runs at max speed or the XPS UI stays snappy needs live load/latency measurement.
- `runtime` "it would be nice if everything I did on my laptop was super fast and never really had to wait on the server." — 07-20 · `web/features/media/routes.py:312`, `web/features/sync/readthrough.py:315`, `web/features/trash/routes.py:94` — Local-first non-blocking paths exist for thumbs, develop base, and trash; “never really had to wait” is only settleable by measuring interactive actions with the hub unreachable or slow.
- `partial` "Id like to use my laptops SSDs as staging (and keep them there for while as I work through them/edit them) and they get quietly moved to the server while being ready to work as quickly as possible." — 07-20 · `web/features/imports/staging.py:94`, `web/features/imports/staging.py:517`, `web/features/sync/sync_worker.py:125` — Local library staging and quiet background upload exist; quiet move off the laptop only happens when the user runs Free up space (not automatic reclamation).
- `partial` "when the images are going from the laptop to the server, they should be processed on arrival, and written to the HDD." — 07-20 · `web/features/sync/hub.py:708`, `web/features/sync/hub.py:775`, `web/features/sync/hub.py:828` — On complete upload the hub verifies hashes, places into the configured library root, and catalogs the file; no on-arrival thumb/AI process path in hub.py, and HDD is deployment config not enforced by code.
- `partial` "we should really only ever read the image from the HDD once, or preferable do it all on ingest off the SSD and then just archive it away on the HDD" — 07-20 · `web/thumbnails/harvest.py:229`, `web/thumbnails/harvest.py:258`, `web/features/sync/hashing.py:14` — Bulk harvest has a read-once-into-RAM path under the HDD governor; freeup confirmation and other hash paths re-read originals, so a single-touch guarantee is not universal.
- `live` "I want to work in the desktop windows app, and the omarchy box runs a headless sever that acts like a NAS and remote compute." — 07-21 · Tauri shell at `desktop/src-tauri/src/`
- `partial` "The software should be smart enough to seemlessly handle all of this elegantly behind the scenes, not going to differnt ports for differnt interfaces, Infact the only time id ever goto the hub web interface is if I didnt have the desktop app installed." — 07-30 · `desktop/src-tauri/src/server.rs:12`, `desktop/src-tauri/src/server.rs:69`, `desktop/src-tauri/src/engine.rs:56` — Desktop always opens one local surface at 127.0.0.1:8010/d; hub pairing still asks for a URL/port, and the hub web UI remains a separate surface when the desktop app is not used.
- `partial` "its a tanuri app or whatever not a web app, I want a native desktop experieance" — 07-30 · `desktop/src-tauri/src/main.rs:8`, `desktop/src-tauri/src/main.rs:22`, `desktop/src-tauri/src/server.rs:13` — Tauri provides a native window/tray/shell, but the product UI is the web desktop app loaded in a webview from the local engine.
- `partial` "I want one canocial archive on the omarchy server (dont rearrange the files more than you have too) data safety is important, and on the laptop is just a small chache, with one super easy elegant UI, with good product design." — 07-31 · `web/features/sync/hub.py:134`, `web/features/sync/hub.py:655`, `web/features/sync/hub.py:855` — Hub places durable originals into one library tree and satellites mirror/cache; laptop “small cache” depends on manual freeup plus thumb budgets, and “super easy elegant UI” cannot be graded from source alone.
- `open` "the app should have two modes, a local only mode like lightroom classic, and a Desktop + NAS/Server/mode like lightroom CC but self hosted. either way, to the the user its always canonical set , if theres a NAS its the NAS. (assuming the nas is bigger) (or user can select on onboarding which device to use as the server" — 07-31 · **the storage architecture.** Resolves the same-drive/canonical-archive contradiction: canonical is a role, not a fixed place. Local-only mode is a first-class product mode, not a degraded state.

### 1.4 Import and storage

- `partial` "when I import I really dont want to have to click too much or where the photos go, I want it to all be simple and elegant, no managing folders of images or whatnot, the app should just know, 1 click, and the photos are magically imported to my computer and organized neatly into the main archive on omarchy." — 07-16 · designed in [INVISIBLE_IMPORT.md](docs/INVISIBLE_IMPORT.md), not confirmed shipped
- `open` "maybe just 1 option, RAW, personal, film scan, or exported edit, but really the software should be able to figure that out, from the source no?" — 07-16 · **confirmed current 07-31** when asked directly: collapse to one import action and make detection carry it
- `runtime` "I want to get this to the point where I never have to think about where or how my photos are stored." — 07-16 · `web/features/imports/taxonomy.py:15`, `web/features/imports/staging.py:95`, `web/settings.py:90` — Subjective end-state; settle it by a user session where imports and browsing never surface folder choice or path decisions.
- `runtime` "Its kinda all a mess and I want to make it so its not a mess and I never have to think too much about it again" — 07-16 · `web/features/imports/taxonomy.py:15`, `web/features/imports/taxonomy.py:387` — Subjective archive cleanliness; settle it by auditing the live library for misplaced trees and unrepaired mess, not from source alone.
- `superseded 07-31` "person photos is for cellphone shots, Raws are for digital camera raws, exported edits are for well exported edits, and maybe we need another one for film scans." — 07-13 · shipped as four destinations (`web/features/imports/taxonomy.py:39`), superseded by the three-root directive below
- `open` "I think Edits/ Raws/ and Snapshots/ is the right top ordering these define the image state and desire, Edits are edited images ready for sharing, RAWs/ are camera raws and film tiff scans or astro tiffs etc, and snapshots is cellphone pics that may be raws or jgp or random memes or family photos or facebook takeout data, etc that arent inteded to be edited or shared, but are still nice to browse through quickyl search and manage" — 07-31 · **the taxonomy.** Three roots, not four. The axis is *state and intent*, not file format: `Film Scans` folds into `Raws/` (a scan is a negative), and `Personal Photos` becomes `Snapshots/` with a wider remit (takeout dumps, memes, family photos). Supersedes the 07-13 four-destination split.
- `open` "whatever is the most grmatically correct" — 07-31 · **canonical root names are `Edits/`, `Raws/`, `Snapshots/`** — ordinary title case, not shouty caps, and "Exported" dropped as redundant. The archive lives on a case-sensitive filesystem, so these spellings are functional: `RAWS` and `Raws` are different directories and a wrong guess silently creates a duplicate tree. Match the spelling exactly and never normalise case in code.
- `open` "Leftover — should go away" — 07-31 · `_intake/` is an artifact of earlier work with no remaining job. Retire it rather than building support for it; prove it holds no originals before removing it.
- `open` "Ill just rename the roof folders myself" — 07-31 · **Sean owns the one-time root migration; no agent moves these files.** He renames `Film Scans/` → under `Raws/` and `Personal Photos/` → `Snapshots/` by hand. The agent-side job is therefore *not* a destructive pipeline: it is making the app correct against roots that have already changed underneath it — update the taxonomy constants, never re-create the old roots, do not choke or re-file when it meets the new names, and repair catalog paths that still point at the old ones. Supersedes the earlier "physically migrate" answer from the same day.
- `open` "Ask me when ambiguous" — 07-31 · how import decides between `Snapshots/` and `Raws/`, given the axis is intent rather than format. Auto-file the confident cases from source provenance (`classify_source_kind` already does this), and hold genuinely unclear ones — a DNG from an unrecognised source — in a small review queue. Filing something wrong silently is worse than asking.
- `superseded 07-31` "on desktop I want 2 simple import buttons, import from card, (raws on external cards get important and sorted into raws) and import film scans (opens file picket to import film scans, often tiffs in a zip or rar file, imported and sorted under film scans automatically)." — 07-29 · shipped (card + film routes in `web/features/imports/routes.py`), but superseded as the destination: asked on 07-31, Sean chose one button now. The two routes stay as the mechanism behind it.
- `live` "id like the catlog and images to be organized like this D / pictures / lightroom / catolog file and D / pictures / YYYY / (current year images organized yyyy-mm-dd)" — 07-19 · `D:\Pictures\{2026,Lightroom}` verified on disk 07-31
- **`open`** "id like to retire and remove c/pictures once we have propperly moved everything to its new home" — 07-19 · `C:\Pictures` still holds `2025\`, `Lightroom\`, and loose files. Restated 07-31: *"you never fixed the folders what are pictures?"*
- `open` "lets keep the catlog and the images on the same drive, that seems cleaner to my mind." — 07-19 · `web/core/runtime_paths.py:184`, `web/features/imports/staging.py:95`, `web/settings.py:36` — Defaults split catalog to app data_dir and originals to Pictures/Azimuth Imports with no same-drive co-location policy (searched same drive/co-locate in web/desktop product code).
- `runtime` "just make sure ALLL of the photos are organized into their propper places and nothing is lost please." — 07-19 · `web/features/imports/staging.py:517`, `web/features/imports/taxonomy.py:387` — Verified copy and repair tools exist, but whether every real photo is organized and none lost requires a live disk inventory of the user archive.
- `runtime` "i want it to be super elegant and automatically maintained and sorted. Edits, (film, digital and edited phone shots) Raws, (Digital Raws and Film Scan Raws), and Phone (facebook photos, google photos, etc combined into one date sorted) all nice and elegant and organized automatically" — 07-31 · `web/features/imports/taxonomy.py:15`, `web/features/imports/staging.py:483` — Subjective polish bar; settle it by continuous live use that new imports stay sorted without manual folder work or reclassify repairs.
- `unverified` "everyphoto should have metadata or an exisiting folder that tells you what is is." — 07-31
- `unverified` "photo is already in a folder. the only ones with issues are the personal folders" — 07-31
- `unverified` "when files are imported they should be imported and sorted by the structure I said." — 07-31
- `partial` "i dont want to do all this through you, I want the app to support this so users can easily and seemlessly migrate from LRC to this." — 07-12 · `web/features/develop/import_routes.py:78`, `web/features/develop/lrcat_import.py:417`, `web/static/js/desktop/drawer.js:761` — LRCAT scan/import API and importer exist, but no desktop/static UI calls /api/develop/lrcat (searched web/static, web/templates, desktop, android); Connect Lightroom is a bridge, not seamless full migrate.

### 1.5 Editing and colour

- `runtime` "the raw thumbnails look great, but the as soon as i go into the edit/develop tab the colors get super ugly, this is absolutely critical to get right." — 07-15 · `web/thumbnails/generation.py:120-126`, `web/raw_thumb_ops.py:57-82`, `web/features/develop/rawproc.py:613-621` — Thumbs use embedded JPEG or LibRaw demosaic, not Develop; anti-vomit `develop_default_render` exists, but “super ugly” needs a live thumb-vs-Develop A/B on real RAWs.
- `partial` "I'm treating Develop color fidelity as P0: we should establish a measurable reference pipeline before adding more editing features, because every slider is downstream of that foundation." — 07-15 · `web/eval/fit_camera_profile.py:1-6`, `web/eval/fit_camera_profile.py:302-338`, `web/features/develop/render.py:642-661` — Offline LR-pair fitter, DNG goldens, and default-render twin exist; no continuous product/CI colour-fidelity (ΔE) gate, and feature work was not blocked on it.
- `runtime` "lets do propper super accuate canon color science (and other cameras too) but we really need to nail this 100% like a professional photo editor, colors are kinda the most important part" — 07-12 · `web/features/develop/pipeline.py:1134-1206`, `web/features/develop/dng_pipeline.py:1-12`, `web/features/develop/camera_profile.py:85-99` — Adobe DNG + fitted Canon residual paths ship (R5/R7/RP fitted; only R5 tone_trusted), but “100% pro editor” accuracy is a visual/metric bar vs reference exports.
- `runtime` "all the raw images have a wierd purply color when opened fully, lets make sure we really nail the color science." — 07-10 · `web/static/js/desktop/loupe.js:358-387`, `web/raw_thumb_ops.py:57-82`, `web/features/develop/rawproc.py:519-541` — Full-open loupe uses lg thumbs (LibRaw/embed), not Develop; no purple-cast fix found (only DefringePurple); settle by opening full RAW loupe and checking cast vs embedded sm.
- `partial` "it would be great if we can do soemthing better than just film presets too, like what if we also had the very best most accuate film emulation?" — 07-10 · `web/features/develop/film.py:1-13`, `web/features/develop/film.py:202-238`, `web/static/js/desktop/develop/film_panel.js:16-47` — Physical film engine (H&D/halation/grain/DIR) plus 14 stocks and UI is more than LUT presets; “very best most accurate” needs scan/reference comparison, not source alone.
- `live` "we need to add a elegant way to handle RAW vs edits stacks, (I talked about this before in a differnt session) see if you can find that info" — 07-10 · `web/features/stacks/builders.py:322-397`, `web/data/repositories/stacks.py:447-530`, `web/features/develop/routes.py:1011-1013` — RAW↔edit version stacks match, persist on export, show as RAW+N, and toggle via V / Edit RAW end-to-end.

### 1.6 Refine and ranking

- `runtime` "I want the refine mode running on my laptop to work super well be really polish, instant click responsiveness on ranking the photos and good algos for the differnt modes for refining." — 07-21 · `web/static/js/desktop/refine.js:24`, `web/static/js/desktop/refine.js:690`, `web/static/js/mobile/refine.js:212` — Strategies, optimistic pick UI, and mode code exist, but “super well / polish / instant / good algos” is a subjective quality bar—settle with click→visible-replacement p95 and a live polish pass on a real…
- `live` "in refine in the dual mode, both images should be replaced each round." — 07-21 · `web/static/js/desktop/refine.js:489`, `web/static/js/desktop/refine.js:493`, `web/static/js/desktop/refine.js:696` — Desktop duel `replacementIndices` returns every index so both cells are replaced; mobile pick advances to a full new set.
- `partial` "dual should only compare two images of similar aspect ratio, gotta control for aspect ratio in duel" — 07-21 · orientation pairing pool exists in `web/features/compare/service.py`; aspect-ratio matching itself not found
- **`open`** "Im on the diverse mode but its showing me images that all look the same, in this mode it should show images that are very differnt unless not otherwise possible (this is to take max advantage of the elo propigation system to sort clusters of images relative to eachother quickly)" — 07-21 · `diverse_sample()` exists; the quality complaint about its output is unaddressed
- `unverified` "when using the refine mode, can we somehow limit it to the files that already have thumbnails and are ready to serv so we never get blank spots like this?" — 07-21

### 1.7 Mobile and Android

- `partial` "I want to build a native andorid app, like google photos, with super deep rich intergration, not a pwa but a rich native android experience so that I can entirely replace google photos, so that every photo I take on my phone gets saved into this, and I can use it as my phones default photo app." — 07-12 · `android/app/build.gradle.kts`, `android/app/src/main/AndroidManifest.xml:34`, `android/app/src/main/AndroidManifest.xml:44` — Native Kotlin/Compose app, MediaStore backup, and gallery/viewer/picker intents exist; full "entirely replace Google Photos" is not demonstrated from source alone.
- `partial` "photos (and videos?) should be quietly moved off my phone and onto the main sever." — 07-12 · `android/app/src/main/java/app/azimuthphoto/mobile/backup/BackupWorker.kt:55`, `android/app/src/main/java/app/azimuthphoto/mobile/backup/BackupWorker.kt:59`, `android/app/src/main/java/app/azimuthphoto/mobile/data/Settings.kt:58` — Quiet photo+video upload exists; free-up off the phone is opt-in (default false), needs MANAGE_MEDIA, and worker FreeUpSpace.runIfEnabled is a no-op.
- `live` "it should be two way too, for isntance the photos from yesterday imported from my camera on my computer should show up there." — 07-12 · `android/app/src/main/java/app/azimuthphoto/mobile/data/ArchiveApi.kt:72`, `android/app/src/main/java/app/azimuthphoto/mobile/data/UnifiedTimeline.kt:73`, `android/app/src/main/java/app/azimuthphoto/mobile/data/UnifiedTimeline.kt:86` — Timeline All-scope merges hub rankings with device media so computer-imported archive photos appear on the phone.
- `partial` "I want to be able to share to other apps, I mean really just make it like a total copy of google photos." — 07-13 · `android/app/src/main/java/app/azimuthphoto/mobile/ui/ViewerScreen.kt:815`, `android/app/src/main/java/app/azimuthphoto/mobile/ui/TimelineScreen.kt:558` — Outbound ACTION_SEND / SEND_MULTIPLE share exists; "total copy of google photos" is not met.
- `live` "For editing we should add a button to edit RAW in snapseed or other installed editing apps" — 07-13 · `android/app/src/main/java/app/azimuthphoto/mobile/ui/ViewerScreen.kt:360`, `android/app/src/main/java/app/azimuthphoto/mobile/ui/ViewerScreen.kt:824`, `android/app/src/main/java/app/azimuthphoto/mobile/ui/ViewerScreen.kt:829` — Edit RAW (.dng) uses ACTION_EDIT with a system chooser ("Edit with"), so Snapseed and other installed editors can handle it.
- `superseded 07-31` "lets keep the phone app simple and elegant for now." — 07-12 · asked directly on 07-31; Sean chose full Google Photos replacement as the scope
- `superseded 07-31` "most of the real work should be done on a computer, we can worry about those types of features later." — 07-12 · same resolution; heavy editing still belongs on desktop, but phone scope is no longer deferred

### 1.8 Sharing, publishing, website

- `live` "I want to publish a collection to my website, www.seankennethdoherty.com and share a link that way" — 07-08 · `web/features/publish/routes.py:171`, `web/features/publish/routes.py:296`, `web/features/publish/routes.py:778` — Snapshot-to-website, static export, optional deploy hook, and `{publish_site_base_url}/g/{slug}/` links exist; domain is settings, not hard-coded.
- `partial` "To be clear I want very clear manual approval for website publishing." — 07-08 · `web/static/js/desktop/shared.js:430`, `web/static/js/desktop/shared.js:625`, `web/static/js/desktop/panel.js:828` — Publish is user-initiated (drag / Export / Publish button) with republish confirm only; searched publish+shared+panel for approv|pending_publish|manual_approval — no approval queue or first-publish gate.
- `partial` "in the publishing page it should be extremely clear which collection is for the website and other personal/private/shared privately/shared with clients" — 07-09 · `web/templates/desktop.html:197`, `web/features/publish/nodes.py:16`, `web/static/js/desktop/panel.js:234` — Shared page clearly separates Collections / Website / Private links; client galleries live only in Deliver tabs, not a publishing pane — no personal or shared-with-clients columns on that page.
- `live` "maybe there's something where you can update it and you can see what photos you wanna add, but it should be static once it's moved into there. And it should be a very deliberate thing to move that into the public" — 07-09 · `web/features/publish/nodes.py:79`, `web/features/publish/nodes.py:195`, `web/features/publish/nodes.py:553` — Snapshots freeze into `published_node_images`, diffs show added/removed with Apply update, and Export site is a separate deliberate public write.
- `partial` "I want to tie my website and this app together in an elegant way that also is friendly to other people using this software in their own workflows and websites." — 07-09 · `web/settings.py:91`, `web/static/js/desktop/drawer.js:1032`, `web/features/publish/deployer.py:28` — Any install can set publish_dir, site base URL, brand name, and hook; deeper elegant productized multi-site integration is not in code, and “elegant” needs a live UX review.
- `partial` "I want the the images on the website to be generated from the code, and, preferably, have little interactive sections based off the real code of it." — 07-12 · `web/features/publish/builder.py:102`, `web/features/publish/builder.py:160`, `web/templates/share_gallery.html:91` — Site JPEGs are generated by the app builder; interactivity is a self-contained lightbox in share_gallery.html, not desktop/app UI modules.
- `open` "the app and website need the same style and the website needs to show bits of the actual UI, not something totally differnt." — 07-14 · `web/templates/share_gallery.html:13`, `web/features/publish/builder.py:160` — Searched share_gallery.html and web/features/publish/ for desktop.css, static/js/desktop, iframe/embed of app UI — none; gallery owns its own CSS/JS and does not show app UI chrome.

### 1.9 UI and UX bar

- `unverified` "I really want all the agents to focus on improving UX, finding and fixing bugs, improving preformance, hyper otpomizing everything, and just working hard and thoughly to make this app feel like absolute perfected magic, like factorio levels of polish and bug free high preformance, extreamly user friendly, perfect UX." — 07-16
- `unverified` "I dont like overlays for large UIs, it should be a pannel or a page, not something that depends the whole app every time its open." — 07-09
- `unverified` "all pannels should be collapseable" — 07-09
- `unverified` "I want a UI refresh/improvement on Azimuth with a very subtle yet elegant aerospace theme." — 07-15 · "(dont over do it)" — 07-14
- `unverified` "I want each UI to be as elegant, intutive, and clear as possible, yet professional and powerful, every features in its right spot, everytool where it should be, configurable etc." — 07-06
- `unverified` "explore how the app looks at differnt screen shapes ... lets make sure its all very responsive." — 07-09
- `unverified` "the smarter search is a big priority for me, the search needs to really be as smart as possible, (also keep in mind, we want to build in a way that the features we could work elegantly and symbiotically with other features) that's were the real power comes from." — 07-09
- `unverified` "the smart collections should be a very powerful set of filters that drive it." — 07-09
- `unverified` "suggested collections are pretty bad, just going by date, we should be much smarter about suggested collections and organize it better, lets refernce lightroom classic alot more here." — 07-09
- `live` "when I right click sources, it should give an option to open in explorer." — 07-15 · `revealFolder()` in `web/static/js/desktop/api.js`

---

## 2. Derived queue

Everything section 1 says is not done. Ordered by what unblocks the most.
`runtime` rows are absent on purpose: they need a measurement, not a build, and
the measurement is listed in the row's own note.

### Blocking product decisions already made

| # | Item | From | Status |
|---|---|---|---|
| 1 | **Three-root taxonomy** `Edits/` `Raws/` `Snapshots/` — state and intent, not format. Film scans fold into Raws; Personal Photos becomes Snapshots | 1.4 | `open`, directive 07-31 |
| 2 | **Follow the roots Sean renamed by hand.** He does the one-time move; agents make the app correct against it — taxonomy constants, never re-create old roots, repair catalog paths | 1.4 | `open`, decided 07-31 |
| 3 | **Ambiguity review queue** at import — auto-file confident cases from provenance, hold unclear ones rather than filing them wrong silently | 1.4 | `open`, decided 07-31 |
| 4 | **Retire `C:\Pictures`** — 9,911/10,689 hash-proven in hub; 778 still need homes before anything is removed | 1.4 | `open`, restated twice |
| 5 | **Collapse import to one action.** Detection already works (`classify_source_kind`); this is UI collapse, not classification | 1.4 | `open`, decided 07-31 |
| 6 | **Two storage modes** — local-only and desktop+server, canonical as a role rather than a drive, chosen at onboarding | 1.3 | `open`, architecture 07-31 |

### Half-built — the dangerous ones

These read as done in any status report and do not survive contact with the app.

| # | Item | From | Status |
|---|---|---|---|
| 7 | Free up space is manual; backup works, reclamation never happens on its own | 1.3 | `partial` |
| 8 | Phone free-up is a no-op — `FreeUpSpace.runIfEnabled` does nothing, and defaults off | 1.7 | `partial` |
| 9 | No processing on arrival at the hub — files are verified and catalogued, thumbs and AI come later | 1.3 | `partial` |
| 10 | Read-once is not universal — free-up confirmation and other hash paths re-read originals | 1.3 | `partial` |
| 11 | Perf history is written to a runtime dir, not logged into commits as asked | 1.2 | `partial` |
| 12 | No overnight bottleneck loop — the measuring tools exist, the repeating automation does not | 1.2 | `partial` |
| 13 | LrC migration has no UI — the `lrcat` importer exists but nothing in the app calls it | 1.4 | `partial` |
| 14 | Hub pairing still asks for a URL and port, so "never think about ports" is not met | 1.3 | `partial` |

### Refine

| # | Item | From | Status |
|---|---|---|---|
| 15 | Diverse mode still picks a high-cosine partner, so it serves lookalikes | 1.6 | `open` |
| 16 | Aspect-ratio matching in Dual — orientation is a filter, not pairing | 1.6 | `open` |

### Publishing

| # | Item | From | Status |
|---|---|---|---|
| 17 | Website and app share no styling — the gallery owns its own CSS/JS and shows no app UI | 1.8 | `open` |
| 18 | No first-publish approval gate; only republish confirms | 1.8 | `partial` |

## 3. Keeping this file honest

- **Every Sean statement about this product gets logged here, verbatim, same
  session it was said.** Not paraphrased into a task title — the exact words, with
  the date. Paraphrase is where intent leaks out.
- **A multi-item message gets one row per item.** The 07-19 message carried four
  asks; the first shipped and the fourth was never recorded. One row each, or the
  tail is lost.
- **Never delete a row.** Mark it `live` when it ships, or `declined` with Sean's
  own words declining it. A removed row reads as done.
- **`unverified` is a debt, not a status quo.** It means the claim has never been
  checked. Do not report an `unverified` item as working.
- **Ask when a new statement contradicts an old one.** Before building, check §1
  for a row that the new ask fights with. If one exists, stop and ask Sean which
  wins — do not silently pick the newer, the older, or a blend. Log the answer as
  a new dated row and mark the loser `superseded` with the date it lost. The same
  applies when a statement is too ambiguous to build from: ask, then log the
  answer verbatim. An unresolved contradiction goes in §4 and stays there until
  Sean settles it. Guessing between two things he said is how a want gets built
  wrong and then rebuilt.
- This file is exempt from the "no plans or dated status files" rule in
  [AGENTS.md](AGENTS.md) documentation hygiene. It is an owner document, not
  coordination scaffolding. The retired 1,580-line lane-era plan remains readable
  at `git show 937dee04:MASTER_PLAN.md`.

## 4. Open contradictions

Statements in section 1 that fight with each other. Do not build the affected
area until Sean settles the row. Resolving one means: log his answer as a new
dated section 1 row, mark the losing row `superseded`, and delete the entry here.

**None open.** Four were found when this file was assembled and all four were put
to Sean on 07-31:

- **C1 import** — one smart import wins; the two 07-29 buttons are the mechanism,
  not the destination. Detection must carry it.
- **C2 phone scope** — full Google Photos replacement. Section 1.7 is a real
  workstream, not a someday list.
- **C3 private vs product** — both. Public product, one actual user, prod-first
  development survives.
- **C4 storage** — resolved into new architecture: two modes, local-only and
  desktop+server, with canonical as a role rather than a fixed drive.

### 1.10 Non-goals

What Azimuth deliberately refuses. A non-goal is worth as much to an agent as a
goal — it is permission to stop.

- `open` "No cloud, no accounts, no telemetry" — 07-31 · nothing may require an external service or account to work. Constrains dependencies, not features.
- `open` "No general media manager" — 07-31 · photos and the photographer's workflow only. Not video editing, not documents, not a Drive replacement. Stay narrow exactly where Immich went broad.
- `open` "ignore it, im keeping the astro shots out of this program sope they are too special leave them out" — 07-31 · **astrophotography is out of scope.** The `Astrophotography/` root is not a library root, not a taxonomy destination, and not Azimuth's business. This is also a **data-safety boundary**: no scan, index, migration, cleanup, dedup or free-up path may read, move, rename or delete anything under it. Stacking, calibration frames and session structure are why — that workflow is not what this app models, and half-modelling it would damage it.

### 1.11 Snapshots

- `open` "Everything, just not by default" — 07-31 · the full app works on `Snapshots/` if you go looking, but snapshots never enter Refine queues, Develop scopes, or ranking surfaces unless explicitly scoped to. Default-off, not absent — 40k phone pictures and memes must never dilute the professional surfaces, and must never be unreachable either.
- `open` "local first we should treat it like aftershoot, we have alot to learn from them too" — 07-31 · **ML is local-first, not a server feature.** Faces and semantic search run on the machine the user is sitting at; a hub accelerates them, it does not own them. Aftershoot is the named reference for locally-run AI culling and is worth studying the way Immich was. Bounds the two-mode design: local-only mode does not lose capabilities, only speed.
- `open` "cahce of all the thumbnails we can fit, plus the most recent orignals we can fit, (esply raws)... so its all automatic but still gives the user the mbest UX for most usecases and the SSD space useable? lets workshop it" — 07-31 · **the laptop storage model, deliberately unsettled.** Direction: all thumbnails that fit, plus the most recent originals that fit, RAWs prioritised, sized automatically to the machine. Sean asked to workshop the policy rather than have it decided for him, so **do not implement an eviction policy from this row alone.** Resolves the cache-versus-working-set contradiction in principle: one automatic tiered budget, not two user-managed places.

### 1.12 Ranking and RAW support

- `superseded 07-31` "Real stars alongside Elo" — 07-31 · recorded as two independent axes, corrected the same day by the row below. Kept because the *requirement* it captured still holds: stars must be real, stored, filterable and sortable, not a dead write like the current mobile rating sheet.
- `open` "Stars should be applied by the photos elo" — 07-31 · **stars are Elo's readable face, not a second signal.** One quality signal earned from pairwise comparison; the star is its projection, computed and stored so it can be filtered, sorted and exported to Lightroom as a real value. This supersedes the independent-axes design above — there is no competing user-asserted score, which keeps the propagation system on the single signal it was built for.
  - `open` "Keeps filtering" — 07-31 · **1–5 filters, it does not rate.** Asked directly, Sean kept the keys as "show me 3+ and better". This settles the override question left open above: there is no manual star gesture, so **the star is purely computed and Elo is the only way to change it**. You change a photo's standing by ranking it in Refine, which is the entire point of the earned signal. The earlier inferred "remembered correction" override is withdrawn — do not build it.
  - The cost Sean accepted knowingly: every Lightroom migrant, himself included, will at some point press 3 expecting to rate and watch the grid filter instead. If that friction proves real in daily use it is a UX problem to solve with affordances, not by adding a second signal.
  - Follow-on for export: every star has a real value, so Lightroom always receives one. A user-set star is exported as authoritative; a computed star is exported marked inferred.
- `open` "Widen now" — 07-31 · **multi-vendor RAW becomes real support this cycle.** The importer already files `.arw/.nef/.orf/.raf/.rw2` (`web/features/imports/taxonomy.py:55`) into an archive the scanner never catalogs and XMP write-back never touches — a silent trap for exactly the second user named in 1.1. Scanner acceptance, catalog, thumbnail decode, Develop and XMP write-back all widen together; a format is not supported until it survives the whole chain. Acceptance coverage is the gate, and today it passes for 1 of 3 fitted Canon bodies, so the suite widens before the format list does.

### 1.13 Interaction shape

- `open` "Keep Develop as a mode, make it instant" — 07-31 · **Develop stays a mode you enter, and the 1,377 ms becomes a hard latency budget rather than an architecture change.** Familiar to every Lightroom user, keeps edit controls out of the culling surface, and turns the slowest interactive operation in the app into a number to beat (`develop_open_202_to_200_ms` p50 ≤ 400, `web/perf/baseline.json:43`). Rejected: editing in place in the loupe.
- `open` "something the remebers by source if data doesnt make it ovbious" — 07-31 · **import learns per source.** Photos appear immediately and file themselves from provenance. When the data genuinely does not settle it, ask once for that source — then remember the answer and never ask again for the same source. The ambiguity queue is therefore self-emptying by design: each question asked is one that never recurs. Pairs with the one-button import directive in 1.4.
- `open` "Quiet chip only when something is owed" — 07-31 · **offline is invisible until it costs something.** No connection status, no permanent indicator. A small chip appears only when work is genuinely waiting on the server, and is gone when nothing is owed — so the indicator always means something instead of being decoration. Reuses the existing peek-chip vocabulary. Requires that "nothing owed" be *provable* rather than inferred, or the chip becomes a liar.
