package app.azimuthphoto.mobile

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import androidx.work.Configuration
import app.azimuthphoto.mobile.backup.BackupScheduler
import app.azimuthphoto.mobile.data.SettingsStore
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.decode.VideoFrameDecoder
import coil.disk.DiskCache
import coil.memory.MemoryCache
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient

class App : Application(), Configuration.Provider, ImageLoaderFactory {

    // Kept warm from DataStore so every Coil request to the hub can carry the
    // paired device token — a secured hub 401s thumbnails without it.
    @Volatile private var hubUrl: HttpUrl? = null
    @Volatile private var hubToken: String = ""

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
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            SettingsStore.flow(this@App).collect { settings ->
                hubUrl = settings.serverUrl.toHttpUrlOrNull()
                hubToken = settings.deviceToken
            }
        }
    }

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().build()

    override fun newImageLoader(): ImageLoader = ImageLoader.Builder(this)
        .components { add(VideoFrameDecoder.Factory()) }
        .okHttpClient {
            OkHttpClient.Builder()
                .addInterceptor { chain ->
                    val hub = hubUrl
                    val token = hubToken
                    val url = chain.request().url
                    val isHub = hub != null && url.scheme == hub.scheme &&
                        url.host == hub.host && url.port == hub.port
                    if (isHub && token.isNotBlank()) {
                        chain.proceed(
                            chain.request().newBuilder().header("X-Device-Token", token).build()
                        )
                    } else {
                        chain.proceed(chain.request())
                    }
                }
                .build()
        }
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
