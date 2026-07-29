package app.azimuthphoto.mobile.backup

import android.app.Activity
import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Quietly removes device copies of media the hub has confirmed, once they age
 * past the keep window. Uses MediaStore.createTrashRequest, which auto-approves
 * (no dialog) when the MANAGE_MEDIA special access is granted; must be launched
 * from an Activity, so this runs on app open rather than from the worker.
 */
object FreeUpSpace {
    private const val REQUEST_CODE = 4207
    const val MAX_BATCH = 300

    fun hasManageMedia(context: Context): Boolean =
        MediaStore.canManageMedia(context)

    /** What a "Free up now" would reclaim, so the user can see before they commit. */
    data class Preview(val count: Int, val bytes: Long)

    /** Hub-verified, aged-out local copies eligible for removal, with their sizes. */
    private suspend fun eligible(activity: Activity): List<Pair<Uri, Long>> =
        withContext(Dispatchers.IO) {
            val settings = SettingsStore.current(activity)
            if (!settings.freeUpSpaceEnabled) return@withContext emptyList()
            val states = BackupDb.get(activity).allStates()
            val cutoffMs = System.currentTimeMillis() - settings.keepDays * 24L * 3600 * 1000
            val aged = DeviceMedia.queryAll(activity).filter { item ->
                val state = states[item.id]
                (state == BackupDb.STATE_UPLOADED || state == BackupDb.STATE_PRESENT) &&
                    item.dateTakenMs < cutoffMs
            }.take(MAX_BATCH)
            val hashed = aged.mapNotNull { item -> hashFor(activity, item.id)?.let { item to it } }
            if (hashed.isEmpty()) return@withContext emptyList()

            // Deletion gate: /api/sync/have re-hashes the hub's live bytes
            // (TOPOLOGY: confirm the same complete bytes immediately before local
            // deletion). The manifest's "known" is stat-size only — never enough
            // to remove the phone's only copy.
            val confirmed = try {
                SyncClient(settings.serverUrl, settings.deviceToken.takeIf { it.isNotBlank() })
                    .verifiedPresent(hashed.map { it.second })
            } catch (e: Exception) {
                return@withContext emptyList()
            }

            hashed.filter { it.second in confirmed }.map { (item, _) ->
                ContentUris.withAppendedId(
                    if (item.isVideo) MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                    else MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    item.id,
                ) to item.sizeBytes
            }
        }

    /** Dry run: how many items (and bytes) "Free up now" would remove right now. */
    suspend fun preview(activity: Activity): Preview {
        if (!hasManageMedia(activity)) return Preview(0, 0)
        val e = eligible(activity)
        return Preview(e.size, e.sumOf { it.second })
    }

    /** Re-check eligibility against the hub, then trash the aged local copies. */
    suspend fun run(activity: Activity): Int {
        if (!hasManageMedia(activity)) return 0
        val uris = eligible(activity).map { it.first }
        if (uris.isEmpty()) return 0

        val pending = withContext(Dispatchers.IO) {
            MediaStore.createTrashRequest(activity.contentResolver, uris, true)
        }
        withContext(Dispatchers.Main) {
            activity.startIntentSenderForResult(pending.intentSender, REQUEST_CODE, null, 0, 0, 0)
        }
        return uris.size
    }

    private fun hashFor(context: Context, mediaId: Long): String? =
        BackupDb.get(context).readableDatabase.rawQuery(
            "SELECT content_hash FROM items WHERE media_id = ?",
            arrayOf(mediaId.toString()),
        ).use { if (it.moveToFirst()) it.getString(0).ifEmpty { null } else null }

    /** Worker-side hook: no Activity available, so just no-op for now. */
    fun runIfEnabled(context: Context) = Unit
}
