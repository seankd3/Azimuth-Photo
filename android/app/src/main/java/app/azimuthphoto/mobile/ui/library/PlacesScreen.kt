package app.azimuthphoto.mobile.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.data.MapMarker
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.TextSecondary
import kotlinx.coroutines.launch
import org.osmdroid.config.Configuration
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.BoundingBox
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.MapView
import org.osmdroid.views.overlay.Marker

/** The hub returns up to a few thousand markers; keep the map responsive by capping overlays. */
private const val MAX_MARKERS = 1500

@Composable
fun PlacesScreen(api: LibraryApi, onBack: () -> Unit, onOpenPhotos: (List<ArchiveImage>, Int) -> Unit) {
    var markers by remember { mutableStateOf<List<MapMarker>?>(null) }
    var loading by remember { mutableStateOf(true) }
    var loadFailed by remember { mutableStateOf(false) }
    var mapView by remember { mutableStateOf<MapView?>(null) }
    val lifecycleOwner = LocalLifecycleOwner.current
    val scope = rememberCoroutineScope()

    suspend fun loadMarkers() {
        loading = true
        loadFailed = false
        runCatching { api.markers() }
            .onSuccess { markers = it }
            .onFailure { loadFailed = true }
        loading = false
    }

    LaunchedEffect(Unit) { loadMarkers() }

    DisposableEffect(lifecycleOwner, mapView) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_RESUME -> mapView?.onResume()
                Lifecycle.Event.ON_PAUSE -> mapView?.onPause()
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        if (lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) {
            mapView?.onResume()
        }
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    Column(Modifier.fillMaxSize().background(Ink)) {
        LibraryTopBar(title = "Places", onBack = onBack)
        val loaded = markers
        when {
            loading || loaded == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = TextSecondary)
            }
            loadFailed -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "Couldn't load places",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextSecondary,
                    )
                    TextButton(onClick = { scope.launch { loadMarkers() } }) {
                        Text("Retry", color = TextSecondary)
                    }
                }
            }
            loaded.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    text = "No places yet — photos with location will appear here",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(32.dp),
                )
            }
            else -> PlacesMap(shown = loaded.take(MAX_MARKERS), setMapView = { mapView = it }, onOpenPhotos = onOpenPhotos)
        }
    }
}

@Composable
private fun PlacesMap(
    shown: List<MapMarker>,
    setMapView: (MapView?) -> Unit,
    onOpenPhotos: (List<ArchiveImage>, Int) -> Unit,
) {
    Box(Modifier.fillMaxSize()) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { context ->
                Configuration.getInstance().userAgentValue = context.packageName
                MapView(context).apply {
                    setMapView(this)
                    setTileSource(TileSourceFactory.MAPNIK)
                    setMultiTouchControls(true)
                    // Seed the viewer with every located photo so tapping a pin lets you
                    // swipe through the whole map, not just the one you touched.
                    val locatedPhotos = shown.map { m ->
                        ArchiveImage(id = m.id, filename = m.filename, thumb_url = m.thumb_url)
                    }
                    val points = shown.mapIndexed { index, marker ->
                        val point = GeoPoint(marker.lat, marker.lng)
                        overlays.add(
                            Marker(this).apply {
                                position = point
                                title = marker.filename
                                setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_BOTTOM)
                                setOnMarkerClickListener { _, _ ->
                                    onOpenPhotos(locatedPhotos, index)
                                    true
                                }
                            },
                        )
                        point
                    }
                    post { zoomToBoundingBox(BoundingBox.fromGeoPoints(points), true, 48) }
                }
            },
            onRelease = { releasedMapView ->
                setMapView(null)
                releasedMapView.onDetach()
            },
        )
    }
}
