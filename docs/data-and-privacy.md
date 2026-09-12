# Data and privacy

Azimuth Photo is one local process. It needs no account, no server, and no
cloud service to catalog, browse, rank, cull, develop, and export a library,
and it uploads nothing.

## Originals are read in place

- Attaching a folder reads it and records what is there. It never moves,
  renames, or rewrites a photograph.
- Browsing, ranking, search, People, Develop and export write nothing into a
  source folder. Develop keeps its settings as decisions in the catalog; the
  only file it ever writes beside a photograph is a Lightroom sidecar, and
  only when you ask for one.
- Trash is the one exception: rejecting moves the original into a `.trash`
  folder on the same drive, and **Empty trash** deletes those moved files.
- A drive that is away keeps its rows. Previews, rankings, search and People
  stay usable until it is attached again.

## Where the app keeps its own data

Everything Azimuth makes for itself lives in the one home you chose at first
launch, beside each other like a Lightroom catalog:

```text
<home>\catalog\azimuth.db
<home>\previews\
```

The one file outside the home is the pointer that names it, at
`%LOCALAPPDATA%\Azimuth Photo\home`. `AZIMUTH_HOME` names a home directly
without reading or writing the pointer; that is the only environment variable
the app reads. Everything in the home can be rebuilt from the originals except
your decisions, which are the catalog's reason to exist.

## Local AI

The embedding and face models are downloaded once, on request, and run on
this machine. Without them the app still browses, culls, and exports; search,
Best and labels sharpen as the space grows.

## Presentation assets

Public repository visuals under `docs/assets/` use synthetic or demo-safe
content only: no personal photographs, real paths, recognizable people, or
private metadata.
