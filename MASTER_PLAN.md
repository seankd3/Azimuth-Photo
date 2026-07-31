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
- `unverified` "lets keep it all open source and free." — 07-06
- `unverified` "all of the custom solutions im building for me, ideally id like to be built in such an elegantly generalized way that they can benefit anyone who wants to use this software." — 07-19
- `unverified` "we are building this for me, but in a way that anyone can use it" — 07-21
- `unverified` "I want it to feel similar to lightroom classic yet much stronger and better" — 07-06
- `unverified` "I really want to eat lightrooms lunch." — 07-10
- `unverified` "I want full 100% feature parity with lightroom classic, with all the same editing tools and locations, aswell as all the other features I have and a really brillant way to unify RAW editing workflow with exported edits, and linking them in a good, way." — 07-10
- `unverified` "rather than bolting on lots of differnt yet similar features Id like an elegant feature set that has a few powerful multi use features rather than tons of differnt yet similar ones." — 07-09
- `unverified` "I want this to all feel like magic." — 07-15
- `unverified` "everything about the app needs to be best in class" — 07-15

### 1.2 Speed

- `unverified` "the data should all be there somewhere we really want to focus on extreame preformace too, LRCs fatal flaw is how slow it is, our biggest pitch is lightnight fast speed that feels like magic, this is 2026 and you are AGI, we shouldnt be afraid to use low level code if we need to to accelerate and hyper optomize the program." — 07-10
- `unverified` "I want every part of this app hyper optimized like VLC levels, it should be wayyyyy faster than anything else, most software is horribly inefficent." — 07-16
- `unverified` "no bloat, no slop, just extreamly good engineering to make this app the fastest photo app ever made." — 07-16
- `unverified` "I want this running as fast and as snappy on any system as its physically possible." — 07-16
- `unverified` "as you work overnight I want you to continously idenitify the limiting factor of large work, find the slowest part of the app the bottleneck and agressively optomize it, until something else is the bottle neck, and then do the same for it. repeat this process over and over and over" — 07-20
- `unverified` "I want to squeeze every possible drop of preformance out of any and all hardware we have acess too, and not be silly with how we do it, such to not waste cycles, reads writes etc" — 07-20
- `unverified` "SSd space, ram, cpu, gpu, all of it keep track of benchmarks over time and log them in the commits as you go, I want to be able to really track progress and watch as the app gets faster and faster" — 07-20
- `unverified` "dont be afraid to use lots of my ram to accelerate the xps app, I have 64gb, and I want the app to be super super snappy as much as possible" — 07-21

### 1.3 Laptop and server split

- `unverified` "Im currently in california and my home server is in austin, so we should have some elegant way to work import cull and edit work locally, some sort of local cache system or something that keeps everything very preformant." — 07-11
- `unverified` "I want to be able to work on my latop, impor photos quickly and edit off my laptops fast SSDs while also backing up to the omarchy server and freeing up space on my laptop." — 07-16
- `unverified` "the server needs to run at max speed as much as possible, its doing the grunt work, my xps will be the nice pretty ui thats super fast and responsive." — 07-19
- `unverified` "it would be nice if everything I did on my laptop was super fast and never really had to wait on the server." — 07-20
- `unverified` "Id like to use my laptops SSDs as staging (and keep them there for while as I work through them/edit them) and they get quietly moved to the server while being ready to work as quickly as possible." — 07-20
- `unverified` "when the images are going from the laptop to the server, they should be processed on arrival, and written to the HDD." — 07-20
- `unverified` "we should really only ever read the image from the HDD once, or preferable do it all on ingest off the SSD and then just archive it away on the HDD" — 07-20
- `live` "I want to work in the desktop windows app, and the omarchy box runs a headless sever that acts like a NAS and remote compute." — 07-21 · Tauri shell at `desktop/src-tauri/src/`
- `unverified` "The software should be smart enough to seemlessly handle all of this elegantly behind the scenes, not going to differnt ports for differnt interfaces, Infact the only time id ever goto the hub web interface is if I didnt have the desktop app installed." — 07-30
- `unverified` "its a tanuri app or whatever not a web app, I want a native desktop experieance" — 07-30
- `unverified` "I want one canocial archive on the omarchy server (dont rearrange the files more than you have too) data safety is important, and on the laptop is just a small chache, with one super easy elegant UI, with good product design." — 07-31

### 1.4 Import and storage

- `partial` "when I import I really dont want to have to click too much or where the photos go, I want it to all be simple and elegant, no managing folders of images or whatnot, the app should just know, 1 click, and the photos are magically imported to my computer and organized neatly into the main archive on omarchy." — 07-16 · designed in [INVISIBLE_IMPORT.md](docs/INVISIBLE_IMPORT.md), not confirmed shipped
- `unverified` "maybe just 1 option, RAW, personal, film scan, or exported edit, but really the software should be able to figure that out, from the source no?" — 07-16
- `unverified` "I want to get this to the point where I never have to think about where or how my photos are stored." — 07-16
- `unverified` "Its kinda all a mess and I want to make it so its not a mess and I never have to think too much about it again" — 07-16
- `live` "person photos is for cellphone shots, Raws are for digital camera raws, exported edits are for well exported edits, and maybe we need another one for film scans." — 07-13 · `web/features/imports/film.py`, destination `Film Scans/<archive>/`
- `live` "on desktop I want 2 simple import buttons, import from card, (raws on external cards get important and sorted into raws) and import film scans (opens file picket to import film scans, often tiffs in a zip or rar file, imported and sorted under film scans automatically)." — 07-29 · card + film routes exist in `web/features/imports/routes.py`
- `live` "id like the catlog and images to be organized like this D / pictures / lightroom / catolog file and D / pictures / YYYY / (current year images organized yyyy-mm-dd)" — 07-19 · `D:\Pictures\{2026,Lightroom}` verified on disk 07-31
- **`open`** "id like to retire and remove c/pictures once we have propperly moved everything to its new home" — 07-19 · `C:\Pictures` still holds `2025\`, `Lightroom\`, and loose files. Restated 07-31: *"you never fixed the folders what are pictures?"*
- `unverified` "lets keep the catlog and the images on the same drive, that seems cleaner to my mind." — 07-19
- `unverified` "just make sure ALLL of the photos are organized into their propper places and nothing is lost please." — 07-19
- `unverified` "i want it to be super elegant and automatically maintained and sorted." — 07-31
- `unverified` "i dont want to do all this through you, I want the app to support this so users can easily and seemlessly migrate from LRC to this." — 07-12

### 1.5 Editing and colour

- `unverified` "the raw thumbnails look great, but the as soon as i go into the edit/develop tab the colors get super ugly, this is absolutely critical to get right." — 07-15
- `unverified` "I'm treating Develop color fidelity as P0: we should establish a measurable reference pipeline before adding more editing features, because every slider is downstream of that foundation." — 07-15
- `unverified` "lets do propper super accuate canon color science (and other cameras too) but we really need to nail this 100% like a professional photo editor, colors are kinda the most important part" — 07-12
- `unverified` "all the raw images have a wierd purply color when opened fully, lets make sure we really nail the color science." — 07-10
- `unverified` "it would be great if we can do soemthing better than just film presets too, like what if we also had the very best most accuate film emulation?" — 07-10
- `unverified` "we need to add a elegant way to handle RAW vs edits stacks, (I talked about this before in a differnt session) see if you can find that info" — 07-10

### 1.6 Refine and ranking

- `unverified` "I want the refine mode running on my laptop to work super well be really polish, instant click responsiveness on ranking the photos and good algos for the differnt modes for refining." — 07-21
- `unverified` "in refine in the dual mode, both images should be replaced each round." — 07-21
- `partial` "dual should only compare two images of similar aspect ratio, gotta control for aspect ratio in duel" — 07-21 · orientation pairing pool exists in `web/features/compare/service.py`; aspect-ratio matching itself not found
- **`open`** "Im on the diverse mode but its showing me images that all look the same, in this mode it should show images that are very differnt unless not otherwise possible (this is to take max advantage of the elo propigation system to sort clusters of images relative to eachother quickly)" — 07-21 · `diverse_sample()` exists; the quality complaint about its output is unaddressed
- `unverified` "when using the refine mode, can we somehow limit it to the files that already have thumbnails and are ready to serv so we never get blank spots like this?" — 07-21

### 1.7 Mobile and Android

- `unverified` "I want to build a native andorid app, like google photos, with super deep rich intergration, not a pwa but a rich native android experience so that I can entirely replace google photos, so that every photo I take on my phone gets saved into this, and I can use it as my phones default photo app." — 07-12
- `unverified` "photos (and videos?) should be quietly moved off my phone and onto the main sever." — 07-12
- `unverified` "it should be two way too, for isntance the photos from yesterday imported from my camera on my computer should show up there." — 07-12
- `unverified` "I want to be able to share to other apps, I mean really just make it like a total copy of google photos." — 07-13
- `unverified` "For editing we should add a button to edit RAW in snapseed or other installed editing apps" — 07-13
- `unverified` "lets keep the phone app simple and elegant for now." — 07-12
- `unverified` "most of the real work should be done on a computer, we can worry about those types of features later." — 07-12

### 1.8 Sharing, publishing, website

- `unverified` "I want to publish a collection to my website, www.seankennethdoherty.com and share a link that way" — 07-08
- `unverified` "To be clear I want very clear manual approval for website publishing." — 07-08
- `unverified` "in the publishing page it should be extremely clear which collection is for the website and other personal/private/shared privately/shared with clients" — 07-09
- `unverified` "maybe there's something where you can update it and you can see what photos you wanna add, but it should be static once it's moved into there. And it should be a very deliberate thing to move that into the public" — 07-09
- `unverified` "I want to tie my website and this app together in an elegant way that also is friendly to other people using this software in their own workflows and websites." — 07-09
- `unverified` "I want the the images on the website to be generated from the code, and, preferably, have little interactive sections based off the real code of it." — 07-12
- `unverified` "the app and website need the same style and the website needs to show bits of the actual UI, not something totally differnt." — 07-14

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

Only items whose status above is `open` or `partial`. Everything else is either
already true or has never been checked — checking is itself the first job.

| # | Item | From | Status |
|---|---|---|---|
| 1 | Retire and remove `C:\Pictures` after proving every file has a home | 1.4 | `open`, restated twice |
| 2 | Diverse refine mode returns visually similar images; fix the sampling so it spans clusters | 1.6 | `open` |
| 3 | Aspect-ratio matching in Dual (orientation pairing is not the same thing) | 1.6 | `partial` |
| 4 | One-click invisible import: confirm what of `INVISIBLE_IMPORT.md` actually shipped | 1.4 | `partial` |
| 5 | Verification sweep: convert §1 `unverified` entries to `live`/`open` | §1 | 60+ entries |

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
- This file is exempt from the "no plans or dated status files" rule in
  [AGENTS.md](AGENTS.md) documentation hygiene. It is an owner document, not
  coordination scaffolding. The retired 1,580-line lane-era plan remains readable
  at `git show 937dee04:MASTER_PLAN.md`.
