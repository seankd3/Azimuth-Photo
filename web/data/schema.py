"""Schema contract checks for the SQLite catalog database."""

import os

from data.repositories import catalog as catalog_repository

EXPECTED_EMBEDDING_DIM = 2048  # Qwen3-VL-Embedding-2B native dimension
SCHEMA_VERSION = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_sources (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    included INTEGER NOT NULL DEFAULT 1,
    online INTEGER NOT NULL DEFAULT 1,
    image_count INTEGER NOT NULL DEFAULT 0,
    active_image_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_scan_at REAL DEFAULT NULL,
    last_seen_at REAL DEFAULT NULL,
    removed_at REAL DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES catalog_sources(id),
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL UNIQUE,
    elo REAL DEFAULT 1200.0,
    comparisons INTEGER DEFAULT 0,
    propagated_updates INTEGER DEFAULT 0,
    status TEXT DEFAULT 'kept',
    flag TEXT DEFAULT 'unflagged',
    orientation TEXT DEFAULT NULL,
    date_taken TEXT DEFAULT NULL,
    camera_make TEXT DEFAULT NULL,
    camera_model TEXT DEFAULT NULL,
    lens TEXT DEFAULT NULL,
    file_ext TEXT DEFAULT NULL,
    file_size INTEGER DEFAULT NULL,
    file_modified_at REAL DEFAULT NULL,
    width INTEGER DEFAULT NULL,
    height INTEGER DEFAULT NULL,
    latitude REAL DEFAULT NULL,
    longitude REAL DEFAULT NULL,
    metadata_scanned_at REAL DEFAULT NULL,
    metadata_version INTEGER DEFAULT NULL,
    missing_at REAL DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS images_metadata_fts
USING fts5(
    filename,
    filepath,
    date_taken,
    camera_make,
    camera_model,
    lens,
    file_ext,
    content='images',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS images_metadata_fts_ai AFTER INSERT ON images BEGIN
    INSERT INTO images_metadata_fts(
        rowid, filename, filepath, date_taken, camera_make, camera_model, lens, file_ext
    )
    VALUES (
        new.id, new.filename, new.filepath, new.date_taken, new.camera_make,
        new.camera_model, new.lens, new.file_ext
    );
END;

CREATE TRIGGER IF NOT EXISTS images_metadata_fts_ad AFTER DELETE ON images BEGIN
    INSERT INTO images_metadata_fts(
        images_metadata_fts, rowid, filename, filepath, date_taken, camera_make,
        camera_model, lens, file_ext
    )
    VALUES (
        'delete', old.id, old.filename, old.filepath, old.date_taken,
        old.camera_make, old.camera_model, old.lens, old.file_ext
    );
END;

CREATE TRIGGER IF NOT EXISTS images_metadata_fts_au
AFTER UPDATE OF filename, filepath, date_taken, camera_make, camera_model, lens, file_ext
ON images BEGIN
    INSERT INTO images_metadata_fts(
        images_metadata_fts, rowid, filename, filepath, date_taken, camera_make,
        camera_model, lens, file_ext
    )
    VALUES (
        'delete', old.id, old.filename, old.filepath, old.date_taken,
        old.camera_make, old.camera_model, old.lens, old.file_ext
    );
    INSERT INTO images_metadata_fts(
        rowid, filename, filepath, date_taken, camera_make, camera_model, lens, file_ext
    )
    VALUES (
        new.id, new.filename, new.filepath, new.date_taken, new.camera_make,
        new.camera_model, new.lens, new.file_ext
    );
END;

CREATE TABLE IF NOT EXISTS comparisons (
    id INTEGER PRIMARY KEY,
    winner_id INTEGER REFERENCES images(id),
    loser_id INTEGER REFERENCES images(id),
    mode TEXT,
    elo_before_winner REAL,
    elo_before_loser REAL,
    action_id TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
CREATE INDEX IF NOT EXISTS idx_images_source_id ON images(source_id);
CREATE INDEX IF NOT EXISTS idx_catalog_sources_path ON catalog_sources(path);
CREATE INDEX IF NOT EXISTS idx_catalog_sources_active ON catalog_sources(included, online);
CREATE INDEX IF NOT EXISTS idx_images_elo ON images(elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_comparisons ON images(comparisons);
CREATE INDEX IF NOT EXISTS idx_comparisons_pair ON comparisons(winner_id, loser_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_loser ON comparisons(loser_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_action_id ON comparisons(action_id);

CREATE TABLE IF NOT EXISTS propagation_updates (
    id INTEGER PRIMARY KEY,
    action_id TEXT NOT NULL,
    image_id INTEGER NOT NULL REFERENCES images(id),
    elo_before REAL NOT NULL,
    propagated_updates_before INTEGER NOT NULL,
    elo_after REAL NOT NULL,
    delta REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_propagation_updates_action_id
ON propagation_updates(action_id);

-- Composite indexes for fast sorted queries with status filter
CREATE INDEX IF NOT EXISTS idx_images_status_elo ON images(status, elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_status_elo_asc ON images(status, elo ASC);
CREATE INDEX IF NOT EXISTS idx_images_status_id ON images(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_images_status_comparisons ON images(status, comparisons DESC);
CREATE INDEX IF NOT EXISTS idx_images_status_filename ON images(status, filename ASC);
CREATE INDEX IF NOT EXISTS idx_images_status_orient_elo ON images(status, orientation, elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_status_comps_elo ON images(status, comparisons, elo DESC);

-- Hot-path partial indexes for the active Library/Compare working set.
-- These avoid temp B-tree sorts caused by status IN ('kept', 'maybe').
CREATE INDEX IF NOT EXISTS idx_images_active_elo
ON images(elo DESC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_elo_asc
ON images(elo ASC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_comparisons
ON images(comparisons DESC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_comparisons_asc
ON images(comparisons ASC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_visible_comparisons_elo
ON images(comparisons ASC, elo DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_filename
ON images(filename ASC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_id
ON images(id DESC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_filepath
ON images(filepath ASC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_orientation_elo
ON images(orientation, elo DESC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_visible_orientation_elo
ON images(orientation, elo DESC) WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_camera
ON images(camera_make, camera_model) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_lens
ON images(lens) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_date_taken_sort_desc
ON images((date_taken IS NULL), date_taken DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_date_taken_sort_asc
ON images((date_taken IS NULL), date_taken ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_file_size_sort_desc
ON images((file_size IS NULL), file_size DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_file_size_sort_asc
ON images((file_size IS NULL), file_size ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_modified_sort_desc
ON images((file_modified_at IS NULL), file_modified_at DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_modified_sort_asc
ON images((file_modified_at IS NULL), file_modified_at ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_resolution_sort_desc
ON images(((width * height) IS NULL), (width * height) DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_resolution_sort_asc
ON images(((width * height) IS NULL), (width * height) ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_camera_sort_asc
ON images((camera_make IS NULL), camera_make ASC, camera_model ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_camera_sort_desc
ON images((camera_make IS NULL), camera_make DESC, camera_model DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;

-- Source-aware indexes for the active working set. Source state now determines
-- whether an image is active; status is retained only for old DB compatibility.
CREATE INDEX IF NOT EXISTS idx_images_source_elo
ON images(source_id, elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_elo_asc
ON images(source_id, elo ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_comparisons
ON images(source_id, comparisons DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_comparisons_asc
ON images(source_id, comparisons ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_filename
ON images(source_id, filename ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_id_desc
ON images(source_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_filepath
ON images(source_id, filepath ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_orientation_elo
ON images(source_id, orientation, elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_date_taken
ON images(source_id, date_taken DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_file_size
ON images(source_id, file_size DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_file_ext
ON images(source_id, file_ext);
CREATE INDEX IF NOT EXISTS idx_images_source_camera
ON images(source_id, camera_make, camera_model);
CREATE INDEX IF NOT EXISTS idx_images_source_lens
ON images(source_id, lens);
CREATE INDEX IF NOT EXISTS idx_images_source_missing
ON images(source_id, missing_at);
CREATE INDEX IF NOT EXISTS idx_images_source_missing_filepath
ON images(source_id, missing_at, filepath ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_missing_filepath_filename
ON images(source_id, missing_at, filepath, filename);
CREATE INDEX IF NOT EXISTS idx_images_source_missing_id
ON images(source_id, missing_at, id);
CREATE INDEX IF NOT EXISTS idx_images_missing_source_filepath_id
ON images(missing_at, source_id, filepath ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_images_active_orientation_count
ON images(orientation, missing_at) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_missing_file_ext_source
ON images(missing_at, file_ext, source_id);
CREATE INDEX IF NOT EXISTS idx_images_missing_file_ext_source_size
ON images(missing_at, file_ext, source_id, file_size);
CREATE INDEX IF NOT EXISTS idx_images_missing_year_source
ON images(missing_at, substr(date_taken, 1, 4), source_id)
WHERE date_taken IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_images_missing_lower_file_ext_source
ON images(missing_at, LOWER(file_ext), source_id)
WHERE file_ext IS NOT NULL AND file_ext != '';
CREATE INDEX IF NOT EXISTS idx_images_missing_date_source
ON images(missing_at, date_taken, source_id);
CREATE INDEX IF NOT EXISTS idx_images_missing_camera_label_source
ON images(missing_at, TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')), source_id)
WHERE camera_make IS NOT NULL OR camera_model IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_images_missing_lens_source
ON images(missing_at, lens, source_id)
WHERE lens IS NOT NULL AND lens != '';
CREATE INDEX IF NOT EXISTS idx_images_active_gps_count
ON images(source_id, latitude, longitude)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL
AND latitude IS NOT NULL AND longitude IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_images_rating_signal_cover
ON images(comparisons, propagated_updates, elo);
CREATE INDEX IF NOT EXISTS idx_images_source_missing_rating_signal
ON images(source_id, missing_at, comparisons, propagated_updates, elo);
CREATE TABLE IF NOT EXISTS embeddings (
    image_id INTEGER PRIMARY KEY REFERENCES images(id),
    embedding BLOB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS embedding_models (
    model_key TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS embeddings_by_model (
    model_key TEXT NOT NULL REFERENCES embedding_models(model_key),
    image_id INTEGER NOT NULL REFERENCES images(id),
    embedding BLOB NOT NULL,
    dimension INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (model_key, image_id)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_by_model_image_id
ON embeddings_by_model(image_id);

CREATE TABLE IF NOT EXISTS search_query_embeddings (
    model_key TEXT NOT NULL REFERENCES embedding_models(model_key),
    query_key TEXT NOT NULL,
    query TEXT NOT NULL,
    embedding BLOB NOT NULL,
    dimension INTEGER NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_used_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (model_key, query_key)
);

CREATE INDEX IF NOT EXISTS idx_search_query_embeddings_used
ON search_query_embeddings(last_used_at DESC);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_root TEXT NOT NULL,
    size TEXT NOT NULL,
    image_id INTEGER NOT NULL REFERENCES images(id),
    path TEXT NOT NULL,
    source_signature TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_accessed REAL NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (cache_root, size, image_id)
);

CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_access
ON cache_entries(cache_root, size, last_accessed);
CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_bytes
ON cache_entries(cache_root, size, size_bytes);

-- For pregen candidate batch query (ORDER BY filepath ASC with status filter)
CREATE INDEX IF NOT EXISTS idx_images_status_filepath ON images(status, filepath ASC);

-- For LRU eviction ordering (avoids TEMP B-TREE sort during budget enforcement)
CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_accessed_id
ON cache_entries(cache_root, size, last_accessed, image_id);

CREATE TABLE IF NOT EXISTS cache_metadata (
    cache_root TEXT PRIMARY KEY,
    thumb_config_signature TEXT NOT NULL,
    thumb_config_changed_at REAL NOT NULL,
    replace_stale_thumbnails INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unknown',
    representative_face_id INTEGER DEFAULT NULL,
    merged_into_person_id INTEGER DEFAULT NULL REFERENCES people(id),
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS face_detections (
    id INTEGER PRIMARY KEY,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    detection_key TEXT NOT NULL UNIQUE,
    bbox_x REAL NOT NULL DEFAULT 0,
    bbox_y REAL NOT NULL DEFAULT 0,
    bbox_w REAL NOT NULL DEFAULT 0,
    bbox_h REAL NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    quality REAL NOT NULL DEFAULT 0,
    embedding BLOB DEFAULT NULL,
    embedding_model TEXT NOT NULL DEFAULT '',
    cache_path TEXT NOT NULL DEFAULT '',
    ignored INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS face_assignments (
    face_id INTEGER PRIMARY KEY REFERENCES face_detections(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id),
    source TEXT NOT NULL DEFAULT 'worker',
    active INTEGER NOT NULL DEFAULT 1,
    assigned_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS person_image_membership (
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    face_count INTEGER NOT NULL DEFAULT 0,
    best_quality REAL NOT NULL DEFAULT 0,
    latest_face_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (person_id, image_id)
);

CREATE TABLE IF NOT EXISTS people_merge_suggestions (
    id INTEGER PRIMARY KEY,
    source_person_id INTEGER NOT NULL REFERENCES people(id),
    target_person_id INTEGER NOT NULL REFERENCES people(id),
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE(source_person_id, target_person_id)
);

CREATE TABLE IF NOT EXISTS face_scan_images (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    face_count INTEGER NOT NULL DEFAULT 0,
    cache_path TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    scanned_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (image_id, model_id)
);

CREATE INDEX IF NOT EXISTS idx_people_status_seen
ON people(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_face_detections_image_model
ON face_detections(image_id, embedding_model);
CREATE INDEX IF NOT EXISTS idx_face_detections_status_model
ON face_detections(ignored, embedding_model, quality DESC);
CREATE INDEX IF NOT EXISTS idx_face_assignments_person
ON face_assignments(person_id, active);
CREATE INDEX IF NOT EXISTS idx_person_image_membership_image
ON person_image_membership(image_id, person_id);
CREATE INDEX IF NOT EXISTS idx_people_merge_suggestions_pending
ON people_merge_suggestions(status, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_face_scan_images_status
ON face_scan_images(model_id, status, scanned_at);
"""

PRE_SCHEMA_CATALOG_SOURCES_DDL = (
    "CREATE TABLE IF NOT EXISTS catalog_sources ("
    "id INTEGER PRIMARY KEY, "
    "path TEXT NOT NULL UNIQUE, "
    "display_name TEXT NOT NULL, "
    "included INTEGER NOT NULL DEFAULT 1, "
    "online INTEGER NOT NULL DEFAULT 1, "
    "image_count INTEGER NOT NULL DEFAULT 0, "
    "active_image_count INTEGER NOT NULL DEFAULT 0, "
    "created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')), "
    "last_scan_at REAL DEFAULT NULL, "
    "last_seen_at REAL DEFAULT NULL, "
    "removed_at REAL DEFAULT NULL"
    ")"
)

IMAGE_COMPAT_COLUMNS = (
    ("source_id", "INTEGER REFERENCES catalog_sources(id)"),
    ("orientation", "TEXT DEFAULT NULL"),
    ("flag", "TEXT DEFAULT 'unflagged'"),
    ("propagated_updates", "INTEGER DEFAULT 0"),
    ("predicted_elo", "REAL DEFAULT NULL"),
    ("uncertainty", "REAL DEFAULT NULL"),
    ("aspect_ratio", "REAL DEFAULT NULL"),
    ("date_taken", "TEXT DEFAULT NULL"),
    ("camera_make", "TEXT DEFAULT NULL"),
    ("camera_model", "TEXT DEFAULT NULL"),
    ("lens", "TEXT DEFAULT NULL"),
    ("file_ext", "TEXT DEFAULT NULL"),
    ("file_size", "INTEGER DEFAULT NULL"),
    ("file_modified_at", "REAL DEFAULT NULL"),
    ("width", "INTEGER DEFAULT NULL"),
    ("height", "INTEGER DEFAULT NULL"),
    ("metadata_scanned_at", "REAL DEFAULT NULL"),
    ("latitude", "REAL DEFAULT NULL"),
    ("longitude", "REAL DEFAULT NULL"),
    ("metadata_version", "INTEGER DEFAULT NULL"),
    ("missing_at", "REAL DEFAULT NULL"),
)

CATALOG_SOURCE_COMPAT_COLUMNS = (
    ("display_name", "TEXT DEFAULT ''"),
    ("included", "INTEGER NOT NULL DEFAULT 1"),
    ("online", "INTEGER NOT NULL DEFAULT 1"),
    ("image_count", "INTEGER NOT NULL DEFAULT 0"),
    ("active_image_count", "INTEGER NOT NULL DEFAULT 0"),
    ("created_at", "REAL DEFAULT NULL"),
    ("last_scan_at", "REAL DEFAULT NULL"),
    ("last_seen_at", "REAL DEFAULT NULL"),
    ("removed_at", "REAL DEFAULT NULL"),
)

COMPAT_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_images_flag ON images(flag)",
    "CREATE INDEX IF NOT EXISTS idx_comparisons_action_id ON comparisons(action_id)",
    "CREATE INDEX IF NOT EXISTS idx_comparisons_loser ON comparisons(loser_id)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_id ON images(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_sources_path ON catalog_sources(path)",
    (
        "CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_bytes "
        "ON cache_entries(cache_root, size, size_bytes)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_catalog_sources_active "
        "ON catalog_sources(included, online)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_images_source_elo ON images(source_id, elo DESC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_elo_asc ON images(source_id, elo ASC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_comparisons ON images(source_id, comparisons DESC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_comparisons_asc ON images(source_id, comparisons ASC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_filename ON images(source_id, filename ASC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_id_desc ON images(source_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_filepath ON images(source_id, filepath ASC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_orientation_elo ON images(source_id, orientation, elo DESC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_date_taken ON images(source_id, date_taken DESC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_file_size ON images(source_id, file_size DESC)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_file_ext ON images(source_id, file_ext)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_camera ON images(source_id, camera_make, camera_model)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_lens ON images(source_id, lens)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_missing ON images(source_id, missing_at)",
    "CREATE INDEX IF NOT EXISTS idx_images_source_missing_filepath ON images(source_id, missing_at, filepath ASC)",
    (
        "CREATE INDEX IF NOT EXISTS idx_images_source_missing_filepath_filename "
        "ON images(source_id, missing_at, filepath, filename)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_images_source_missing_id ON images(source_id, missing_at, id)",
    (
        "CREATE INDEX IF NOT EXISTS idx_images_missing_source_filepath_id "
        "ON images(missing_at, source_id, filepath ASC, id ASC)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_orientation_count "
        "ON images(orientation, missing_at) WHERE status IN ('kept', 'maybe')"
    ),
    "CREATE INDEX IF NOT EXISTS idx_images_missing_file_ext_source ON images(missing_at, file_ext, source_id)",
    (
        "CREATE INDEX IF NOT EXISTS idx_images_missing_file_ext_source_size "
        "ON images(missing_at, file_ext, source_id, file_size)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_missing_year_source "
        "ON images(missing_at, substr(date_taken, 1, 4), source_id) "
        "WHERE date_taken IS NOT NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_missing_lower_file_ext_source "
        "ON images(missing_at, LOWER(file_ext), source_id) "
        "WHERE file_ext IS NOT NULL AND file_ext != ''"
    ),
    "CREATE INDEX IF NOT EXISTS idx_images_missing_date_source ON images(missing_at, date_taken, source_id)",
    (
        "CREATE INDEX IF NOT EXISTS idx_images_missing_camera_label_source "
        "ON images(missing_at, TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')), source_id) "
        "WHERE camera_make IS NOT NULL OR camera_model IS NOT NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_missing_lens_source "
        "ON images(missing_at, lens, source_id) WHERE lens IS NOT NULL AND lens != ''"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_gps_count "
        "ON images(source_id, latitude, longitude) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL"
    ),
    "CREATE INDEX IF NOT EXISTS idx_images_rating_signal_cover ON images(comparisons, propagated_updates, elo)",
    (
        "CREATE INDEX IF NOT EXISTS idx_images_source_missing_rating_signal "
        "ON images(source_id, missing_at, comparisons, propagated_updates, elo)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_date_taken "
        "ON images(date_taken DESC) WHERE status IN ('kept', 'maybe')"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_file_size "
        "ON images(file_size DESC) WHERE status IN ('kept', 'maybe')"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_file_ext "
        "ON images(file_ext) WHERE status IN ('kept', 'maybe')"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_camera "
        "ON images(camera_make, camera_model) WHERE status IN ('kept', 'maybe')"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_lens "
        "ON images(lens) WHERE status IN ('kept', 'maybe')"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_date_taken_sort_desc "
        "ON images((date_taken IS NULL), date_taken DESC, id DESC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_date_taken_sort_asc "
        "ON images((date_taken IS NULL), date_taken ASC, id ASC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_file_size_sort_desc "
        "ON images((file_size IS NULL), file_size DESC, id DESC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_file_size_sort_asc "
        "ON images((file_size IS NULL), file_size ASC, id ASC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_modified_sort_desc "
        "ON images((file_modified_at IS NULL), file_modified_at DESC, id DESC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_modified_sort_asc "
        "ON images((file_modified_at IS NULL), file_modified_at ASC, id ASC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_resolution_sort_desc "
        "ON images(((width * height) IS NULL), (width * height) DESC, id DESC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_resolution_sort_asc "
        "ON images(((width * height) IS NULL), (width * height) ASC, id ASC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_camera_sort_asc "
        "ON images((camera_make IS NULL), camera_make ASC, camera_model ASC, id ASC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_camera_sort_desc "
        "ON images((camera_make IS NULL), camera_make DESC, camera_model DESC, id DESC) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_gps "
        "ON images(latitude, longitude) WHERE latitude IS NOT NULL"
    ),
)

REQUIRED_TABLES = {
    "catalog_sources",
    "images",
    "images_metadata_fts",
    "comparisons",
    "embeddings",
    "embedding_models",
    "embeddings_by_model",
    "search_query_embeddings",
    "cache_entries",
    "cache_metadata",
    "people",
    "face_detections",
    "face_assignments",
    "person_image_membership",
    "people_merge_suggestions",
    "face_scan_images",
}

REQUIRED_COLUMNS = {
    "images": {
        "source_id",
        "orientation",
        "flag",
        "propagated_updates",
        "predicted_elo",
        "uncertainty",
        "aspect_ratio",
        "date_taken",
        "camera_make",
        "camera_model",
        "lens",
        "file_ext",
        "file_size",
        "file_modified_at",
        "width",
        "height",
        "metadata_scanned_at",
        "latitude",
        "longitude",
        "metadata_version",
        "missing_at",
    },
    "catalog_sources": {
        "display_name",
        "included",
        "online",
        "image_count",
        "active_image_count",
        "created_at",
        "last_scan_at",
        "last_seen_at",
        "removed_at",
    },
    "comparisons": {"action_id"},
    "cache_metadata": {"replace_stale_thumbnails"},
}

REQUIRED_INDEXES = {
    "idx_catalog_sources_active",
    "idx_images_missing_source_filepath_id",
    "idx_images_source_missing_id",
    "idx_images_source_missing_rating_signal",
    "idx_images_active_visible_orientation_elo",
    "idx_images_visible_comparisons_elo",
    "idx_images_missing_file_ext_source_size",
    "idx_images_active_camera_sort_desc",
    "idx_images_rating_signal_cover",
    "idx_embeddings_by_model_image_id",
    "idx_search_query_embeddings_used",
    "idx_cache_entries_root_size_bytes",
    "idx_cache_entries_root_size_accessed_id",
    "idx_people_status_seen",
    "idx_face_detections_image_model",
    "idx_face_detections_status_model",
    "idx_face_assignments_person",
    "idx_person_image_membership_image",
    "idx_people_merge_suggestions_pending",
    "idx_face_scan_images_status",
}


async def table_columns(conn, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in await cursor.fetchall()}


async def table_exists(conn, table: str) -> bool:
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    return await cursor.fetchone() is not None


async def _add_columns_if_missing(conn, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    if not await table_exists(conn, table):
        return
    existing = await table_columns(conn, table)
    for col, defn in columns:
        if col in existing:
            continue
        try:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        except Exception:
            pass


async def prepare_existing_database_for_schema(conn) -> None:
    """Repair legacy tables before running SCHEMA indexes.

    Older databases can lack columns referenced by CREATE INDEX statements in
    SCHEMA. Add that narrow column set first so executescript remains safe.
    """
    await conn.execute(PRE_SCHEMA_CATALOG_SOURCES_DDL)
    await _add_columns_if_missing(conn, "images", IMAGE_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "comparisons", (("action_id", "TEXT DEFAULT NULL"),))


async def ensure_compatibility_columns(conn) -> None:
    await _add_columns_if_missing(conn, "images", IMAGE_COMPAT_COLUMNS)
    await _add_columns_if_missing(
        conn,
        "cache_metadata",
        (("replace_stale_thumbnails", "INTEGER NOT NULL DEFAULT 0"),),
    )
    await _add_columns_if_missing(conn, "comparisons", (("action_id", "TEXT DEFAULT NULL"),))
    await _add_columns_if_missing(conn, "catalog_sources", CATALOG_SOURCE_COMPAT_COLUMNS)


async def ensure_compatibility_indexes(conn) -> None:
    for sql in COMPAT_INDEX_SQL:
        await conn.execute(sql)


async def backfill_legacy_aspect_ratios(conn) -> None:
    await conn.execute(
        "UPDATE images SET aspect_ratio = 1.5 WHERE orientation = 'landscape' AND aspect_ratio IS NULL"
    )
    await conn.execute(
        "UPDATE images SET aspect_ratio = 0.6667 WHERE orientation = 'portrait' AND aspect_ratio IS NULL"
    )


async def _executescript_in_transaction(conn, script: str) -> None:
    try:
        await conn.executescript(f"BEGIN;\n{script}\nCOMMIT;")
    except Exception:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise


async def apply_schema_and_migrations(conn, *, db_exists: bool) -> None:
    if db_exists:
        await prepare_existing_database_for_schema(conn)
    await _executescript_in_transaction(conn, SCHEMA)
    try:
        await conn.execute("BEGIN")
        for table in (
            "semantic_search_result_cache",
            "deep_search_query_embeddings",
            "deep_search_queries",
        ):
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await ensure_compatibility_columns(conn)
        await ensure_compatibility_indexes(conn)
        await backfill_legacy_aspect_ratios(conn)
        await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise


async def normalize_legacy_image_state(conn) -> bool:
    changed = False
    cursor = await conn.execute(
        "UPDATE images SET flag = 'rejected' "
        "WHERE status = 'rejected' "
        "AND (flag IS NULL OR flag = '' OR flag = 'unflagged')"
    )
    if cursor.rowcount:
        changed = True

    cursor = await conn.execute(
        "UPDATE images SET flag = 'unflagged' WHERE flag IS NULL OR flag = ''"
    )
    if cursor.rowcount:
        changed = True

    # Status used to control membership in older versions. Sources now own
    # membership, so normalize old statuses after preserving rejection as a flag.
    cursor = await conn.execute(
        "UPDATE images SET status = 'kept' WHERE status IS NULL OR status != 'kept'"
    )
    if cursor.rowcount:
        changed = True
    return changed


async def migrate_catalog_sources(conn) -> bool:
    normalized = await normalize_legacy_image_state(conn)

    await conn.execute(
        "UPDATE images SET source_id = NULL "
        "WHERE source_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM catalog_sources WHERE catalog_sources.id = images.source_id)"
    )

    cursor = await conn.execute(
        "SELECT filepath FROM images WHERE source_id IS NULL ORDER BY filepath"
    )
    rows = await cursor.fetchall()
    if rows:
        dirs = [os.path.dirname(row["filepath"]) for row in rows if row["filepath"]]
        try:
            root = os.path.commonpath(dirs) if dirs else os.path.expanduser("~/Pictures")
        except ValueError:
            root = dirs[0] if dirs else os.path.expanduser("~/Pictures")
        source = await catalog_repository.ensure_catalog_source_on_conn(conn, root, included=True)
        await conn.execute(
            "UPDATE images SET source_id = ? WHERE source_id IS NULL",
            (source["id"],),
        )

    await catalog_repository.update_source_counts_on_conn(conn)
    return normalized


async def schema_is_current(conn) -> bool:
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    schema_version = int(row[0] if row is not None else 0)

    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = {row["name"] for row in await cursor.fetchall()}
    if not REQUIRED_TABLES.issubset(tables):
        return False

    for table, columns in REQUIRED_COLUMNS.items():
        existing = await table_columns(conn, table)
        if not columns.issubset(existing):
            return False

    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    indexes = {row["name"] for row in await cursor.fetchall()}
    if not REQUIRED_INDEXES.issubset(indexes):
        return False

    for sql in (
        "SELECT 1 FROM images WHERE source_id IS NULL LIMIT 1",
        "SELECT 1 FROM images WHERE COALESCE(status, '') != 'kept' LIMIT 1",
    ):
        cursor = await conn.execute(sql)
        if await cursor.fetchone():
            return False

    await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    await conn.commit()
    return schema_version >= SCHEMA_VERSION


async def ensure_metadata_fts(conn) -> None:
    try:
        cursor = await conn.execute("SELECT COUNT(*) AS count FROM images")
        image_count = int((await cursor.fetchone())["count"] or 0)
        cursor = await conn.execute("SELECT COUNT(*) AS count FROM images_metadata_fts_docsize")
        fts_count = int((await cursor.fetchone())["count"] or 0)
        if image_count != fts_count:
            await conn.execute("INSERT INTO images_metadata_fts(images_metadata_fts) VALUES('rebuild')")
    except Exception:
        # FTS is a speed path only; metadata search falls back to regular SQL.
        pass
