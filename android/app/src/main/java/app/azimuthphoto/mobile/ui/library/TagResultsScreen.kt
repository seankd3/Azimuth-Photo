package app.azimuthphoto.mobile.ui.library

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import app.azimuthphoto.mobile.ui.gridDensityPinch
import app.azimuthphoto.mobile.ui.rememberGridColumns
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.flow.distinctUntilChanged

private const val PAGE_SIZE = 200

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TagResultsScreen(
    api: LibraryApi,
    tag: String,
    onBack: () -> Unit,
    onOpenPhotos: (List<ArchiveImage>, Int) -> Unit,
) {
    var images by remember(tag) { mutableStateOf<List<ArchiveImage>?>(null) }
    var offset by remember(tag) { mutableStateOf(0) }
    var loading by remember(tag) { mutableStateOf(false) }
    var pageError by remember(tag) { mutableStateOf(false) }
    var reachedEnd by remember(tag) { mutableStateOf(false) }

    val gridState = rememberLazyGridState()
    val columns = rememberGridColumns()

    suspend fun loadMore() {
        if (loading || reachedEnd) return
        loading = true
        pageError = false
        runCatching { api.tagPhotos(tag, offset, PAGE_SIZE) }
            .onSuccess { page ->
                images = (images ?: emptyList()) + page
                offset += page.size
                if (page.size < PAGE_SIZE) reachedEnd = true
            }
            .onFailure { pageError = true }
        loading = false
    }

    LaunchedEffect(tag) { loadMore() }

    LaunchedEffect(gridState, images?.size) {
        snapshotFlow { gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }
            .distinctUntilChanged()
            .collect { last ->
                val count = images?.size ?: 0
                if (count > 0 && last >= count - 24) loadMore()
            }
    }

    BackHandler(onBack = onBack)

    Scaffold(
        containerColor = Ink,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Ink,
                    titleContentColor = TextPrimary,
                    navigationIconContentColor = TextPrimary,
                ),
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back")
                    }
                },
                title = {
                    Text(text = tag, style = MaterialTheme.typography.titleLarge)
                },
            )
        },
    ) { padding ->
        val loaded = images
        when {
            loaded == null -> {
                Box(
                    Modifier.fillMaxSize().background(Ink).padding(padding),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(color = TextSecondary)
                }
            }
            loaded.isEmpty() -> {
                Box(
                    Modifier.fillMaxSize().background(Ink).padding(padding),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = "Nothing tagged ‘$tag’ yet.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextSecondary,
                    )
                }
            }
            else -> {
                LazyVerticalGrid(
                    state = gridState,
                    columns = GridCells.Fixed(columns),
                    modifier = Modifier.fillMaxSize().background(Ink).padding(padding).gridDensityPinch(columns),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    itemsIndexed(items = loaded, key = { _, image -> image.id }) { index, image ->
                        Box(
                            Modifier
                                .aspectRatio(1f)
                                .background(Panel)
                                .clickable { onOpenPhotos(loaded, index) },
                        ) {
                            AsyncImage(
                                model = ImageRequest.Builder(LocalContext.current)
                                    .data(api.imageThumb(image.id, "sm"))
                                    .crossfade(false)
                                    .size(256)
                                    .build(),
                                contentDescription = image.filename,
                                contentScale = ContentScale.Crop,
                                modifier = Modifier.fillMaxSize(),
                            )
                        }
                    }
                }
            }
        }
    }
}
