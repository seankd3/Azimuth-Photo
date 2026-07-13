package app.azimuthphoto.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveFolder
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.MediaBucket
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.data.SettingsStore
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.launch

@Composable
fun SearchScreen(onImmersive: (Boolean) -> Unit = {}) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val settings by SettingsStore.flow(context).collectAsState(initial = null)
    val currentSettings = settings
    if (currentSettings == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    val api = remember(currentSettings.serverUrl) { ArchiveApi(currentSettings.serverUrl) }
    var query by rememberSaveable { mutableStateOf("") }
    var activeQuery by rememberSaveable { mutableStateOf("") }
    var activeShelf by remember { mutableStateOf<ArchiveFolder?>(null) }
    var activeBucket by remember { mutableStateOf<MediaBucket?>(null) }
    var hasResults by rememberSaveable { mutableStateOf(false) }
    var requestGeneration by remember { mutableIntStateOf(0) }
    var shelves by remember { mutableStateOf<List<ArchiveFolder>>(emptyList()) }
    var buckets by remember { mutableStateOf<List<MediaBucket>>(emptyList()) }
    var archiveImages by remember { mutableStateOf<List<ArchiveImage>>(emptyList()) }
    var localItems by remember { mutableStateOf<List<MediaItem>>(emptyList()) }
    var archiveLoading by remember { mutableStateOf(false) }
    var archiveDone by remember { mutableStateOf(false) }
    var archiveError by remember { mutableStateOf(false) }
    var archiveViewerIndex by rememberSaveable { mutableStateOf<Int?>(null) }
    var localViewerIndex by rememberSaveable { mutableStateOf<Int?>(null) }
    val gridState = rememberLazyGridState()

    fun submit(term: String) {
        val clean = term.trim()
        if (clean.isEmpty()) return
        query = clean
        activeQuery = clean
        activeShelf = null
        activeBucket = null
        hasResults = true
        requestGeneration++
        scope.launch { SettingsStore.addRecentSearch(context, clean) }
    }

    LaunchedEffect(api) {
        shelves = runCatching { api.shelves() }.getOrDefault(emptyList())
        buckets = DeviceMedia.queryBuckets(context)
    }

    LaunchedEffect(requestGeneration) {
        if (!hasResults) return@LaunchedEffect
        archiveImages = emptyList()
        localItems = emptyList()
        archiveDone = activeBucket != null
        archiveError = false
        archiveLoading = activeBucket == null
        if (activeBucket == null) {
            runCatching {
                api.page(
                    offset = 0,
                    search = activeQuery,
                    folder = activeShelf?.path.orEmpty(),
                )
            }.onSuccess { page ->
                archiveImages = page.images
                archiveDone = page.images.isEmpty()
            }.onFailure { archiveError = true }
            archiveLoading = false
        }
        val allLocal = DeviceMedia.collapseRawPairs(DeviceMedia.queryAll(context))
        localItems = when {
            activeBucket != null -> allLocal.filter { it.bucketId == activeBucket?.id }.take(200)
            activeShelf != null -> emptyList()
            else -> allLocal.filter {
                it.displayName.contains(activeQuery, ignoreCase = true)
            }.take(200)
        }
    }

    LaunchedEffect(requestGeneration, activeQuery, activeShelf) {
        snapshotFlow { gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }
            .collect { lastVisible ->
                val resultCount = archiveImages.size + localItems.size + 2
                if (
                    hasResults && activeBucket == null && !archiveLoading && !archiveDone &&
                    lastVisible >= resultCount - 40
                ) {
                    archiveLoading = true
                    runCatching {
                        api.page(
                            offset = archiveImages.size,
                            search = activeQuery,
                            folder = activeShelf?.path.orEmpty(),
                        )
                    }.onSuccess { page ->
                        if (page.images.isEmpty()) archiveDone = true
                        else archiveImages = (archiveImages + page.images).distinctBy { it.id }
                    }.onFailure { archiveError = true }
                    archiveLoading = false
                }
            }
    }

    LaunchedEffect(archiveViewerIndex != null || localViewerIndex != null) {
        onImmersive(archiveViewerIndex != null || localViewerIndex != null)
    }
    archiveViewerIndex?.let { index ->
        ArchiveViewer(
            api = api,
            images = archiveImages,
            startIndex = index,
            onClose = { archiveViewerIndex = null },
        )
        return
    }
    localViewerIndex?.let { index ->
        ViewerScreen(
            items = localItems,
            startIndex = index,
            onClose = { localViewerIndex = null },
        )
        return
    }

    BackHandler(enabled = hasResults || query.isNotBlank()) {
        if (hasResults) {
            hasResults = false
            activeQuery = ""
            activeShelf = null
            activeBucket = null
        } else query = ""
    }

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            placeholder = { Text("Search your photos", color = TextSecondary) },
            leadingIcon = { Icon(Icons.Outlined.Search, null, tint = TextSecondary) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { submit(query) }),
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = Panel,
                unfocusedContainerColor = Panel,
                focusedBorderColor = PanelHigh,
                unfocusedBorderColor = Panel,
            ),
            shape = MaterialTheme.shapes.extraLarge,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
        )

        if (!hasResults) {
            SearchHome(
                recentSearches = currentSettings.recentSearches,
                shelves = shelves,
                buckets = buckets,
                onRecent =(::submit),
                onShelf = { shelf ->
                    activeQuery = ""
                    activeShelf = shelf
                    activeBucket = null
                    hasResults = true
                    requestGeneration++
                },
                onBucket = { bucket ->
                    activeQuery = ""
                    activeShelf = null
                    activeBucket = bucket
                    hasResults = true
                    requestGeneration++
                },
            )
        } else {
            LazyVerticalGrid(
                state = gridState,
                columns = GridCells.Fixed(4),
                verticalArrangement = Arrangement.spacedBy(2.dp),
                horizontalArrangement = Arrangement.spacedBy(2.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                item(key = "archive-header", span = { GridItemSpan(maxLineSpan) }) {
                    ResultHeader("From your archive")
                }
                if (archiveError) {
                    item(key = "archive-error", span = { GridItemSpan(maxLineSpan) }) {
                        Text(
                            "Archive unreachable — check Tailscale",
                            color = TextSecondary,
                            modifier = Modifier.padding(16.dp),
                        )
                    }
                } else if (!archiveLoading && archiveImages.isEmpty()) {
                    item(key = "archive-empty", span = { GridItemSpan(maxLineSpan) }) {
                        Text("No archive matches", color = TextSecondary, modifier = Modifier.padding(16.dp))
                    }
                }
                itemsIndexed(archiveImages, key = { _, image -> "a${image.id}" }) { index, image ->
                    ArchiveResultCell(api, image) { archiveViewerIndex = index }
                }
                item(key = "device-header", span = { GridItemSpan(maxLineSpan) }) {
                    ResultHeader("On this device")
                }
                if (localItems.isEmpty()) {
                    item(key = "device-empty", span = { GridItemSpan(maxLineSpan) }) {
                        Text("No device matches", color = TextSecondary, modifier = Modifier.padding(16.dp))
                    }
                }
                itemsIndexed(localItems, key = { _, item -> "d${item.id}" }) { index, item ->
                    DeviceResultCell(item) { localViewerIndex = index }
                }
                if (archiveLoading) {
                    item(key = "loading", span = { GridItemSpan(maxLineSpan) }) {
                        Box(Modifier.fillMaxWidth().padding(20.dp), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator()
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SearchHome(
    recentSearches: List<String>,
    shelves: List<ArchiveFolder>,
    buckets: List<MediaBucket>,
    onRecent: (String) -> Unit,
    onShelf: (ArchiveFolder) -> Unit,
    onBucket: (MediaBucket) -> Unit,
) {
    Column(Modifier.fillMaxSize()) {
        if (recentSearches.isNotEmpty()) {
            Text(
                "Recent searches",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
            )
            Row(
                Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                recentSearches.forEach { search ->
                    AssistChip(onClick = { onRecent(search) }, label = { Text(search) })
                }
            }
        }
        Text(
            "Your shelves",
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
        )
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            shelves.forEach { shelf ->
                ElevatedCard(onClick = { onShelf(shelf) }) {
                    Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
                        Text(shelf.name, color = TextPrimary)
                        Text("${shelf.count} photos", color = TextSecondary, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        Text(
            "On this device",
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
        )
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            buckets.forEach { bucket ->
                FilterChip(
                    selected = false,
                    onClick = { onBucket(bucket) },
                    label = { Text("${bucket.name} · ${bucket.count}") },
                )
            }
        }
    }
}

@Composable
private fun ResultHeader(title: String) {
    Text(
        title,
        style = MaterialTheme.typography.titleMedium,
        color = TextPrimary,
        modifier = Modifier.padding(start = 14.dp, top = 20.dp, bottom = 10.dp),
    )
}

@Composable
private fun ArchiveResultCell(api: ArchiveApi, image: ArchiveImage, onClick: () -> Unit) {
    Box(
        Modifier
            .aspectRatio(1f)
            .background(Panel)
            .clickable(onClick = onClick),
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current)
                .data(api.thumbUrl(image))
                .crossfade(false)
                .build(),
            contentDescription = image.filename,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )
    }
}

@Composable
private fun DeviceResultCell(item: MediaItem, onClick: () -> Unit) {
    Box(
        Modifier
            .aspectRatio(1f)
            .background(Panel)
            .clickable(onClick = onClick),
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current)
                .data(item.uri)
                .crossfade(false)
                .size(256)
                .build(),
            contentDescription = item.displayName,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )
    }
}
