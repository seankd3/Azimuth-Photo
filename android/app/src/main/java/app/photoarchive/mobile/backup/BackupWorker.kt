package app.photoarchive.mobile.backup

import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.ForegroundInfo
import androidx.work.WorkerParameters
import app.photoarchive.mobile.App
import app.photoarchive.mobile.R
import app.photoarchive.mobile.data.DeviceMedia
import app.photoarchive.mobile.data.MediaItem
import app.photoarchive.mobile.data.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.io.IOException
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

data class BackupProgress(
    val running: Boolean = false,
    val total: Int = 0,
    val done: Int = 0,
    val currentName: String = "",
    val lastError: String? = null,
)

/**
 * Scans the device for photos/videos not yet on the hub, then hashes,
 * manifests, and uploads them with resumable chunks.
 */
class BackupWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val context = applicationContext
        // Content-URI triggers are one-shot; re-arm so the next photo also wakes us.
        BackupScheduler.scheduleContentTrigger(context)
        val settings = SettingsStore.current(context)
        if (!settings.backupEnabled) return Result.success()
        if (settings.wifiOnly && isMetered(context)) return Result.retry()

        val db = BackupDb.get(context)
        val client = SyncClient(settings.serverUrl)
        val known = db.allStates()

        val all = DeviceMedia.queryAll(context)
        val candidates = all.filter { item ->
            val stateOk = known[item.id] != BackupDb.STATE_UPLOADED &&
                known[item.id] != BackupDb.STATE_PRESENT
            val typeOk = !item.isVideo || settings.backupVideos
            val bucketOk = settings.backupBuckets.isEmpty() ||
                item.bucketId in settings.backupBuckets
            stateOk && typeOk && bucketOk && item.sizeBytes > 0
        }
        if (candidates.isEmpty()) {
            publish(BackupProgress(running = false))
            return Result.success()
        }

        setForeground(foregroundInfo("Backing up ${candidates.size} items"))
        var done = 0
        var failures = 0

        for (batch in candidates.chunked(MANIFEST_BATCH)) {
            // Hash the batch, declare it, then upload only what the hub is missing.
            val hashed = ArrayList<Pair<MediaItem, ManifestItem>>(batch.size)
            for (item in batch) {
                try {
                    publish(BackupProgress(true, candidates.size, done, item.displayName))
                    hashed.add(item to manifestItemFor(context, item))
                } catch (e: Exception) {
                    failures++
                    db.upsert(item.id, "", item.sizeBytes, BackupDb.STATE_FAILED)
                }
            }
            if (hashed.isEmpty()) continue

            val response = try {
                client.manifest(hashed.map { it.second })
            } catch (e: IOException) {
                publish(BackupProgress(false, candidates.size, done, lastError = e.message))
                return Result.retry()
            }
            val knownHashes = response.known.map { it.content_hash }.toSet()

            for ((item, manifest) in hashed) {
                try {
                    if (manifest.content_hash in knownHashes) {
                        db.upsert(item.id, manifest.content_hash, item.sizeBytes, BackupDb.STATE_PRESENT)
                    } else {
                        publish(BackupProgress(true, candidates.size, done, item.displayName))
                        client.upload(manifest.content_hash, item.sizeBytes, {
                            context.contentResolver.openInputStream(item.uri)
                                ?: throw IOException("cannot open ${item.uri}")
                        })
                        db.upsert(item.id, manifest.content_hash, item.sizeBytes, BackupDb.STATE_UPLOADED)
                    }
                } catch (e: IOException) {
                    failures++
                    db.upsert(item.id, manifest.content_hash, item.sizeBytes, BackupDb.STATE_FAILED)
                }
                done++
                setForeground(foregroundInfo("Backing up $done / ${candidates.size}"))
            }
        }

        publish(BackupProgress(running = false, total = candidates.size, done = done))
        FreeUpSpace.runIfEnabled(context)
        return if (failures > 0) Result.retry() else Result.success()
    }

    private suspend fun manifestItemFor(context: Context, item: MediaItem): ManifestItem {
        val resolver = context.applicationContext.contentResolver
        val contentHash = resolver.openInputStream(item.uri)!!.use {
            Hashing.contentHash(it, item.sizeBytes)
        }
        val fullHash = resolver.openInputStream(item.uri)!!.use { Hashing.fullHash(it) }
        val taken = Instant.ofEpochMilli(item.dateTakenMs).atZone(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))
        return ManifestItem(
            content_hash = contentHash,
            full_hash = fullHash,
            bytes = item.sizeBytes,
            filename = item.displayName.ifEmpty { "IMG_${item.id}" },
            date_taken = taken,
        )
    }

    private fun isMetered(context: Context): Boolean {
        val cm = context.getSystemService(android.net.ConnectivityManager::class.java)
        return cm.isActiveNetworkMetered
    }

    private fun publish(progress: BackupProgress) {
        progressFlow.value = progress
    }

    private fun foregroundInfo(text: String): ForegroundInfo {
        val notification = NotificationCompat.Builder(applicationContext, App.BACKUP_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("photoArchive backup")
            .setContentText(text)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        return ForegroundInfo(
            NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        )
    }

    companion object {
        const val MANIFEST_BATCH = 50
        const val NOTIFICATION_ID = 100

        /** Live progress for the UI; survives across worker runs in-process. */
        val progressFlow: MutableStateFlow<BackupProgress> = MutableStateFlow(BackupProgress())
        val progress: StateFlow<BackupProgress> get() = progressFlow
    }
}
