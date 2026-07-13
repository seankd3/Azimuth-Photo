package app.azimuthphoto.mobile.backup

import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import androidx.work.Constraints
import androidx.work.BackoffPolicy
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import app.azimuthphoto.mobile.data.SettingsStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit

object BackupScheduler {
    private const val PERIODIC_WORK = "backup-periodic"
    private const val NOW_WORK = "backup-now"

    // Reading settings + enqueuing never touches the caller's thread (often the UI).
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    /** Periodic safety net + content-change trigger so new photos upload promptly. */
    fun ensureScheduled(context: Context) = scope.launch {
        val settings = SettingsStore.current(context)
        val request = PeriodicWorkRequestBuilder<BackupWorker>(1, TimeUnit.HOURS)
            .setConstraints(constraints(settings.wifiOnly, settings.chargingOnly))
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            PERIODIC_WORK, ExistingPeriodicWorkPolicy.UPDATE, request
        )
        scheduleContentTrigger(context)
    }

    /** Re-armed after every run: fires shortly after anything new lands in MediaStore. */
    fun scheduleContentTrigger(context: Context) = scope.launch {
        val settings = SettingsStore.current(context)
        val request = OneTimeWorkRequestBuilder<BackupWorker>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(
                        if (settings.wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED
                    )
                    .setRequiresCharging(settings.chargingOnly)
                    .addContentUriTrigger(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true)
                    .addContentUriTrigger(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, true)
                    .setTriggerContentUpdateDelay(30, TimeUnit.SECONDS)
                    .setTriggerContentMaxDelay(5, TimeUnit.MINUTES)
                    .build()
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "backup-content-trigger", ExistingWorkPolicy.REPLACE, request
        )
    }

    fun runNow(context: Context) = scope.launch {
        val settings = SettingsStore.current(context)
        val request = OneTimeWorkRequestBuilder<BackupWorker>()
            .setConstraints(constraints(settings.wifiOnly, settings.chargingOnly))
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            NOW_WORK, ExistingWorkPolicy.REPLACE, request
        )
    }

    private fun constraints(wifiOnly: Boolean, chargingOnly: Boolean) = Constraints.Builder()
        .setRequiredNetworkType(if (wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
        .setRequiresCharging(chargingOnly)
        .build()
}
