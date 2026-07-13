package app.azimuthphoto.mobile

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import androidx.work.Configuration
import app.azimuthphoto.mobile.backup.BackupScheduler
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.decode.VideoFrameDecoder
import coil.disk.DiskCache
import coil.memory.MemoryCache

class App : Application(), Configuration.Provider, ImageLoaderFactory {

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(
                BACKUP_CHANNEL_ID,
                "Backup",
                NotificationManager.IMPORTANCE_LOW,
            ).apply { description = "Photo backup progress" }
        )
        // BackupScheduler reads DataStore + enqueues on its own IO scope.
        BackupScheduler.ensureScheduled(this)
    }

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().build()

    override fun newImageLoader(): ImageLoader = ImageLoader.Builder(this)
        .components { add(VideoFrameDecoder.Factory()) }
        .memoryCache {
            MemoryCache.Builder(this)
                .maxSizePercent(0.25)
                .build()
        }
        .diskCache {
            DiskCache.Builder()
                .directory(cacheDir.resolve("image_cache"))
                .maxSizeBytes(512L * 1024L * 1024L)
                .build()
        }
        .crossfade(false)
        .respectCacheHeaders(false)
        .build()

    companion object {
        const val BACKUP_CHANNEL_ID = "backup"
    }
}
