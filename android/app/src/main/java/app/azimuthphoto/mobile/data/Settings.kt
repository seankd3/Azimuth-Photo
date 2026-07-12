package app.azimuthphoto.mobile.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "settings")

data class AppSettings(
    val serverUrl: String,
    val backupEnabled: Boolean,
    val wifiOnly: Boolean,
    val backupVideos: Boolean,
    /** MediaStore bucket ids selected for backup; empty = all buckets. */
    val backupBuckets: Set<String>,
    val freeUpSpaceEnabled: Boolean,
    /** Backed-up media older than this many days is quietly removed from the device. */
    val keepDays: Int,
)

object SettingsStore {
    const val DEFAULT_SERVER_URL = "http://100.102.150.104:8000"

    private val SERVER_URL = stringPreferencesKey("server_url")
    private val BACKUP_ENABLED = booleanPreferencesKey("backup_enabled")
    private val WIFI_ONLY = booleanPreferencesKey("wifi_only")
    private val BACKUP_VIDEOS = booleanPreferencesKey("backup_videos")
    private val BACKUP_BUCKETS = stringSetPreferencesKey("backup_buckets")
    private val FREE_UP_SPACE = booleanPreferencesKey("free_up_space")
    private val KEEP_DAYS = intPreferencesKey("keep_days")

    fun flow(context: Context): Flow<AppSettings> =
        context.dataStore.data.map { p ->
            AppSettings(
                serverUrl = (p[SERVER_URL] ?: DEFAULT_SERVER_URL).trimEnd('/'),
                backupEnabled = p[BACKUP_ENABLED] ?: true,
                wifiOnly = p[WIFI_ONLY] ?: false,
                backupVideos = p[BACKUP_VIDEOS] ?: true,
                backupBuckets = p[BACKUP_BUCKETS] ?: emptySet(),
                freeUpSpaceEnabled = p[FREE_UP_SPACE] ?: false,
                keepDays = p[KEEP_DAYS] ?: 30,
            )
        }

    suspend fun current(context: Context): AppSettings = flow(context).first()

    suspend fun setServerUrl(context: Context, url: String) =
        context.dataStore.edit { it[SERVER_URL] = url.trim().trimEnd('/') }

    suspend fun setBackupEnabled(context: Context, enabled: Boolean) =
        context.dataStore.edit { it[BACKUP_ENABLED] = enabled }

    suspend fun setWifiOnly(context: Context, wifiOnly: Boolean) =
        context.dataStore.edit { it[WIFI_ONLY] = wifiOnly }

    suspend fun setBackupVideos(context: Context, enabled: Boolean) =
        context.dataStore.edit { it[BACKUP_VIDEOS] = enabled }

    suspend fun setBackupBuckets(context: Context, buckets: Set<String>) =
        context.dataStore.edit { it[BACKUP_BUCKETS] = buckets }

    suspend fun setFreeUpSpace(context: Context, enabled: Boolean) =
        context.dataStore.edit { it[FREE_UP_SPACE] = enabled }

    suspend fun setKeepDays(context: Context, days: Int) =
        context.dataStore.edit { it[KEEP_DAYS] = days }
}
