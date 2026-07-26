package app.azimuthphoto.mobile.data

import java.net.URLEncoder
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Every filter the hub's /api/rankings understands, in one place. The same model
 * drives the search screen, the refine sheet, and smart-collection saving — the
 * hub validates smart queries against these exact keys (features/collections/smart.py).
 */
data class SearchFilters(
    val q: String = "",
    val deep: Boolean = false,
    /** Person (face cluster) ids; the hub takes them comma-joined. */
    val people: List<Long> = emptyList(),
    val tag: String = "",
    /** "2024" or "2024-05" — the hub prefix-matches date_taken. */
    val dateTaken: String = "",
    /** Lowercase extension ("dng", "jpg") or "video" per the hub's file_type. */
    val fileType: String = "",
    val camera: String = "",
    val lens: String = "",
    val minStars: Int = 0,
    /** "picked" | "rejected" | "unflagged" | "" */
    val flag: String = "",
    /** "landscape" | "portrait" | "" */
    val orientation: String = "",
    val folders: List<String> = emptyList(),
    val sort: String = "date_taken",
) {
    /** True when nothing narrows the library (sort alone isn't a filter). */
    val isEmpty: Boolean
        get() = q.isBlank() && people.isEmpty() && tag.isBlank() && dateTaken.isBlank() &&
            fileType.isBlank() && camera.isBlank() && lens.isBlank() && minStars == 0 &&
            flag.isBlank() && orientation.isBlank() && folders.isEmpty()

    /** How many distinct filters are active — drives the refine badge. */
    val activeCount: Int
        get() = listOf(
            q.isNotBlank(), people.isNotEmpty(), tag.isNotBlank(), dateTaken.isNotBlank(),
            fileType.isNotBlank(), camera.isNotBlank(), lens.isNotBlank(), minStars > 0,
            flag.isNotBlank(), orientation.isNotBlank(), folders.isNotEmpty(),
        ).count { it }

    /** Query string for /api/rankings, /api/date-groups, etc. (no leading '?'). */
    fun toQueryString(offset: Int, limit: Int): String = buildString {
        append("sort=").append(enc(sort.ifBlank { "date_taken" }))
        append("&limit=").append(limit).append("&offset=").append(offset)
        if (q.isNotBlank()) append("&q=").append(enc(q))
        if (deep && q.isNotBlank()) append("&deep=true")
        if (people.isNotEmpty()) append("&people=").append(people.joinToString(","))
        if (tag.isNotBlank()) append("&tag=").append(enc(tag))
        if (dateTaken.isNotBlank()) append("&date_taken=").append(enc(dateTaken))
        if (fileType.isNotBlank()) append("&file_type=").append(enc(fileType))
        if (camera.isNotBlank()) append("&camera=").append(enc(camera))
        if (lens.isNotBlank()) append("&lens=").append(enc(lens))
        if (minStars > 0) append("&min_stars=").append(minStars)
        if (flag.isNotBlank()) append("&flag=").append(enc(flag))
        if (orientation.isNotBlank()) append("&orientation=").append(enc(orientation))
        folders.forEach { append("&folder=").append(enc(it)) }
    }

    /**
     * The smart-collection query payload. Keys/types must match the hub's
     * ALLOWED_QUERY_KEYS (strings + int min_stars); unknown keys are rejected.
     */
    fun toSmartQuery(): JsonObject {
        val entries = mutableMapOf<String, JsonPrimitive>()
        if (q.isNotBlank()) entries["q"] = JsonPrimitive(q)
        if (people.isNotEmpty()) entries["people"] = JsonPrimitive(people.joinToString(","))
        if (tag.isNotBlank()) entries["tag"] = JsonPrimitive(tag)
        if (dateTaken.isNotBlank()) entries["date_taken"] = JsonPrimitive(dateTaken)
        if (fileType.isNotBlank()) entries["file_type"] = JsonPrimitive(fileType)
        if (camera.isNotBlank()) entries["camera"] = JsonPrimitive(camera)
        if (lens.isNotBlank()) entries["lens"] = JsonPrimitive(lens)
        if (minStars > 0) entries["min_stars"] = JsonPrimitive(minStars)
        if (flag.isNotBlank()) entries["flag"] = JsonPrimitive(flag)
        if (orientation.isNotBlank()) entries["orientation"] = JsonPrimitive(orientation)
        if (folders.isNotEmpty()) entries["folder"] = JsonPrimitive(folders.first())
        if (sort.isNotBlank() && sort != "date_taken") entries["sort"] = JsonPrimitive(sort)
        return JsonObject(entries)
    }

    private fun enc(v: String) = URLEncoder.encode(v, "UTF-8")
}
