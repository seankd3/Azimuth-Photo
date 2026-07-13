package app.azimuthphoto.mobile.backup

import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.ForegroundInfo
import androidx.work.WorkerParameters
import app.azimuthphoto.mobile.App
import app.azimuthphoto.mobile.R
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.data.SettingsStore
import android.util.Log
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
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

    override suspend fun doWork(): Result = uploadMutex.withLock {
        val context = applicationContext
        setForeground(foregroundInfo("Preparing backup"))
        // Content-URI triggers are one-shot; re-arm so the next photo also wakes us.
        BackupScheduler.scheduleContentTrigger(context)
        val settings = SettingsStore.current(context)
        if (!settings.backupEnabled) return Result.success()
        if (settings.wifiOnly && isMetered(context)) return Result.retry()

        val db = BackupDb.get(context)
        val client = SyncClient(settings.serverUrl, settings.deviceToken.takeIf { it.isNotBlank() })
        val known = db.allStates()

        val all = DeviceMedia.queryAll(context)
        val eligible = all.filter { item ->
            val stateOk = known[item.id] != BackupDb.STATE_UPLOADED &&
                known[item.id] != BackupDb.STATE_PRESENT
            val typeOk = !item.isVideo || settings.backupVideos
            val bucketOk = settings.backupBuckets.isEmpty() ||
                item.bucketId in settings.backupBuckets
            stateOk && typeOk && bucketOk
        }
        val zeroSizeSkipped = eligible.count { it.sizeBytes <= 0 }
        if (zeroSizeSkipped > 0) Log.i(TAG, "Skipped $zeroSizeSkipped zero-size media items")
        val candidates = eligible.filter { it.sizeBytes > 0 }
        if (candidates.isEmpty()) {
            publish(BackupProgress(running = false))
            return Result.success()
        }

        var done = 0
        var hasTransientFailure = false

        for (batch in candidates.chunked(MANIFEST_BATCH)) {
            // Hash the batch, declare it, then upload only what the hub is missing.
            val hashed = ArrayList<Pair<MediaItem, ManifestItem>>(batch.size)
            for (item in batch) {
                try {
                    publish(BackupProgress(true, candidates.size, done, item.displayName))
                    hashed.add(item to manifestItemFor(context, item))
                } catch (e: Exception) {
                    db.upsert(item.id, "", item.sizeBytes, BackupDb.STATE_FAILED)
                    done++
                }
            }
            if (hashed.isEmpty()) continue

            val response = try {
                client.manifest(hashed.map { it.second })
            } catch (e: IOException) {
                if (classifyBackupFailure(e) == BackupFailure.TRANSIENT) hasTransientFailure = true
                for ((item, manifest) in hashed) {
                    db.upsert(item.id, manifest.content_hash, item.sizeBytes, BackupDb.STATE_FAILED)
                    done++
                }
                publish(BackupProgress(true, candidates.size, done, lastError = e.message))
                continue
            }
            val knownHashes = response.known.associate { it.content_hash to it.image_id }

            for ((item, manifest) in hashed) {
                try {
                    if (manifest.content_hash in knownHashes) {
                        db.upsert(
                            item.id,
                            manifest.content_hash,
                            item.sizeBytes,
                            BackupDb.STATE_PRESENT,
                            knownHashes[manifest.content_hash],
                        )
                    } else {
                        publish(BackupProgress(true, candidates.size, done, item.displayName))
                        val hubImageId = client.upload(manifest.content_hash, item.sizeBytes, {
                            context.contentResolver.openInputStream(item.uri)
                                ?: throw IOException("cannot open ${item.uri}")
                        })
                        db.upsert(
                            item.id,
                            manifest.content_hash,
                            item.sizeBytes,
                            BackupDb.STATE_UPLOADED,
                            hubImageId,
                        )
                    }
                } catch (e: IOException) {
                    if (classifyBackupFailure(e) == BackupFailure.TRANSIENT) hasTransientFailure = true
                    db.upsert(item.id, manifest.content_hash, item.sizeBytes, BackupDb.STATE_FAILED)
                }
                done++
                setForeground(foregroundInfo("Backing up $done / ${candidates.size}"))
            }
        }

        publish(BackupProgress(running = false, total = candidates.size, done = done))
        FreeUpSpace.runIfEnabled(context)
        return if (hasTransientFailure) Result.retry() else Result.success()
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
            folder = PHONE_FOLDER,
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
            .setContentTitle("Azimuth Photo backup")
            .setContentText(text)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        return ForegroundInfo(
            NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        )
    }

    companion object {
        /** Phone shots live in their own tree on the hub. */
        const val PHONE_FOLDER = "Personal Photos"
        const val MANIFEST_BATCH = 50
        const val NOTIFICATION_ID = 100
        private const val TAG = "BackupWorker"
        private val uploadMutex = Mutex()

        /** Live progress for the UI; survives across worker runs in-process. */
        val progressFlow: MutableStateFlow<BackupProgress> = MutableStateFlow(BackupProgress())
        val progress: StateFlow<BackupProgress> get() = progressFlow
    }
}

internal enum class BackupFailure { PERMANENT, TRANSIENT }

internal fun classifyBackupFailure(error: IOException): BackupFailure =
    if (error is HubHttpException && error.code in 400..499) BackupFailure.PERMANENT
    else BackupFailure.TRANSIENT
