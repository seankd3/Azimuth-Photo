package app.azimuthphoto.mobile

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import androidx.work.Configuration
import app.azimuthphoto.mobile.backup.BackupScheduler

class App : Application(), Configuration.Provider {

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
        BackupScheduler.ensureScheduled(this)
    }

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().build()

    companion object {
        const val BACKUP_CHANNEL_ID = "backup"
    }
}
