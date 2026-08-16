-- The core's tables. Five when it is finished; each arrives with the step that
-- needs it, so this file never describes something that is not yet true.
--
-- Step 1: drives.

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
