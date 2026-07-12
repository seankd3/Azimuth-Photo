package app.azimuthphoto.mobile.ui

import android.app.Activity
import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.backup.BackupDb
import app.azimuthphoto.mobile.backup.BackupScheduler
import app.azimuthphoto.mobile.backup.BackupWorker
import app.azimuthphoto.mobile.backup.FreeUpSpace
import app.azimuthphoto.mobile.data.SettingsStore
import kotlinx.coroutines.launch
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
fun SettingsScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val settings by SettingsStore.flow(context).collectAsState(initial = null)
    val progress by BackupWorker.progress.collectAsState()
    var counts by remember { mutableStateOf<Map<String, Int>>(emptyMap()) }
    var freedCount by remember { mutableStateOf<Int?>(null) }
    LaunchedEffect(progress) {
        counts = withContext(Dispatchers.IO) { BackupDb.get(context).countByState() }
    }

    val s = settings ?: return
    var serverUrl by remember(s.serverUrl) { mutableStateOf(s.serverUrl) }
    var deviceToken by remember(s.deviceToken) { mutableStateOf(s.deviceToken) }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 12.dp)
    ) {
        SectionTitle("Backup")
        if (progress.running) {
            Text(
                "Backing up ${progress.done} / ${progress.total} — ${progress.currentName}",
                style = MaterialTheme.typography.bodySmall, color = TextSecondary,
            )
            Spacer(Modifier.height(6.dp))
            LinearProgressIndicator(
                progress = {
                    if (progress.total == 0) 0f else progress.done.toFloat() / progress.total
                },
                modifier = Modifier.fillMaxWidth(),
            )
        } else {
            val safe = (counts["uploaded"] ?: 0) + (counts["present"] ?: 0)
            Text(
                "$safe items backed up" +
                    (counts["failed"]?.takeIf { it > 0 }?.let { " · $it failed" } ?: ""),
                style = MaterialTheme.typography.bodySmall, color = TextSecondary,
            )
        }
        Spacer(Modifier.height(10.dp))

        ToggleRow("Back up automatically", s.backupEnabled) {
            scope.launch { SettingsStore.setBackupEnabled(context, it) }
        }
        ToggleRow("Include videos", s.backupVideos) {
            scope.launch { SettingsStore.setBackupVideos(context, it) }
        }
        ToggleRow("Wi-Fi only", s.wifiOnly) {
            scope.launch { SettingsStore.setWifiOnly(context, it) }
        }
        TextButton(onClick = { BackupScheduler.runNow(context) }) { Text("Back up now") }

        HorizontalDivider(Modifier.padding(vertical = 14.dp), color = PanelHigh)

        SectionTitle("Free up space")
        ToggleRow(
            "Remove backed-up media after ${s.keepDays} days",
            s.freeUpSpaceEnabled,
        ) { enabled ->
            scope.launch { SettingsStore.setFreeUpSpace(context, enabled) }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            listOf(30, 90, 365).forEach { days ->
                TextButton(onClick = { scope.launch { SettingsStore.setKeepDays(context, days) } }) {
                    Text(
                        "$days d",
                        color = if (s.keepDays == days) Accent else TextSecondary,
                    )
                }
            }
        }
        TextButton(onClick = {
            scope.launch { freedCount = FreeUpSpace.run(context as Activity) }
        }) { Text("Free up now") }
        freedCount?.let { Text("Freed up $it items", style = MaterialTheme.typography.bodySmall, color = TextSecondary) }
        if (!FreeUpSpace.hasManageMedia(context)) {
            Text(
                "Grant “Manage media” so cleanup can run silently.",
                style = MaterialTheme.typography.bodySmall, color = TextSecondary,
            )
            TextButton(onClick = {
                context.startActivity(
                    Intent(Settings.ACTION_REQUEST_MANAGE_MEDIA).setData(
                        android.net.Uri.parse("package:${context.packageName}")
                    )
                )
            }) { Text("Open settings") }
        }

        HorizontalDivider(Modifier.padding(vertical = 14.dp), color = PanelHigh)

        SectionTitle("Server")
        OutlinedTextField(
            value = serverUrl,
            onValueChange = { serverUrl = it },
            label = { Text("Hub URL") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = deviceToken,
            onValueChange = { deviceToken = it },
            label = { Text("Device token (optional)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        TextButton(onClick = {
            scope.launch {
                SettingsStore.setServerUrl(context, serverUrl)
                SettingsStore.setDeviceToken(context, deviceToken)
            }
        }) {
            Text("Save")
        }
        Spacer(Modifier.height(40.dp))
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleMedium, color = TextPrimary)
    Spacer(Modifier.height(8.dp))
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = TextPrimary,
            modifier = Modifier.weight(1f),
        )
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
