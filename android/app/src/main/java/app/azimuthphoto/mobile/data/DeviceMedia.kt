package app.azimuthphoto.mobile.data

import android.content.ContentUris
import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

data class MediaItem(
    val id: Long,
    val uri: Uri,
    val isVideo: Boolean,
    val dateTakenMs: Long,
    val bucketId: String,
    val bucketName: String,
    val displayName: String,
    val sizeBytes: Long,
    val width: Int,
    val height: Int,
    val durationMs: Long,
    val relativePath: String,
) {
    val day: LocalDate
        get() = Instant.ofEpochMilli(dateTakenMs).atZone(ZoneId.systemDefault()).toLocalDate()

    val isRaw: Boolean get() = displayName.endsWith(".dng", ignoreCase = true)

    /**
     * RAW+JPEG twins share this key. Pixel names the pair with per-format
     * suffixes — PXL_….RAW-01.jpg / PXL_….RAW-02.ORIGINAL.dng (Top Shot uses
     * TS-nnn-…) — so strip those after dropping the extension.
     */
    val shotKey: String
        get() {
            val base = displayName.substringBeforeLast('.').lowercase()
                .replace(PIXEL_PAIR_SUFFIX, "")
            return "$bucketId/$base"
        }

    private companion object {
        val PIXEL_PAIR_SUFFIX = Regex("\\.(raw|ts-\\d+)-\\d+(\\.original)?$")
    }
}

data class MediaBucket(val id: String, val name: String, val count: Int)

object DeviceMedia {

    /** Hide a RAW twin when its rendered JPEG is present; keep solo DNGs visible. */
    fun collapseRawPairs(items: List<MediaItem>): List<MediaItem> {
        val jpegShots = items.asSequence()
            .filterNot { it.isRaw }
            .map { it.shotKey }
            .toHashSet()
        return items.filterNot { it.isRaw && it.shotKey in jpegShots }
    }

    private val PROJECTION = arrayOf(
        MediaStore.Files.FileColumns._ID,
        MediaStore.Files.FileColumns.MEDIA_TYPE,
        MediaStore.Files.FileColumns.DATE_TAKEN,
        MediaStore.Files.FileColumns.DATE_ADDED,
        MediaStore.Files.FileColumns.BUCKET_ID,
        MediaStore.Files.FileColumns.BUCKET_DISPLAY_NAME,
        MediaStore.Files.FileColumns.DISPLAY_NAME,
        MediaStore.Files.FileColumns.SIZE,
        MediaStore.Files.FileColumns.WIDTH,
        MediaStore.Files.FileColumns.HEIGHT,
        MediaStore.Files.FileColumns.DURATION,
        MediaStore.Files.FileColumns.RELATIVE_PATH,
    )

    private val COLLECTION: Uri = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL)

    private const val MEDIA_SELECTION =
        "(${MediaStore.Files.FileColumns.MEDIA_TYPE} = ${MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE}" +
            " OR ${MediaStore.Files.FileColumns.MEDIA_TYPE} = ${MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO})"

    /** All device photos & videos, newest first. */
    suspend fun queryAll(context: Context, sinceAddedSec: Long = 0): List<MediaItem> =
        query(context, sinceAddedSec, trashedOnly = false)

    /** MediaStore items currently in the system trash. */
    suspend fun queryTrashed(context: Context): List<MediaItem> =
        query(context, sinceAddedSec = 0, trashedOnly = true)

    private suspend fun query(
        context: Context,
        sinceAddedSec: Long,
        trashedOnly: Boolean,
    ): List<MediaItem> =
        withContext(Dispatchers.IO) {
            val items = ArrayList<MediaItem>(4096)
            val selection = if (sinceAddedSec > 0) {
                "$MEDIA_SELECTION AND ${MediaStore.Files.FileColumns.DATE_ADDED} > $sinceAddedSec"
            } else MEDIA_SELECTION
            val sortOrder = "CASE WHEN ${MediaStore.Files.FileColumns.DATE_TAKEN} > 0 THEN " +
                    "${MediaStore.Files.FileColumns.DATE_TAKEN} ELSE " +
                    "${MediaStore.Files.FileColumns.DATE_ADDED}*1000 END DESC, " +
                    "${MediaStore.Files.FileColumns._ID} DESC"
            val cursor = if (trashedOnly) {
                context.contentResolver.query(
                    COLLECTION,
                    PROJECTION,
                    Bundle().apply {
                        putString(ContentResolver.QUERY_ARG_SQL_SELECTION, selection)
                        putString(ContentResolver.QUERY_ARG_SQL_SORT_ORDER, sortOrder)
                        putInt(MediaStore.QUERY_ARG_MATCH_TRASHED, MediaStore.MATCH_ONLY)
                    },
                    null,
                )
            } else {
                context.contentResolver.query(
                    COLLECTION,
                    PROJECTION,
                    selection,
                    null,
                    sortOrder,
                )
            }
            cursor?.use { c ->
                val iId = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns._ID)
                val iType = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.MEDIA_TYPE)
                val iTaken = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.DATE_TAKEN)
                val iAdded = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.DATE_ADDED)
                val iBucket = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.BUCKET_ID)
                val iBucketName = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.BUCKET_DISPLAY_NAME)
                val iName = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.DISPLAY_NAME)
                val iSize = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.SIZE)
                val iW = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.WIDTH)
                val iH = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.HEIGHT)
                val iDur = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.DURATION)
                val iPath = c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.RELATIVE_PATH)
                while (c.moveToNext()) {
                    val id = c.getLong(iId)
                    val isVideo =
                        c.getInt(iType) == MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO
                    val taken = c.getLong(iTaken).takeIf { it > 0 } ?: (c.getLong(iAdded) * 1000)
                    items.add(
                        MediaItem(
                            id = id,
                            uri = ContentUris.withAppendedId(
                                if (isVideo) MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                                else MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                                id,
                            ),
                            isVideo = isVideo,
                            dateTakenMs = taken,
                            bucketId = c.getString(iBucket) ?: "",
                            bucketName = c.getString(iBucketName) ?: "",
                            displayName = c.getString(iName) ?: "",
                            sizeBytes = c.getLong(iSize),
                            width = c.getInt(iW),
                            height = c.getInt(iH),
                            durationMs = c.getLong(iDur),
                            relativePath = c.getString(iPath) ?: "",
                        )
                    )
                }
            }
            items
        }

    suspend fun resolveId(context: Context, uri: Uri): Long? = withContext(Dispatchers.IO) {
        runCatching {
            context.contentResolver.query(
                uri,
                arrayOf(MediaStore.MediaColumns._ID),
                null,
                null,
                null,
            )?.use { cursor ->
                if (cursor.moveToFirst()) cursor.getLong(0) else null
            }
        }.getOrNull()
    }

    /** Distinct buckets (folders) with counts, for the backup-folder picker. */
    suspend fun queryBuckets(context: Context): List<MediaBucket> =
        withContext(Dispatchers.IO) {
            val counts = LinkedHashMap<String, Pair<String, Int>>()
            context.contentResolver.query(
                COLLECTION,
                arrayOf(
                    MediaStore.Files.FileColumns.BUCKET_ID,
                    MediaStore.Files.FileColumns.BUCKET_DISPLAY_NAME,
                ),
                MEDIA_SELECTION, null, null,
            )?.use { c ->
                while (c.moveToNext()) {
                    val id = c.getString(0) ?: continue
                    val name = c.getString(1) ?: ""
                    val prev = counts[id]
                    counts[id] = (prev?.first ?: name) to ((prev?.second ?: 0) + 1)
                }
            }
            counts.map { (id, v) -> MediaBucket(id, v.first, v.second) }
                .sortedByDescending { it.count }
        }
}
