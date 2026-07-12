package app.azimuthphoto.mobile.backup

import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

object BackupScheduler {
    private const val PERIODIC_WORK = "backup-periodic"
    private const val NOW_WORK = "backup-now"

    /** Periodic safety net + content-change trigger so new photos upload promptly. */
    fun ensureScheduled(context: Context) {
        val request = PeriodicWorkRequestBuilder<BackupWorker>(1, TimeUnit.HOURS)
            .setConstraints(constraints(wifiOnly = false))
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            PERIODIC_WORK, ExistingPeriodicWorkPolicy.KEEP, request
        )
        scheduleContentTrigger(context)
    }

    /** Re-armed after every run: fires shortly after anything new lands in MediaStore. */
    fun scheduleContentTrigger(context: Context) {
        val request = OneTimeWorkRequestBuilder<BackupWorker>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .addContentUriTrigger(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true)
                    .addContentUriTrigger(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, true)
                    .setTriggerContentUpdateDelay(30, TimeUnit.SECONDS)
                    .setTriggerContentMaxDelay(5, TimeUnit.MINUTES)
                    .build()
            )
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "backup-content-trigger", ExistingWorkPolicy.REPLACE, request
        )
    }

    fun runNow(context: Context) {
        val request = OneTimeWorkRequestBuilder<BackupWorker>()
            .setConstraints(constraints(wifiOnly = false))
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            NOW_WORK, ExistingWorkPolicy.REPLACE, request
        )
    }

    private fun constraints(wifiOnly: Boolean) = Constraints.Builder()
        .setRequiredNetworkType(if (wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
        .build()
}
