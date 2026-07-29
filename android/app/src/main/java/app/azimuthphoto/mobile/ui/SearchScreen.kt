package app.azimuthphoto.mobile.ui

import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.itemsIndexed as rowItemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.BookmarkAdd
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.InputChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.FilterOptions
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.data.MediaBucket
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.data.Person
import app.azimuthphoto.mobile.data.SearchFilters
import app.azimuthphoto.mobile.data.SettingsStore
import app.azimuthphoto.mobile.data.Shelf
import app.azimuthphoto.mobile.data.ViewerMedia
import app.azimuthphoto.mobile.ui.library.AddToCollectionSheet
import app.azimuthphoto.mobile.ui.library.RefineSheet
import coil.compose.AsyncImage
import coil.request.ImageRequest
import java.util.Locale
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val PAGE_SIZE = 120

@Composable
fun SearchScreen(
    onImmersive: (Boolean) -> Unit = {},
    onFindSimilar: (ArchiveImage) -> Unit = {},
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val settings by SettingsStore.flow(context).collectAsState(initial = null)
    val currentSettings = settings
    if (currentSettings == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    val api = remember(currentSettings.serverUrl, currentSettings.deviceToken) {
        ArchiveApi(currentSettings.serverUrl, currentSettings.deviceToken.takeIf { it.isNotBlank() })
    }
    val libApi = remember(currentSettings.serverUrl, currentSettings.deviceToken) {
        LibraryApi(currentSettings.serverUrl, currentSettings.deviceToken.takeIf { it.isNotBlank() })
    }

    var query by rememberSaveable { mutableStateOf("") }
    var filters by remember { mutableStateOf(SearchFilters()) }
    var activeBucket by remember { mutableStateOf<MediaBucket?>(null) }
    var retryTick by remember { mutableIntStateOf(0) }

    var shelves by remember { mutableStateOf<List<Shelf>>(emptyList()) }
    var buckets by remember { mutableStateOf<List<MediaBucket>>(emptyList()) }
    var filterOptions by remember { mutableStateOf<FilterOptions?>(null) }
    var peopleList by remember { mutableStateOf<List<Person>>(emptyList()) }

    var archiveImages by remember { mutableStateOf<List<ArchiveImage>>(emptyList()) }
    var visibleTotal by remember { mutableStateOf(0L) }
    var exhausted by remember { mutableStateOf(false) }
    var firstPageLoading by remember { mutableStateOf(false) }
    var pageLoading by remember { mutableStateOf(false) }
    var archiveError by remember { mutableStateOf(false) }
    var localItems by remember { mutableStateOf<List<MediaItem>>(emptyList()) }

    var showRefine by remember { mutableStateOf(false) }
    var refineDraft by remember { mutableStateOf<SearchFilters?>(null) }
    var refineCount by remember { mutableStateOf<Long?>(null) }

    var showSaveDialog by remember { mutableStateOf(false) }
    var addToCollection by remember { mutableStateOf<List<Long>?>(null) }
    var archiveViewerIndex by rememberSaveable { mutableStateOf<Int?>(null) }
    var localViewerIndex by rememberSaveable { mutableStateOf<Int?>(null) }

    val hasResults = !filters.isEmpty || activeBucket != null

    fun submit(term: String) {
        val clean = term.trim()
        if (clean.isEmpty()) return
        query = clean
        activeBucket = null
        filters = filters.copy(q = clean)
        scope.launch { SettingsStore.addRecentSearch(context, clean) }
    }

    LaunchedEffect(api) {
        shelves = runCatching { api.shelves() }.getOrDefault(emptyList())
        buckets = DeviceMedia.queryBuckets(context)
    }
    LaunchedEffect(libApi) {
        filterOptions = runCatching { libApi.filterOptions() }.getOrNull()
        peopleList = runCatching { libApi.people() }.getOrDefault(emptyList())
    }

    // The one live search: any change to filters (submit, chip removal, refine
    // apply) or a retry resets paging and re-queries the hub + device.
    LaunchedEffect(filters, activeBucket, retryTick) {
        archiveImages = emptyList()
        visibleTotal = 0L
        exhausted = false
        archiveError = false
        firstPageLoading = false
        localItems = emptyList()
        if (activeBucket != null) {
            val bucket = activeBucket
            val allLocal = DeviceMedia.collapseRawPairs(DeviceMedia.queryAll(context))
            localItems = allLocal.filter { it.bucketId == bucket?.id }.take(200)
            return@LaunchedEffect
        }
        if (filters.isEmpty) return@LaunchedEffect
        firstPageLoading = true
        runCatching { libApi.search(filters, 0, PAGE_SIZE) }
            .onSuccess { page ->
                if (page.status_stale) {
                    // 200-but-busy (sqlite lock): the empty page and 0 total are not
                    // real results — surface the retry affordance, never "0 photos".
                    archiveError = true
                } else {
                    archiveImages = page.images
                    // total_images = real match count; visible_images only counts
                    // thumb-ready photos and undercounts while the hub processes.
                    visibleTotal = page.total_images
                }
            }
            .onFailure { archiveError = true }
        firstPageLoading = false
        // Device-local matches only make sense for a text query; other
        // refinements are hub-only concepts.
        if (filters.q.isNotBlank()) {
            val allLocal = DeviceMedia.collapseRawPairs(DeviceMedia.queryAll(context))
            localItems = allLocal
                .filter { it.displayName.contains(filters.q, ignoreCase = true) }
                .take(200)
        }
    }

    fun loadNextPage() {
        if (pageLoading || firstPageLoading || archiveError || exhausted) return
        if (archiveImages.isEmpty() || archiveImages.size >= visibleTotal) return
        pageLoading = true
        val launched = filters
        scope.launch {
            runCatching { libApi.search(launched, archiveImages.size, PAGE_SIZE) }
                .onSuccess { page ->
                    if (filters == launched && !page.status_stale) {
                        archiveImages = (archiveImages + page.images).distinctBy { it.id }
                        visibleTotal = page.total_images
                        // The hub only serves thumb-ready images; an empty page
                        // before total_images means the rest are still processing.
                        if (page.images.isEmpty()) exhausted = true
                    }
                }
            pageLoading = false
        }
    }

    // Live count for the refine sheet's draft, debounced.
    LaunchedEffect(refineDraft) {
        val draft = refineDraft ?: return@LaunchedEffect
        refineCount = null
        delay(350)
        refineCount = runCatching { libApi.search(draft, 0, 1).total_images }.getOrNull()
    }

    LaunchedEffect(archiveViewerIndex != null || localViewerIndex != null) {
        onImmersive(archiveViewerIndex != null || localViewerIndex != null)
    }
    DisposableEffect(Unit) { onDispose { onImmersive(false) } }
    BackHandler(enabled = hasResults || query.isNotBlank()) {
        if (hasResults) {
            filters = SearchFilters()
            activeBucket = null
        } else {
            query = ""
        }
    }

    val chips = remember(filters, peopleList) { refinementChips(filters, peopleList) }

    // The search UI stays composed under the viewers so grid scroll and loaded
    // pages survive opening and closing a photo.
    Box(Modifier.fillMaxSize()) {
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

            // Deep toggle + active refinement chips scroll; Refine stays pinned at the end.
            Row(
                Modifier.fillMaxWidth().padding(start = 12.dp, end = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    Modifier
                        .weight(1f)
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    FilterChip(
                        selected = filters.deep,
                        enabled = filters.q.isNotBlank(),
                        onClick = { filters = filters.copy(deep = !filters.deep) },
                        label = { Text("Deep") },
                    )
                    chips.forEach { chip ->
                        InputChip(
                            selected = true,
                            onClick = { filters = chip.cleared },
                            label = { Text(chip.label) },
                            trailingIcon = {
                                Icon(
                                    Icons.Outlined.Close,
                                    contentDescription = "Remove ${chip.label}",
                                    modifier = Modifier.size(InputChipDefaults.IconSize),
                                )
                            },
                        )
                    }
                    if (chips.isNotEmpty()) {
                        TextButton(onClick = { filters = SearchFilters(q = filters.q, deep = filters.deep) }) {
                            Text("Clear all", color = TextSecondary)
                        }
                    }
                }
                IconButton(onClick = {
                    refineDraft = filters
                    refineCount = visibleTotal.takeIf { hasResults && !archiveError }
                    showRefine = true
                }) {
                    BadgedBox(
                        badge = {
                            if (filters.activeCount > 0) {
                                Badge(containerColor = Accent, contentColor = Ink) {
                                    Text("${filters.activeCount}")
                                }
                            }
                        },
                    ) {
                        Icon(Icons.Outlined.Tune, contentDescription = "Refine", tint = TextPrimary)
                    }
                }
            }

            when {
                !hasResults -> SearchHome(
                    recentSearches = currentSettings.recentSearches,
                    shelves = shelves,
                    buckets = buckets,
                    onRecent = (::submit),
                    onShelf = { shelf ->
                        activeBucket = null
                        filters = SearchFilters(folders = shelf.paths.map { "/$it" })
                    },
                    onBucket = { bucket ->
                        filters = SearchFilters()
                        activeBucket = bucket
                    },
                )

                activeBucket != null -> LazyVerticalGrid(
                    columns = GridCells.Fixed(4),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    itemsIndexed(localItems, key = { _, item -> "d${item.id}" }) { index, item ->
                        DeviceResultCell(item) { localViewerIndex = index }
                    }
                }

                firstPageLoading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }

                archiveError -> Column(
                    Modifier.fillMaxSize(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    Text("Archive unreachable", color = TextSecondary)
                    TextButton(onClick = { retryTick++ }) { Text("Retry") }
                }

                else -> Column(Modifier.fillMaxSize()) {
                    if (localItems.isNotEmpty()) {
                        Text(
                            "On this device",
                            style = MaterialTheme.typography.titleSmall,
                            color = TextPrimary,
                            modifier = Modifier.padding(start = 14.dp, top = 10.dp, bottom = 6.dp),
                        )
                        LazyRow(
                            contentPadding = PaddingValues(horizontal = 12.dp),
                            horizontalArrangement = Arrangement.spacedBy(2.dp),
                        ) {
                            rowItemsIndexed(localItems, key = { _, item -> "d${item.id}" }) { index, item ->
                                Box(Modifier.size(96.dp)) {
                                    DeviceResultCell(item) { localViewerIndex = index }
                                }
                            }
                        }
                    }
                    Row(
                        Modifier.fillMaxWidth().padding(start = 14.dp, end = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            String.format(Locale.US, "%,d photos", visibleTotal),
                            style = MaterialTheme.typography.bodySmall,
                            color = TextSecondary,
                            modifier = Modifier.weight(1f),
                        )
                        if (archiveImages.isNotEmpty() && !filters.isEmpty) {
                            IconButton(onClick = { showSaveDialog = true }) {
                                Icon(
                                    Icons.Outlined.BookmarkAdd,
                                    contentDescription = "Save search as collection",
                                    tint = TextSecondary,
                                )
                            }
                        }
                    }
                    if (archiveImages.isEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text("Nothing matches", color = TextSecondary)
                        }
                    } else {
                        PhotoGrid(
                            images = archiveImages,
                            thumbModel = { image -> api.thumbUrl(image) },
                            onOpen = { index -> archiveViewerIndex = index },
                            modifier = Modifier.weight(1f),
                            contentPadding = PaddingValues(bottom = 12.dp),
                            onNearEnd = { loadNextPage() },
                        )
                    }
                }
            }
        }

        archiveViewerIndex?.let { index ->
            ViewerScreen(
                items = archiveImages.map { ViewerMedia.Remote(it) },
                startIndex = index,
                onClose = { archiveViewerIndex = null },
                api = api,
                onAddToCollection = { id -> addToCollection = listOf(id) },
                onFindSimilar = onFindSimilar,
            )
        }
        localViewerIndex?.let { index ->
            ViewerScreen(
                items = localItems,
                startIndex = index,
                onClose = { localViewerIndex = null },
                onChanged = { retryTick++ },
            )
        }
    }

    addToCollection?.let { ids ->
        AddToCollectionSheet(
            api = libApi,
            imageIds = ids,
            onDismiss = { addToCollection = null },
        )
    }

    if (showRefine) {
        RefineSheet(
            initial = filters,
            options = filterOptions,
            people = peopleList.map { person -> person.copy(face_thumb_url = libApi.thumb(person.face_thumb_url)) },
            liveCount = refineCount,
            onDraftChanged = { draft -> refineDraft = draft },
            onApply = { applied ->
                filters = applied
                activeBucket = null
                showRefine = false
            },
            onDismiss = { showRefine = false },
        )
    }

    if (showSaveDialog) {
        SaveSearchDialog(
            defaultName = defaultCollectionName(filters, chips),
            onDismiss = { showSaveDialog = false },
            onSave = { name ->
                showSaveDialog = false
                scope.launch {
                    val id = libApi.createSmartCollection(name, filters)
                    Toast.makeText(
                        context,
                        if (id != null) "Saved to collections" else "Couldn't save",
                        Toast.LENGTH_SHORT,
                    ).show()
                }
            },
        )
    }
}

/** One removable chip per active refinement — human label + the filters with just that field cleared. */
private data class RefinementChip(val label: String, val cleared: SearchFilters)

private fun refinementChips(filters: SearchFilters, people: List<Person>): List<RefinementChip> = buildList {
    filters.people.forEach { id ->
        val name = people.firstOrNull { it.id == id }?.displayName ?: "Person $id"
        add(RefinementChip(name, filters.copy(people = filters.people - id)))
    }
    if (filters.tag.isNotBlank()) add(RefinementChip("#${filters.tag}", filters.copy(tag = "")))
    if (filters.dateTaken.isNotBlank()) add(RefinementChip(filters.dateTaken, filters.copy(dateTaken = "")))
    if (filters.fileType.isNotBlank()) {
        val label = if (filters.fileType == "video") "Video" else ".${filters.fileType}"
        add(RefinementChip(label, filters.copy(fileType = "")))
    }
    if (filters.camera.isNotBlank()) add(RefinementChip(filters.camera, filters.copy(camera = "")))
    if (filters.lens.isNotBlank()) add(RefinementChip(filters.lens, filters.copy(lens = "")))
    if (filters.minStars > 0) add(RefinementChip("★${filters.minStars}+", filters.copy(minStars = 0)))
    if (filters.flag.isNotBlank()) {
        add(
            RefinementChip(
                filters.flag.replaceFirstChar { it.uppercase() },
                filters.copy(flag = ""),
            ),
        )
    }
    if (filters.orientation.isNotBlank()) {
        add(
            RefinementChip(
                filters.orientation.replaceFirstChar { it.uppercase() },
                filters.copy(orientation = ""),
            ),
        )
    }
    filters.folders.forEach { folder ->
        val label = folder.trim('/').substringAfterLast('/').ifBlank { folder }
        add(RefinementChip(label, filters.copy(folders = filters.folders - folder)))
    }
}

private fun defaultCollectionName(filters: SearchFilters, chips: List<RefinementChip>): String {
    val parts = buildList {
        if (filters.q.isNotBlank()) add(filters.q)
        chips.forEach { add(it.label) }
    }
    return parts.joinToString(" · ").take(40).ifBlank { "Saved search" }
}

@Composable
private fun SaveSearchDialog(
    defaultName: String,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
) {
    var name by remember { mutableStateOf(defaultName) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Save search") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    singleLine = true,
                    label = { Text("Name") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    "Saves as a smart collection that stays in sync with this search.",
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                )
            }
        },
        confirmButton = {
            TextButton(
                enabled = name.isNotBlank(),
                onClick = { onSave(name.trim()) },
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun SearchHome(
    recentSearches: List<String>,
    shelves: List<Shelf>,
    buckets: List<MediaBucket>,
    onRecent: (String) -> Unit,
    onShelf: (Shelf) -> Unit,
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
