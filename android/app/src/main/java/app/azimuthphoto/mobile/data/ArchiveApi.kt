package app.azimuthphoto.mobile.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.util.concurrent.TimeUnit
import android.content.Context
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File

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

    fun thumbUrl(image: ArchiveImage, size: String = "sm"): String =
        if (image.thumb_url.isNotEmpty()) "$baseUrl${image.thumb_url}"
        else "$baseUrl/api/thumb/$size/${image.id}"

    fun largeUrl(image: ArchiveImage): String = "$baseUrl/api/thumb/lg/${image.id}"

    suspend fun trashImage(id: Long): Boolean = postJson("/api/images/trash", "{\"ids\":[$id]}")

    suspend fun setFlag(id: Long, flag: String): Boolean = postJson("/api/image/$id/flag", "{\"flag\":\"$flag\"}")

    private suspend fun postJson(path: String, body: String): Boolean = withContext(Dispatchers.IO) {
        val req = Request.Builder().url("$baseUrl$path")
            .post(body.toRequestBody("application/json".toMediaType())).build()
        http.newCall(req).execute().use { it.isSuccessful }
    }

    /** Downloads the large JPEG preview to the cache dir for sharing to other apps. */
    suspend fun downloadToCache(context: Context, image: ArchiveImage): File = withContext(Dispatchers.IO) {
        val req = Request.Builder().url(largeUrl(image)).build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("download failed: HTTP ${resp.code}")
            val file = File(context.cacheDir, "share_${image.id}.jpg")
            file.outputStream().use { out -> resp.body!!.byteStream().copyTo(out) }
            file
        }
    }
}
