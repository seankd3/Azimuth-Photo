"""People, face assignment, and People-filter SQL helpers."""

import time as _time

from data import connection


def parse_people_ids(value) -> tuple[int, ...]:
    if isinstance(value, (list, tuple, set)):
        raw_parts = value
    else:
        raw_parts = str(value or "").replace(";", ",").split(",")
    ids: list[int] = []
    seen: set[int] = set()
    for part in raw_parts:
        token = str(part or "").strip()
        if not token.isdigit():
            continue
        person_id = int(token)
        if person_id <= 0 or person_id in seen:
            continue
        seen.add(person_id)
        ids.append(person_id)
    return tuple(ids)


def _face_embedding_blob(vector) -> bytes | None:
    try:
        import numpy as np

        arr = np.asarray(vector, dtype=np.float32).reshape(-1)
        if arr.size <= 0:
            return None
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr = arr / norm
        return arr.astype(np.float32).tobytes()
    except Exception:
        return None


def _face_embedding_vector(blob):
    if not blob:
        return None
    try:
        import numpy as np

        arr = np.frombuffer(blob, dtype=np.float32).astype(np.float32)
        if arr.size <= 0:
            return None
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr = arr / norm
        return arr
    except Exception:
        return None


async def canonical_person_ids_on_conn(conn, person_ids: tuple[int, ...]) -> tuple[int, ...]:
    if not person_ids:
        return ()
    placeholders = ",".join("?" for _ in person_ids)
    cursor = await conn.execute(
        "SELECT id, merged_into_person_id FROM people "
        f"WHERE id IN ({placeholders})",
        list(person_ids),
    )
    mapping = {
        int(row["id"]): int(row["merged_into_person_id"] or row["id"])
        for row in await cursor.fetchall()
    }
    canonical: list[int] = []
    seen: set[int] = set()
    for person_id in person_ids:
        mapped = mapping.get(int(person_id), int(person_id))
        if mapped <= 0 or mapped in seen:
            continue
        seen.add(mapped)
        canonical.append(mapped)
    return tuple(canonical)


async def create_person_on_conn(conn, *, status: str = "unknown", name: str = "") -> int:
    now = _time.time()
    clean_status = status if status in {"unknown", "named", "ignored"} else "unknown"
    cursor = await conn.execute(
        "INSERT INTO people (name, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (str(name or "").strip(), clean_status, now, now),
    )
    return int(cursor.lastrowid)


async def refresh_people_membership_on_conn(
    conn,
    person_ids: tuple[int, ...] | None = None,
) -> None:
    params: list = []
    person_filter = ""
    if person_ids is not None:
        person_ids = tuple(sorted({int(person_id) for person_id in person_ids if int(person_id) > 0}))
        if not person_ids:
            return
        placeholders = ",".join("?" for _ in person_ids)
        person_filter = f" AND fa.person_id IN ({placeholders})"
        params.extend(person_ids)
        await conn.execute(
            f"DELETE FROM person_image_membership WHERE person_id IN ({placeholders})",
            list(person_ids),
        )
    else:
        await conn.execute("DELETE FROM person_image_membership")

    await conn.execute(
        "INSERT OR REPLACE INTO person_image_membership "
        "(person_id, image_id, face_count, best_quality, latest_face_at) "
        "SELECT fa.person_id, fd.image_id, COUNT(*) AS face_count, "
        "MAX(fd.quality) AS best_quality, MAX(fd.updated_at) AS latest_face_at "
        "FROM face_assignments fa "
        "JOIN face_detections fd ON fd.id = fa.face_id "
        "JOIN people p ON p.id = fa.person_id "
        "WHERE fa.active = 1 AND fd.ignored = 0 "
        "AND p.status != 'ignored' AND p.merged_into_person_id IS NULL "
        f"{person_filter} "
        "GROUP BY fa.person_id, fd.image_id",
        params,
    )

    if person_ids is None:
        target_filter = ""
        face_filter = ""
        face_params: list = []
        target_params: list = []
    else:
        placeholders = ",".join("?" for _ in person_ids)
        target_filter = f" WHERE id IN ({placeholders})"
        face_filter = f" AND fa.person_id IN ({placeholders})"
        face_params = list(person_ids)
        target_params = list(person_ids)
    await conn.execute(
        "WITH ranked_faces AS ("
        "  SELECT fa.person_id, fd.id AS face_id, "
        "  ROW_NUMBER() OVER ("
        "    PARTITION BY fa.person_id "
        "    ORDER BY fd.quality DESC, fd.confidence DESC, fd.updated_at DESC"
        "  ) AS rank "
        "  FROM face_assignments fa "
        "  JOIN face_detections fd ON fd.id = fa.face_id "
        "  WHERE fa.active = 1 AND fd.ignored = 0"
        f"{face_filter}"
        ") "
        "UPDATE people SET representative_face_id = ("
        "  SELECT face_id FROM ranked_faces "
        "  WHERE ranked_faces.person_id = people.id AND rank = 1"
        "), updated_at = ?"
        f"{target_filter}",
        [*face_params, _time.time(), *target_params],
    )


async def refresh_people_membership(db_path: str, person_ids: tuple[int, ...] | None = None) -> None:
    conn = await connection.open_async(db_path)
    try:
        await refresh_people_membership_on_conn(conn, person_ids)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_people_image_id_filter(db_path: str, person_ids: tuple[int, ...] | str) -> set[int] | None:
    parsed = parse_people_ids(person_ids) if isinstance(person_ids, str) else tuple(person_ids or ())
    if not parsed:
        return None
    conn = await connection.open_async(db_path)
    try:
        canonical = await canonical_person_ids_on_conn(conn, parsed)
        if not canonical:
            return set()
        placeholders = ",".join("?" for _ in canonical)
        cursor = await conn.execute(
            "SELECT image_id FROM person_image_membership "
            f"WHERE person_id IN ({placeholders}) "
            "GROUP BY image_id HAVING COUNT(DISTINCT person_id) = ?",
            list(canonical) + [len(canonical)],
        )
        return {int(row["image_id"]) for row in await cursor.fetchall()}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_images_needing_faces(
    db_path: str,
    *,
    model_id: str,
    cache_root: str,
    limit: int = 16,
    retry_after_seconds: int = 86400,
) -> list[dict]:
    conn = await connection.open_async(db_path)
    try:
        cache_sizes = ("lg", "md", "sm")
        placeholders = ",".join("?" for _ in cache_sizes)
        cursor = await conn.execute(
            "SELECT i.id, i.filename, c.path AS cache_path, c.size AS cache_size, "
            "fsi.status AS scan_status, fsi.scanned_at "
            "FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "JOIN cache_entries c ON c.image_id = i.id "
            "LEFT JOIN face_scan_images fsi ON fsi.image_id = i.id AND fsi.model_id = ? "
            "WHERE s.included = 1 AND i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL "
            "AND c.cache_root = ? "
            f"AND c.size IN ({placeholders}) "
            "AND (fsi.image_id IS NULL OR (fsi.status = 'error' AND fsi.scanned_at < ?)) "
            "ORDER BY i.id ASC, CASE c.size WHEN 'lg' THEN 0 WHEN 'md' THEN 1 ELSE 2 END "
            "LIMIT ?",
            [model_id, cache_root, *cache_sizes, _time.time() - int(retry_after_seconds), max(1, int(limit) * 3)],
        )
        rows = [dict(row) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)
    selected: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        image_id = int(row.get("id") or 0)
        if image_id <= 0 or image_id in seen:
            continue
        seen.add(image_id)
        selected.append(row)
        if len(selected) >= max(1, int(limit)):
            break
    return selected


async def count_images_needing_faces(
    db_path: str,
    *,
    model_id: str,
    cache_root: str,
    retry_after_seconds: int = 86400,
) -> int:
    conn = await connection.open_async(db_path)
    try:
        return await count_images_needing_faces_on_conn(
            conn,
            model_id=model_id,
            cache_root=cache_root,
            retry_after_seconds=retry_after_seconds,
        )
    finally:
        await connection.close_async(conn, db_path=db_path)


async def count_images_needing_faces_on_conn(
    conn,
    *,
    model_id: str,
    cache_root: str,
    retry_after_seconds: int = 86400,
) -> int:
    cursor = await conn.execute(
        "WITH ready AS ("
        "SELECT image_id FROM face_scan_backlog INDEXED BY idx_face_scan_backlog_ready "
        "WHERE cache_root = ? AND model_id = ? AND error_scanned_at IS NULL "
        "UNION ALL "
        "SELECT image_id FROM face_scan_backlog INDEXED BY idx_face_scan_backlog_retry "
        "WHERE cache_root = ? AND model_id = ? AND error_scanned_at < ?"
        ") "
        "SELECT COUNT(*) AS count FROM ready "
        "JOIN images i ON i.id = ready.image_id "
        "JOIN catalog_sources s ON s.id = i.source_id "
        "WHERE s.included = 1 AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
        [
            cache_root,
            model_id,
            cache_root,
            model_id,
            _time.time() - int(retry_after_seconds),
        ],
    )
    row = await cursor.fetchone()
    return int(row["count"] or 0) if row else 0

async def store_face_scan_result(
    db_path: str,
    *,
    image_id: int,
    model_id: str,
    cache_path: str = "",
    faces: list[dict] | None = None,
    status: str = "scanned",
    error: str = "",
) -> dict:
    safe_faces = faces or []
    conn = await connection.open_async(db_path)
    try:
        await conn.execute(
            "INSERT OR IGNORE INTO face_scan_models(model_id) VALUES (?)",
            (model_id,),
        )
        cursor = await conn.execute(
            "SELECT DISTINCT fa.person_id FROM face_detections fd "
            "JOIN face_assignments fa ON fa.face_id = fd.id "
            "WHERE fd.image_id = ? AND fd.embedding_model = ?",
            (image_id, model_id),
        )
        affected_people = {int(row["person_id"]) for row in await cursor.fetchall()}
        await conn.execute(
            "DELETE FROM face_assignments WHERE face_id IN ("
            "SELECT id FROM face_detections WHERE image_id = ? AND embedding_model = ?"
            ")",
            (image_id, model_id),
        )
        await conn.execute(
            "DELETE FROM face_detections WHERE image_id = ? AND embedding_model = ?",
            (image_id, model_id),
        )
        now = _time.time()
        inserted_face_ids: list[int] = []
        for index, face in enumerate(safe_faces):
            bbox = face.get("bbox") or {}
            embedding_blob = _face_embedding_blob(face.get("embedding"))
            confidence = float(face.get("confidence") or 0.0)
            quality = float(face.get("quality") or confidence)
            cursor = await conn.execute(
                "INSERT INTO face_detections "
                "(image_id, detection_key, bbox_x, bbox_y, bbox_w, bbox_h, confidence, "
                "quality, embedding, embedding_model, cache_path, ignored, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    int(image_id),
                    f"{model_id}:{image_id}:{index}",
                    float(bbox.get("x") or 0.0),
                    float(bbox.get("y") or 0.0),
                    float(bbox.get("w") or 0.0),
                    float(bbox.get("h") or 0.0),
                    confidence,
                    quality,
                    embedding_blob,
                    model_id,
                    cache_path or "",
                    now,
                    now,
                ),
            )
            inserted_face_ids.append(int(cursor.lastrowid))
        await conn.execute(
            "INSERT INTO face_scan_images "
            "(image_id, model_id, status, face_count, cache_path, last_error, scanned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(image_id, model_id) DO UPDATE SET "
            "status = excluded.status, face_count = excluded.face_count, "
            "cache_path = excluded.cache_path, last_error = excluded.last_error, "
            "scanned_at = excluded.scanned_at",
            (
                int(image_id),
                model_id,
                status,
                len(safe_faces),
                cache_path or "",
                str(error or "")[:500],
                now,
            ),
        )
        if affected_people:
            await refresh_people_membership_on_conn(conn, tuple(affected_people))
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return {"face_ids": inserted_face_ids, "face_count": len(inserted_face_ids)}


async def cluster_unassigned_faces(
    db_path: str,
    *,
    model_id: str,
    similarity_threshold: float = 0.52,
    merge_threshold: float = 0.62,
    limit: int = 500,
) -> dict:
    try:
        import numpy as np
    except Exception:
        return {"assigned": 0, "created_people": 0, "merge_suggestions": 0, "error": "numpy unavailable"}

    conn = await connection.open_async(db_path)
    affected_people: set[int] = set()
    try:
        cursor = await conn.execute(
            "SELECT p.id AS person_id, fd.embedding FROM people p "
            "JOIN face_assignments fa ON fa.person_id = p.id AND fa.active = 1 "
            "JOIN face_detections fd ON fd.id = fa.face_id "
            "WHERE p.status != 'ignored' AND p.merged_into_person_id IS NULL "
            "AND fd.ignored = 0 AND fd.embedding_model = ? AND fd.embedding IS NOT NULL",
            (model_id,),
        )
        person_vectors: dict[int, list] = {}
        for row in await cursor.fetchall():
            vec = _face_embedding_vector(row["embedding"])
            if vec is not None:
                person_vectors.setdefault(int(row["person_id"]), []).append(vec)

        centroids: dict[int, object] = {}
        for person_id, vectors in person_vectors.items():
            centroid = np.mean(np.stack(vectors), axis=0)
            norm = float(np.linalg.norm(centroid))
            if norm > 0:
                centroid = centroid / norm
            centroids[person_id] = centroid.astype(np.float32)

        cursor = await conn.execute(
            "SELECT fd.id, fd.embedding FROM face_detections fd "
            "LEFT JOIN face_assignments fa ON fa.face_id = fd.id AND fa.active = 1 "
            "WHERE fa.face_id IS NULL AND fd.ignored = 0 "
            "AND fd.embedding_model = ? AND fd.embedding IS NOT NULL "
            "ORDER BY fd.quality DESC, fd.confidence DESC, fd.updated_at DESC LIMIT ?",
            (model_id, max(1, int(limit))),
        )
        unassigned = [dict(row) for row in await cursor.fetchall()]
        assigned = 0
        created_people = 0
        threshold = float(similarity_threshold or 0.52)

        for row in unassigned:
            vec = _face_embedding_vector(row["embedding"])
            if vec is None:
                continue
            best_person_id = 0
            best_similarity = -1.0
            for person_id, centroid in centroids.items():
                similarity = float(np.dot(vec, centroid))
                if similarity > best_similarity:
                    best_person_id = person_id
                    best_similarity = similarity
            if best_person_id <= 0 or best_similarity < threshold:
                best_person_id = await create_person_on_conn(conn)
                centroids[best_person_id] = vec
                created_people += 1
            else:
                existing = centroids[best_person_id]
                merged = existing * 0.9 + vec * 0.1
                norm = float(np.linalg.norm(merged))
                centroids[best_person_id] = (merged / norm).astype(np.float32) if norm > 0 else merged
            await conn.execute(
                "INSERT OR REPLACE INTO face_assignments "
                "(face_id, person_id, source, active, assigned_at) VALUES (?, ?, 'worker', 1, ?)",
                (int(row["id"]), best_person_id, _time.time()),
            )
            affected_people.add(best_person_id)
            assigned += 1

        if affected_people:
            await refresh_people_membership_on_conn(conn, tuple(affected_people))

        merge_suggestions = 0
        centroid_items = list(centroids.items())
        merge_floor = float(merge_threshold or 0.62)
        for left_index, (left_id, left_vec) in enumerate(centroid_items):
            for right_id, right_vec in centroid_items[left_index + 1:]:
                similarity = float(np.dot(left_vec, right_vec))
                if similarity < merge_floor:
                    continue
                source_id, target_id = sorted((int(left_id), int(right_id)))
                cursor = await conn.execute(
                    "SELECT status FROM people_merge_suggestions "
                    "WHERE source_person_id = ? AND target_person_id = ?",
                    (source_id, target_id),
                )
                existing = await cursor.fetchone()
                if existing and str(existing["status"] or "") in {"rejected", "merged"}:
                    continue
                await conn.execute(
                    "INSERT INTO people_merge_suggestions "
                    "(source_person_id, target_person_id, confidence, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'pending', ?, ?) "
                    "ON CONFLICT(source_person_id, target_person_id) DO UPDATE SET "
                    "confidence = excluded.confidence, status = 'pending', updated_at = excluded.updated_at",
                    (source_id, target_id, similarity, _time.time(), _time.time()),
                )
                merge_suggestions += 1
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return {
        "assigned": assigned,
        "created_people": created_people,
        "merge_suggestions": merge_suggestions,
        "_affected_people": sorted(affected_people),
    }


_ACTIVE_PEOPLE_CTE = (
    "WITH active_people AS ("
    "SELECT p.id, p.name, p.status, p.representative_face_id, "
    "p.photo_count, p.face_count, p.best_quality, "
    "COALESCE(p.latest_face_at, p.updated_at) AS latest_face_at, "
    "CASE WHEN p.status = 'named' OR TRIM(COALESCE(p.name, '')) != '' THEN 1 ELSE 0 END AS is_named "
    "FROM people p "
    "WHERE p.merged_into_person_id IS NULL AND p.status != 'ignored' "
    "AND p.face_count > 0"
    ") "
)

_ACTIVE_PEOPLE_COLUMNS = (
    "id, name, status, representative_face_id, photo_count, face_count, "
    "best_quality, latest_face_at, is_named"
)

_UNKNOWN_PEOPLE_ORDER = (
    "photo_count DESC, face_count DESC, best_quality DESC, latest_face_at DESC, id ASC"
)

_NAMED_PEOPLE_ORDER = (
    "CASE WHEN TRIM(COALESCE(name, '')) = '' THEN printf('person %012d', id) "
    "ELSE LOWER(TRIM(name)) END ASC, photo_count DESC, id ASC"
)


async def _active_people_rows(
    conn,
    *,
    where_sql: str = "1 = 1",
    params: list | tuple | None = None,
    order_sql: str = _UNKNOWN_PEOPLE_ORDER,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    query = (
        f"{_ACTIVE_PEOPLE_CTE} "
        f"SELECT {_ACTIVE_PEOPLE_COLUMNS} FROM active_people "
        f"WHERE {where_sql} "
        f"ORDER BY {order_sql}"
    )
    query_params = list(params or [])
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        query_params.extend([max(0, int(limit)), max(0, int(offset))])
    cursor = await conn.execute(query, query_params)
    return [dict(row) for row in await cursor.fetchall()]


async def _active_people_rows_by_id(conn, person_ids: set[int]) -> list[dict]:
    ids = tuple(sorted({int(person_id) for person_id in person_ids if int(person_id) > 0}))
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return await _active_people_rows(
        conn,
        where_sql=f"id IN ({placeholders})",
        params=list(ids),
        order_sql="id ASC",
    )


async def _people_review_rows(
    conn,
    *,
    limit: int,
) -> tuple[list[dict], dict]:
    unknown_cursor = await conn.execute(
        "SELECT id, name, status, representative_face_id, photo_count, face_count, "
        "best_quality, latest_face_at, 0 AS is_named "
        "FROM people INDEXED BY idx_people_unknown_review "
        "WHERE merged_into_person_id IS NULL AND status != 'ignored' AND status != 'named' "
        "AND TRIM(name) = '' AND face_count > 0 "
        f"ORDER BY {_UNKNOWN_PEOPLE_ORDER} LIMIT ?",
        (limit * 2,),
    )
    unknown_rows = [dict(row) for row in await unknown_cursor.fetchall()]
    named_cursor = await conn.execute(
        "SELECT id, name, status, representative_face_id, photo_count, face_count, "
        "best_quality, latest_face_at, 1 AS is_named "
        "FROM people INDEXED BY idx_people_named_review "
        "WHERE merged_into_person_id IS NULL AND status != 'ignored' AND face_count > 0 "
        "AND (status = 'named' OR TRIM(name) != '') "
        f"ORDER BY {_NAMED_PEOPLE_ORDER} LIMIT ?",
        (limit,),
    )
    named_rows = [dict(row) for row in await named_cursor.fetchall()]
    counts_cursor = await conn.execute(
        "SELECT COUNT(*) AS people, "
        "COALESCE(SUM(CASE WHEN status = 'named' OR TRIM(name) != '' THEN 1 ELSE 0 END), 0) "
        "AS named_people "
        "FROM people WHERE merged_into_person_id IS NULL AND status != 'ignored' AND face_count > 0"
    )
    counts_row = await counts_cursor.fetchone()
    named_count = int(counts_row["named_people"] or 0) if counts_row else 0
    people_count = int(counts_row["people"] or 0) if counts_row else 0
    for rank, row in enumerate(unknown_rows, 1):
        row["section_rank"] = rank
    for rank, row in enumerate(named_rows, 1):
        row["section_rank"] = rank
    return [*unknown_rows, *named_rows], {
        "people": people_count,
        "named_people": named_count,
        "unknown_people": max(0, people_count - named_count),
    }


async def _hydrate_people_rows(conn, rows: list[dict]) -> list[dict]:
    if not rows:
        return []

    representative_ids = sorted({
        int(row.get("representative_face_id") or 0)
        for row in rows
        if int(row.get("representative_face_id") or 0) > 0
    })
    face_by_id: dict[int, dict] = {}
    if representative_ids:
        placeholders = ",".join("?" for _ in representative_ids)
        cursor = await conn.execute(
            "SELECT fd.id AS face_id, fd.image_id, i.filename, "
            "fd.bbox_x, fd.bbox_y, fd.bbox_w, fd.bbox_h, fd.cache_path "
            "FROM face_detections fd JOIN images i ON i.id = fd.image_id "
            f"WHERE fd.id IN ({placeholders})",
            representative_ids,
        )
        face_by_id = {int(row["face_id"]): dict(row) for row in await cursor.fetchall()}

    fallback_person_ids = [
        int(row["id"])
        for row in rows
        if int(row.get("representative_face_id") or 0) <= 0
        or int(row.get("representative_face_id") or 0) not in face_by_id
    ]
    best_face_by_person: dict[int, dict] = {}
    if fallback_person_ids:
        placeholders = ",".join("?" for _ in fallback_person_ids)
        cursor = await conn.execute(
            "SELECT fa.person_id, fd.id AS face_id, fd.image_id, i.filename, "
            "fd.bbox_x, fd.bbox_y, fd.bbox_w, fd.bbox_h, fd.cache_path "
            "FROM face_detections fd "
            "JOIN face_assignments fa ON fa.face_id = fd.id "
            "JOIN images i ON i.id = fd.image_id "
            f"WHERE fa.person_id IN ({placeholders}) AND fa.active = 1 AND fd.ignored = 0 "
            "ORDER BY fa.person_id ASC, fd.quality DESC, fd.confidence DESC, fd.updated_at DESC",
            fallback_person_ids,
        )
        for face_row in await cursor.fetchall():
            person_id = int(face_row["person_id"])
            if person_id not in best_face_by_person:
                best_face_by_person[person_id] = dict(face_row)

    hydrated: list[dict] = []
    for row in rows:
        person_id = int(row["id"])
        face_id = int(row.get("representative_face_id") or 0)
        face_row = face_by_id.get(face_id) or best_face_by_person.get(person_id)
        image_id = int(face_row["image_id"]) if face_row else 0
        representative_face_id = int(face_row["face_id"]) if face_row else 0
        name = str(row.get("name") or "").strip()
        label = name or f"Person {person_id}"
        hydrated.append({
            "id": person_id,
            "name": name,
            "label": label,
            "status": str(row.get("status") or "unknown"),
            "photo_count": int(row.get("photo_count") or 0),
            "face_count": int(row.get("face_count") or 0),
            "best_quality": round(float(row.get("best_quality") or 0.0), 4),
            "latest_face_at": float(row.get("latest_face_at") or 0.0),
            "representative_face_id": representative_face_id,
            "representative_image_id": image_id,
            "representative_filename": str(face_row["filename"] or "") if face_row else "",
            "representative_bbox": {
                "x": round(float(face_row["bbox_x"] or 0.0), 2) if face_row else 0.0,
                "y": round(float(face_row["bbox_y"] or 0.0), 2) if face_row else 0.0,
                "w": round(float(face_row["bbox_w"] or 0.0), 2) if face_row else 0.0,
                "h": round(float(face_row["bbox_h"] or 0.0), 2) if face_row else 0.0,
            },
            "face_thumb_url": (
                f"/api/people/faces/{representative_face_id}/thumb"
                if representative_face_id > 0
                else ""
            ),
            "image_thumb_url": f"/api/thumb/sm/{image_id}" if image_id > 0 else "",
            "thumb_url": f"/api/thumb/sm/{image_id}" if image_id > 0 else "",
        })
    return hydrated


async def _people_operational_counts_on_conn(
    conn,
    *,
    face_model_id: str,
    cache_root: str,
) -> dict:
    status_cursor = await conn.execute(
        "SELECT status, value AS count FROM face_scan_status_counts WHERE value > 0"
    )
    scan_counts = {
        str(row["status"] or "unknown"): int(row["count"] or 0)
        for row in await status_cursor.fetchall()
    }
    pending_faces = await count_images_needing_faces_on_conn(
        conn,
        model_id=face_model_id,
        cache_root=cache_root,
    )
    face_cursor = await conn.execute(
        "SELECT value AS count FROM people_operational_metrics "
        "WHERE metric = 'detected_faces'"
    )
    detected_faces = int((await face_cursor.fetchone())["count"] or 0)
    suggestion_cursor = await conn.execute(
        "SELECT COUNT(*) AS count FROM people_merge_suggestions WHERE status = 'pending'"
    )
    merge_suggestions = int((await suggestion_cursor.fetchone())["count"] or 0)
    return {
        "detected_faces": detected_faces,
        "pending_cached_images": pending_faces,
        "merge_suggestions": merge_suggestions,
        "scan": scan_counts,
    }


async def _people_counts_on_conn(
    conn,
    *,
    long_tail_threshold: int,
    face_model_id: str,
    cache_root: str,
    visible_most_seen_count: int = 0,
) -> dict:
    cursor = await conn.execute(
        f"{_ACTIVE_PEOPLE_CTE} "
        "SELECT COUNT(*) AS people, "
        "COALESCE(SUM(CASE WHEN is_named = 1 THEN 1 ELSE 0 END), 0) AS named_people, "
        "COALESCE(SUM(CASE WHEN is_named = 0 THEN 1 ELSE 0 END), 0) AS unknown_people "
        "FROM active_people"
    )
    row = await cursor.fetchone()
    counts = {
        "people": int(row["people"] or 0) if row else 0,
        "named_people": int(row["named_people"] or 0) if row else 0,
        "unknown_people": int(row["unknown_people"] or 0) if row else 0,
    }
    counts["other_faces"] = max(
        0,
        counts["unknown_people"] - max(0, int(visible_most_seen_count)),
    )
    counts.update(await _people_operational_counts_on_conn(
        conn,
        face_model_id=face_model_id,
        cache_root=cache_root,
    ))
    return counts


async def get_people_status_counts(
    db_path: str,
    *,
    long_tail_threshold: int = 1,
    face_model_id: str,
    cache_root: str,
) -> dict:
    conn = await connection.open_async(db_path)
    try:
        return await _people_counts_on_conn(
            conn,
            long_tail_threshold=long_tail_threshold,
            face_model_id=face_model_id,
            cache_root=cache_root,
        )
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_people_review(
    db_path: str,
    *,
    limit: int = 24,
    long_tail_threshold: int = 1,
    face_model_id: str,
    cache_root: str,
) -> dict:
    safe_limit = max(1, min(int(limit or 24), 100))
    conn = await connection.open_async(db_path)
    try:
        review_rows, people_counts = await _people_review_rows(
            conn,
            limit=safe_limit,
        )
        most_seen_rows = [
            row for row in review_rows
            if int(row["is_named"] or 0) == 0
            and int(row["section_rank"] or 0) <= safe_limit
            and int(row["photo_count"] or 0) > int(long_tail_threshold)
        ]
        most_seen_ids = {int(row["id"]) for row in most_seen_rows}
        named_rows = [
            row for row in review_rows
            if int(row["is_named"] or 0) == 1
        ]
        other_rows = [
            row for row in review_rows
            if int(row["is_named"] or 0) == 0 and int(row["id"]) not in most_seen_ids
        ][:safe_limit]

        visible_rows_by_id: dict[int, dict] = {}
        for row in [*most_seen_rows, *named_rows, *other_rows]:
            visible_rows_by_id.setdefault(int(row["id"]), row)

        cursor = await conn.execute(
            "SELECT ms.id, ms.source_person_id, ms.target_person_id, ms.confidence, "
            "src.name AS source_name, tgt.name AS target_name "
            "FROM people_merge_suggestions ms "
            "JOIN people src ON src.id = ms.source_person_id "
            "JOIN people tgt ON tgt.id = ms.target_person_id "
            "WHERE ms.status = 'pending' "
            "ORDER BY ms.confidence DESC, ms.updated_at DESC LIMIT ?",
            (safe_limit,),
        )
        suggestion_rows = [dict(row) for row in await cursor.fetchall()]
        suggestion_person_ids = {
            int(row["source_person_id"])
            for row in suggestion_rows
            if int(row["source_person_id"] or 0) > 0
        } | {
            int(row["target_person_id"])
            for row in suggestion_rows
            if int(row["target_person_id"] or 0) > 0
        }
        missing_suggestion_ids = suggestion_person_ids - set(visible_rows_by_id)
        for row in await _active_people_rows_by_id(conn, missing_suggestion_ids):
            visible_rows_by_id[int(row["id"])] = row

        hydrated_people = await _hydrate_people_rows(conn, list(visible_rows_by_id.values()))
        people_by_id = {person["id"]: person for person in hydrated_people}
        most_seen = [people_by_id[int(row["id"])] for row in most_seen_rows if int(row["id"]) in people_by_id]
        named = [people_by_id[int(row["id"])] for row in named_rows if int(row["id"]) in people_by_id]
        other_faces = [people_by_id[int(row["id"])] for row in other_rows if int(row["id"]) in people_by_id]

        suggestions = []
        for row in suggestion_rows:
            source_id = int(row["source_person_id"])
            target_id = int(row["target_person_id"])
            source_person = people_by_id.get(source_id)
            target_person = people_by_id.get(target_id)
            suggestions.append({
                "id": int(row["id"]),
                "source_person_id": source_id,
                "target_person_id": target_id,
                "source": source_person,
                "target": target_person,
                "source_label": str(
                    (source_person or {}).get("label")
                    or row["source_name"]
                    or f"Person {source_id}"
                ),
                "target_label": str(
                    (target_person or {}).get("label")
                    or row["target_name"]
                    or f"Person {target_id}"
                ),
                "confidence": round(float(row["confidence"] or 0.0), 4),
            })

        counts = dict(people_counts)
        counts["other_faces"] = max(
            0,
            counts["unknown_people"] - len(most_seen),
        )
        counts.update(await _people_operational_counts_on_conn(
            conn,
            face_model_id=face_model_id,
            cache_root=cache_root,
        ))
        return {
            "sections": {
                "most_seen": most_seen,
                "named_people": named,
                "needs_review": suggestions,
                "other_faces": other_faces,
            },
            "counts": counts,
            "ranking_policy": "distinct_photo_count_first",
            "identity_policy": "face_embeddings_only",
            "source_files_preserved": True,
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_face_thumbnail_context(db_path: str, face_id: int) -> dict | None:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT fd.id AS face_id, fd.image_id, fd.bbox_x, fd.bbox_y, fd.bbox_w, fd.bbox_h, "
            "fd.cache_path AS face_cache_path, fsi.cache_path AS scan_cache_path "
            "FROM face_detections fd "
            "LEFT JOIN face_scan_images fsi "
            "ON fsi.image_id = fd.image_id AND fsi.model_id = fd.embedding_model "
            "WHERE fd.id = ? AND fd.ignored = 0",
            (int(face_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        data = dict(row)
        data["cache_path"] = str(data.get("face_cache_path") or data.get("scan_cache_path") or "")
        return data
    finally:
        await connection.close_async(conn, db_path=db_path)


async def label_person(db_path: str, person_id: int, name: str) -> dict:
    label = str(name or "").strip()
    if not label:
        return {"ok": False, "error": "Person label is required."}
    conn = await connection.open_async(db_path)
    try:
        await conn.execute(
            "UPDATE people SET name = ?, status = 'named', updated_at = ? "
            "WHERE id = ? AND merged_into_person_id IS NULL",
            (label, _time.time(), int(person_id)),
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return {"ok": True, "person_id": int(person_id), "name": label}


async def merge_people(db_path: str, source_person_id: int, target_person_id: int) -> dict:
    source_id = int(source_person_id)
    target_id = int(target_person_id)
    if source_id <= 0 or target_id <= 0 or source_id == target_id:
        return {"ok": False, "error": "Choose two different people to merge."}
    conn = await connection.open_async(db_path)
    try:
        canonical = await canonical_person_ids_on_conn(conn, (source_id, target_id))
        if len(canonical) < 2:
            return {"ok": False, "error": "People already resolve to the same identity."}
        source_id, target_id = canonical[0], canonical[1]
        cursor = await conn.execute(
            "SELECT id, name FROM people WHERE id IN (?, ?)",
            (source_id, target_id),
        )
        names = {
            int(row["id"]): str(row["name"] or "").strip()
            for row in await cursor.fetchall()
        }
        source_name = names.get(source_id, "")
        target_name = names.get(target_id, "")
        now = _time.time()
        await conn.execute(
            "UPDATE face_assignments SET person_id = ?, source = 'merge', assigned_at = ? "
            "WHERE person_id = ?",
            (target_id, now, source_id),
        )
        if source_name and not target_name:
            await conn.execute(
                "UPDATE people SET name = ?, status = 'named', updated_at = ? "
                "WHERE id = ? AND merged_into_person_id IS NULL",
                (source_name, now, target_id),
            )
        await conn.execute(
            "UPDATE people SET status = 'merged', merged_into_person_id = ?, updated_at = ? "
            "WHERE id = ?",
            (target_id, now, source_id),
        )
        await conn.execute(
            "UPDATE people_merge_suggestions SET status = 'merged', updated_at = ? "
            "WHERE (source_person_id = ? AND target_person_id = ?) "
            "OR (source_person_id = ? AND target_person_id = ?)",
            (now, source_id, target_id, target_id, source_id),
        )
        await refresh_people_membership_on_conn(conn, (source_id, target_id))
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return {"ok": True, "source_person_id": source_id, "target_person_id": target_id}


async def reject_merge_suggestion(db_path: str, suggestion_id: int) -> dict:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "UPDATE people_merge_suggestions SET status = 'rejected', updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (_time.time(), int(suggestion_id)),
        )
        await conn.commit()
        return {"ok": cursor.rowcount >= 0, "suggestion_id": int(suggestion_id)}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def assign_face(db_path: str, face_id: int, person_id: int | None = None, name: str = "") -> dict:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT fa.person_id FROM face_assignments fa WHERE fa.face_id = ? AND fa.active = 1",
            (int(face_id),),
        )
        row = await cursor.fetchone()
        old_person_id = int(row["person_id"]) if row else 0
        target_id = int(person_id or 0)
        if target_id <= 0:
            target_id = await create_person_on_conn(conn, status="named" if name else "unknown", name=name)
        else:
            canonical = await canonical_person_ids_on_conn(conn, (target_id,))
            target_id = canonical[0] if canonical else target_id
        await conn.execute(
            "UPDATE face_detections SET ignored = 0, updated_at = ? WHERE id = ?",
            (_time.time(), int(face_id)),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO face_assignments "
            "(face_id, person_id, source, active, assigned_at) VALUES (?, ?, 'manual', 1, ?)",
            (int(face_id), target_id, _time.time()),
        )
        affected = tuple({pid for pid in (old_person_id, target_id) if pid > 0})
        await refresh_people_membership_on_conn(conn, affected)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return {"ok": True, "face_id": int(face_id), "person_id": target_id}


async def ignore_face(db_path: str, face_id: int) -> dict:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT fa.person_id FROM face_assignments fa WHERE fa.face_id = ? AND fa.active = 1",
            (int(face_id),),
        )
        row = await cursor.fetchone()
        old_person_id = int(row["person_id"]) if row else 0
        await conn.execute(
            "UPDATE face_detections SET ignored = 1, updated_at = ? WHERE id = ?",
            (_time.time(), int(face_id)),
        )
        await conn.execute("UPDATE face_assignments SET active = 0 WHERE face_id = ?", (int(face_id),))
        if old_person_id > 0:
            await refresh_people_membership_on_conn(conn, (old_person_id,))
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return {"ok": True, "face_id": int(face_id)}


async def ignore_person(db_path: str, person_id: int) -> dict:
    conn = await connection.open_async(db_path)
    try:
        await conn.execute(
            "UPDATE people SET status = 'ignored', updated_at = ? WHERE id = ?",
            (_time.time(), int(person_id)),
        )
        await refresh_people_membership_on_conn(conn, (int(person_id),))
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return {"ok": True, "person_id": int(person_id)}
