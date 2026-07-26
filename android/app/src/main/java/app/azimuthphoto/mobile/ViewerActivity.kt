package app.azimuthphoto.mobile

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.media3.common.MediaItem as ExoMediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.ui.AzimuthPhotoTheme
import app.azimuthphoto.mobile.ui.ViewerScreen
import coil.compose.AsyncImage

/** Handles system viewing and the camera's swipe-through review flow. */
class ViewerActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val uri = intent?.data
        if (uri == null) {
            finish()
            return
        }
        val mimeType = intent.type ?: contentResolver.getType(uri)
        val secure = intent.action == ACTION_REVIEW_SECURE
        // Only the secure review flow may appear over the lock screen — and then
        // only the single shot the camera handed us, never the whole library.
        if (secure) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        val review = !secure && intent.action in REVIEW_ACTIONS
        setContent {
            AzimuthPhotoTheme {
                if (review) {
                    ReviewViewer(uri = uri, mimeType = mimeType, onClose = ::finish)
                } else {
                    SingleUriViewer(uri = uri, mimeType = mimeType)
                }
            }
        }
    }

    private companion object {
        const val ACTION_REVIEW_SECURE = "android.provider.action.REVIEW_SECURE"
        val REVIEW_ACTIONS = setOf(
            "com.android.camera.action.REVIEW",
            "android.provider.action.REVIEW",
            ACTION_REVIEW_SECURE,
        )
    }
}

private sealed interface ReviewState {
    data object Loading : ReviewState
    data object Fallback : ReviewState
    data class Timeline(val items: List<MediaItem>, val index: Int) : ReviewState
}

@Composable
private fun ReviewViewer(uri: Uri, mimeType: String?, onClose: () -> Unit) {
    val context = LocalContext.current
    val state by produceState<ReviewState>(ReviewState.Loading, uri) {
        val id = DeviceMedia.resolveId(context, uri)
        val items = DeviceMedia.collapseRawPairs(DeviceMedia.queryAll(context))
        val index = id?.let { target -> items.indexOfFirst { it.id == target } } ?: -1
        value = if (index >= 0) ReviewState.Timeline(items, index) else ReviewState.Fallback
    }
    when (val current = state) {
        ReviewState.Loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        ReviewState.Fallback -> SingleUriViewer(uri = uri, mimeType = mimeType)
        is ReviewState.Timeline -> ViewerScreen(
            items = current.items,
            startIndex = current.index,
            onClose = onClose,
        )
    }
}

@Composable
private fun SingleUriViewer(uri: Uri, mimeType: String?) {
    val isVideo = mimeType?.startsWith("video/") == true
    if (isVideo) {
        VideoViewer(uri)
    } else {
        AsyncImage(
            model = uri,
            contentDescription = null,
            contentScale = ContentScale.Fit,
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black),
        )
    }
}

@Composable
private fun VideoViewer(uri: Uri) {
    val context = LocalContext.current
    val player = remember(uri) {
        ExoPlayer.Builder(context).build().apply {
            setMediaItem(ExoMediaItem.fromUri(uri))
            prepare()
            playWhenReady = true
        }
    }
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner, player) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) player.pause()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            player.release()
        }
    }
    AndroidView(
        factory = { PlayerView(it).apply { this.player = player } },
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black),
    )
}
