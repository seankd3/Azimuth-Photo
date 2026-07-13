package app.azimuthphoto.mobile.ui

import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.MediaStore
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
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
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.rounded.VolumeOff
import androidx.compose.material.icons.rounded.VolumeUp
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
import androidx.exifinterface.media.ExifInterface
import androidx.media3.common.Player
import androidx.media3.common.MediaItem as ExoMediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import app.azimuthphoto.mobile.ViewerActivity
import app.azimuthphoto.mobile.backup.BackupDb
import app.azimuthphoto.mobile.backup.BackupRecord
import app.azimuthphoto.mobile.data.MediaItem
import coil.compose.AsyncImage
import coil.request.ImageRequest
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.abs

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ViewerScreen(items: List<MediaItem>, startIndex: Int, onClose: () -> Unit) {
    BackHandler(onBack = onClose)
    val pagerState = rememberPagerState(initialPage = startIndex) { items.size }
    var chromeVisible by remember { mutableStateOf(true) }
    var infoFor by remember { mutableStateOf<MediaItem?>(null) }
    var menuVisible by remember { mutableStateOf(false) }
    var currentZoom by remember { mutableFloatStateOf(1f) }
    val dismissY = remember { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val dismissFraction = (dismissY.value / 600f).coerceIn(0f, 1f)

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 1f - dismissFraction))
            .pointerInput(currentZoom) {
                if (currentZoom <= 1.01f) {
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
                }
            },
    ) {
        HorizontalPager(
            state = pagerState,
            key = { items[it].id },
            beyondViewportPageCount = 0,
            modifier = Modifier.graphicsLayer {
                translationY = dismissY.value
                val dismissalScale = 1f - dismissFraction * 0.2f
                scaleX = dismissalScale
                scaleY = dismissalScale
            },
        ) { page ->
            val item = items[page]
            if (item.isVideo) {
                LaunchedEffect(page) { if (pagerState.currentPage == page) currentZoom = 1f }
                VideoPage(item, isActive = pagerState.currentPage == page)
            } else {
                ZoomableImage(
                    item = item,
                    onTap = { chromeVisible = !chromeVisible },
                    onZoomChanged = { if (pagerState.currentPage == page) currentZoom = it },
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
                IconButton(onClick = { shareItem(context as Activity, current) }) {
                    Icon(Icons.Outlined.Share, "Share", tint = Color.White)
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
                        DropdownMenuItem(
                            text = { Text("Use as") },
                            onClick = {
                                menuVisible = false
                                useAs(context as Activity, current)
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("Open with") },
                            onClick = {
                                menuVisible = false
                                openWith(context as Activity, current)
                            },
                        )
                    }
                }
                IconButton(onClick = {
                    trashItem(context, current)
                    onClose()
                }) {
                    Icon(Icons.Outlined.Delete, "Delete", tint = Color.White)
                }
            }
        }
    }

    infoFor?.let { item ->
        InfoSheet(item = item, onDismiss = { infoFor = null })
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun InfoSheet(item: MediaItem, onDismiss: () -> Unit) {
    val context = LocalContext.current
    var details by remember(item.id) { mutableStateOf<MediaDetails?>(null) }
    LaunchedEffect(item.id) { details = loadMediaDetails(context, item) }
    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = Panel) {
        Column(Modifier.padding(horizontal = 20.dp).navigationBarsPadding()) {
            Text(item.displayName, style = MaterialTheme.typography.titleMedium, color = TextPrimary)
            Spacer(Modifier.height(10.dp))
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
            Spacer(Modifier.height(24.dp))
        }
    }
}

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
    item: MediaItem,
    onTap: () -> Unit,
    onZoomChanged: (Float) -> Unit,
) {
    var scale by remember(item.id) { mutableFloatStateOf(1f) }
    var offsetX by remember(item.id) { mutableFloatStateOf(0f) }
    var offsetY by remember(item.id) { mutableFloatStateOf(0f) }
    var width by remember(item.id) { mutableFloatStateOf(0f) }
    var height by remember(item.id) { mutableFloatStateOf(0f) }

    Box(
        Modifier
            .fillMaxSize()
            .onSizeChanged { size -> width = size.width.toFloat(); height = size.height.toFloat() }
            .pointerInput(item.id) {
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
            .pointerInput(item.id) {
                detectTransformGestures { _, pan, zoom, _ ->
                    scale = (scale * zoom).coerceIn(1f, 8f)
                    if (scale > 1f) {
                        offsetX += pan.x
                        offsetY += pan.y
                    } else {
                        offsetX = 0f
                        offsetY = 0f
                    }
                    onZoomChanged(scale)
                }
            }
            .pointerInput(item.id, scale) {
                detectDragGestures { change, amount ->
                    if (scale > 1f) {
                        change.consume()
                        offsetX += amount.x
                        offsetY += amount.y
                    }
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current)
                .data(item.uri)
                .crossfade(true)
                .build(),
            contentDescription = item.displayName,
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
private fun VideoPage(item: MediaItem, isActive: Boolean) {
    val context = LocalContext.current
    val hostView = LocalView.current
    var muted by remember(item.id) { mutableStateOf(false) }
    var isPlaying by remember(item.id) { mutableStateOf(false) }
    val player = remember(item.id) {
        ExoPlayer.Builder(context).build().apply {
            setMediaItem(ExoMediaItem.fromUri(item.uri))
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
                player.setMediaItem(ExoMediaItem.fromUri(item.uri))
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

private fun shareItem(activity: Activity, item: MediaItem) {
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = if (item.isVideo) "video/*" else "image/*"
        putExtra(Intent.EXTRA_STREAM, item.uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    activity.startActivity(Intent.createChooser(intent, null))
}

private fun useAs(activity: Activity, item: MediaItem) {
    val mimeType = activity.contentResolver.getType(item.uri)
        ?: if (item.isVideo) "video/*" else "image/*"
    val intent = Intent(Intent.ACTION_ATTACH_DATA).apply {
        setDataAndType(item.uri, mimeType)
        putExtra("mimeType", mimeType)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    activity.startActivity(Intent.createChooser(intent, null))
}

private fun openWith(activity: Activity, item: MediaItem) {
    val mimeType = activity.contentResolver.getType(item.uri)
        ?: if (item.isVideo) "video/*" else "image/*"
    val intent = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(item.uri, mimeType)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    val chooser = Intent.createChooser(intent, null).apply {
        putExtra(
            Intent.EXTRA_EXCLUDE_COMPONENTS,
            arrayOf(ComponentName(activity, ViewerActivity::class.java)),
        )
    }
    activity.startActivity(chooser)
}

private fun trashItem(context: Context, item: MediaItem) {
    val pending = MediaStore.createTrashRequest(context.contentResolver, listOf(item.uri), true)
    (context as? Activity)?.startIntentSenderForResult(pending.intentSender, 4208, null, 0, 0, 0)
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
