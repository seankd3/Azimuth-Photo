package app.azimuthphoto.mobile.ui

import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.MediaStore
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.rounded.VolumeOff
import androidx.compose.material.icons.rounded.VolumeUp
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.exifinterface.media.ExifInterface
import androidx.media3.common.Player
import androidx.media3.common.MediaItem as ExoMediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import app.azimuthphoto.mobile.ViewerActivity
import app.azimuthphoto.mobile.backup.BackupDb
import app.azimuthphoto.mobile.backup.BackupRecord
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.data.UnifiedTimeline
import app.azimuthphoto.mobile.data.ViewerMedia
import coil.compose.AsyncImage
import coil.request.ImageRequest
import java.io.File
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The one viewer. Every photo — on this phone or only in the archive — gets
 * identical gestures (pinch/double-tap zoom, one-finger pan, swipe-down
 * dismiss, swipe-between) and identical actions (share, edit, info, use as,
 * open with, delete). Local actions ride content URIs; remote actions ride the
 * hub API and a cached full-resolution render.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ViewerScreen(
    items: List<ViewerMedia>,
    startIndex: Int,
    onClose: () -> Unit,
    api: ArchiveApi? = null,
    onChanged: () -> Unit = {},
) {
    BackHandler(onBack = onClose)
    if (items.isEmpty()) {
        LaunchedEffect(Unit) { onClose() }
        return
    }
    // A saved index can outlive its list (process death, mutation) — never seed
    // the pager past the end or it throws on init.
    val safeStart = startIndex.coerceIn(0, (items.size - 1).coerceAtLeast(0))
    val pagerState = rememberPagerState(initialPage = safeStart) { items.size }
    val trashLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartIntentSenderForResult(),
    ) { result ->
        // Only leave the viewer / refresh the grid when the trash actually happened.
        if (result.resultCode == Activity.RESULT_OK) { onChanged(); onClose() }
    }
    var chromeVisible by remember { mutableStateOf(true) }
    var infoFor by remember { mutableStateOf<ViewerMedia?>(null) }
    var menuVisible by remember { mutableStateOf(false) }
    var confirmHubTrash by remember { mutableStateOf<ArchiveImage?>(null) }
    var busy by remember { mutableStateOf(false) }
    var currentZoom by remember { mutableFloatStateOf(1f) }
    val dismissY = remember { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val dismissFraction = (dismissY.value / 600f).coerceIn(0f, 1f)
    // Each new page starts unzoomed, so dismiss re-arms and the pager re-enables.
    LaunchedEffect(pagerState.currentPage) { currentZoom = 1f }

    /** Resolve a shareable content URI — local media directly, remote via cached render. */
    suspend fun shareableUri(media: ViewerMedia): Pair<Uri, String>? = when (media) {
        is ViewerMedia.Local -> media.item.uri to
            (context.contentResolver.getType(media.item.uri)
                ?: if (media.isVideo) "video/*" else "image/*")
        is ViewerMedia.Remote -> api?.let {
            runCatching {
                val file = it.downloadToCache(context, media.image)
                FileProvider.getUriForFile(context, "${context.packageName}.files", file) to
                    (if (media.isVideo) "video/*" else "image/jpeg")
            }.getOrNull()
        }
    }

    fun withShareable(media: ViewerMedia, action: (Uri, String) -> Unit) {
        scope.launch {
            busy = true
            try {
                val prepared = shareableUri(media)
                if (prepared == null) {
                    Toast.makeText(context, "Couldn't reach the archive", Toast.LENGTH_SHORT).show()
                } else {
                    action(prepared.first, prepared.second)
                }
            } finally {
                busy = false
            }
        }
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 1f - dismissFraction))
            .pointerInput(Unit) {
                // Zoomed pages consume their touches, so this only ever sees
                // unzoomed vertical drags — no need to re-key on zoom level.
                detectVerticalDragGestures(
                    onVerticalDrag = { change, dragAmount ->
                        change.consume()
                        scope.launch {
                            dismissY.snapTo((dismissY.value + dragAmount).coerceAtLeast(0f))
                        }
                    },
                    onDragEnd = {
                        scope.launch {
                            if (dismissY.value > 180f) {
                                dismissY.animateTo(900f)
                                onClose()
                            } else {
                                dismissY.animateTo(0f, spring())
                            }
                        }
                    },
                    onDragCancel = {
                        scope.launch { dismissY.animateTo(0f, spring()) }
                    },
                )
            },
    ) {
        HorizontalPager(
            state = pagerState,
            key = { items[it].key },
            beyondViewportPageCount = 1,
            userScrollEnabled = currentZoom <= 1.01f,
            modifier = Modifier.graphicsLayer {
                translationY = dismissY.value
                val dismissalScale = 1f - dismissFraction * 0.2f
                scaleX = dismissalScale
                scaleY = dismissalScale
            },
        ) { page ->
            val media = items[page]
            val isActive = pagerState.currentPage == page
            when {
                media is ViewerMedia.Local && media.isVideo -> {
                    LaunchedEffect(page) { if (isActive) currentZoom = 1f }
                    VideoPage(source = media.item.uri, key = media.key, isActive = isActive)
                }
                media is ViewerMedia.Remote && media.isVideo -> {
                    LaunchedEffect(page) { if (isActive) currentZoom = 1f }
                    // Streams straight off the hub; ExoPlayer speaks http natively.
                    VideoPage(
                        source = Uri.parse(api?.fullUrl(media.image.id).orEmpty()),
                        key = media.key,
                        isActive = isActive,
                    )
                }
                else -> ZoomableImage(
                    key = media.key,
                    model = when (media) {
                        is ViewerMedia.Local -> media.item.uri
                        is ViewerMedia.Remote -> api?.largeUrl(media.image) ?: media.image.thumb_url
                    },
                    // Remote zoom quietly upgrades to the full-resolution render.
                    fullModel = (media as? ViewerMedia.Remote)?.let { api?.fullUrl(it.image.id) },
                    onTap = { chromeVisible = !chromeVisible },
                    onZoomChanged = { if (isActive) currentZoom = it },
                )
            }
        }

        if (chromeVisible) {
            val current = items[pagerState.currentPage]
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(Color.Black.copy(alpha = 0.45f))
                    .statusBarsPadding()
                    .align(Alignment.TopCenter),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = onClose) {
                    Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Back", tint = Color.White)
                }
                Spacer(Modifier.weight(1f))
                if (busy) {
                    CircularProgressIndicator(
                        color = Color.White,
                        strokeWidth = 2.dp,
                        modifier = Modifier.padding(horizontal = 12.dp).size(18.dp),
                    )
                }
                IconButton(enabled = !busy, onClick = {
                    withShareable(current) { uri, mime -> share(context, uri, mime) }
                }) {
                    Icon(Icons.Outlined.Share, "Share", tint = Color.White)
                }
                if (!current.isVideo) {
                    IconButton(enabled = !busy, onClick = {
                        val local = current as? ViewerMedia.Local
                        if (local?.rawTwin != null) {
                            menuVisible = true // RAW pair: choose in the menu below
                        } else {
                            withShareable(current) { uri, mime -> edit(context, uri, mime) }
                        }
                    }) {
                        Icon(Icons.Outlined.Edit, "Edit", tint = Color.White)
                    }
                }
                IconButton(onClick = { infoFor = current }) {
                    Icon(Icons.Outlined.Info, "Info", tint = Color.White)
                }
                Box {
                    IconButton(onClick = { menuVisible = true }) {
                        Icon(Icons.Outlined.MoreVert, "More", tint = Color.White)
                    }
                    DropdownMenu(
                        expanded = menuVisible,
                        onDismissRequest = { menuVisible = false },
                    ) {
                        (current as? ViewerMedia.Local)?.rawTwin?.let { twin ->
                            DropdownMenuItem(
                                text = { Text("Edit RAW (.dng)") },
                                onClick = {
                                    menuVisible = false
                                    edit(
                                        context, twin.uri,
                                        context.contentResolver.getType(twin.uri) ?: "image/x-adobe-dng",
                                    )
                                },
                            )
                            DropdownMenuItem(
                                text = { Text("Edit JPEG") },
                                onClick = {
                                    menuVisible = false
                                    withShareable(current) { uri, mime -> edit(context, uri, mime) }
                                },
                            )
                        }
                        DropdownMenuItem(
                            text = { Text("Use as") },
                            onClick = {
                                menuVisible = false
                                withShareable(current) { uri, mime -> useAs(context, uri, mime) }
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("Open with") },
                            onClick = {
                                menuVisible = false
                                withShareable(current) { uri, mime -> openWith(context, uri, mime) }
                            },
                        )
                    }
                }
                IconButton(enabled = !busy, onClick = {
                    when (current) {
                        is ViewerMedia.Local -> {
                            val pending = MediaStore.createTrashRequest(
                                context.contentResolver, listOf(current.item.uri), true,
                            )
                            trashLauncher.launch(IntentSenderRequest.Builder(pending).build())
                        }
                        is ViewerMedia.Remote -> confirmHubTrash = current.image
                    }
                }) {
                    Icon(Icons.Outlined.Delete, "Delete", tint = Color.White)
                }
            }
        }
    }

    confirmHubTrash?.let { image ->
        AlertDialog(
            onDismissRequest = { confirmHubTrash = null },
            containerColor = Panel,
            title = { Text("Move to archive trash?") },
            text = { Text("${image.filename} moves to your server's trash. You can restore it from the desktop app.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmHubTrash = null
                    scope.launch {
                        busy = true
                        val ok = runCatching { api?.trash(listOf(image.id)) == true }.getOrDefault(false)
                        busy = false
                        if (ok) { onChanged(); onClose() }
                        else Toast.makeText(context, "Couldn't move to archive trash", Toast.LENGTH_SHORT).show()
                    }
                }) { Text("Move to trash") }
            },
            dismissButton = {
                TextButton(onClick = { confirmHubTrash = null }) { Text("Cancel") }
            },
        )
    }

    infoFor?.let { media ->
        InfoSheet(media = media, api = api, onDismiss = { infoFor = null })
    }
}

/** Compatibility entry for device-only flows (camera review, picker preview). */
@Composable
fun ViewerScreen(
    items: List<MediaItem>,
    startIndex: Int,
    onClose: () -> Unit,
    onChanged: () -> Unit = {},
) {
    val rawTwins = remember(items) { rawTwinsByShotKey(items) }
    ViewerScreen(
        items = items.map { ViewerMedia.Local(it, rawTwins[it.shotKey]?.takeIf { twin -> twin.id != it.id }) },
        startIndex = startIndex,
        onClose = onClose,
        api = null,
        onChanged = onChanged,
    )
}

/** DNG twins keyed by shot, so Edit can offer the RAW even though the grid hides it. */
fun rawTwinsByShotKey(items: List<MediaItem>): Map<String, MediaItem> =
    items.asSequence().filter { it.isRaw }.associateBy { it.shotKey }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun InfoSheet(media: ViewerMedia, api: ArchiveApi?, onDismiss: () -> Unit) {
    val context = LocalContext.current
    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = Panel) {
        Column(Modifier.padding(horizontal = 20.dp).navigationBarsPadding()) {
            Text(media.displayName, style = MaterialTheme.typography.titleMedium, color = TextPrimary)
            Spacer(Modifier.height(10.dp))
            when (media) {
                is ViewerMedia.Local -> LocalInfo(context, media.item)
                is ViewerMedia.Remote -> RemoteInfo(media.image, api)
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun LocalInfo(context: Context, item: MediaItem) {
    var details by remember(item.id) { mutableStateOf<MediaDetails?>(null) }
    LaunchedEffect(item.id) { details = loadMediaDetails(context, item) }
    InfoLine("Taken", formatTimestamp(item.dateTakenMs))
    InfoLine("Folder", item.relativePath.ifEmpty { item.bucketName })
    InfoLine("Size", formatBytes(item.sizeBytes))
    if (item.width > 0) InfoLine("Dimensions", "${item.width} × ${item.height}")
    if (item.isVideo) InfoLine("Duration", formatDuration(item.durationMs))
    details?.cameraModel?.let { InfoLine("Camera", it) }
    details?.aperture?.let { InfoLine("Aperture", it) }
    details?.shutter?.let { InfoLine("Shutter", it) }
    details?.iso?.let { InfoLine("ISO", it) }
    details?.focalLength?.let { InfoLine("Focal length", it) }
    details?.location?.let { (lat, lon) ->
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Located at %.5f, %.5f".format(lat, lon),
                style = MaterialTheme.typography.bodyMedium,
                color = TextPrimary,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = { openMap(context, lat, lon) }) { Text("Open in Maps") }
        }
    }
    val record = details?.backupRecord
    InfoLine(
        "Backup",
        if (record?.isBackedUp == true) {
            "Backed up to archive" + (record.hubImageId?.let { " · #$it" } ?: "")
        } else {
            "Not backed up yet"
        },
    )
}

@Composable
private fun RemoteInfo(image: ArchiveImage, api: ArchiveApi?) {
    var exif by remember(image.id) { mutableStateOf<Map<String, String>>(emptyMap()) }
    LaunchedEffect(image.id) { exif = api?.exif(image.id) ?: emptyMap() }
    image.date_taken?.let {
        InfoLine("Taken", formatTimestamp(UnifiedTimeline.hubMillis(it)))
    }
    image.file_size?.let { InfoLine("Size", formatBytes(it)) }
    if ((image.width ?: 0) > 0) InfoLine("Dimensions", "${image.width} × ${image.height}")
    (image.camera_model ?: exif.firstValue("camera", "model"))?.let { InfoLine("Camera", it) }
    (image.lens ?: exif.firstValue("lens"))?.let { InfoLine("Lens", it) }
    exif.firstValue("aperture", "f_number", "fnumber")?.let { InfoLine("Aperture", it) }
    exif.firstValue("shutter", "exposure")?.let { InfoLine("Shutter", it) }
    exif.firstValue("iso")?.let { InfoLine("ISO", it) }
    exif.firstValue("focal")?.let { InfoLine("Focal length", it) }
    InfoLine("Archive", "In your archive · #${image.id}")
}

private fun Map<String, String>.firstValue(vararg keyParts: String): String? =
    entries.firstOrNull { (k, _) -> keyParts.any { k.contains(it, ignoreCase = true) } }?.value

private val BackupRecord.isBackedUp: Boolean
    get() = state == BackupDb.STATE_UPLOADED || state == BackupDb.STATE_PRESENT

@Composable
private fun InfoLine(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = TextSecondary,
            modifier = Modifier.weight(0.35f),
        )
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            color = TextPrimary,
            modifier = Modifier.weight(0.65f),
        )
    }
}

@Composable
private fun ZoomableImage(
    key: String,
    model: Any,
    fullModel: Any?,
    onTap: () -> Unit,
    onZoomChanged: (Float) -> Unit,
) {
    var scale by remember(key) { mutableFloatStateOf(1f) }
    var offsetX by remember(key) { mutableFloatStateOf(0f) }
    var offsetY by remember(key) { mutableFloatStateOf(0f) }
    var width by remember(key) { mutableFloatStateOf(0f) }
    var height by remember(key) { mutableFloatStateOf(0f) }
    var wantFull by remember(key) { mutableStateOf(false) }
    LaunchedEffect(key, scale > 1.2f) {
        if (scale > 1.2f && fullModel != null) wantFull = true
    }

    Box(
        Modifier
            .fillMaxSize()
            .onSizeChanged { size -> width = size.width.toFloat(); height = size.height.toFloat() }
            .pointerInput(key) {
                detectTapGestures(
                    onTap = { onTap() },
                    onDoubleTap = { tap ->
                        if (scale > 1f) {
                            scale = 1f
                            offsetX = 0f
                            offsetY = 0f
                        } else {
                            val target = 2.5f
                            offsetX = (width / 2f - tap.x) * (target - 1f)
                            offsetY = (height / 2f - tap.y) * (target - 1f)
                            scale = target
                        }
                        onZoomChanged(scale)
                    },
                )
            }
            .pointerInput(key) {
                // Custom transform detector: claims touches ONLY for two-finger
                // gestures or one-finger pans while zoomed. A single finger at 1x
                // stays unconsumed so the pager's swipe and the dismiss drag work.
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    while (true) {
                        val event = awaitPointerEvent()
                        val pressed = event.changes.count { it.pressed }
                        if (pressed == 0) break
                        val transforming = pressed >= 2 || scale > 1f
                        if (transforming) {
                            val zoom = event.calculateZoom()
                            val pan = event.calculatePan()
                            scale = (scale * zoom).coerceIn(1f, 8f)
                            if (scale > 1f) {
                                val maxX = width * (scale - 1f) / 2f
                                val maxY = height * (scale - 1f) / 2f
                                offsetX = (offsetX + pan.x).coerceIn(-maxX, maxX)
                                offsetY = (offsetY + pan.y).coerceIn(-maxY, maxY)
                            } else {
                                offsetX = 0f
                                offsetY = 0f
                            }
                            onZoomChanged(scale)
                            event.changes.forEach { if (it.positionChanged()) it.consume() }
                        }
                    }
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current)
                .data(if (wantFull) fullModel else model)
                // Keep showing the preview while the full render streams in.
                .placeholderMemoryCacheKey(model.toString())
                // Cap the decode so an 8x zoom of a full render can't OOM.
                .size(4096)
                .crossfade(false)
                .build(),
            contentDescription = null,
            contentScale = ContentScale.Fit,
            modifier = Modifier
                .fillMaxSize()
                .graphicsLayer {
                    scaleX = scale
                    scaleY = scale
                    translationX = offsetX
                    translationY = offsetY
                },
        )
    }
}

@Composable
private fun VideoPage(source: Uri, key: String, isActive: Boolean) {
    val context = LocalContext.current
    val hostView = LocalView.current
    var muted by remember(key) { mutableStateOf(false) }
    var isPlaying by remember(key) { mutableStateOf(false) }
    val player = remember(key) {
        ExoPlayer.Builder(context).build().apply {
            setMediaItem(ExoMediaItem.fromUri(source))
            prepare()
        }
    }
    DisposableEffect(player, hostView) {
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(value: Boolean) { isPlaying = value }
        }
        player.addListener(listener)
        onDispose {
            hostView.keepScreenOn = false
            player.removeListener(listener)
            player.release()
        }
    }
    LaunchedEffect(isPlaying) { hostView.keepScreenOn = isPlaying }
    LaunchedEffect(isActive) {
        if (isActive) {
            if (player.playbackState == Player.STATE_IDLE) {
                player.setMediaItem(ExoMediaItem.fromUri(source))
                player.prepare()
            }
        } else {
            player.stop()
        }
    }
    Box(Modifier.fillMaxSize()) {
        AndroidView(
            factory = { ctx ->
                PlayerView(ctx).apply {
                    this.player = player
                    useController = true
                    controllerShowTimeoutMs = 2_000
                }
            },
            modifier = Modifier.fillMaxSize(),
        )
        IconButton(
            onClick = {
                muted = !muted
                player.volume = if (muted) 0f else 1f
            },
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .navigationBarsPadding()
                .padding(16.dp)
                .background(Color.Black.copy(alpha = 0.5f), MaterialTheme.shapes.large)
                .size(44.dp),
        ) {
            Icon(
                if (muted) Icons.Rounded.VolumeOff else Icons.Rounded.VolumeUp,
                contentDescription = if (muted) "Unmute" else "Mute",
                tint = Color.White,
            )
        }
    }
}

private data class MediaDetails(
    val cameraModel: String?,
    val aperture: String?,
    val shutter: String?,
    val iso: String?,
    val focalLength: String?,
    val location: Pair<Double, Double>?,
    val backupRecord: BackupRecord?,
)

private suspend fun loadMediaDetails(context: Context, item: MediaItem): MediaDetails =
    withContext(Dispatchers.IO) {
        val canReadLocation = ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.ACCESS_MEDIA_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED
        val original = if (canReadLocation) {
            runCatching { MediaStore.setRequireOriginal(item.uri) }.getOrDefault(item.uri)
        } else item.uri
        val exif = if (item.isVideo) null else readExif(context, original)
        val latLong = if (canReadLocation) exif?.latLong else null
        val exposure = exif?.getAttributeDouble(ExifInterface.TAG_EXPOSURE_TIME, Double.NaN)
            ?.takeUnless { it.isNaN() || it <= 0.0 }
        val aperture = exif?.getAttributeDouble(ExifInterface.TAG_F_NUMBER, Double.NaN)
            ?.takeUnless { it.isNaN() || it <= 0.0 }
        val focal = exif?.getAttributeDouble(ExifInterface.TAG_FOCAL_LENGTH, Double.NaN)
            ?.takeUnless { it.isNaN() || it <= 0.0 }
        MediaDetails(
            cameraModel = exif?.getAttribute(ExifInterface.TAG_MODEL),
            aperture = aperture?.let { "f/%.1f".format(it) },
            shutter = exposure?.let(::formatExposure),
            iso = exif?.getAttribute(ExifInterface.TAG_PHOTOGRAPHIC_SENSITIVITY)
                ?: exif?.getAttribute(ExifInterface.TAG_ISO_SPEED_RATINGS),
            focalLength = focal?.let { "%.1f mm".format(it) },
            location = latLong?.let { it[0] to it[1] },
            backupRecord = BackupDb.get(context).recordFor(item.id),
        )
    }

private fun readExif(context: Context, uri: Uri): ExifInterface? = runCatching {
    context.contentResolver.openFileDescriptor(uri, "r")?.use { descriptor ->
        ExifInterface(descriptor.fileDescriptor)
    }
}.getOrNull()

private fun formatExposure(seconds: Double): String =
    if (seconds >= 1.0) "%.1f s".format(seconds)
    else "1/%d s".format((1.0 / seconds).toInt().coerceAtLeast(1))

private fun openMap(context: Context, latitude: Double, longitude: Double) {
    val intent = Intent(Intent.ACTION_VIEW, Uri.parse("geo:$latitude,$longitude?q=$latitude,$longitude"))
    runCatching { context.startActivity(intent) }
}

private fun share(context: Context, uri: Uri, mimeType: String) {
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = mimeType
        putExtra(Intent.EXTRA_STREAM, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    context.startActivity(Intent.createChooser(intent, null))
}

private fun edit(context: Context, uri: Uri, mimeType: String) {
    val intent = Intent(Intent.ACTION_EDIT).apply {
        setDataAndType(uri, mimeType)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
    }
    runCatching { context.startActivity(Intent.createChooser(intent, "Edit with")) }
}

private fun useAs(context: Context, uri: Uri, mimeType: String) {
    val intent = Intent(Intent.ACTION_ATTACH_DATA).apply {
        setDataAndType(uri, mimeType)
        putExtra("mimeType", mimeType)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    context.startActivity(Intent.createChooser(intent, null))
}

private fun openWith(context: Context, uri: Uri, mimeType: String) {
    val intent = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(uri, mimeType)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    val chooser = Intent.createChooser(intent, null).apply {
        putExtra(
            Intent.EXTRA_EXCLUDE_COMPONENTS,
            arrayOf(ComponentName(context, ViewerActivity::class.java)),
        )
    }
    context.startActivity(chooser)
}

private fun formatTimestamp(ms: Long): String =
    Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern("EEE, MMM d, yyyy · h:mm a"))

fun formatBytes(bytes: Long): String = when {
    bytes >= 1L shl 30 -> "%.1f GB".format(bytes / 1e9)
    bytes >= 1L shl 20 -> "%.1f MB".format(bytes / 1e6)
    bytes >= 1L shl 10 -> "%.0f KB".format(bytes / 1e3)
    else -> "$bytes B"
}
