package app.azimuthphoto.mobile.backup

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayInputStream

class SyncClientTest {
    private lateinit var server: MockWebServer
    private lateinit var client: SyncClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = SyncClient(server.url("/").toString().removeSuffix("/"))
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun freshUploadSendsSequentialChunksAndReturnsImageId() {
        val payload = payload()
        server.enqueue(jsonResponse("""{"offset":0}"""))
        server.enqueue(jsonResponse("""{"offset":${SyncClient.CHUNK_BYTES}}"""))
        server.enqueue(jsonResponse("""{"image_id":42}"""))

        val imageId = client.upload("fresh", payload.size.toLong(), { ByteArrayInputStream(payload) })

        assertEquals(42L, imageId)
        assertEquals("/api/sync/upload/fresh/status", server.takeRequest().path)

        val firstChunk = server.takeRequest()
        assertEquals("0", firstChunk.getHeader("X-Offset"))
        assertEquals(payload.size.toString(), firstChunk.getHeader("X-Total-Bytes"))
        assertArrayEquals(payload.copyOfRange(0, SyncClient.CHUNK_BYTES), firstChunk.body.readByteArray())

        val secondChunk = server.takeRequest()
        assertEquals(SyncClient.CHUNK_BYTES.toString(), secondChunk.getHeader("X-Offset"))
        assertEquals(payload.size.toString(), secondChunk.getHeader("X-Total-Bytes"))
        assertArrayEquals(payload.copyOfRange(SyncClient.CHUNK_BYTES, payload.size), secondChunk.body.readByteArray())
    }

    @Test
    fun resumedUploadStartsAtServerOffsetAndSkipsCommittedBytes() {
        val payload = payload()
        val resumeOffset = 64
        server.enqueue(jsonResponse("""{"offset":$resumeOffset}"""))
        server.enqueue(jsonResponse("""{"offset":${resumeOffset + SyncClient.CHUNK_BYTES}}"""))
        server.enqueue(jsonResponse("""{"image_id":84}"""))

        val imageId = client.upload("resumed", payload.size.toLong(), { ByteArrayInputStream(payload) })

        assertEquals(84L, imageId)
        server.takeRequest()

        val firstChunk = server.takeRequest()
        assertEquals(resumeOffset.toString(), firstChunk.getHeader("X-Offset"))
        assertArrayEquals(
            payload.copyOfRange(resumeOffset, resumeOffset + SyncClient.CHUNK_BYTES),
            firstChunk.body.readByteArray(),
        )

        val secondChunk = server.takeRequest()
        assertEquals((resumeOffset + SyncClient.CHUNK_BYTES).toString(), secondChunk.getHeader("X-Offset"))
        assertArrayEquals(
            payload.copyOfRange(resumeOffset + SyncClient.CHUNK_BYTES, payload.size),
            secondChunk.body.readByteArray(),
        )
    }

    @Test
    fun offsetMismatchRealignsAndCompletesUpload() {
        val payload = payload()
        val serverOffset = 50
        server.enqueue(jsonResponse("""{"offset":0}"""))
        server.enqueue(jsonResponse("""{"error":"offset-mismatch","offset":$serverOffset}""", 409))
        server.enqueue(jsonResponse("""{"offset":${serverOffset + SyncClient.CHUNK_BYTES}}"""))
        server.enqueue(jsonResponse("""{"image_id":126}"""))

        val imageId = client.upload("realign", payload.size.toLong(), { ByteArrayInputStream(payload) })

        assertEquals(126L, imageId)
        server.takeRequest()

        val rejectedChunk = server.takeRequest()
        assertEquals("0", rejectedChunk.getHeader("X-Offset"))

        val realignedChunk = server.takeRequest()
        assertEquals(serverOffset.toString(), realignedChunk.getHeader("X-Offset"))
        assertArrayEquals(
            payload.copyOfRange(serverOffset, serverOffset + SyncClient.CHUNK_BYTES),
            realignedChunk.body.readByteArray(),
        )

        val finalChunk = server.takeRequest()
        assertEquals((serverOffset + SyncClient.CHUNK_BYTES).toString(), finalChunk.getHeader("X-Offset"))
        assertArrayEquals(
            payload.copyOfRange(serverOffset + SyncClient.CHUNK_BYTES, payload.size),
            finalChunk.body.readByteArray(),
        )
    }

    @Test
    fun manifestParsesKnownAndMissingItems() {
        server.enqueue(
            jsonResponse(
                """{"missing":["missing-hash"],"known":[{"content_hash":"known-hash","image_id":321}]}""",
            ),
        )

        val response = client.manifest(
            listOf(ManifestItem("known-hash", bytes = 10, filename = "known.jpg")),
        )

        assertEquals(listOf("missing-hash"), response.missing)
        assertEquals(listOf(KnownItem("known-hash", 321)), response.known)
        assertEquals("/api/sync/manifest", server.takeRequest().path)
    }

    @Test
    fun verifiedPresentUsesByteProofEndpointAndParsesPresentSet() {
        server.enqueue(jsonResponse("""{"present":["hash-a"]}"""))

        val present = client.verifiedPresent(listOf("hash-a", "hash-b"))

        assertEquals(setOf("hash-a"), present)
        assertEquals("/api/sync/have", server.takeRequest().path)
    }

    @Test
    fun completedBytesWithoutImageIdThrows() {
        val payload = ByteArray(100)
        server.enqueue(jsonResponse("""{"offset":0}"""))
        server.enqueue(jsonResponse("""{"offset":${payload.size}}"""))

        try {
            client.upload("unfinished", payload.size.toLong(), { ByteArrayInputStream(payload) })
            fail("Expected upload to require hub finalization")
        } catch (e: java.io.IOException) {
            assertEquals("upload reached end without hub finalize", e.message)
        }
    }

    private fun payload() = ByteArray(SyncClient.CHUNK_BYTES + 100) { (it % 251).toByte() }

    private fun jsonResponse(body: String, code: Int = 200) = MockResponse()
        .setResponseCode(code)
        .setHeader("Content-Type", "application/json")
        .setBody(body)
}
