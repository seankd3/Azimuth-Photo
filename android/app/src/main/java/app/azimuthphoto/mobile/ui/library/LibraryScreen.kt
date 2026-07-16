package app.azimuthphoto.mobile.ui.library

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.Collection
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.data.Person
import app.azimuthphoto.mobile.data.SettingsStore
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.PanelHigh
import app.azimuthphoto.mobile.ui.SearchScreen
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import app.azimuthphoto.mobile.ui.ViewerScreen
import app.azimuthphoto.mobile.data.ViewerMedia
import coil.compose.AsyncImage
import coil.request.ImageRequest

private sealed class Route {
    data object Home : Route()
    data object Search : Route()
    data object People : Route()
    data class Person(val person: app.azimuthphoto.mobile.data.Person) : Route()
    data object Collections : Route()
    data class Collection(val collection: app.azimuthphoto.mobile.data.Collection) : Route()
    data object Places : Route()
    data class Tag(val tag: String) : Route()
    data class Similar(val image: ArchiveImage) : Route()
}

/**
 * The desktop-parity library: one search-and-browse home over people, collections,
 * places, tags, and memories, with a simple push/pop route stack. Every photo list
 * opens the same unified viewer, so archive photos behave exactly like device ones.
 */
@Composable
fun LibraryScreen(onImmersive: (Boolean) -> Unit = {}) {
    val context = LocalContext.current
    val settings by SettingsStore.flow(context).collectAsState(initial = null)
    val serverUrl = settings?.serverUrl ?: return
    val token = settings?.deviceToken?.takeIf { it.isNotBlank() }
    val api = remember(serverUrl, token) { LibraryApi(serverUrl, token) }
    val archiveApi = remember(serverUrl) { ArchiveApi(serverUrl) }

    val backStack = remember { mutableStateListOf<Route>(Route.Home) }
    fun push(route: Route) { backStack.add(route) }
    fun pop() { if (backStack.size > 1) backStack.removeAt(backStack.lastIndex) }
    val route = backStack.last()

    var viewer by remember { mutableStateOf<Pair<List<ArchiveImage>, Int>?>(null) }
    var addToCollection by remember { mutableStateOf<List<Long>?>(null) }
    val openPhotos: (List<ArchiveImage>, Int) -> Unit = { images, index -> viewer = images to index }

    LaunchedEffect(viewer != null) { onImmersive(viewer != null) }
    DisposableEffect(Unit) { onDispose { onImmersive(false) } }

    // Back closes the viewer first (its own handler), then pops the route stack.
    BackHandler(enabled = viewer == null && backStack.size > 1) { pop() }

    Box(Modifier.fillMaxSize()) {
        // The route stays composed under the viewer, so scroll and loaded pages
        // survive opening and closing a photo.
        when (val r = route) {
            Route.Home -> LibraryHome(
                api = api,
                serverUrl = serverUrl,
                onOpenSearch = { push(Route.Search) },
                onOpenPeople = { push(Route.People) },
                onOpenPerson = { push(Route.Person(it)) },
                onOpenCollections = { push(Route.Collections) },
                onOpenCollection = { push(Route.Collection(it)) },
                onOpenPlaces = { push(Route.Places) },
                onOpenTag = { push(Route.Tag(it)) },
                onOpenPhotos = openPhotos,
            )
            Route.Search -> SearchScreen(
                onImmersive = onImmersive,
                onFindSimilar = { push(Route.Similar(it)) },
            )
            Route.People -> PeopleScreen(api = api, onOpenPerson = { push(Route.Person(it)) })
            is Route.Person -> PersonScreen(api, r.person, onBack = { pop() }, onOpenPhotos = openPhotos)
            Route.Collections -> CollectionsScreen(
                api = api,
                onOpenCollection = { push(Route.Collection(it)) },
                onCreate = {},
            )
            is Route.Collection -> CollectionScreen(api, r.collection, onBack = { pop() }, onOpenPhotos = openPhotos)
            Route.Places -> PlacesScreen(api = api, onOpenPhoto = { id -> openPhotos(listOf(ArchiveImage(id = id)), 0) })
            is Route.Tag -> TagResultsScreen(api, r.tag, onBack = { pop() }, onOpenPhotos = openPhotos)
            is Route.Similar -> SimilarScreen(archiveApi, api, r.image, onBack = { pop() }, onOpenPhotos = openPhotos)
        }

        viewer?.let { (images, index) ->
            ViewerScreen(
                items = images.map { ViewerMedia.Remote(it) },
                startIndex = index,
                onClose = { viewer = null },
                api = archiveApi,
                onAddToCollection = { id -> addToCollection = listOf(id) },
                onFindSimilar = { image -> viewer = null; push(Route.Similar(image)) },
            )
        }
    }

    addToCollection?.let { ids ->
        AddToCollectionSheet(api = api, imageIds = ids, onDismiss = { addToCollection = null })
    }
}

@Composable
private fun LibraryHome(
    api: LibraryApi,
    serverUrl: String,
    onOpenSearch: () -> Unit,
    onOpenPeople: () -> Unit,
    onOpenPerson: (Person) -> Unit,
    onOpenCollections: () -> Unit,
    onOpenCollection: (Collection) -> Unit,
    onOpenPlaces: () -> Unit,
    onOpenTag: (String) -> Unit,
    onOpenPhotos: (List<ArchiveImage>, Int) -> Unit,
) {
    var people by remember { mutableStateOf<LoadState<List<Person>>>(LoadState.Loading) }
    var collections by remember { mutableStateOf<LoadState<List<Collection>>>(LoadState.Loading) }
    var reloads by remember { mutableStateOf(0) }
    LaunchedEffect(api, reloads) {
        people = LoadState.Loading
        collections = LoadState.Loading
        people = loadState { api.people() }
        collections = loadState { api.collections() }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).statusBarsPadding(),
    ) {
        // Search entry — routes to the full search surface.
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 10.dp)
                .clip(MaterialTheme.shapes.extraLarge)
                .background(Panel)
                .clickable(onClick = onOpenSearch)
                .padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(Icons.Outlined.Search, null, tint = TextSecondary)
            Spacer(Modifier.width(12.dp))
            Text("Search your photos", color = TextSecondary, style = MaterialTheme.typography.bodyLarge)
        }

        MemoriesStrip(serverUrl = serverUrl, onOpenPhotos = onOpenPhotos)

        val loadedPeople = (people as? LoadState.Ok<List<Person>>)?.value
        if (loadedPeople?.isNotEmpty() == true) {
            SectionHeader("People", actionLabel = "See all", onAction = onOpenPeople)
            Row(
                Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                    .padding(horizontal = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                loadedPeople.take(12).forEach { person ->
                    Column(
                        Modifier.width(76.dp).clickable { onOpenPerson(person) },
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        AsyncImage(
                            model = ImageRequest.Builder(LocalContext.current)
                                .data(api.thumb(person.face_thumb_url)).crossfade(false).build(),
                            contentDescription = person.displayName,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier.size(72.dp).clip(CircleShape).background(Panel),
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            person.displayName,
                            color = if (person.named) TextPrimary else TextSecondary,
                            style = MaterialTheme.typography.labelSmall,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }

        else if (people is LoadState.Error) {
            SectionHeader("People")
            ArchiveOfflineRow(onRetry = { reloads++ }, modifier = Modifier.padding(horizontal = 16.dp))
        }

        val loadedCollections = (collections as? LoadState.Ok<List<Collection>>)?.value
        if (loadedCollections?.isNotEmpty() == true) {
            SectionHeader("Collections", actionLabel = "See all", onAction = onOpenCollections)
            Row(
                Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                    .padding(horizontal = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                loadedCollections.take(10).forEach { collection ->
                    Column(Modifier.width(150.dp).clickable { onOpenCollection(collection) }) {
                        AsyncImage(
                            model = ImageRequest.Builder(LocalContext.current)
                                .data(collection.cover_image_id?.let { api.imageThumb(it, "md") })
                                .crossfade(false).build(),
                            contentDescription = collection.name,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier.fillMaxWidth().aspectRatio(1f)
                                .clip(MaterialTheme.shapes.medium).background(Panel),
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            collection.name, color = TextPrimary,
                            style = MaterialTheme.typography.bodyMedium, maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            "${collection.image_count} photos", color = TextSecondary,
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                }
            }
        } else if (collections is LoadState.Error) {
            SectionHeader("Collections")
            ArchiveOfflineRow(onRetry = { reloads++ }, modifier = Modifier.padding(horizontal = 16.dp))
        }

        // Places entry card.
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 12.dp)
                .clip(MaterialTheme.shapes.large).background(PanelHigh)
                .clickable(onClick = onOpenPlaces).padding(18.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(Icons.Outlined.Place, null, tint = TextPrimary)
            Spacer(Modifier.width(14.dp))
            Text("Places", color = TextPrimary, style = MaterialTheme.typography.titleMedium)
        }

        TagsSection(api = api, onOpenTag = onOpenTag)
        Spacer(Modifier.height(40.dp))
    }
}

@Composable
private fun SectionHeader(title: String, actionLabel: String? = null, onAction: () -> Unit = {}) {
    Row(
        Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 20.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, color = TextPrimary, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
        if (actionLabel != null) TextButton(onClick = onAction) { Text(actionLabel) }
    }
}
