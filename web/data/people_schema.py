"""Exact query projections maintained for the People review surface."""


PEOPLE_QUERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_image_presence (
    cache_root TEXT NOT NULL,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    PRIMARY KEY (cache_root, image_id)
);
CREATE INDEX IF NOT EXISTS idx_cache_entries_root_image_size
ON cache_entries(cache_root, image_id, size);

CREATE TRIGGER IF NOT EXISTS cache_entries_presence_ai
AFTER INSERT ON cache_entries BEGIN
    INSERT OR IGNORE INTO cache_image_presence(cache_root, image_id)
    VALUES (NEW.cache_root, NEW.image_id);
END;
CREATE TRIGGER IF NOT EXISTS cache_entries_presence_ad
AFTER DELETE ON cache_entries BEGIN
    DELETE FROM cache_image_presence
    WHERE cache_root = OLD.cache_root AND image_id = OLD.image_id
    AND NOT EXISTS (
        SELECT 1 FROM cache_entries
        WHERE cache_root = OLD.cache_root AND image_id = OLD.image_id
    );
END;
CREATE TRIGGER IF NOT EXISTS cache_entries_presence_au
AFTER UPDATE OF cache_root, image_id ON cache_entries BEGIN
    INSERT OR IGNORE INTO cache_image_presence(cache_root, image_id)
    VALUES (NEW.cache_root, NEW.image_id);
    DELETE FROM cache_image_presence
    WHERE cache_root = OLD.cache_root AND image_id = OLD.image_id
    AND NOT EXISTS (
        SELECT 1 FROM cache_entries
        WHERE cache_root = OLD.cache_root AND image_id = OLD.image_id
    );
END;

CREATE TABLE IF NOT EXISTS face_scan_models (
    model_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS face_scan_backlog (
    cache_root TEXT NOT NULL,
    model_id TEXT NOT NULL,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    error_scanned_at REAL DEFAULT NULL,
    PRIMARY KEY (cache_root, model_id, image_id)
);
CREATE INDEX IF NOT EXISTS idx_face_scan_backlog_ready
ON face_scan_backlog(cache_root, model_id, image_id)
WHERE error_scanned_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_face_scan_backlog_retry
ON face_scan_backlog(cache_root, model_id, error_scanned_at, image_id)
WHERE error_scanned_at IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS face_scan_models_ai
AFTER INSERT ON face_scan_models BEGIN
    INSERT OR IGNORE INTO face_scan_backlog(cache_root, model_id, image_id, error_scanned_at)
    SELECT cached.cache_root, NEW.model_id, cached.image_id,
           CASE WHEN fsi.status = 'error' THEN fsi.scanned_at ELSE NULL END
    FROM cache_image_presence cached
    LEFT JOIN face_scan_images fsi
      ON fsi.image_id = cached.image_id AND fsi.model_id = NEW.model_id
    WHERE fsi.image_id IS NULL OR fsi.status = 'error';
END;
CREATE TRIGGER IF NOT EXISTS face_scan_models_ad
AFTER DELETE ON face_scan_models BEGIN
    DELETE FROM face_scan_backlog WHERE model_id = OLD.model_id;
END;
CREATE TRIGGER IF NOT EXISTS cache_image_presence_face_backlog_ai
AFTER INSERT ON cache_image_presence BEGIN
    INSERT OR IGNORE INTO face_scan_backlog(cache_root, model_id, image_id, error_scanned_at)
    SELECT NEW.cache_root, models.model_id, NEW.image_id,
           CASE WHEN fsi.status = 'error' THEN fsi.scanned_at ELSE NULL END
    FROM face_scan_models models
    LEFT JOIN face_scan_images fsi
      ON fsi.image_id = NEW.image_id AND fsi.model_id = models.model_id
    WHERE fsi.image_id IS NULL OR fsi.status = 'error';
END;
CREATE TRIGGER IF NOT EXISTS cache_image_presence_face_backlog_ad
AFTER DELETE ON cache_image_presence BEGIN
    DELETE FROM face_scan_backlog
    WHERE cache_root = OLD.cache_root AND image_id = OLD.image_id;
END;
CREATE TRIGGER IF NOT EXISTS face_scan_images_backlog_ai
AFTER INSERT ON face_scan_images BEGIN
    DELETE FROM face_scan_backlog WHERE model_id = NEW.model_id AND image_id = NEW.image_id;
    INSERT OR IGNORE INTO face_scan_backlog(cache_root, model_id, image_id, error_scanned_at)
    SELECT cache_root, NEW.model_id, NEW.image_id, NEW.scanned_at
    FROM cache_image_presence
    WHERE image_id = NEW.image_id AND NEW.status = 'error';
END;
CREATE TRIGGER IF NOT EXISTS face_scan_images_backlog_au
AFTER UPDATE OF status, scanned_at ON face_scan_images BEGIN
    DELETE FROM face_scan_backlog WHERE model_id = NEW.model_id AND image_id = NEW.image_id;
    INSERT OR IGNORE INTO face_scan_backlog(cache_root, model_id, image_id, error_scanned_at)
    SELECT cache_root, NEW.model_id, NEW.image_id, NEW.scanned_at
    FROM cache_image_presence
    WHERE image_id = NEW.image_id AND NEW.status = 'error';
END;
CREATE TRIGGER IF NOT EXISTS face_scan_images_backlog_ad
AFTER DELETE ON face_scan_images BEGIN
    INSERT OR IGNORE INTO face_scan_backlog(cache_root, model_id, image_id, error_scanned_at)
    SELECT cache_root, OLD.model_id, OLD.image_id, NULL
    FROM cache_image_presence
    WHERE image_id = OLD.image_id
      AND EXISTS (SELECT 1 FROM face_scan_models WHERE model_id = OLD.model_id);
END;

CREATE TABLE IF NOT EXISTS people_operational_metrics (
    metric TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS face_scan_status_counts (
    status TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO people_operational_metrics(metric, value)
VALUES ('detected_faces', 0);

CREATE TRIGGER IF NOT EXISTS face_detections_metric_ai
AFTER INSERT ON face_detections WHEN NEW.ignored = 0 BEGIN
    UPDATE people_operational_metrics SET value = value + 1
    WHERE metric = 'detected_faces';
END;
CREATE TRIGGER IF NOT EXISTS face_detections_metric_ad
AFTER DELETE ON face_detections WHEN OLD.ignored = 0 BEGIN
    UPDATE people_operational_metrics SET value = MAX(value - 1, 0)
    WHERE metric = 'detected_faces';
END;
CREATE TRIGGER IF NOT EXISTS face_detections_metric_au
AFTER UPDATE OF ignored ON face_detections WHEN OLD.ignored != NEW.ignored BEGIN
    UPDATE people_operational_metrics
    SET value = MAX(value + CASE WHEN NEW.ignored = 0 THEN 1 ELSE -1 END, 0)
    WHERE metric = 'detected_faces';
END;
CREATE TRIGGER IF NOT EXISTS face_scan_status_counts_ai
AFTER INSERT ON face_scan_images BEGIN
    INSERT INTO face_scan_status_counts(status, value) VALUES (NEW.status, 1)
    ON CONFLICT(status) DO UPDATE SET value = value + 1;
END;
CREATE TRIGGER IF NOT EXISTS face_scan_status_counts_ad
AFTER DELETE ON face_scan_images BEGIN
    UPDATE face_scan_status_counts SET value = MAX(value - 1, 0)
    WHERE status = OLD.status;
END;
CREATE TRIGGER IF NOT EXISTS face_scan_status_counts_au
AFTER UPDATE OF status ON face_scan_images WHEN OLD.status != NEW.status BEGIN
    UPDATE face_scan_status_counts SET value = MAX(value - 1, 0)
    WHERE status = OLD.status;
    INSERT INTO face_scan_status_counts(status, value) VALUES (NEW.status, 1)
    ON CONFLICT(status) DO UPDATE SET value = value + 1;
END;

CREATE INDEX IF NOT EXISTS idx_people_unknown_review
ON people(photo_count DESC, face_count DESC, best_quality DESC, latest_face_at DESC, id ASC)
WHERE merged_into_person_id IS NULL AND status != 'ignored' AND status != 'named'
AND TRIM(name) = '' AND face_count > 0;
CREATE INDEX IF NOT EXISTS idx_people_named_review
ON people(
    CASE WHEN TRIM(COALESCE(name, '')) = '' THEN printf('person %012d', id)
         ELSE LOWER(TRIM(COALESCE(name, ''))) END,
    photo_count DESC,
    id ASC
)
WHERE merged_into_person_id IS NULL AND status != 'ignored' AND face_count > 0
AND (status = 'named' OR TRIM(name) != '');

CREATE TRIGGER IF NOT EXISTS person_image_membership_ai
AFTER INSERT ON person_image_membership BEGIN
    UPDATE people SET
        photo_count = photo_count + 1,
        face_count = face_count + NEW.face_count,
        best_quality = MAX(best_quality, NEW.best_quality),
        latest_face_at = MAX(latest_face_at, NEW.latest_face_at)
    WHERE id = NEW.person_id;
END;
CREATE TRIGGER IF NOT EXISTS person_image_membership_ad
AFTER DELETE ON person_image_membership BEGIN
    UPDATE people SET
        photo_count = MAX(photo_count - 1, 0),
        face_count = MAX(face_count - OLD.face_count, 0),
        best_quality = CASE WHEN OLD.best_quality >= best_quality THEN COALESCE((
            SELECT MAX(best_quality) FROM person_image_membership
            WHERE person_id = OLD.person_id
        ), 0) ELSE best_quality END,
        latest_face_at = CASE WHEN OLD.latest_face_at >= latest_face_at THEN COALESCE((
            SELECT MAX(latest_face_at) FROM person_image_membership
            WHERE person_id = OLD.person_id
        ), 0) ELSE latest_face_at END
    WHERE id = OLD.person_id;
END;

INSERT OR IGNORE INTO face_scan_models(model_id) VALUES ('buffalo_l');
"""
