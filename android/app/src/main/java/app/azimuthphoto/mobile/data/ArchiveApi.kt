package app.azimuthphoto.mobile.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.util.concurrent.TimeUnit

@Serializable
data class ArchiveImage(
    val id: Long,
    val filename: String = "",
    val flag: String? = null,
    val aspect_ratio: Double? = null,
    val date_taken: String? = null,
    val camera_model: String? = null,
    val lens: String? = null,
    val file_ext: String? = null,
    val file_size: Long? = null,
    val width: Int? = null,
    val height: Int? = null,
    val thumb_url: String = "",
    val date_group: String? = null,
)

@Serializable
data class RankingsPage(
    val images: List<ArchiveImage> = emptyList(),
    val visible_images: Long = 0,
    val total_images: Long = 0,
)

@Serializable
data class ArchiveCollection(
    val id: Long,
    val name: String = "",
    val description: String = "",
    val image_count: Int = 0,
    val active_cover_image_id: Long? = null,
    val smart: Boolean = false,
    val images: List<ArchiveImage> = emptyList(),
)

@Serializable
data class CollectionsResponse(
    val collections: List<ArchiveCollection> = emptyList(),
)

@Serializable
data class CollectionDetail(
    val collection: ArchiveCollection,
)

/** Read-only client for the hub's library API. */
class ArchiveApi(private val baseUrl: String) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    private val json = Json { ignoreUnknownKeys = true }

    suspend fun page(offset: Int, limit: Int = 200, search: String = ""): RankingsPage =
        withContext(Dispatchers.IO) {
            val url = buildString {
                append(baseUrl)
                append("/api/rankings?sort=date_taken&limit=").append(limit)
                append("&offset=").append(offset)
                if (search.isNotBlank()) {
                    // q + deep=true engages the hub's semantic search.
                    append("&q=").append(java.net.URLEncoder.encode(search, "UTF-8"))
                    append("&deep=true")
                }
            }
            http.newCall(Request.Builder().url(url).build()).execute().use { resp ->
                if (!resp.isSuccessful) throw IOException("rankings failed: HTTP ${resp.code}")
                json.decodeFromString<RankingsPage>(resp.body!!.string())
            }
        }

    suspend fun collections(): List<ArchiveCollection> = withContext(Dispatchers.IO) {
        http.newCall(Request.Builder().url("$baseUrl/api/user-collections").build()).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("collections failed: HTTP ${resp.code}")
            json.decodeFromString<CollectionsResponse>(resp.body!!.string()).collections
        }
    }

    suspend fun collection(id: Long): CollectionDetail = withContext(Dispatchers.IO) {
        http.newCall(
            Request.Builder().url("$baseUrl/api/user-collections/$id?limit=500").build(),
        ).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("collection failed: HTTP ${resp.code}")
            json.decodeFromString<CollectionDetail>(resp.body!!.string())
        }
    }

    fun thumbUrl(image: ArchiveImage, size: String = "sm"): String =
        if (image.thumb_url.isNotEmpty()) "$baseUrl${image.thumb_url}" else thumbUrl(image.id, size)

    fun thumbUrl(imageId: Long, size: String = "sm"): String = "$baseUrl/api/thumb/$size/$imageId"

    fun largeUrl(image: ArchiveImage): String = "$baseUrl/api/thumb/lg/${image.id}"
}
