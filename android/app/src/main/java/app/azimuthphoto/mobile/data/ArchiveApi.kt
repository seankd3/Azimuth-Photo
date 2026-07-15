package app.azimuthphoto.mobile.data

import android.content.Context
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

private val JSON_TYPE = "application/json".toMediaType()

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
) {
    private val ext get() = (file_ext ?: filename.substringAfterLast('.', "")).lowercase().removePrefix(".")
    val isVideo: Boolean get() = ext in VIDEO_EXTS
    val isRaw: Boolean get() = ext in RAW_EXTS

    private companion object {
        val VIDEO_EXTS = setOf("mp4", "mov", "m4v", "avi", "mkv", "webm", "3gp")
        val RAW_EXTS = setOf("cr2", "cr3", "arw", "nef", "raf", "rw2", "dng", "orf", "raw")
    }
}

@Serializable
data class RankingsPage(
    val images: List<ArchiveImage> = emptyList(),
    val visible_images: Long = 0,
    val total_images: Long = 0,
)

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
        folders: List<String> = emptyList(),
    ): RankingsPage = withContext(Dispatchers.IO) {
        val url = buildString {
            append(baseUrl)
            append("/api/rankings?sort=date_taken&limit=").append(limit)
            append("&offset=").append(offset)
            if (search.isNotBlank()) {
                append("&q=").append(java.net.URLEncoder.encode(search, "UTF-8"))
                append("&deep=true")
            }
            folders.filter { it.isNotBlank() }.forEach { folder ->
                append("&folder=").append(java.net.URLEncoder.encode("/$folder", "UTF-8"))
            }
        }
        http.newCall(Request.Builder().url(url).build()).execute().use { response ->
            if (!response.isSuccessful) {
                throw IOException("rankings failed: HTTP ${response.code}")
            }
            json.decodeFromString<RankingsPage>(response.body!!.string())
        }
    }

    suspend fun shelves(): List<Shelf> = withContext(Dispatchers.IO) {
        val all = http.newCall(Request.Builder().url("$baseUrl/api/folders").build())
            .execute().use { response ->
                if (!response.isSuccessful) {
                    throw IOException("folders failed: HTTP ${response.code}")
                }
                json.decodeFromString<FoldersResponse>(response.body!!.string()).folders
            }
        // Same-named folders in different roots (e.g. Personal Photos in the old
        // tree and under RAWS) are one shelf to the user — merge, scope to all paths.
        collapseShelfFolders(all)
            .groupBy { it.name }
            .map { (name, group) -> Shelf(name, group.sumOf { it.count }, group.map { it.path }) }
            .sortedByDescending { it.count }
    }

    suspend fun stats(timeoutSeconds: Long = 3): ArchiveStats = withContext(Dispatchers.IO) {
        val client = http.newBuilder()
            .connectTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .readTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .callTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .build()
        client.newCall(Request.Builder().url("$baseUrl/api/stats").build()).execute().use { response ->
            if (!response.isSuccessful) throw IOException("stats failed: HTTP ${response.code}")
            val root = json.parseToJsonElement(response.body?.string().orEmpty())
            ArchiveStats(photoCount = findPhotoCount(root))
        }
    }

    fun thumbUrl(image: ArchiveImage, size: String = "sm"): String =
        if (image.thumb_url.isNotEmpty()) "$baseUrl${image.thumb_url}"
        else "$baseUrl/api/thumb/$size/${image.id}"

    fun largeUrl(image: ArchiveImage): String = "$baseUrl/api/thumb/lg/${image.id}"

    /** Full-resolution render — what deep zoom and sharing deserve. */
    fun fullUrl(imageId: Long): String = "$baseUrl/api/full/$imageId"

    /** Move hub images to the archive's restorable trash. */
    suspend fun trash(ids: List<Long>): Boolean = withContext(Dispatchers.IO) {
        val body = json.encodeToString(ImageIdsBody(ids)).toRequestBody(JSON_TYPE)
        http.newCall(Request.Builder().url("$baseUrl/api/images/trash").post(body).build())
            .execute().use { it.isSuccessful }
    }

    /** EXIF fields for the info sheet; shapes vary, so everything renders defensively. */
    suspend fun exif(imageId: Long): Map<String, String> = withContext(Dispatchers.IO) {
        runCatching {
            http.newCall(Request.Builder().url("$baseUrl/api/image/$imageId/exif").build())
                .execute().use { response ->
                    if (!response.isSuccessful) return@use emptyMap()
                    val root = json.parseToJsonElement(response.body?.string().orEmpty())
                    if (root !is JsonObject) return@use emptyMap()
                    root.entries.mapNotNull { (k, v) ->
                        (v as? JsonPrimitive)?.content?.takeIf { it.isNotBlank() && it != "null" }
                            ?.let { k to it }
                    }.toMap()
                }
        }.getOrDefault(emptyMap())
    }

    /**
     * Download the full render into the app cache so a hub photo can ride the
     * exact same share / edit / open-with intents as a local file.
     */
    suspend fun downloadToCache(context: Context, image: ArchiveImage): File =
        withContext(Dispatchers.IO) {
            val dir = File(context.cacheDir, "shared").apply { mkdirs() }
            val ext = (image.file_ext ?: ".jpg").removePrefix(".").ifEmpty { "jpg" }
            val name = image.filename.substringBeforeLast('.').ifEmpty { "azimuth-${image.id}" }
            val target = File(dir, "$name-${image.id}.${if (image.isVideo) ext else "jpg"}")
            if (target.length() > 0) return@withContext target
            // Stream to a temp file and rename, so a failed download is never
            // mistaken for a complete one on the next attempt.
            val tmp = File(dir, "${target.name}.part")
            try {
                http.newCall(Request.Builder().url(fullUrl(image.id)).build()).execute().use { response ->
                    if (!response.isSuccessful) throw IOException("full download failed: HTTP ${response.code}")
                    tmp.outputStream().use { out -> response.body!!.byteStream().copyTo(out) }
                }
                if (!tmp.renameTo(target)) throw IOException("could not finalize download")
            } finally {
                tmp.delete()
            }
            trimSharedCache(dir)
            target
        }

    /** Keep the share cache bounded: newest first, drop past 256 MB. */
    private fun trimSharedCache(dir: File, maxBytes: Long = 256L * 1024 * 1024) {
        val files = dir.listFiles()?.sortedByDescending { it.lastModified() } ?: return
        var total = 0L
        files.forEach { file ->
            total += file.length()
            if (total > maxBytes) file.delete()
        }
    }
}

@Serializable
private data class ImageIdsBody(val ids: List<Long>)

data class ArchiveStats(val photoCount: Long?)

private fun findPhotoCount(element: JsonElement): Long? {
    if (element !is JsonObject) return null
    val preferred = listOf("total_images", "photo_count", "image_count", "total", "photos")
    preferred.forEach { key ->
        val value = element[key]
        if (value is JsonPrimitive && !value.isString) {
            value.content.toLongOrNull()?.let { return it }
        }
    }
    element.values.forEach { child -> findPhotoCount(child)?.let { return it } }
    return null
}

internal fun collapseShelfFolders(all: List<ArchiveFolder>): List<ArchiveFolder> {
    val children = HashMap<String, MutableList<ArchiveFolder>>()
    all.forEach { folder ->
        val parent = folder.path.substringBeforeLast('/', "")
        children.getOrPut(parent) { mutableListOf() }.add(folder)
    }
    val shelves = mutableListOf<ArchiveFolder>()
    val dateLike = Regex("^\\d{4}(-\\d{2}(-\\d{2})?)?$")
    fun descend(node: ArchiveFolder) {
        val kids = children[node.path].orEmpty()
        when {
            kids.isEmpty() -> shelves.add(node)
            kids.size == 1 && kids[0].count == node.count -> descend(kids[0])
            kids.size == 1 -> shelves.add(node)
            kids.all { dateLike.matches(it.name) } -> shelves.add(node)
            else -> kids.forEach { shelves.add(it) }
        }
    }
    all.filter { it.depth == 0 }.forEach(::descend)
    // Year/date folders are timeline structure, not shelves — never surface them as chips.
    return shelves.filterNot { dateLike.matches(it.name) }.sortedByDescending { it.count }
}

@Serializable
data class ArchiveFolder(val path: String, val count: Int, val depth: Int) {
    val name: String get() = path.substringAfterLast('/')
}

/** A user-facing library shelf; may span several same-named folders. */
data class Shelf(val name: String, val count: Int, val paths: List<String>)

@Serializable
private data class FoldersResponse(val folders: List<ArchiveFolder> = emptyList())
