package app.azimuthphoto.mobile.backup

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

/** Client for the hub's FIELD_SPEC sync endpoints (manifest + resumable chunked upload). */
class SyncClient(private val baseUrl: String) {

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
            Request.Builder().url("$baseUrl/api/sync/manifest").post(body).build()
        ).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("manifest failed: HTTP ${resp.code}")
            return json.decodeFromString(resp.body!!.string())
        }
    }

    fun uploadOffset(contentHash: String): Long {
        http.newCall(
            Request.Builder().url("$baseUrl/api/sync/upload/$contentHash/status").build()
        ).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("status failed: HTTP ${resp.code}")
            return Regex("\"offset\"\\s*:\\s*(\\d+)")
                .find(resp.body!!.string())?.groupValues?.get(1)?.toLong() ?: 0L
        }
    }

    /**
     * Upload the stream in sequential chunks, resuming from the hub's committed offset.
     * The stream must start at byte 0; already-committed bytes are skipped locally.
     * Returns the hub image_id once complete, or null if the hub didn't report one.
     */
    fun upload(
        contentHash: String,
        totalBytes: Long,
        openStream: () -> InputStream,
        onProgress: (Long) -> Unit = {},
    ): Long? {
        var offset = uploadOffset(contentHash)
        var stream = openStream()
        try {
            stream.skipFully(offset)
            val buf = ByteArray(CHUNK_BYTES)
            while (offset < totalBytes) {
                val want = minOf(CHUNK_BYTES.toLong(), totalBytes - offset).toInt()
                stream.readFully(buf, want)
                val request = Request.Builder()
                    .url("$baseUrl/api/sync/upload/$contentHash")
                    .header("X-Offset", offset.toString())
                    .header("X-Total-Bytes", totalBytes.toString())
                    .post(buf.copyOf(want).toRequestBody(binType))
                    .build()
                http.newCall(request).execute().use { resp ->
                    when {
                        resp.code == 409 -> {
                            // Offset mismatch: hub tells us its committed offset; reopen and realign.
                            val actual = Regex("\"offset\"\\s*:\\s*(\\d+)")
                                .find(resp.body!!.string())?.groupValues?.get(1)?.toLong()
                                ?: throw IOException("409 without offset")
                            stream.close()
                            stream = openStream()
                            stream.skipFully(actual)
                            offset = actual
                        }
                        !resp.isSuccessful ->
                            throw IOException("upload failed: HTTP ${resp.code} ${resp.body?.string()?.take(200)}")
                        else -> {
                            val text = resp.body!!.string()
                            val imageId = Regex("\"image_id\"\\s*:\\s*(\\d+)")
                                .find(text)?.groupValues?.get(1)?.toLong()
                            if (imageId != null) return imageId
                            offset += want
                            onProgress(offset)
                        }
                    }
                }
            }
            return null
        } finally {
            stream.close()
        }
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
    }
}
