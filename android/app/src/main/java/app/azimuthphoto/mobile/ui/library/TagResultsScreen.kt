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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.PhotoGrid
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import kotlinx.coroutines.launch

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

    val scope = rememberCoroutineScope()

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
                PhotoGrid(
                    images = loaded,
                    thumbModel = { api.imageThumb(it.id, "sm") },
                    onOpen = { index -> onOpenPhotos(loaded, index) },
                    onNearEnd = { scope.launch { loadMore() } },
                    modifier = Modifier.fillMaxSize().background(Ink).padding(padding),
                )
            }
        }
    }
}
