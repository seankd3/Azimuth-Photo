# Catalog recovery

Use this runbook when Photo Archive starts but the catalog cannot be opened, or
when the Library Health view reports a corrupt catalog.

## What the app detects

At normal startup, the app performs SQLite `PRAGMA quick_check` before catalog
migrations or background work. If it fails, startup leaves the catalog file in
place and the server remains available for recovery status. The System integrity
status reports `catalog.state: "corrupt"` and its SQLite error; the server log
includes `catalog startup blocked`.

This check does not repair or replace anything automatically.

## Restore a snapshot

1. In Library Health, choose a catalog snapshot and select **Prepare restore**.
   This validates the gzip archive and writes `photoarchive.restored.db` beside
   the live catalog. It never overwrites the live catalog.
2. Stop the Photo Archive server.
3. In the catalog directory, retain the failed catalog with a dated name, then
   put the prepared catalog in its place:

   ```bash
   mv photoarchive.db "photoarchive.corrupt-$(date +%Y%m%d-%H%M%S).db"
   mv photoarchive.restored.db photoarchive.db
   ```

   Use the configured catalog location (`PHOTOARCHIVE_DB_PATH`) when it differs
   from the default runtime location.
4. Start the server and confirm Library Health reports an `ok` catalog check.
   Keep the renamed corrupt file until the recovered catalog has been reviewed.

If the snapshot cannot be prepared, do not replace the live database. Select a
different snapshot or retain the files for SQLite recovery support.

## What a catalog snapshot contains

Snapshots preserve the SQLite catalog at their creation time: image records,
organization, edits, rankings, source configuration, cached thumbnail records,
and catalog-backed settings. They do not copy original photos or videos, files
in source `.trash` folders, thumbnail files themselves, or settings stored in
the separate settings file.

Changes made after the selected snapshot are lost from the restored catalog.
Original media remains in its source folders; it needs its own backup strategy.
