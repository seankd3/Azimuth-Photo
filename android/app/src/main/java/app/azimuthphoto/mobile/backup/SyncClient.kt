package app.azimuthphoto.mobile.backup

import app.azimuthphoto.mobile.data.HUB_API_REV
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.io.InputStream
import java.util.concurrent.TimeUnit

@Serializable
data class ManifestItem(
    val content_hash: String,
    val full_hash: String? = null,
    val bytes: Long,
    val filename: String,
    val date_taken: String? = null,
    /** Hub routes the upload under raws_root/<folder>/YYYY/date when set. */
    val folder: String? = null,
)

@Serializable
private data class ManifestRequest(val items: List<ManifestItem>)

@Serializable
data class KnownItem(val content_hash: String, val image_id: Long)

@Serializable
data class ManifestResponse(val missing: List<String>, val known: List<KnownItem>)

@Serializable
private data class HaveRequest(val content_hashes: List<String>)

@Serializable
private data class HaveResponse(val present: List<String> = emptyList())

@Serializable
private data class SyncOpResponse(
    val offset: Long? = null,
    val image_id: Long? = null,
    val error: String? = null,
)

class HubHttpException(val code: Int, detail: String? = null) :
    IOException("hub failed: HTTP $code${detail?.let { " $it" } ?: ""}")

/** Client for the hub's FIELD_SPEC sync endpoints (manifest + resumable chunked upload). */
class SyncClient(
    private val baseUrl: String,
    private val deviceToken: String? = null,
) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(300, TimeUnit.SECONDS)
        .build()

    private val json = Json { ignoreUnknownKeys = true }
    private val jsonType = "application/json".toMediaType()
    private val binType = "application/octet-stream".toMediaType()

    fun manifest(items: List<ManifestItem>): ManifestResponse {
        val body = json.encodeToString(ManifestRequest(items)).toRequestBody(jsonType)
        http.newCall(
            requestBuilder("$baseUrl/api/sync/manifest").post(body).build()
        ).execute().use { resp ->
            if (!resp.isSuccessful) throw hubException("manifest", resp)
            return json.decodeFromString(resp.body!!.string())
        }
    }

    /**
     * Byte-verification gate for destructive cleanup: the hub re-derives each
     * content hash from the live bytes it holds right now, so a truncated or
     * bit-rotted hub copy never green-lights deleting the phone's only copy.
     * (Manifest "known" is only an existence + stat-size check — never enough.)
     */
    fun verifiedPresent(contentHashes: List<String>): Set<String> {
        if (contentHashes.isEmpty()) return emptySet()
        val body = json.encodeToString(HaveRequest(contentHashes)).toRequestBody(jsonType)
        http.newCall(
            requestBuilder("$baseUrl/api/sync/have").post(body).build()
        ).execute().use { resp ->
            if (!resp.isSuccessful) throw hubException("have", resp)
            return json.decodeFromString<HaveResponse>(resp.body!!.string()).present.toSet()
        }
    }

    fun uploadOffset(contentHash: String): Long {
        http.newCall(
            requestBuilder("$baseUrl/api/sync/upload/$contentHash/status").build()
        ).execute().use { resp ->
            if (!resp.isSuccessful) throw hubException("status", resp)
            return parseSyncResponse(resp.body!!.string()).offset
                ?: throw IOException("status response without offset")
        }
    }

    /**
     * Upload the stream in sequential chunks, resuming from the hub's committed offset.
     * The stream must start at byte 0; already-committed bytes are skipped locally.
     * Returns the hub image_id once complete.
     */
    fun upload(
        contentHash: String,
        totalBytes: Long,
        openStream: () -> InputStream,
        onProgress: (Long) -> Unit = {},
    ): Long {
        var offset = uploadOffset(contentHash)
        var stream = openStream()
        var realigns = 0
        try {
            stream.skipFully(offset)
            val buf = ByteArray(CHUNK_BYTES)
            while (offset < totalBytes) {
                val want = minOf(CHUNK_BYTES.toLong(), totalBytes - offset).toInt()
                stream.readFully(buf, want)
                val request = requestBuilder("$baseUrl/api/sync/upload/$contentHash")
                    .header("X-Offset", offset.toString())
                    .header("X-Total-Bytes", totalBytes.toString())
                    .post(buf.copyOf(want).toRequestBody(binType))
                    .build()
                http.newCall(request).execute().use { resp ->
                    when {
                        resp.code == 409 -> {
                            // Offset mismatch: hub tells us its committed offset; reopen and realign.
                            if (++realigns > MAX_REALIGNS) {
                                throw IOException("too many upload realigns")
                            }
                            val actual = parseSyncResponse(resp.body!!.string()).offset
                                ?: throw IOException("409 without offset")
                            stream = stream.realign(openStream, actual)
                            offset = actual
                        }
                        !resp.isSuccessful ->
                            throw hubException("upload", resp)
                        else -> {
                            val response = parseSyncResponse(resp.body!!.string())
                            val imageId = response.image_id
                            if (imageId != null) return imageId
                            val actual = response.offset ?: offset + want
                            if (actual != offset + want) stream = stream.realign(openStream, actual)
                            offset = actual
                            onProgress(offset)
                        }
                    }
                }
            }
            throw IOException("upload reached end without hub finalize")
        } finally {
            stream.close()
        }
    }

    private fun requestBuilder(url: String): Request.Builder = Request.Builder()
        .url(url)
        .header("X-PA-Api-Rev", HUB_API_REV.toString())
        .apply { deviceToken?.let { header("X-Device-Token", it) } }

    private fun parseSyncResponse(body: String): SyncOpResponse = try {
        json.decodeFromString(body)
    } catch (e: Exception) {
        throw IOException("invalid sync response", e)
    }

    private fun hubException(operation: String, response: okhttp3.Response): HubHttpException =
        HubHttpException(response.code, "$operation ${response.body?.string()?.take(200).orEmpty()}")

    private fun InputStream.realign(openStream: () -> InputStream, offset: Long): InputStream {
        close()
        return openStream().also { it.skipFully(offset) }
    }

    private fun InputStream.skipFully(count: Long) {
        var remaining = count
        while (remaining > 0) {
            val skipped = skip(remaining)
            if (skipped <= 0) {
                if (read() == -1) throw IOException("EOF while skipping")
                remaining -= 1
            } else remaining -= skipped
        }
    }

    private fun InputStream.readFully(buf: ByteArray, count: Int) {
        var done = 0
        while (done < count) {
            val n = read(buf, done, count - done)
            if (n <= 0) throw IOException("EOF while reading chunk")
            done += n
        }
    }

    companion object {
        const val CHUNK_BYTES = 4 * 1024 * 1024
        private const val MAX_REALIGNS = 5
    }
}
