package app.azimuthphoto.mobile.backup

import org.bouncycastle.crypto.digests.Blake2bDigest
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * FIELD_SPEC content identity: BLAKE2b-128 over file[0:min(size, 8 MiB)]
 * followed by the file size as one unsigned 8-byte little-endian integer.
 * Must match features/sync/hashing.py on the hub exactly.
 */
object Hashing {
    private const val PREFIX_BYTES = 8 * 1024 * 1024
    private const val DIGEST_BYTES = 16

    fun contentHash(stream: InputStream, fileSize: Long): String {
        val digest = Blake2bDigest(DIGEST_BYTES * 8)
        val buf = ByteArray(256 * 1024)
        var remaining = PREFIX_BYTES
        while (remaining > 0) {
            val n = stream.read(buf, 0, minOf(buf.size, remaining))
            if (n <= 0) break
            digest.update(buf, 0, n)
            remaining -= n
        }
        val sizeBytes = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(fileSize).array()
        digest.update(sizeBytes, 0, 8)
        val out = ByteArray(DIGEST_BYTES)
        digest.doFinal(out, 0)
        return out.joinToString("") { "%02x".format(it) }
    }

    fun fullHash(stream: InputStream): String {
        val digest = Blake2bDigest(DIGEST_BYTES * 8)
        val buf = ByteArray(1024 * 1024)
        while (true) {
            val n = stream.read(buf)
            if (n <= 0) break
            digest.update(buf, 0, n)
        }
        val out = ByteArray(DIGEST_BYTES)
        digest.doFinal(out, 0)
        return out.joinToString("") { "%02x".format(it) }
    }
}
