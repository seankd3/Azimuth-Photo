# Azimuth Photo V2 Architecture

This document owns the shape of the V2 code. `CORE.md` owns the product model
and invariants. `MASTER_PLAN.md` preserves the owner's asks. `REWRITE_LEDGER.md`
records what has actually been replaced and proved. When they differ, fix the
lower-authority document or code; never create a compatibility layer between
two competing truths.

## One sentence

Azimuth Photo is one local desktop process: a native window calls a small
Python product vocabulary, which derives every answer from one local SQLite
catalog and the folders its owner attached.

It is not a server. V2 has no HTTP routes, port, child engine, remote mode,
browser authentication, or V1 adapter.

## The dependency direction

```text
native window + modular UI
            |
      direct JS bridge
            |
       desktop.py
            |
    boot.Library / OwnedLibrary
       /       |       \
  library   work/cache   render/metadata
            |
          model
            |
      SQLite + photo folders
```

Every arrow points down. A lower layer never imports or calls a higher layer.
There are no sideways feature calls and no second route to the same behavior.

## Python boundaries

### `web/model/` — durable truth

The model owns the five-table catalog, transactions, and the smallest durable
vocabulary:

- `drives`: explicitly attached roots identified by stable markers;
- `photos`: identity and safe access to originals;
- `copies`: observations of a photo on a drive;
- `decisions`: the append-only owner log;
- `sets` and `scope`: organization expressed over the same facts;
- `cache`: immutable computed answers and their recipes.

Only this layer writes durable facts. It never knows about windows, UI state,
JSON, background presentation, or transport.

### Product modules — derived behavior

`library.py`, `work.py`, `metadata.py`, `render.py`, and `tiles.py` turn model
facts into useful answers. They accept explicit values and connections. They do
not own process-global state, request objects, or presentation decisions.

There is one implementation of each behavior. A rewritten behavior replaces
and deletes its predecessor; wrappers around V1 do not count as V2.

### `web/boot.py` — one open product

`Library` is the complete in-process product boundary. It owns one catalog,
one tile store, and the background derivative worker. Its methods use product
language: attach, refresh, browse, counts, details, tile, set date, and debt.

`OwnedLibrary` confines the interactive SQLite connection to one thread. A
single separate sweep lane keeps a long folder refresh from blocking browsing.
Closing refuses new work, drains admitted work, stops derivatives, and releases
the catalog exactly once.

This ownership is lifecycle control, not a service framework. Do not grow it
into a scheduler, repository layer, event bus, or dependency container.

### `web/desktop.py` — the native edge

The desktop module owns exactly four things:

1. platform data and cache paths;
2. the pywebview window;
3. the direct JavaScript-facing product verbs;
4. deterministic shutdown.

The bridge translates simple values and errors. It contains no business logic.
The native folder chooser belongs here because choosing a folder is a window
capability. Tiles cross the serverless bridge as data URLs until measured
whole-library behavior justifies a smaller mechanism.

No method may bind a port, fetch a URL, choose between V1 and V2, or accept a
remote host. Closing the window closes the product in the same process.

## UI boundaries

The V2 UI lives under `web/static/v2/` and has five one-way layers:

```text
kit  <-  net  <-  store  <-  lens  <-  shell
```

- `kit/` contains presentation primitives with no product state.
- `net/` is the only JavaScript-to-Python bridge vocabulary. The name means
  boundary, not network; it contains no `fetch`, URLs, or route strings.
- `store/` owns the small current view state and lens registry.
- `lens/` renders one way of seeing the shared library.
- `shell/` coordinates product actions and global interaction.

The shell obtains lenses through the registry rather than importing their
implementations. A lens may render and emit product intent; it may not call the
bridge directly. UI modules never import inherited `web/static/js` code.

Whole-library browsing is a sparse window, not an ever-growing array. The grid
computes positions from total count and viewport geometry, `kit/page-cache.js`
owns deduplicated pages and rejects stale generations, and the lens keeps only
the visible overscan neighborhood in the document. Sorting begins a new
generation; a late answer from the old order cannot repaint it.

`web/templates/v2.html` is semantic structure, not a hidden application. The
build bundles the modules and inlines the CSS and JavaScript into one serverless
document because a frozen pywebview app has no static-file server.

## Data and threading

The catalog lives on the local machine. Photo originals and immutable computed
answers may live on attached drives. SQLite never lives on a network share.

The interactive connection has one owner thread. A refresh gets its own bounded
connection and commits progressive batches so the grid can fill while scanning.
Background derivatives query what is owed; they do not maintain a queue or
shadow status table.

UI calls may arrive concurrently. The owned lanes serialize access at the
catalog boundary, not with sleeps or UI-level locks.

## Error shape

Refuse invalid input at the narrowest boundary. Model and product modules raise
specific Python exceptions; the desktop bridge presents one useful message.
Absence is `None` only when absence is a normal answer. A failed derivative is
a recorded fact, not an endless retry.

Do not add defensive branches for impossible legacy states. Either the V2
schema permits a state and the product handles it, or the schema/invariant
forbids it and tests prove the refusal.

## Packaging

`scripts/build_desktop_ui.py` bundles the modular UI into one generated HTML
document. `scripts/build_desktop.py` freezes the window, product, schema, and
document into `dist/azimuth-photo/`. `scripts/build_windows_desktop.ps1` is the
clean Windows entry point for that build.

Build output is generated and ignored. The only checked-in desktop asset is the
canonical Windows icon. A release artifact is presently an unsigned zipped
application directory. Installer, signing, and updates remain explicit future
work; none may reintroduce a second process or network transport.

## Proof, not confidence

A V2 slice is complete only when all of these are true:

1. The replacement expresses the CORE invariant directly.
2. The obsolete implementation and its tests are deleted.
3. Focused refuters cover the dangerous behavior, not implementation trivia.
4. The real product path is exercised with a fresh catalog and real files.
5. Native behavior is visually checked when a unit test cannot prove it.
6. Shutdown leaves no process and releases the catalog handle.
7. `REWRITE_LEDGER.md` records the replacement, removal, and evidence.
8. `scripts/rewrite_status.py --check` agrees with the ledger.

Rebuilt means the new code has replaced its predecessor and passed deliberate
review. Proven is stricter: the finished product uses it exclusively, obsolete
code is physically gone, and real-product evidence is recorded.

## Anti-regrowth rules

V2 must not contain:

- HTTP frameworks, routes, ports, CORS, auth, or remote-host configuration;
- Tauri/Rust shell code or a child Python engine;
- V1 imports, schema prelude, feature flags, adapters, or dual paths;
- repositories around a connection, manager objects around one function, or
  generic abstractions with only one real use;
- module-global catalog connections or mutable product singletons;
- UI `fetch` calls, API literals, or product logic inside rendering modules;
- unbounded whole-library reads when a page, scope, or owed query is enough;
- deletion based on similarity, guessed paths, or incomplete drive evidence.

When a future requirement genuinely needs one of these shapes, change the
architecture deliberately with product evidence. Do not smuggle it in as a
convenience.
