package app.azimuthphoto.mobile.data

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** A face cluster from the hub (people are grouped by the desktop's face model). */
@Serializable
data class Person(
    val id: Long,
    val name: String = "",
    val label: String = "",
    val status: String = "",
    val photo_count: Int = 0,
    val face_count: Int = 0,
    val representative_image_id: Long? = null,
    val face_thumb_url: String = "",
    val thumb_url: String = "",
) {
    /** The user's name if set, otherwise the auto label ("Person 3271"). */
    val displayName: String get() = name.ifBlank { label.ifBlank { "Unnamed" } }
    val named: Boolean get() = name.isNotBlank()
}

@Serializable
private data class PeopleResponse(val sections: Map<String, List<Person>> = emptyMap())

/** A user collection (album) with cover + publish/share state. */
@Serializable
data class Collection(
    val id: Long,
    val uuid: String = "",
    val name: String = "",
    val description: String = "",
    val image_count: Int = 0,
    val cover_image_id: Long? = null,
    val cover_filename: String = "",
    val visibility: String = "private",
    val published: Boolean = false,
    val publish_slug: String = "",
    val smart: Boolean = false,
)

@Serializable
private data class CollectionsResponse(val collections: List<Collection> = emptyList())

@Serializable
data class MapMarker(
    val id: Long,
    val filename: String = "",
    val lat: Double,
    val lng: Double,
    val thumb_url: String = "",
)

@Serializable
private data class MarkersResponse(val markers: List<MapMarker> = emptyList())

@Serializable
data class Tag(val tag: String, val count: Int = 0)

@Serializable
private data class TagsResponse(val tags: List<Tag> = emptyList())

@Serializable
data class Caption(
    val image_id: Long = 0,
    val caption: String = "",
    val tags: List<String> = emptyList(),
    val has_caption: Boolean = false,
)

/**
 * The desktop-parity library surface: people/faces, collections/albums, places,
 * captions/tags, and visual-similar. Read paths need no auth; write paths accept
 * an optional X-Device-Token. Response shapes for write endpoints are verified
 * against the live hub by each feature's builder.
 */
class LibraryApi(private val baseUrl: String, private val deviceToken: String? = null) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    private val json = Json { ignoreUnknownKeys = true }
    private val jsonType = "application/json".toMediaType()

    private fun builder(path: String) = Request.Builder().url("$baseUrl$path")
        .apply { deviceToken?.let { header("X-Device-Token", it) } }

    private fun get(path: String): String =
        http.newCall(builder(path).build()).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("GET $path -> HTTP ${resp.code}")
            resp.body!!.string()
        }

    private fun postJson(path: String, body: String): String =
        http.newCall(builder(path).post(body.toRequestBody(jsonType)).build()).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("POST $path -> HTTP ${resp.code}")
            resp.body?.string().orEmpty()
        }

    // ---- Absolute media URLs (share the hub's thumb/full endpoints) ----
    fun thumb(url: String): String = if (url.startsWith("http")) url else "$baseUrl$url"
    fun imageThumb(imageId: Long, size: String = "sm"): String = "$baseUrl/api/thumb/$size/$imageId"
    fun imageLarge(imageId: Long): String = "$baseUrl/api/thumb/lg/$imageId"

    // ---- People / faces ----
    suspend fun people(): List<Person> = withContext(Dispatchers.IO) {
        val sections = json.decodeFromString<PeopleResponse>(get("/api/people")).sections
        // Flatten every section (most_seen, named, …), keep one entry per id, most photos first.
        sections.values.flatten().associateBy { it.id }.values.sortedByDescending { it.photo_count }
    }

    suspend fun personPhotos(personId: Long, offset: Int = 0, limit: Int = 200): List<ArchiveImage> =
        withContext(Dispatchers.IO) {
            val body = get("/api/rankings?sort=date_taken&people=$personId&limit=$limit&offset=$offset")
            json.decodeFromString<RankingsPage>(body).images
        }

    suspend fun labelPerson(personId: Long, name: String): Boolean = withContext(Dispatchers.IO) {
        runCatching { postJson("/api/people/$personId/label", """{"name":${json.encodeToString(name)}}""") }
            .isSuccess
    }

    suspend fun ignorePerson(personId: Long): Boolean = withContext(Dispatchers.IO) {
        runCatching { postJson("/api/people/$personId/ignore", "{}") }.isSuccess
    }

    /** Verify payload against the hub before relying on this. */
    suspend fun mergePeople(sourceId: Long, targetId: Long): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            postJson("/api/people/merge", """{"source_id":$sourceId,"target_id":$targetId}""")
        }.isSuccess
    }

    // ---- Collections / albums ----
    suspend fun collections(): List<Collection> = withContext(Dispatchers.IO) {
        json.decodeFromString<CollectionsResponse>(get("/api/user-collections")).collections
    }

    /** Collection detail JSON (parse images defensively — shape verified per builder). */
    suspend fun collectionDetail(id: Long): JsonObject = withContext(Dispatchers.IO) {
        json.parseToJsonElement(get("/api/user-collections/$id")).jsonObject
    }

    suspend fun createCollection(name: String): Long? = withContext(Dispatchers.IO) {
        runCatching {
            val out = postJson("/api/user-collections", """{"name":${json.encodeToString(name)}}""")
            json.parseToJsonElement(out).jsonObject["id"]?.toString()?.trim('"')?.toLongOrNull()
        }.getOrNull()
    }

    suspend fun addToCollection(collectionId: Long, imageIds: List<Long>): Boolean =
        withContext(Dispatchers.IO) {
            runCatching {
                postJson("/api/user-collections/$collectionId/images", """{"ids":${imageIds}}""")
            }.isSuccess
        }

    /** Returns the public share URL/slug when publishing succeeds. */
    suspend fun publish(collectionId: Long): String? = withContext(Dispatchers.IO) {
        runCatching {
            val out = postJson("/api/user-collections/$collectionId/publish", "{}")
            val obj = json.parseToJsonElement(out).jsonObject
            (obj["url"] ?: obj["publish_slug"] ?: obj["slug"])?.toString()?.trim('"')
        }.getOrNull()
    }

    suspend fun share(collectionId: Long): String? = withContext(Dispatchers.IO) {
        runCatching {
            val out = postJson("/api/user-collections/$collectionId/share", "{}")
            val obj = json.parseToJsonElement(out).jsonObject
            (obj["url"] ?: obj["share_url"] ?: obj["token"])?.toString()?.trim('"')
        }.getOrNull()
    }

    // ---- Places ----
    suspend fun markers(limit: Int = 5000): List<MapMarker> = withContext(Dispatchers.IO) {
        json.decodeFromString<MarkersResponse>(get("/api/map/markers?limit=$limit")).markers
    }

    // ---- Tags / captions / similar ----
    suspend fun tags(): List<Tag> = withContext(Dispatchers.IO) {
        json.decodeFromString<TagsResponse>(get("/api/tags")).tags
    }

    suspend fun tagPhotos(tag: String, offset: Int = 0, limit: Int = 200): List<ArchiveImage> =
        withContext(Dispatchers.IO) {
            val q = java.net.URLEncoder.encode(tag, "UTF-8")
            json.decodeFromString<RankingsPage>(
                get("/api/rankings?sort=date_taken&tag=$q&limit=$limit&offset=$offset"),
            ).images
        }

    suspend fun caption(imageId: Long): Caption = withContext(Dispatchers.IO) {
        runCatching { json.decodeFromString<Caption>(get("/api/image/$imageId/caption")) }
            .getOrDefault(Caption(image_id = imageId))
    }

    suspend fun similar(imageId: Long, limit: Int = 60): List<ArchiveImage> = withContext(Dispatchers.IO) {
        runCatching {
            val el = json.parseToJsonElement(get("/api/similar/$imageId?limit=$limit"))
            val arr = (el as? JsonObject)?.get("images") as? JsonArray ?: (el as? JsonArray)
            arr?.let { json.decodeFromString<List<ArchiveImage>>(it.toString()) } ?: emptyList()
        }.getOrDefault(emptyList())
    }
}
