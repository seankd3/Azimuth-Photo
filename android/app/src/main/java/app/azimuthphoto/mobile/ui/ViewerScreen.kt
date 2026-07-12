package app.azimuthphoto.mobile.ui

import android.app.Activity
import android.content.Intent
import android.provider.MediaStore
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem as ExoMediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import coil.request.ImageRequest
import app.azimuthphoto.mobile.data.MediaItem
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Full-screen media viewer: swipe between items, pinch to zoom, share/info/trash. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ViewerScreen(items: List<MediaItem>, startIndex: Int, onClose: () -> Unit) {
    BackHandler(onBack = onClose)
    val pagerState = rememberPagerState(initialPage = startIndex) { items.size }
    var chromeVisible by remember { mutableStateOf(true) }
    var infoFor by remember { mutableStateOf<MediaItem?>(null) }
    val context = LocalContext.current

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        HorizontalPager(state = pagerState, key = { items[it].id }) { page ->
            val item = items[page]
            if (item.isVideo) {
                VideoPage(item, isActive = pagerState.currentPage == page)
            } else {
                ZoomableImage(item, onTap = { chromeVisible = !chromeVisible })
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
                IconButton(onClick = { trashItem(context, current) }) {
                    Icon(Icons.Outlined.Delete, "Delete", tint = Color.White)
                }
            }
        }
    }

    infoFor?.let { item ->
        ModalBottomSheet(
            onDismissRequest = { infoFor = null },
            containerColor = Panel,
        ) {
            Column(Modifier.padding(horizontal = 20.dp).navigationBarsPadding()) {
                Text(item.displayName, style = MaterialTheme.typography.titleMedium, color = TextPrimary)
                Spacer(Modifier.height(10.dp))
                InfoLine("Taken", formatTimestamp(item.dateTakenMs))
                InfoLine("Folder", item.relativePath.ifEmpty { item.bucketName })
                InfoLine("Size", formatBytes(item.sizeBytes))
                if (item.width > 0) InfoLine("Dimensions", "${item.width} × ${item.height}")
                if (item.isVideo) InfoLine("Duration", formatDuration(item.durationMs))
                Spacer(Modifier.height(24.dp))
            }
        }
    }
}

@Composable
private fun InfoLine(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = TextSecondary, modifier = Modifier.weight(0.35f))
        Text(value, style = MaterialTheme.typography.bodyMedium, color = TextPrimary, modifier = Modifier.weight(0.65f))
    }
}

@Composable
private fun ZoomableImage(item: MediaItem, onTap: () -> Unit) {
    var scale by remember { mutableStateOf(1f) }
    var offsetX by remember { mutableStateOf(0f) }
    var offsetY by remember { mutableStateOf(0f) }

    Box(
        Modifier
            .fillMaxSize()
            .pointerInput(item.id) {
                detectTapGestures(
                    onTap = { onTap() },
                    onDoubleTap = {
                        if (scale > 1f) { scale = 1f; offsetX = 0f; offsetY = 0f } else scale = 2.5f
                    },
                )
            }
            .pointerInput(item.id) {
                detectTransformGestures { _, pan, zoom, _ ->
                    scale = (scale * zoom).coerceIn(1f, 8f)
                    if (scale > 1f) {
                        offsetX += pan.x
                        offsetY += pan.y
                    } else { offsetX = 0f; offsetY = 0f }
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
    val player = remember(item.id) {
        ExoPlayer.Builder(context).build().apply {
            setMediaItem(ExoMediaItem.fromUri(item.uri))
            prepare()
        }
    }
    androidx.compose.runtime.DisposableEffect(item.id) {
        onDispose { player.release() }
    }
    androidx.compose.runtime.LaunchedEffect(isActive) {
        if (!isActive) player.pause()
    }
    AndroidView(
        factory = { ctx -> PlayerView(ctx).apply { this.player = player } },
        modifier = Modifier.fillMaxSize(),
    )
}

private fun shareItem(activity: Activity, item: MediaItem) {
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = if (item.isVideo) "video/*" else "image/*"
        putExtra(Intent.EXTRA_STREAM, item.uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    activity.startActivity(Intent.createChooser(intent, null))
}

private fun trashItem(context: android.content.Context, item: MediaItem) {
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
