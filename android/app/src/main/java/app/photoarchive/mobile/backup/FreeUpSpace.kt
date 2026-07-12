package app.photoarchive.mobile.backup

import android.app.Activity
import android.content.ContentUris
import android.content.Context
import android.provider.MediaStore
import app.photoarchive.mobile.data.DeviceMedia
import app.photoarchive.mobile.data.SettingsStore

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

    /** Re-check eligibility against the hub, then trash the aged local copies. */
    suspend fun run(activity: Activity): Int {
        val settings = SettingsStore.current(activity)
        if (!settings.freeUpSpaceEnabled) return 0
        val db = BackupDb.get(activity)
        val states = db.allStates()
        val cutoffMs = System.currentTimeMillis() - settings.keepDays * 24L * 3600 * 1000

        val eligible = DeviceMedia.queryAll(activity).filter { item ->
            val state = states[item.id]
            (state == BackupDb.STATE_UPLOADED || state == BackupDb.STATE_PRESENT) &&
                item.dateTakenMs < cutoffMs
        }.take(MAX_BATCH)
        if (eligible.isEmpty()) return 0

        // Safety: never trash anything the hub doesn't confirm it has right now.
        val client = SyncClient(settings.serverUrl)
        val confirmed = try {
            val response = client.manifest(
                eligible.map { item ->
                    ManifestItem(
                        content_hash = hashFor(activity, item.id) ?: return 0,
                        bytes = item.sizeBytes,
                        filename = item.displayName.ifEmpty { "IMG_${item.id}" },
                    )
                }
            )
            response.known.map { it.content_hash }.toSet()
        } catch (e: Exception) {
            return 0
        }

        val uris = eligible
            .filter { hashFor(activity, it.id) in confirmed }
            .map {
                ContentUris.withAppendedId(
                    if (it.isVideo) MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                    else MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    it.id,
                )
            }
        if (uris.isEmpty()) return 0

        val pending = MediaStore.createTrashRequest(activity.contentResolver, uris, true)
        activity.startIntentSenderForResult(pending.intentSender, REQUEST_CODE, null, 0, 0, 0)
        return uris.size
    }

    private val hashCache = HashMap<Long, String?>()

    private fun hashFor(context: Context, mediaId: Long): String? =
        hashCache.getOrPut(mediaId) {
            BackupDb.get(context).readableDatabase.rawQuery(
                "SELECT content_hash FROM items WHERE media_id = ?",
                arrayOf(mediaId.toString()),
            ).use { if (it.moveToFirst()) it.getString(0).ifEmpty { null } else null }
        }

    /** Worker-side hook: no Activity available, so just no-op for now. */
    fun runIfEnabled(context: Context) = Unit
}
