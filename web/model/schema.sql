-- The core's tables. Five when it is finished; each arrives with the step that
-- needs it, so this file never describes something that is not yet true.
--
-- Steps 1 and 4: drives, copies.

CREATE TABLE IF NOT EXISTS drives (
    id         INTEGER PRIMARY KEY,
    uuid       TEXT    NOT NULL UNIQUE,
    root       TEXT    NOT NULL,
    label      TEXT    NOT NULL DEFAULT '',
    -- May this drive be the last copy? The archive may; the working disk may
    -- not. It is the only policy bit in the storage model, and reads, backup
    -- and reclaim all derive their behaviour from it.
    is_record  INTEGER NOT NULL DEFAULT 0,
    seen_at    REAL
);

-- A copy is a hint that this drive held this photo. It is never a truth:
-- reading verifies, and deleting verifies harder. Because a stale copy row
-- costs nothing, a sweep may drop what it did not see without any of the
-- ratios, thresholds and override switches that guarding a *verdict* required.
CREATE TABLE IF NOT EXISTS copies (
    photo_id  INTEGER NOT NULL,
    drive_id  INTEGER NOT NULL REFERENCES drives(id) ON DELETE CASCADE,
    -- NULL means "at the photo's own tail", which is the normal case. It is
    -- spelled out only when a copy sits somewhere else.
    tail      TEXT,
    seen_at   REAL NOT NULL,
    PRIMARY KEY (photo_id, drive_id)
);

CREATE INDEX IF NOT EXISTS idx_copies_drive ON copies(drive_id);
