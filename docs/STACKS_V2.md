# Stacks v2

Stacks is Azimuth Photo's high-speed resolve surface. It groups photos by the
decision the photographer can safely make, not by the implementation that found
the relationship.

## Model

| View | Meaning | Default behavior |
| --- | --- | --- |
| Identical | Files proven byte-for-byte identical | Keep the oldest; move verified copies to Trash |
| Bursts | Different frames from one moment | Recommend a keeper; require human review |
| Versions | RAWs, edits, exports, and named variants | Preserve the lineage |
| Similar | Strong visual matches that are not proven copies | Require human review |
| Manual | Groups explicitly made by the photographer | Preserve user intent |

Cross-source is an attribute, not a decision class. Files on different sources
may be identical, similar, or versions.

## Identical contract

The catalog's fast content identity is candidate generation only. It hashes the
first 8 MiB plus file size and never authorizes cleanup.

Before Azimuth enables archive-wide cleanup it must:

1. Read every byte of every candidate file and compare full-file digests.
2. Confirm each file's device, inode, size, and modified time stayed unchanged
   during verification.
3. Keep the file with the earliest filesystem-modified time. Ties go to the
   earliest catalog record and then the lowest image id.
4. Recheck those file tokens immediately before moving anything.
5. Defer the whole group when a source is offline, a file changes, or full
   digests disagree.
6. Move redundant copies to Azimuth Trash. Never permanently delete them from
   Stacks. The existing Trash restore flow is the undo path.

The result must read as a plain plan: groups verified, copies removable, bytes
recoverable, and exceptions deferred. Users review exceptions rather than
thousands of obvious matches.

## Interaction

- Identical is the default Stacks view.
- Images are large enough to judge without opening Loupe; rows use responsive
  260-420 px imagery on desktop.
- Repeated metadata is hidden. Show filenames and only the fields that differ.
- Every group explains why it exists: candidate, byte-identical, or deferred.
- The recommended keeper is visibly labeled `Oldest` with its file date.
- Cleanup is a single reversible action with an Undo toast.
- Bursts and Similar never receive an archive-wide trash action.

## Performance

- The frame and cached counts appear immediately.
- Candidate listing uses the indexed fast identity and paginates rows.
- Full-file verification runs outside the request path and exposes progress.
- Thumbnails lazy-load at the displayed size; stack rows remain horizontally
  scrollable and vertically incremental.
