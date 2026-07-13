package app.azimuthphoto.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.FilterChip
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import app.azimuthphoto.mobile.data.AppSettings
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveFolder
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.SettingsStore

/** The full hub library: every photo you own, browsable and searchable from the couch. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ArchiveScreen() {
    val context = LocalContext.current
    var settings by remember { mutableStateOf<AppSettings?>(null) }
    LaunchedEffect(Unit) { settings = SettingsStore.current(context) }
    val currentSettings = settings
    if (currentSettings == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    val api = remember(currentSettings.serverUrl) { ArchiveApi(currentSettings.serverUrl) }

    var shelves by remember { mutableStateOf<List<ArchiveFolder>>(emptyList()) }
    var activeShelf by remember { mutableStateOf<ArchiveFolder?>(null) }
    var images by remember { mutableStateOf<List<ArchiveImage>>(emptyList()) }
    var totalVisible by remember { mutableStateOf(0L) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var retryToken by remember { mutableStateOf(0) }
    var viewerIndex by rememberSaveable { mutableStateOf<Int?>(null) }
    val gridState = rememberLazyGridState()
    var pageLoadInFlight by remember(activeShelf) { mutableStateOf(false) }
    var lastPageWasEmpty by remember(activeShelf) { mutableStateOf(false) }

    LaunchedEffect(api) {
        shelves = runCatching { api.shelves() }.getOrDefault(emptyList())
    }

    LaunchedEffect(activeShelf, retryToken) {
        loading = true
        error = null
        try {
            val page = api.page(offset = 0, folder = activeShelf?.path ?: "")
            images = page.images
            totalVisible = page.visible_images
            lastPageWasEmpty = page.images.isEmpty()
        } catch (e: Exception) {
            error = e.message ?: "Archive unreachable"
        }
        loading = false
    }

    LaunchedEffect(activeShelf) {
        snapshotFlow { gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }
            .collect { lastVisible ->
                if (
                    !loading && !pageLoadInFlight && !lastPageWasEmpty &&
                    lastVisible >= images.size - 40
                ) {
                    pageLoadInFlight = true
                    try {
                        val next = api.page(
                            offset = images.size,
                            folder = activeShelf?.path ?: "",
                        )
                        if (next.images.isEmpty()) {
                            lastPageWasEmpty = true
                        } else {
                            images = (images + next.images).distinctBy { it.id }
                        }
                    } catch (_: Exception) {
                        // Keep the current grid visible; a later scroll can retry the page.
                    } finally {
                        pageLoadInFlight = false
                    }
                }
            }
    }

    viewerIndex?.let { index ->
        ArchiveViewer(api = api, images = images, startIndex = index, onClose = { viewerIndex = null })
        return
    }

    Column(Modifier.fillMaxSize()) {
        if (shelves.isNotEmpty()) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FilterChip(
                    selected = activeShelf == null,
                    onClick = { activeShelf = null },
                    label = { Text("All") },
                )
                shelves.forEach { shelf ->
                    FilterChip(
                        selected = activeShelf?.path == shelf.path,
                        onClick = {
                            activeShelf = if (activeShelf?.path == shelf.path) null else shelf
                        },
                        label = { Text(shelf.name) },
                    )
                }
            }
        }

        if (error != null) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp),
            ) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "Archive unreachable — check Tailscale",
                        color = TextPrimary,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = { retryToken++ }) { Text("Retry") }
                }
            }
        }

        when {
            loading && images.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            images.isEmpty() && error == null -> Box(
                Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) {
                Text("The archive is empty", color = TextSecondary)
            }
            else -> {
                Box(Modifier.fillMaxSize()) {
                    LazyVerticalGrid(
                        state = gridState,
                        columns = GridCells.Fixed(4),
                        verticalArrangement = Arrangement.spacedBy(2.dp),
                        horizontalArrangement = Arrangement.spacedBy(2.dp),
                        modifier = Modifier.fillMaxSize(),
                    ) {
                        itemsIndexed(images, key = { _, img -> img.id }) { index, image ->
                            Box(
                                Modifier
                                    .aspectRatio(1f)
                                    .background(Panel)
                                    .clickable { viewerIndex = index }
                            ) {
                                AsyncImage(
                                    model = ImageRequest.Builder(context)
                                        .data(api.thumbUrl(image))
                                        .crossfade(false)
                                        .build(),
                                    contentDescription = image.filename,
                                    contentScale = ContentScale.Crop,
                                    modifier = Modifier.fillMaxSize(),
                                )
                            }
                        }
                    }
                    FastScrollScrubber(
                        state = gridState,
                        labelForIndex = { index ->
                            images.getOrNull(index)?.date_group
                                ?.take(7)
                                ?.let { month ->
                                    runCatching {
                                        java.time.YearMonth.parse(month).format(
                                            java.time.format.DateTimeFormatter.ofPattern("MMM yyyy")
                                        )
                                    }.getOrDefault(month)
                                }
                                .orEmpty()
                        },
                        modifier = Modifier.align(Alignment.CenterEnd),
                    )
                }
            }
        }
    }
}

@Composable
fun ArchiveViewer(
    api: ArchiveApi,
    images: List<ArchiveImage>,
    startIndex: Int,
    onClose: () -> Unit,
) {
    BackHandler(onBack = onClose)
    val pagerState = rememberPagerState(initialPage = startIndex) { images.size }
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        HorizontalPager(state = pagerState, key = { images[it].id }) { page ->
            val image = images[page]
            AsyncImage(
                model = ImageRequest.Builder(LocalContext.current)
                    .data(api.largeUrl(image))
                    .crossfade(true)
                    .build(),
                contentDescription = image.filename,
                contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}
