"""Schema contract checks for the SQLite catalog database."""

import asyncio
import os
import shutil
import uuid

from date_inference import infer_image_date
from data.repositories import catalog as catalog_repository
from data.people_schema import PEOPLE_QUERY_SCHEMA
from core.path_groups import safe_commonpath

EXPECTED_EMBEDDING_DIM = 2048  # Qwen3-VL-Embedding-2B native dimension
SCHEMA_VERSION = 29

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
    filepath TEXT NOT NULL,
    content_hash TEXT DEFAULT NULL,
    row_version INTEGER NOT NULL DEFAULT 0,
    hub_image_id INTEGER DEFAULT NULL,
    hub_remote INTEGER NOT NULL DEFAULT 0,
    trash_pending_hub INTEGER NOT NULL DEFAULT 0,
    elo REAL DEFAULT 1200.0,
    comparisons INTEGER DEFAULT 0,
    propagated_updates INTEGER DEFAULT 0,
    status TEXT DEFAULT 'kept',
    flag TEXT DEFAULT 'unflagged',
    orientation TEXT DEFAULT NULL,
    date_taken TEXT DEFAULT NULL,
    date_source TEXT DEFAULT NULL,
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
    location_source TEXT DEFAULT NULL,
    metadata_scanned_at REAL DEFAULT NULL,
    metadata_version INTEGER DEFAULT NULL,
    missing_at REAL DEFAULT NULL,
    trashed_at REAL DEFAULT NULL,
    trash_path TEXT DEFAULT NULL,
    vc_of INTEGER REFERENCES images(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- v29: deterministic collection-suggestion parsing is persisted per image.
-- Rows are populated lazily by the suggestions service and invalidated by its
-- parser version, so schema startup never does catalog-scale filename work.
CREATE TABLE IF NOT EXISTS image_shoot_hints (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    key TEXT,
    title TEXT,
    source TEXT,
    path_date REAL,
    parser_version INTEGER NOT NULL
);

-- Develop v21: canonical Lightroom-compatible edit state.  Unknown crs keys
-- remain in settings so future render stages and XMP export can round-trip them.
CREATE TABLE IF NOT EXISTS develop_settings (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    settings TEXT NOT NULL DEFAULT '{}',
    origin TEXT NOT NULL DEFAULT 'user',
    xmp_path TEXT,
    xmp_mtime REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS develop_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    settings TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_develop_history_image
ON develop_history(image_id, id DESC);

CREATE TABLE IF NOT EXISTS oplog (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    origin_seq INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    family TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts REAL NOT NULL,
    applied_from TEXT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_oplog_origin_seq
ON oplog(origin, origin_seq);
CREATE INDEX IF NOT EXISTS idx_oplog_content_family
ON oplog(content_hash, family);
CREATE TABLE IF NOT EXISTS oplog_family_state (
    content_hash TEXT NOT NULL,
    family TEXT NOT NULL,
    ts REAL NOT NULL,
    origin TEXT NOT NULL,
    origin_seq INTEGER NOT NULL,
    PRIMARY KEY (content_hash, family)
);
CREATE TABLE IF NOT EXISTS oplog_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oplog_cursors (
    origin TEXT PRIMARY KEY,
    last_seen_origin_seq INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_seen REAL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_devices_token_hash ON devices(token_hash);
CREATE INDEX IF NOT EXISTS idx_devices_revoked_at ON devices(revoked_at);

CREATE TABLE IF NOT EXISTS develop_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_develop_presets_folder
ON develop_presets(folder, name);

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
CREATE INDEX IF NOT EXISTS idx_images_content_hash
ON images(content_hash) WHERE content_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_images_hub_image_id
ON images(hub_image_id) WHERE hub_image_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_images_hub_remote
ON images(hub_remote, hub_image_id);
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
CREATE INDEX IF NOT EXISTS idx_images_active_flag_elo
ON images(flag, elo DESC, id DESC) WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_source_flag_elo
ON images(source_id, flag, elo DESC) WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_camera_label_elo
ON images(TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')), elo DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_trashed_at
ON images(trashed_at DESC) WHERE status = 'trashed';
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
CREATE INDEX IF NOT EXISTS idx_images_active_filepath_elo
ON images(filepath, elo DESC, id)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL AND vc_of IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_filepath_date_taken
ON images(filepath, (date_taken IS NULL), date_taken DESC, id)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL AND vc_of IS NULL;
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
CREATE INDEX IF NOT EXISTS idx_images_active_month_source
ON images(substr(date_taken, 1, 7), source_id)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL AND vc_of IS NULL;
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
CREATE INDEX IF NOT EXISTS idx_images_active_gps_markers
ON images(latitude, longitude, source_id, filename)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL AND vc_of IS NULL
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

CREATE TABLE IF NOT EXISTS caption_fts_model (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    model_key TEXT NOT NULL DEFAULT ''
);

INSERT OR IGNORE INTO caption_fts_model (id, model_key) VALUES (1, '');

CREATE TABLE IF NOT EXISTS image_captions (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    model_key TEXT NOT NULL,
    caption TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    quality TEXT DEFAULT NULL,
    user_edited INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (model_key, image_id)
);

CREATE INDEX IF NOT EXISTS idx_image_captions_image
ON image_captions(image_id);

CREATE TABLE IF NOT EXISTS image_tags (
    model_key TEXT NOT NULL,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (model_key, image_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_image_tags_model_tag_image
ON image_tags(model_key, tag, image_id);

CREATE INDEX IF NOT EXISTS idx_image_tags_image_model
ON image_tags(image_id, model_key);

CREATE TRIGGER IF NOT EXISTS image_tags_ai
AFTER INSERT ON image_captions
BEGIN
    INSERT OR IGNORE INTO image_tags(model_key, image_id, tag)
    SELECT new.model_key, new.image_id, lower(trim(value))
    FROM json_each(new.tags)
    WHERE type = 'text' AND trim(value) != '';
END;

CREATE TRIGGER IF NOT EXISTS image_tags_ad
AFTER DELETE ON image_captions
BEGIN
    DELETE FROM image_tags WHERE model_key = old.model_key AND image_id = old.image_id;
END;

CREATE TRIGGER IF NOT EXISTS image_tags_au
AFTER UPDATE OF tags, model_key ON image_captions
BEGIN
    DELETE FROM image_tags WHERE model_key = old.model_key AND image_id = old.image_id;
    INSERT OR IGNORE INTO image_tags(model_key, image_id, tag)
    SELECT new.model_key, new.image_id, lower(trim(value))
    FROM json_each(new.tags)
    WHERE type = 'text' AND trim(value) != '';
END;

CREATE VIRTUAL TABLE IF NOT EXISTS image_captions_fts
USING fts5(caption, tags, tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS image_captions_fts_ai
AFTER INSERT ON image_captions
WHEN new.model_key = (SELECT model_key FROM caption_fts_model WHERE id = 1)
BEGIN
    DELETE FROM image_captions_fts WHERE rowid = new.image_id;
    INSERT INTO image_captions_fts(rowid, caption, tags)
    VALUES (new.image_id, new.caption, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS image_captions_fts_ad
AFTER DELETE ON image_captions
WHEN old.model_key = (SELECT model_key FROM caption_fts_model WHERE id = 1)
BEGIN
    DELETE FROM image_captions_fts WHERE rowid = old.image_id;
END;

CREATE TRIGGER IF NOT EXISTS image_captions_fts_au
AFTER UPDATE OF caption, tags, model_key ON image_captions
BEGIN
    DELETE FROM image_captions_fts
    WHERE rowid IN (old.image_id, new.image_id)
    AND (
        old.model_key = (SELECT model_key FROM caption_fts_model WHERE id = 1)
        OR new.model_key = (SELECT model_key FROM caption_fts_model WHERE id = 1)
    );
    INSERT INTO image_captions_fts(rowid, caption, tags)
    SELECT new.image_id, new.caption, new.tags
    WHERE new.model_key = (SELECT model_key FROM caption_fts_model WHERE id = 1);
END;

CREATE TABLE IF NOT EXISTS caption_scan_images (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    model_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'done', 'error')),
    last_error TEXT NOT NULL DEFAULT '',
    scanned_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (image_id, model_key)
);

CREATE INDEX IF NOT EXISTS idx_caption_scan_images_status
ON caption_scan_images(model_key, status, scanned_at);

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

CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    destination_mode TEXT NOT NULL DEFAULT 'date_shoot',
    destination_root TEXT NOT NULL DEFAULT '',
    destination_path TEXT NOT NULL DEFAULT '',
    source_id INTEGER DEFAULT NULL REFERENCES catalog_sources(id),
    preserve_structure INTEGER NOT NULL DEFAULT 0,
    total_files INTEGER NOT NULL DEFAULT 0,
    imported_files INTEGER NOT NULL DEFAULT 0,
    skipped_files INTEGER NOT NULL DEFAULT 0,
    collision_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    completed_at REAL DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS import_batch_images (
    batch_id INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    filepath TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    imported_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (batch_id, image_id)
);

CREATE INDEX IF NOT EXISTS idx_import_batches_created
ON import_batches(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_import_batch_images_image
ON import_batch_images(image_id, batch_id);

CREATE TABLE IF NOT EXISTS stacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('burst','variant','crosssource','version','manual')),
    representative_image_id INTEGER NOT NULL REFERENCES images(id),
    auto INTEGER NOT NULL DEFAULT 1,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS stack_members (
    stack_id INTEGER NOT NULL REFERENCES stacks(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL UNIQUE REFERENCES images(id),
    score REAL,
    added_at REAL,
    PRIMARY KEY(stack_id, image_id)
);

CREATE INDEX IF NOT EXISTS idx_stacks_representative_image_id
ON stacks(representative_image_id);
CREATE INDEX IF NOT EXISTS idx_stacks_updated_at
ON stacks(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_stacks_kind
ON stacks(kind);
CREATE INDEX IF NOT EXISTS idx_stack_members_stack_id
ON stack_members(stack_id);

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'private',
    status TEXT NOT NULL DEFAULT 'draft',
    query TEXT DEFAULT NULL,
    cover_image_id INTEGER REFERENCES images(id) DEFAULT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS collection_images (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (collection_id, image_id)
);

CREATE TABLE IF NOT EXISTS collection_links (
    parent_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    child_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (parent_id, child_id)
);

CREATE TABLE IF NOT EXISTS published_nodes (
    id INTEGER PRIMARY KEY,
    area TEXT NOT NULL CHECK(area IN ('website', 'private')),
    parent_id INTEGER DEFAULT NULL REFERENCES published_nodes(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    source_collection_id INTEGER DEFAULT NULL REFERENCES collections(id) ON DELETE SET NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS published_node_images (
    node_id INTEGER NOT NULL REFERENCES published_nodes(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (node_id, image_id)
);

CREATE TABLE IF NOT EXISTS collection_shares (
    id INTEGER PRIMARY KEY,
    collection_id INTEGER DEFAULT NULL REFERENCES collections(id),
    published_node_id INTEGER DEFAULT NULL REFERENCES published_nodes(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    expires_at REAL DEFAULT NULL,
    revoked_at REAL DEFAULT NULL,
    password_hash TEXT DEFAULT NULL,
    view_count INTEGER NOT NULL DEFAULT 0,
    first_viewed_at REAL DEFAULT NULL,
    last_viewed_at REAL DEFAULT NULL,
    CHECK ((collection_id IS NOT NULL) != (published_node_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS share_images (
    share_id INTEGER NOT NULL REFERENCES collection_shares(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (share_id, image_id)
);

CREATE TABLE IF NOT EXISTS share_favorites (
    id INTEGER PRIMARY KEY,
    share_id INTEGER NOT NULL REFERENCES collection_shares(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL,
    client_name TEXT NULL,
    created_at REAL NOT NULL,
    UNIQUE(share_id, image_id)
);

CREATE TABLE IF NOT EXISTS collection_publishes (
    id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL UNIQUE REFERENCES collections(id),
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    published_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    image_count INTEGER NOT NULL DEFAULT 0,
    bundle_bytes INTEGER NOT NULL DEFAULT 0,
    last_commit TEXT DEFAULT NULL,
    hook_exit_code INTEGER DEFAULT NULL,
    hook_output TEXT NOT NULL DEFAULT '',
    hook_ran_at REAL DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_collections_updated
ON collections(updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_uuid
ON collections(uuid);
CREATE INDEX IF NOT EXISTS idx_collection_images_image
ON collection_images(image_id, collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_images_position
ON collection_images(collection_id, position, added_at);
CREATE INDEX IF NOT EXISTS idx_collection_links_child
ON collection_links(child_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_collection_links_parent_position
ON collection_links(parent_id, position, added_at, child_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_published_nodes_root_slug
ON published_nodes(area, slug) WHERE parent_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_published_nodes_child_slug
ON published_nodes(area, parent_id, slug) WHERE parent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_published_nodes_tree
ON published_nodes(area, parent_id, position, id);
CREATE INDEX IF NOT EXISTS idx_published_nodes_source
ON published_nodes(source_collection_id);
CREATE INDEX IF NOT EXISTS idx_published_node_images_position
ON published_node_images(node_id, position, added_at, image_id);
CREATE INDEX IF NOT EXISTS idx_published_node_images_image
ON published_node_images(image_id, node_id);
CREATE INDEX IF NOT EXISTS idx_collection_shares_active
ON collection_shares(collection_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_collection_shares_published_active
ON collection_shares(published_node_id, revoked_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_collection_shares_one_active_published
ON collection_shares(published_node_id)
WHERE revoked_at IS NULL AND published_node_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_share_images_image
ON share_images(image_id, share_id);
CREATE INDEX IF NOT EXISTS idx_share_images_position
ON share_images(share_id, position, added_at);
CREATE INDEX IF NOT EXISTS idx_share_favorites_share
ON share_favorites(share_id);
CREATE INDEX IF NOT EXISTS idx_collection_publishes_updated
ON collection_publishes(updated_at DESC);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unknown',
    representative_face_id INTEGER DEFAULT NULL,
    merged_into_person_id INTEGER DEFAULT NULL REFERENCES people(id),
    photo_count INTEGER NOT NULL DEFAULT 0,
    face_count INTEGER NOT NULL DEFAULT 0,
    best_quality REAL NOT NULL DEFAULT 0,
    latest_face_at REAL NOT NULL DEFAULT 0,
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
-- v24: original-file integrity checksums (bit-rot audit). Additive only.
CREATE TABLE IF NOT EXISTS image_checksums (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    checked_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_image_checksums_checked
ON image_checksums(checked_at);
"""

SCHEMA += PEOPLE_QUERY_SCHEMA

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
    ("date_source", "TEXT DEFAULT NULL"),
    ("camera_make", "TEXT DEFAULT NULL"),
    ("camera_model", "TEXT DEFAULT NULL"),
    ("lens", "TEXT DEFAULT NULL"),
    ("file_ext", "TEXT DEFAULT NULL"),
    ("file_size", "INTEGER DEFAULT NULL"),
    ("file_modified_at", "REAL DEFAULT NULL"),
    ("content_hash", "TEXT DEFAULT NULL"),
    ("row_version", "INTEGER NOT NULL DEFAULT 0"),
    ("hub_image_id", "INTEGER DEFAULT NULL"),
    ("hub_remote", "INTEGER NOT NULL DEFAULT 0"),
    ("trash_pending_hub", "INTEGER NOT NULL DEFAULT 0"),
    ("width", "INTEGER DEFAULT NULL"),
    ("height", "INTEGER DEFAULT NULL"),
    ("metadata_scanned_at", "REAL DEFAULT NULL"),
    ("latitude", "REAL DEFAULT NULL"),
    ("longitude", "REAL DEFAULT NULL"),
    ("location_source", "TEXT DEFAULT NULL"),
    ("metadata_version", "INTEGER DEFAULT NULL"),
    ("missing_at", "REAL DEFAULT NULL"),
    ("trashed_at", "REAL DEFAULT NULL"),
    ("trash_path", "TEXT DEFAULT NULL"),
    ("vc_of", "INTEGER REFERENCES images(id) ON DELETE CASCADE"),
)

PEOPLE_COMPAT_COLUMNS = (
    ("photo_count", "INTEGER NOT NULL DEFAULT 0"),
    ("face_count", "INTEGER NOT NULL DEFAULT 0"),
    ("best_quality", "REAL NOT NULL DEFAULT 0"),
    ("latest_face_at", "REAL NOT NULL DEFAULT 0"),
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

COLLECTION_COMPAT_COLUMNS = (
    ("query", "TEXT DEFAULT NULL"),
    ("uuid", "TEXT"),
)

COLLECTION_SHARE_COMPAT_COLUMNS = (
    ("published_node_id", "INTEGER DEFAULT NULL REFERENCES published_nodes(id) ON DELETE CASCADE"),
    ("password_hash", "TEXT DEFAULT NULL"),
    ("view_count", "INTEGER NOT NULL DEFAULT 0"),
    ("first_viewed_at", "REAL DEFAULT NULL"),
    ("last_viewed_at", "REAL DEFAULT NULL"),
)

COLLECTION_PUBLISH_COMPAT_COLUMNS = (
    ("collection_id", "INTEGER REFERENCES collections(id)"),
    ("slug", "TEXT DEFAULT ''"),
    ("title", "TEXT DEFAULT ''"),
    ("published_at", "REAL DEFAULT 0"),
    ("updated_at", "REAL DEFAULT 0"),
    ("image_count", "INTEGER NOT NULL DEFAULT 0"),
    ("bundle_bytes", "INTEGER NOT NULL DEFAULT 0"),
    ("last_commit", "TEXT DEFAULT NULL"),
    ("hook_exit_code", "INTEGER DEFAULT NULL"),
    ("hook_output", "TEXT NOT NULL DEFAULT ''"),
    ("hook_ran_at", "REAL DEFAULT NULL"),
)

IMAGE_CAPTION_COMPAT_COLUMNS = (
    ("user_edited", "INTEGER NOT NULL DEFAULT 0"),
)

COMPAT_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_images_flag ON images(flag)",
    (
        "CREATE INDEX IF NOT EXISTS idx_images_content_hash "
        "ON images(content_hash) WHERE content_hash IS NOT NULL"
    ),
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
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_filepath_elo "
        "ON images(filepath, elo DESC, id) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL AND vc_of IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_filepath_date_taken "
        "ON images(filepath, (date_taken IS NULL), date_taken DESC, id) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL AND vc_of IS NULL"
    ),
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
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_gps_markers "
        "ON images(latitude, longitude, source_id, filename) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL AND vc_of IS NULL "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL"
    ),
    "CREATE INDEX IF NOT EXISTS idx_images_rating_signal_cover ON images(comparisons, propagated_updates, elo)",
    "CREATE INDEX IF NOT EXISTS idx_image_tags_model_tag_image ON image_tags(model_key, tag, image_id)",
    "CREATE INDEX IF NOT EXISTS idx_image_tags_image_model ON image_tags(image_id, model_key)",
    (
        "CREATE INDEX IF NOT EXISTS idx_images_source_missing_rating_signal "
        "ON images(source_id, missing_at, comparisons, propagated_updates, elo)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_date_taken "
        "ON images(date_taken DESC) WHERE status IN ('kept', 'maybe')"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_images_active_month_source "
        "ON images(substr(date_taken, 1, 7), source_id) "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL AND vc_of IS NULL"
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
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_images_hub_image_id ON images(hub_image_id) WHERE hub_image_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_images_hub_remote ON images(hub_remote, hub_image_id)",
    "CREATE INDEX IF NOT EXISTS idx_images_trash_pending_hub ON images(trash_pending_hub) WHERE trash_pending_hub = 1",
    "CREATE INDEX IF NOT EXISTS idx_cache_entries_root_image_size ON cache_entries(cache_root, image_id, size)",
    (
        "CREATE INDEX IF NOT EXISTS idx_people_unknown_review "
        "ON people(photo_count DESC, face_count DESC, best_quality DESC, latest_face_at DESC, id ASC) "
        "WHERE merged_into_person_id IS NULL AND status != 'ignored' AND status != 'named' "
        "AND TRIM(name) = '' AND face_count > 0"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_people_named_review "
        "ON people(CASE WHEN TRIM(COALESCE(name, '')) = '' THEN printf('person %012d', id) "
        "ELSE LOWER(TRIM(COALESCE(name, ''))) END, photo_count DESC, id ASC) "
        "WHERE merged_into_person_id IS NULL AND status != 'ignored' AND face_count > 0 "
        "AND (status = 'named' OR TRIM(name) != '')"
    ),
)

REQUIRED_TABLES = {
    "catalog_sources",
    "images",
    "image_shoot_hints",
    "develop_settings",
    "develop_history",
    "develop_presets",
    "oplog",
    "oplog_family_state",
    "oplog_settings",
    "oplog_cursors",
    "devices",
    "images_metadata_fts",
    "comparisons",
    "embeddings",
    "embedding_models",
    "embeddings_by_model",
    "search_query_embeddings",
    "caption_fts_model",
    "image_captions",
    "image_tags",
    "image_captions_fts",
    "caption_scan_images",
    "cache_entries",
    "cache_image_presence",
    "cache_metadata",
    "import_batches",
    "import_batch_images",
    "stacks",
    "stack_members",
    "collections",
    "collection_images",
    "collection_links",
    "published_nodes",
    "published_node_images",
    "collection_shares",
    "share_images",
    "share_favorites",
    "collection_publishes",
    "people",
    "face_detections",
    "face_assignments",
    "person_image_membership",
    "people_merge_suggestions",
    "face_scan_images",
    "face_scan_models",
    "face_scan_backlog",
    "people_operational_metrics",
    "face_scan_status_counts",
    "image_checksums",
}

REQUIRED_COLUMNS = {
    "devices": {
        "id",
        "name",
        "platform",
        "token_hash",
        "created_at",
        "last_seen",
        "revoked_at",
    },
    "images": {
        "source_id",
        "content_hash",
        "row_version",
        "hub_image_id",
        "hub_remote",
        "trash_pending_hub",
        "orientation",
        "flag",
        "propagated_updates",
        "predicted_elo",
        "uncertainty",
        "aspect_ratio",
        "date_taken",
        "date_source",
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
        "location_source",
        "metadata_version",
        "missing_at",
        "trashed_at",
        "trash_path",
        "vc_of",
    },
    "people": {
        "photo_count",
        "face_count",
        "best_quality",
        "latest_face_at",
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
    "stacks": {"kind", "representative_image_id", "auto", "created_at", "updated_at"},
    "stack_members": {"stack_id", "image_id", "score", "added_at"},
    "collections": {"query", "uuid"},
    "collection_links": {"parent_id", "child_id", "position", "added_at"},
    "published_nodes": {
        "area",
        "parent_id",
        "slug",
        "title",
        "source_collection_id",
        "position",
        "created_at",
        "updated_at",
    },
    "published_node_images": {"node_id", "image_id", "position", "added_at"},
    "collection_shares": {
        "collection_id",
        "published_node_id",
        "password_hash",
        "view_count",
        "first_viewed_at",
        "last_viewed_at",
    },
    "collection_publishes": {
        "collection_id",
        "slug",
        "title",
        "published_at",
        "updated_at",
        "image_count",
        "bundle_bytes",
        "last_commit",
        "hook_exit_code",
        "hook_output",
        "hook_ran_at",
    },
    "image_captions": {"user_edited"},
    "image_checksums": {"image_id", "sha256", "bytes", "checked_at"},
}

REQUIRED_INDEXES = {
    "idx_develop_history_image",
    "idx_develop_presets_folder",
    "idx_oplog_origin_seq",
    "idx_oplog_content_family",
    "idx_devices_token_hash",
    "idx_devices_revoked_at",
    "idx_catalog_sources_active",
    "idx_images_missing_source_filepath_id",
    "idx_images_source_missing_id",
    "idx_images_source_missing_rating_signal",
    "idx_images_active_visible_orientation_elo",
    "idx_images_visible_comparisons_elo",
    "idx_images_missing_file_ext_source_size",
    "idx_images_active_camera_sort_desc",
    "idx_images_active_gps_markers",
    "idx_images_rating_signal_cover",
    "idx_embeddings_by_model_image_id",
    "idx_search_query_embeddings_used",
    "idx_image_captions_image",
    "idx_image_tags_model_tag_image",
    "idx_image_tags_image_model",
    "idx_caption_scan_images_status",
    "idx_cache_entries_root_size_bytes",
    "idx_cache_entries_root_size_accessed_id",
    "idx_cache_entries_root_image_size",
    "idx_import_batches_created",
    "idx_import_batch_images_image",
    "idx_stacks_kind",
    "idx_stack_members_stack_id",
    "idx_collections_updated",
    "idx_collections_uuid",
    "idx_collection_images_image",
    "idx_collection_images_position",
    "idx_collection_links_child",
    "idx_collection_links_parent_position",
    "idx_published_nodes_root_slug",
    "idx_published_nodes_child_slug",
    "idx_published_nodes_tree",
    "idx_published_nodes_source",
    "idx_published_node_images_position",
    "idx_published_node_images_image",
    "idx_collection_shares_active",
    "idx_collection_shares_published_active",
    "idx_collection_shares_one_active_published",
    "idx_share_images_image",
    "idx_share_images_position",
    "idx_share_favorites_share",
    "idx_collection_publishes_updated",
    "idx_people_status_seen",
    "idx_people_unknown_review",
    "idx_people_named_review",
    "idx_face_detections_image_model",
    "idx_face_detections_status_model",
    "idx_face_assignments_person",
    "idx_person_image_membership_image",
    "idx_people_merge_suggestions_pending",
    "idx_face_scan_images_status",
    "idx_face_scan_backlog_ready",
    "idx_face_scan_backlog_retry",
    "idx_image_checksums_checked",
    "idx_images_vc_of",
    "idx_images_row_version",
    "idx_images_hub_image_id",
    "idx_images_hub_remote",
    "idx_images_original_filepath",
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
    await _add_columns_if_missing(conn, "people", PEOPLE_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "comparisons", (("action_id", "TEXT DEFAULT NULL"),))
    await _add_columns_if_missing(conn, "collections", COLLECTION_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "collection_shares", COLLECTION_SHARE_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "collection_publishes", COLLECTION_PUBLISH_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "image_captions", IMAGE_CAPTION_COMPAT_COLUMNS)
    await migrate_stack_kind_for_versions(conn)
    await migrate_collection_shares_for_published_nodes(conn)
    await migrate_share_owner_cascades(conn)


async def migrate_stack_kind_for_versions(conn) -> None:
    """Rebuild legacy ``stacks`` CHECK constraints to admit version groups."""

    if not await table_exists(conn, "stacks"):
        return
    cursor = await conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'stacks'"
    )
    row = await cursor.fetchone()
    if row is None or "'version'" in str(row["sql"] or ""):
        return

    await conn.commit()
    await conn.execute("PRAGMA foreign_keys=OFF")
    try:
        await conn.executescript(
            """
            BEGIN;
            DROP TABLE IF EXISTS stacks_new;
            CREATE TABLE stacks_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('burst','variant','crosssource','version','manual')),
                representative_image_id INTEGER NOT NULL REFERENCES images(id),
                auto INTEGER NOT NULL DEFAULT 1,
                created_at REAL,
                updated_at REAL
            );
            INSERT INTO stacks_new (
                id, kind, representative_image_id, auto, created_at, updated_at
            )
            SELECT id, kind, representative_image_id, auto, created_at, updated_at
            FROM stacks;
            DROP TABLE stacks;
            ALTER TABLE stacks_new RENAME TO stacks;
            COMMIT;
            """
        )
    except Exception:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise
    finally:
        await conn.execute("PRAGMA foreign_keys=ON")


async def migrate_collection_shares_for_published_nodes(conn) -> None:
    """Relax the legacy collection-only share owner without losing share state."""

    if not await table_exists(conn, "collection_shares"):
        return
    cursor = await conn.execute("PRAGMA table_info(collection_shares)")
    columns = {row["name"]: row for row in await cursor.fetchall()}
    collection_id = columns.get("collection_id")
    if "published_node_id" in columns and collection_id is not None and not bool(collection_id["notnull"]):
        await _ensure_collection_share_indexes(conn)
        return

    await conn.commit()
    await _backup_before_v20_rebuild(conn)
    await conn.execute("PRAGMA foreign_keys=OFF")
    try:
        await conn.executescript(
            """
            BEGIN;
            DROP TABLE IF EXISTS collection_shares_new;
            CREATE TABLE collection_shares_new (
                id INTEGER PRIMARY KEY,
                collection_id INTEGER DEFAULT NULL REFERENCES collections(id),
                published_node_id INTEGER DEFAULT NULL REFERENCES published_nodes(id) ON DELETE CASCADE,
                token TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                expires_at REAL DEFAULT NULL,
                revoked_at REAL DEFAULT NULL,
                password_hash TEXT DEFAULT NULL,
                view_count INTEGER NOT NULL DEFAULT 0,
                first_viewed_at REAL DEFAULT NULL,
                last_viewed_at REAL DEFAULT NULL,
                CHECK ((collection_id IS NOT NULL) != (published_node_id IS NOT NULL))
            );
            INSERT INTO collection_shares_new (
                id, collection_id, published_node_id, token, created_at, expires_at,
                revoked_at, password_hash, view_count, first_viewed_at, last_viewed_at
            )
            SELECT
                id, collection_id, NULL, token, created_at, expires_at,
                revoked_at, password_hash, view_count, first_viewed_at, last_viewed_at
            FROM collection_shares;
            DROP TABLE collection_shares;
            ALTER TABLE collection_shares_new RENAME TO collection_shares;
            CREATE INDEX idx_collection_shares_active
            ON collection_shares(collection_id, revoked_at);
            CREATE INDEX idx_collection_shares_published_active
            ON collection_shares(published_node_id, revoked_at);
            CREATE UNIQUE INDEX idx_collection_shares_one_active_published
            ON collection_shares(published_node_id)
            WHERE revoked_at IS NULL AND published_node_id IS NOT NULL;
            COMMIT;
            """
        )
    except Exception:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise
    finally:
        await conn.execute("PRAGMA foreign_keys=ON")


async def migrate_share_owner_cascades(conn) -> None:
    """Make deleting a share cascade through both snapshot child tables."""

    rebuild_images = await table_exists(conn, "share_images") and not await _has_cascade_fk(
        conn,
        "share_images",
        from_column="share_id",
        target_table="collection_shares",
    )
    rebuild_favorites = await table_exists(conn, "share_favorites") and not await _has_cascade_fk(
        conn,
        "share_favorites",
        from_column="share_id",
        target_table="collection_shares",
    )
    if not rebuild_images and not rebuild_favorites:
        return

    statements = ["BEGIN;"]
    if rebuild_images:
        statements.extend(
            [
                "DROP TABLE IF EXISTS share_images_new;",
                """
                CREATE TABLE share_images_new (
                    share_id INTEGER NOT NULL REFERENCES collection_shares(id) ON DELETE CASCADE,
                    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0,
                    added_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                    PRIMARY KEY (share_id, image_id)
                );
                """,
                """
                INSERT INTO share_images_new (share_id, image_id, position, added_at)
                SELECT share_id, image_id, position, added_at FROM share_images;
                """,
                "DROP TABLE share_images;",
                "ALTER TABLE share_images_new RENAME TO share_images;",
                "CREATE INDEX idx_share_images_image ON share_images(image_id, share_id);",
                """
                CREATE INDEX idx_share_images_position
                ON share_images(share_id, position, added_at);
                """,
            ]
        )
    if rebuild_favorites:
        statements.extend(
            [
                "DROP TABLE IF EXISTS share_favorites_new;",
                """
                CREATE TABLE share_favorites_new (
                    id INTEGER PRIMARY KEY,
                    share_id INTEGER NOT NULL REFERENCES collection_shares(id) ON DELETE CASCADE,
                    image_id INTEGER NOT NULL,
                    client_name TEXT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(share_id, image_id)
                );
                """,
                """
                INSERT INTO share_favorites_new (id, share_id, image_id, client_name, created_at)
                SELECT id, share_id, image_id, client_name, created_at FROM share_favorites;
                """,
                "DROP TABLE share_favorites;",
                "ALTER TABLE share_favorites_new RENAME TO share_favorites;",
                "CREATE INDEX idx_share_favorites_share ON share_favorites(share_id);",
            ]
        )
    statements.append("COMMIT;")

    await conn.commit()
    await conn.execute("PRAGMA foreign_keys=OFF")
    try:
        await conn.executescript("\n".join(statements))
    except Exception:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise
    finally:
        await conn.execute("PRAGMA foreign_keys=ON")


async def _has_cascade_fk(
    conn,
    table: str,
    *,
    from_column: str,
    target_table: str,
) -> bool:
    cursor = await conn.execute(f"PRAGMA foreign_key_list({table})")
    return any(
        row["from"] == from_column
        and row["table"] == target_table
        and str(row["on_delete"] or "").upper() == "CASCADE"
        for row in await cursor.fetchall()
    )


async def _ensure_collection_share_indexes(conn) -> None:
    now_sql = "strftime('%s', 'now')"
    await conn.execute(
        f"""
        UPDATE collection_shares
        SET revoked_at = {now_sql}
        WHERE published_node_id IS NOT NULL
          AND revoked_at IS NULL
          AND id NOT IN (
              SELECT MAX(id)
              FROM collection_shares
              WHERE published_node_id IS NOT NULL AND revoked_at IS NULL
              GROUP BY published_node_id
          )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_shares_active "
        "ON collection_shares(collection_id, revoked_at)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_shares_published_active "
        "ON collection_shares(published_node_id, revoked_at)"
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_collection_shares_one_active_published "
        "ON collection_shares(published_node_id) "
        "WHERE revoked_at IS NULL AND published_node_id IS NOT NULL"
    )


async def _backup_before_v20_rebuild(conn) -> None:
    cursor = await conn.execute("PRAGMA database_list")
    main = next((row for row in await cursor.fetchall() if row["name"] == "main"), None)
    db_path = str(main["file"] or "") if main is not None else ""
    if not db_path or db_path == ":memory:":
        return
    backup_path = f"{db_path}.pre-v20.bak"
    if os.path.exists(backup_path):
        return
    try:
        await conn.execute("PRAGMA wal_checkpoint(FULL)")
    except Exception:
        pass
    await asyncio.to_thread(shutil.copy2, db_path, backup_path)


async def ensure_compatibility_columns(conn) -> None:
    await _add_columns_if_missing(conn, "images", IMAGE_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "people", PEOPLE_COMPAT_COLUMNS)
    await _add_columns_if_missing(
        conn,
        "cache_metadata",
        (("replace_stale_thumbnails", "INTEGER NOT NULL DEFAULT 0"),),
    )
    await _add_columns_if_missing(conn, "comparisons", (("action_id", "TEXT DEFAULT NULL"),))
    await _add_columns_if_missing(conn, "catalog_sources", CATALOG_SOURCE_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "collections", COLLECTION_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "collection_shares", COLLECTION_SHARE_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "collection_publishes", COLLECTION_PUBLISH_COMPAT_COLUMNS)
    await _add_columns_if_missing(conn, "image_captions", IMAGE_CAPTION_COMPAT_COLUMNS)


async def ensure_compatibility_indexes(conn) -> None:
    for sql in COMPAT_INDEX_SQL:
        await conn.execute(sql)


async def ensure_collection_uuids(conn) -> None:
    """Give every legacy user collection a stable cross-device identity."""

    rows = await (await conn.execute(
        "SELECT id FROM collections WHERE uuid IS NULL OR trim(uuid) = ''"
    )).fetchall()
    for row in rows:
        await conn.execute(
            "UPDATE collections SET uuid = ? WHERE id = ?",
            (str(uuid.uuid4()), int(row["id"])),
        )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_uuid ON collections(uuid)"
    )


async def ensure_catalog_export_row_versions(conn) -> None:
    await conn.execute("UPDATE images SET row_version = id WHERE row_version = 0")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_images_row_version ON images(row_version)")
    await conn.executescript("""
    CREATE TRIGGER IF NOT EXISTS images_row_version_ai AFTER INSERT ON images BEGIN
        UPDATE images SET row_version = (
            SELECT COALESCE(MAX(row_version), 0) + 1 FROM images WHERE id != NEW.id
        ) WHERE id = NEW.id;
    END;
    CREATE TRIGGER IF NOT EXISTS images_row_version_au AFTER UPDATE ON images
    WHEN NEW.row_version = OLD.row_version BEGIN
        UPDATE images SET row_version = (
            SELECT COALESCE(MAX(row_version), 0) + 1 FROM images
        ) WHERE id = NEW.id;
    END;
    """)


async def backfill_legacy_aspect_ratios(conn) -> None:
    await conn.execute(
        "UPDATE images SET aspect_ratio = 1.5 WHERE orientation = 'landscape' AND aspect_ratio IS NULL"
    )
    await conn.execute(
        "UPDATE images SET aspect_ratio = 0.6667 WHERE orientation = 'portrait' AND aspect_ratio IS NULL"
    )


async def backfill_image_date_sources(conn) -> int:
    """Populate date_source and infer missing dates for legacy rows."""

    changed = 0
    cursor = await conn.execute(
        "UPDATE images SET date_source = 'exif' "
        "WHERE date_taken IS NOT NULL AND date_taken != '' "
        "AND (date_source IS NULL OR date_source = '')"
    )
    changed += max(int(cursor.rowcount or 0), 0)

    cursor = await conn.execute(
        "SELECT i.id, i.filename, i.filepath, i.file_modified_at, s.path AS source_root "
        "FROM images i "
        "LEFT JOIN catalog_sources s ON s.id = i.source_id "
        "WHERE i.date_taken IS NULL OR i.date_taken = ''"
    )
    updates = []
    for row in await cursor.fetchall():
        inferred = infer_image_date(
            filename=row["filename"] or "",
            filepath=row["filepath"] or "",
            file_modified_at=row["file_modified_at"],
            source_root=row["source_root"],
        )
        if inferred is None:
            continue
        updates.append((inferred.date_taken, inferred.date_source, row["id"]))
    if updates:
        await conn.executemany(
            "UPDATE images SET date_taken = ?, date_source = ? "
            "WHERE id = ? AND (date_taken IS NULL OR date_taken = '')",
            updates,
        )
        changed += len(updates)
    return changed


async def backfill_share_images(conn) -> None:
    await conn.execute(
        """
        INSERT OR IGNORE INTO share_images (share_id, image_id, position, added_at)
        SELECT
            s.id,
            ci.image_id,
            ci.position,
            COALESCE(ci.added_at, s.created_at)
        FROM collection_shares s
        JOIN collection_images ci ON ci.collection_id = s.collection_id
        WHERE NOT EXISTS (
            SELECT 1 FROM share_images existing WHERE existing.share_id = s.id
        )
        ORDER BY s.id, ci.position ASC, ci.added_at ASC, ci.image_id ASC
        """
    )


async def backfill_image_tags(conn) -> None:
    await conn.execute(
        """
        INSERT OR IGNORE INTO image_tags(model_key, image_id, tag)
        SELECT c.model_key, c.image_id, lower(trim(j.value))
        FROM image_captions c
        JOIN json_each(c.tags) j
        WHERE j.type = 'text' AND trim(j.value) != ''
        """
    )


async def backfill_people_query_aggregates(conn) -> None:
    await conn.execute(
        "UPDATE people SET "
        "photo_count = (SELECT COUNT(*) FROM person_image_membership pim WHERE pim.person_id = people.id), "
        "face_count = COALESCE((SELECT SUM(face_count) FROM person_image_membership pim WHERE pim.person_id = people.id), 0), "
        "best_quality = COALESCE((SELECT MAX(best_quality) FROM person_image_membership pim WHERE pim.person_id = people.id), 0), "
        "latest_face_at = COALESCE((SELECT MAX(latest_face_at) FROM person_image_membership pim WHERE pim.person_id = people.id), 0)"
    )
    await conn.execute(
        "UPDATE people_operational_metrics SET value = ("
        "SELECT COUNT(*) FROM face_detections WHERE ignored = 0"
        ") WHERE metric = 'detected_faces'"
    )
    await conn.execute("DELETE FROM face_scan_status_counts")
    await conn.execute(
        "INSERT INTO face_scan_status_counts(status, value) "
        "SELECT status, COUNT(*) FROM face_scan_images GROUP BY status"
    )


async def backfill_cache_image_presence(conn) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO cache_image_presence(cache_root, image_id) "
        "SELECT cache_root, image_id FROM cache_entries GROUP BY cache_root, image_id"
    )
    await conn.execute(
        "INSERT OR IGNORE INTO face_scan_models(model_id) "
        "SELECT DISTINCT model_id FROM face_scan_images WHERE model_id != ''"
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
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    previous_schema_version = int(row[0] if row is not None else 0)
    if db_exists:
        await prepare_existing_database_for_schema(conn)
    await _executescript_in_transaction(conn, SCHEMA)
    from features.develop.virtual_copies import ensure_virtual_copies
    await ensure_virtual_copies(conn)
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
        await ensure_collection_uuids(conn)
        await ensure_catalog_export_row_versions(conn)
        from features.develop.presets import ensure_develop_presets
        await ensure_develop_presets(conn)
        # PATCH: quality lane — additive image_quality table (CREATE IF NOT EXISTS; no version bump)
        from features.quality.scorer import ensure_image_quality
        await ensure_image_quality(conn)
        await backfill_share_images(conn)
        await backfill_image_tags(conn)
        await backfill_legacy_aspect_ratios(conn)
        if previous_schema_version < 29:
            await backfill_people_query_aggregates(conn)
            await backfill_cache_image_presence(conn)
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
    # source membership, but trash still uses status as an explicit exclusion.
    cursor = await conn.execute(
        "UPDATE images SET status = 'kept' "
        "WHERE status IS NULL OR status NOT IN ('kept', 'maybe', 'trashed')"
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
        root = safe_commonpath(dirs) if dirs else os.path.expanduser("~/Pictures")
        if root is None:
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
        "SELECT 1 FROM images WHERE COALESCE(status, '') NOT IN ('kept', 'maybe', 'trashed') LIMIT 1",
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
