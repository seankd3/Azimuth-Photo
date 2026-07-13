package app.azimuthphoto.mobile.ui

import android.app.Activity
import android.content.Intent
import android.provider.MediaStore
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.rounded.CloudDone
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.backup.BackupDb
import app.azimuthphoto.mobile.backup.BackupScheduler
import app.azimuthphoto.mobile.backup.BackupWorker
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.data.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
fun TimelineScreen(
    onOpenSettings: () -> Unit,
    onOpenTrash: () -> Unit,
) {
    val context = LocalContext.current
    var items by remember { mutableStateOf<List<MediaItem>?>(null) }
    var backupStates by remember { mutableStateOf<Map<Long, String>>(emptyMap()) }
    var viewerIndex by rememberSaveable { mutableStateOf<Int?>(null) }
    var selectedIds by rememberSaveable(
        stateSaver = listSaver(
            save = { it.toList() },
            restore = { it.toSet() },
        ),
    ) { mutableStateOf(emptySet<Long>()) }
    val progress by BackupWorker.progress.collectAsState()
    val settings by SettingsStore.flow(context).collectAsState(initial = null)

    LaunchedEffect(progress.running) {
        items = DeviceMedia.queryAll(context)
        backupStates = withContext(Dispatchers.IO) { BackupDb.get(context).allStates() }
    }

    val media = items
    if (media == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        return
    }

    val visible = remember(media) { DeviceMedia.collapseRawPairs(media) }
    val indexOf = remember(visible) { visible.withIndex().associate { it.value.id to it.index } }
    val backedUpIds = remember(backupStates) {
        backupStates.filterValues {
            it == BackupDb.STATE_UPLOADED || it == BackupDb.STATE_PRESENT
        }.keys
    }

    viewerIndex?.let { index ->
        ViewerScreen(items = visible, startIndex = index, onClose = { viewerIndex = null })
        return
    }

    BackHandler(enabled = selectedIds.isNotEmpty()) { selectedIds = emptySet() }
    val trashLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartIntentSenderForResult(),
    ) { selectedIds = emptySet() }

    Box(Modifier.fillMaxSize()) {
        if (visible.isEmpty()) {
            Text(
                "No photos yet — take one and it'll land here (and in your archive)",
                color = TextSecondary,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier
                    .align(Alignment.Center)
                    .padding(horizontal = 40.dp),
            )
        } else {
            MediaGrid(
                items = visible,
                selectedIds = selectedIds,
                onTap = { item ->
                    if (selectedIds.isNotEmpty()) {
                        selectedIds = selectedIds.toggle(item.id)
                    } else {
                        viewerIndex = indexOf[item.id]
                    }
                },
                onLongPress = { item -> selectedIds = selectedIds + item.id },
                backedUpIds = backedUpIds,
                showBackupState = true,
                contentPadding = PaddingValues(top = 72.dp),
                modifier = Modifier.fillMaxSize(),
            )
        }
        if (selectedIds.isNotEmpty()) {
            SelectionBar(
                count = selectedIds.size,
                onClose = { selectedIds = emptySet() },
                onShare = {
                    shareItems(context as Activity, visible.filter { it.id in selectedIds })
                    selectedIds = emptySet()
                },
                onTrash = {
                    val uris = visible.filter { it.id in selectedIds }.map { it.uri }
                    val pending = MediaStore.createTrashRequest(context.contentResolver, uris, true)
                    trashLauncher.launch(IntentSenderRequest.Builder(pending).build())
                },
                onBackup = {
                    BackupScheduler.runNow(context)
                    selectedIds = emptySet()
                },
                modifier = Modifier.align(Alignment.TopCenter),
            )
        } else {
            PhotosTopBar(
                backupEnabled = settings?.backupEnabled ?: true,
                allSafe = visible.isNotEmpty() && visible.all { it.id in backedUpIds },
                progress = progress,
                onOpenSettings = onOpenSettings,
                onOpenTrash = onOpenTrash,
                modifier = Modifier.align(Alignment.TopCenter),
            )
        }
    }
}

@Composable
private fun PhotosTopBar(
    backupEnabled: Boolean,
    allSafe: Boolean,
    progress: app.azimuthphoto.mobile.backup.BackupProgress,
    onOpenSettings: () -> Unit,
    onOpenTrash: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var menuVisible by remember { mutableStateOf(false) }
    val pulse = rememberInfiniteTransition(label = "backupPulse")
    val runningAlpha by pulse.animateFloat(
        initialValue = 0.45f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(650), RepeatMode.Reverse),
        label = "backupAlpha",
    )
    Row(
        modifier
            .fillMaxWidth()
            .background(Ink.copy(alpha = 0.85f))
            .statusBarsPadding(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "Azimuth",
            style = MaterialTheme.typography.titleLarge,
            color = TextPrimary,
            modifier = Modifier.padding(start = 16.dp).weight(1f),
        )
        if (progress.running) {
            Text(
                "${progress.done}/${progress.total}",
                style = MaterialTheme.typography.labelSmall,
                color = TextSecondary,
            )
        }
        IconButton(onClick = onOpenSettings) {
            Icon(
                when {
                    !backupEnabled -> Icons.Outlined.CloudOff
                    allSafe -> Icons.Rounded.CloudDone
                    else -> Icons.Outlined.CloudUpload
                },
                contentDescription = "Backup settings",
                tint = if (allSafe && backupEnabled) Positive else TextPrimary,
                modifier = Modifier.alpha(if (progress.running) runningAlpha else 1f),
            )
        }
        Box {
            IconButton(onClick = { menuVisible = true }) {
                Icon(Icons.Outlined.MoreVert, contentDescription = "More")
            }
            DropdownMenu(expanded = menuVisible, onDismissRequest = { menuVisible = false }) {
                DropdownMenuItem(
                    text = { Text("Trash") },
                    onClick = {
                        menuVisible = false
                        onOpenTrash()
                    },
                )
            }
        }
    }
}

@Composable
private fun SelectionBar(
    count: Int,
    onClose: () -> Unit,
    onShare: () -> Unit,
    onTrash: () -> Unit,
    onBackup: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier
            .fillMaxWidth()
            .background(Panel)
            .statusBarsPadding(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onClose) {
            Icon(Icons.Outlined.Close, contentDescription = "Clear selection")
        }
        Text(
            "$count selected",
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.weight(1f),
        )
        IconButton(onClick = onShare) {
            Icon(Icons.Outlined.Share, contentDescription = "Share")
        }
        IconButton(onClick = onTrash) {
            Icon(Icons.Outlined.Delete, contentDescription = "Move to trash")
        }
        IconButton(onClick = onBackup) {
            Icon(Icons.Outlined.CloudUpload, contentDescription = "Back up now")
        }
    }
}

private fun Set<Long>.toggle(id: Long): Set<Long> =
    if (id in this) this - id else this + id

private fun shareItems(activity: Activity, items: List<MediaItem>) {
    val type = when {
        items.all { it.isVideo } -> "video/*"
        items.all { !it.isVideo } -> "image/*"
        else -> "*/*"
    }
    val intent = Intent(Intent.ACTION_SEND_MULTIPLE).apply {
        this.type = type
        putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(items.map { it.uri }))
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    activity.startActivity(Intent.createChooser(intent, null))
}

fun formatDuration(ms: Long): String {
    val totalSec = ms / 1000
    val m = totalSec / 60
    val s = totalSec % 60
    return "%d:%02d".format(m, s)
}
