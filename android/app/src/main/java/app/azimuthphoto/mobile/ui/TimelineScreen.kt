package app.azimuthphoto.mobile.ui

import android.app.Activity
import android.content.Intent
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import android.os.Looper
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
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
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
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import app.azimuthphoto.mobile.backup.BackupDb
import app.azimuthphoto.mobile.backup.BackupScheduler
import app.azimuthphoto.mobile.backup.BackupWorker
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveFolder
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.data.SettingsStore
import app.azimuthphoto.mobile.data.Shelf
import app.azimuthphoto.mobile.data.TimelineEntry
import app.azimuthphoto.mobile.data.UnifiedTimeline
import app.azimuthphoto.mobile.data.ViewerMedia
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

private sealed class Scope {
    data object All : Scope()
    data object NotBackedUp : Scope()
    data class Shelf(val shelf: app.azimuthphoto.mobile.data.Shelf) : Scope()
}

private const val HUB_PAGE = 120

/**
 * One Google-Photos timeline: device photos (offline-safe) merged with hub photos
 * not already on the device. Scope chips focus on a single source (a shelf) or the
 * not-yet-backed-up tail. Device cells select/share/trash; hub cells just open.
 */
@Composable
fun TimelineScreen(
    onOpenSettings: () -> Unit,
    onOpenTrash: () -> Unit,
    onImmersive: (Boolean) -> Unit = {},
) {
    val context = LocalContext.current
    val settings by SettingsStore.flow(context).collectAsState(initial = null)
    val progress by BackupWorker.progress.collectAsState()
    val api = remember(settings?.serverUrl) { settings?.serverUrl?.let { ArchiveApi(it) } }

    var device by remember { mutableStateOf<List<MediaItem>?>(null) }
    var rawTwins by remember { mutableStateOf<Map<String, MediaItem>>(emptyMap()) }
    var deviceKeys by remember { mutableStateOf<Set<String>>(emptySet()) }
    var backupStates by remember { mutableStateOf<Map<Long, String>>(emptyMap()) }
    var shelves by remember { mutableStateOf<List<Shelf>>(emptyList()) }
    var scope by rememberSaveable { mutableStateOf(0) } // 0 All, 1 NotBackedUp, 2+ shelf index
    var hub by remember { mutableStateOf<List<ArchiveImage>>(emptyList()) }
    var hubOffset by remember { mutableStateOf(0) }
    var hubExhausted by remember { mutableStateOf(false) }
    var hubOffline by remember { mutableStateOf(false) }
    var wantMore by remember { mutableStateOf(false) }
    var loadTick by remember { mutableStateOf(0) }
    var mediaStoreChanges by remember { mutableStateOf(0) }
    var viewerSession by remember { mutableStateOf<Pair<List<ViewerMedia>, Int>?>(null) }
    var selectedIds by rememberSaveable(
        stateSaver = listSaver(save = { it.toList() }, restore = { it.toSet() }),
    ) { mutableStateOf(emptySet<Long>()) }

    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(context.contentResolver) {
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean, uri: Uri?) {
                super.onChange(selfChange, uri)
                mediaStoreChanges++
            }
        }
        context.contentResolver.registerContentObserver(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            true,
            observer,
        )
        context.contentResolver.registerContentObserver(
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            true,
            observer,
        )
        onDispose { context.contentResolver.unregisterContentObserver(observer) }
    }
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) loadTick++
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    LaunchedEffect(mediaStoreChanges) {
        if (mediaStoreChanges > 0) {
            delay(500)
            loadTick++
        }
    }

    LaunchedEffect(progress.running, loadTick) {
        val all = DeviceMedia.queryAll(context)
        rawTwins = rawTwinsByShotKey(all)
        deviceKeys = UnifiedTimeline.deviceKeys(all) // pre-collapse: DNG twins count
        device = DeviceMedia.collapseRawPairs(all)
        backupStates = withContext(Dispatchers.IO) { BackupDb.get(context).allStates() }
    }
    LaunchedEffect(api) {
        if (api != null) shelves = runCatching { api.shelves() }.getOrDefault(emptyList())
    }

    val activeScope: Scope = when {
        scope == 0 -> Scope.All
        scope == 1 -> Scope.NotBackedUp
        else -> shelves.getOrNull(scope - 2)?.let { Scope.Shelf(it) } ?: Scope.All
    }
    val folderPaths = (activeScope as? Scope.Shelf)?.shelf?.paths.orEmpty()
    val folderKey = folderPaths.joinToString("|")
    val usesHub = api != null && activeScope !is Scope.NotBackedUp

    // (Re)load the hub source whenever the scope, server, or a manual retry changes.
    LaunchedEffect(folderKey, usesHub, api, loadTick) {
        hub = emptyList(); hubOffset = 0; hubExhausted = false; hubOffline = false; wantMore = false
        if (usesHub && api != null) {
            runCatching { api.page(offset = 0, limit = HUB_PAGE, folders = folderPaths) }
                .onSuccess { hub = it.images; hubOffset = it.images.size; hubExhausted = it.images.isEmpty() }
                .onFailure { hubOffline = true }
        }
    }

    val deviceEntries = remember(device, backupStates, rawTwins) {
        val states = backupStates
        (device ?: emptyList()).map {
            TimelineEntry.Device(
                item = it,
                backedUp = states[it.id] == BackupDb.STATE_UPLOADED || states[it.id] == BackupDb.STATE_PRESENT,
                hasRaw = rawTwins.containsKey(it.shotKey),
            )
        }
    }
    val entries: List<TimelineEntry> = remember(activeScope, deviceEntries, hub) {
        when (activeScope) {
            is Scope.NotBackedUp -> deviceEntries.filter { !it.backedUp }
            is Scope.Shelf -> hub.map { TimelineEntry.Hub(it, UnifiedTimeline.hubMillis(it.date_taken)) }
            is Scope.All ->
                UnifiedTimeline.merge(deviceEntries, UnifiedTimeline.hubEntries(hub, deviceKeys))
        }
    }

    if (device == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }

    // One viewer over the whole merged stream — a phone shot and an archive
    // original sit in the same pager, swipe seamlessly, share the same chrome.
    val viewerMedia = remember(entries, rawTwins) {
        entries.map { entry ->
            when (entry) {
                is TimelineEntry.Device -> ViewerMedia.Local(
                    entry.item,
                    rawTwins[entry.item.shotKey]?.takeIf { it.id != entry.item.id },
                )
                is TimelineEntry.Hub -> ViewerMedia.Remote(entry.image)
            }
        }
    }
    val entryIndex = remember(entries) {
        entries.withIndex().associate { it.value.gridKey to it.index }
    }
    // Selection acts on device items only (you can't multi-trash a server photo from here).
    val selectedItems = remember(entries, selectedIds) {
        entries.mapNotNull { (it as? TimelineEntry.Device)?.item }.filter { it.id in selectedIds }
    }
    LaunchedEffect(viewerSession != null) { onImmersive(viewerSession != null) }
    DisposableEffect(Unit) { onDispose { onImmersive(false) } }
    BackHandler(enabled = selectedIds.isNotEmpty()) { selectedIds = emptySet() }
    val trashLocal = rememberMediaTrash(onTrashed = { loadTick++; selectedIds = emptySet() })

    val backedUpIds = remember(backupStates) {
        backupStates.filterValues { it == BackupDb.STATE_UPLOADED || it == BackupDb.STATE_PRESENT }.keys
    }

    Box(Modifier.fillMaxSize()) {
        if (entries.isEmpty()) {
            Text(
                emptyMessage(activeScope, hubOffline),
                color = TextSecondary,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.align(Alignment.Center).padding(horizontal = 40.dp),
            )
        } else {
            UnifiedGrid(
                entries = entries,
                api = api ?: ArchiveApi(SettingsStore.DEFAULT_SERVER_URL),
                selectedIds = selectedIds,
                onTapEntry = { entry ->
                    if (entry is TimelineEntry.Device && selectedIds.isNotEmpty()) {
                        selectedIds = selectedIds.toggle(entry.item.id)
                    } else {
                        entryIndex[entry.gridKey]?.let { viewerSession = viewerMedia to it }
                    }
                },
                onLongPressDevice = { item -> selectedIds = selectedIds + item.id },
                onNearEnd = { wantMore = true },
                contentPadding = PaddingValues(top = 108.dp),
                modifier = Modifier.fillMaxSize(),
            )
        }

        // One in-flight page at a time: load the next when the grid asks, then disarm.
        LaunchedEffect(wantMore, hubOffset, folderKey) {
            // Only page once the first load has landed (offset>0), one request at a time.
            if (wantMore) {
                if (usesHub && api != null && hubOffset > 0 && !hubExhausted && !hubOffline) {
                    val next = runCatching { api.page(offset = hubOffset, limit = HUB_PAGE, folders = folderPaths) }.getOrNull()
                    if (next == null) hubOffline = true
                    else if (next.images.isEmpty()) hubExhausted = true
                    else {
                        hub = (hub + next.images).distinctBy { it.id }
                        hubOffset += next.images.size
                    }
                }
                wantMore = false
            }
        }

        Column(Modifier.align(Alignment.TopCenter)) {
            if (selectedIds.isNotEmpty()) {
                SelectionBar(
                    count = selectedIds.size,
                    onClose = { selectedIds = emptySet() },
                    onShare = {
                        shareItems(context as Activity, selectedItems)
                        selectedIds = emptySet()
                    },
                    onTrash = { trashLocal(selectedItems.map { it.uri }) },
                    onBackup = { BackupScheduler.runNow(context); selectedIds = emptySet() },
                )
            } else {
                PhotosTopBar(
                    backupEnabled = settings?.backupEnabled ?: true,
                    allSafe = (device?.isNotEmpty() == true) && (device ?: emptyList()).all { it.id in backedUpIds },
                    progress = progress,
                    onOpenSettings = onOpenSettings,
                    onOpenTrash = onOpenTrash,
                )
                ScopeChips(
                    shelves = shelves,
                    selected = scope,
                    onSelect = { scope = it },
                    offline = hubOffline && activeScope is Scope.All,
                    onRetry = { loadTick++ },
                )
            }
        }

        viewerSession?.let { (items, index) ->
            ViewerScreen(
                items = items,
                startIndex = index,
                onClose = { viewerSession = null },
                api = api,
                onChanged = { loadTick++ },
            )
        }
    }

}

@Composable
private fun ScopeChips(
    shelves: List<Shelf>,
    selected: Int,
    onSelect: (Int) -> Unit,
    offline: Boolean,
    onRetry: () -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(Ink.copy(alpha = 0.85f))
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 10.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ScopeChip("All", selected == 0) { onSelect(0) }
        ScopeChip("Not backed up", selected == 1) { onSelect(1) }
        shelves.forEachIndexed { i, shelf ->
            ScopeChip(shelf.name, selected == i + 2) { onSelect(i + 2) }
        }
        if (offline) {
            Spacer(Modifier.width(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Outlined.CloudOff, contentDescription = null, tint = TextSecondary,
                    modifier = Modifier.padding(end = 4.dp),
                )
                Text("Archive offline", color = TextSecondary, style = MaterialTheme.typography.labelMedium)
                TextButton(onClick = onRetry) { Text("Retry") }
            }
        }
    }
}

@Composable
private fun ScopeChip(label: String, selected: Boolean, onClick: () -> Unit) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = { Text(label) },
        colors = FilterChipDefaults.filterChipColors(
            selectedContainerColor = PanelHigh,
            selectedLabelColor = TextPrimary,
        ),
    )
}

private fun emptyMessage(scope: Scope, offline: Boolean): String = when {
    scope is Scope.NotBackedUp -> "Everything on this device is backed up."
    offline -> "Archive offline — no photos on this device yet."
    scope is Scope.Shelf -> "Nothing in ${scope.shelf.name} yet."
    else -> "No photos yet — take one and it'll land here (and in your archive)."
}

@Composable
private fun PhotosTopBar(
    backupEnabled: Boolean,
    allSafe: Boolean,
    progress: app.azimuthphoto.mobile.backup.BackupProgress,
    onOpenSettings: () -> Unit,
    onOpenTrash: () -> Unit,
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
        Modifier.fillMaxWidth().background(Ink.copy(alpha = 0.85f)).statusBarsPadding(),
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
                    onClick = { menuVisible = false; onOpenTrash() },
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
) {
    Row(
        Modifier.fillMaxWidth().background(Panel).statusBarsPadding(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, contentDescription = "Clear selection") }
        Text("$count selected", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
        IconButton(onClick = onShare) { Icon(Icons.Outlined.Share, contentDescription = "Share") }
        IconButton(onClick = onTrash) { Icon(Icons.Outlined.Delete, contentDescription = "Move to trash") }
        IconButton(onClick = onBackup) { Icon(Icons.Outlined.CloudUpload, contentDescription = "Back up now") }
    }
}

private fun Set<Long>.toggle(id: Long): Set<Long> = if (id in this) this - id else this + id

private fun shareItems(activity: Activity, items: List<MediaItem>) {
    if (items.isEmpty()) return
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
    return "%d:%02d".format(totalSec / 60, totalSec % 60)
}
