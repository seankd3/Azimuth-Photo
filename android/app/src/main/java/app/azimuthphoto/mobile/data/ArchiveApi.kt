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

/** Read-only client for the hub's library API. */
class ArchiveApi(private val baseUrl: String) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    private val json = Json { ignoreUnknownKeys = true }

    suspend fun page(
        offset: Int,
        limit: Int = 200,
        search: String = "",
        folder: String = "",
    ): RankingsPage =
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
                if (folder.isNotBlank()) {
                    // Leading slash = absolute scope, rides the hub's indexed range scan.
                    append("&folder=").append(java.net.URLEncoder.encode("/$folder", "UTF-8"))
                }
            }
            http.newCall(Request.Builder().url(url).build()).execute().use { resp ->
                if (!resp.isSuccessful) throw IOException("rankings failed: HTTP ${resp.code}")
                json.decodeFromString<RankingsPage>(resp.body!!.string())
            }
        }

    /**
     * The library's shelf folders: walk each root down single-child chains and
     * surface the first level that actually branches (e.g. RAWS, Exported
     * Edits, Personal Photos) — no hardcoded names.
     */
    suspend fun shelves(): List<ArchiveFolder> = withContext(Dispatchers.IO) {
        val url = "$baseUrl/api/folders"
        val all = http.newCall(Request.Builder().url(url).build()).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("folders failed: HTTP ${resp.code}")
            json.decodeFromString<FoldersResponse>(resp.body!!.string()).folders
        }
        val children = HashMap<String, MutableList<ArchiveFolder>>()
        all.forEach { f ->
            val parent = f.path.substringBeforeLast('/', "")
            children.getOrPut(parent) { mutableListOf() }.add(f)
        }
        val shelves = mutableListOf<ArchiveFolder>()
        fun descend(node: ArchiveFolder) {
            val kids = children[node.path].orEmpty()
            when {
                kids.size == 1 && kids[0].count == node.count -> descend(kids[0])
                kids.isEmpty() -> shelves.add(node)
                kids.size == 1 -> shelves.add(node)
                else -> kids.forEach { shelves.add(it) }
            }
        }
        all.filter { it.depth == 0 }.forEach(::descend)
        shelves.sortedByDescending { it.count }
    }

    fun thumbUrl(image: ArchiveImage, size: String = "sm"): String =
        if (image.thumb_url.isNotEmpty()) "$baseUrl${image.thumb_url}"
        else "$baseUrl/api/thumb/$size/${image.id}"

    fun largeUrl(image: ArchiveImage): String = "$baseUrl/api/thumb/lg/${image.id}"
}

@Serializable
data class ArchiveFolder(val path: String, val count: Int, val depth: Int) {
    val name: String get() = path.substringAfterLast('/')
}

@Serializable
private data class FoldersResponse(val folders: List<ArchiveFolder> = emptyList())
