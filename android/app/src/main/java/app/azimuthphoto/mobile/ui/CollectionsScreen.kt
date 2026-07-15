package app.azimuthphoto.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.AppSettings
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveCollection
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.CollectionDetail
import app.azimuthphoto.mobile.data.SettingsStore
import coil.compose.AsyncImage
import coil.request.ImageRequest

/** A read-only hub collection browser; curation still starts from the photo grid. */
@Composable
fun CollectionsScreen() {
    val context = LocalContext.current
    var settings by remember { mutableStateOf<AppSettings?>(null) }
    LaunchedEffect(Unit) { settings = SettingsStore.current(context) }
    val api = remember(settings?.serverUrl) { settings?.let { ArchiveApi(it.serverUrl) } } ?: return

    var collections by remember { mutableStateOf<List<ArchiveCollection>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var selectedCollection by remember { mutableStateOf<ArchiveCollection?>(null) }

    LaunchedEffect(api) {
        loading = true
        error = null
        runCatching { api.collections() }
            .onSuccess { collections = it }
            .onFailure { error = it.message ?: "Couldn't reach collections" }
        loading = false
    }

    selectedCollection?.let { collection ->
        CollectionPhotosScreen(api, collection, onClose = { selectedCollection = null })
        return
    }

    when {
        loading -> LoadingScreen()
        error != null -> MessageScreen(error!!)
        collections.isEmpty() -> MessageScreen("No collections yet")
        else -> LazyVerticalGrid(
            columns = GridCells.Fixed(2),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            items(collections, key = { it.id }) { collection ->
                CollectionCard(api, collection) { selectedCollection = collection }
            }
        }
    }
}

@Composable
private fun CollectionCard(api: ArchiveApi, collection: ArchiveCollection, onClick: () -> Unit) {
    Column(Modifier.clickable(onClick = onClick)) {
        Box(
            Modifier
                .fillMaxWidth()
                .aspectRatio(1.25f)
                .background(Panel),
            contentAlignment = Alignment.Center,
        ) {
            collection.active_cover_image_id?.let { coverId ->
                AsyncImage(
                    model = ImageRequest.Builder(LocalContext.current)
                        .data(api.thumbUrl(coverId, "md"))
                        .crossfade(true)
                        .build(),
                    contentDescription = "Cover for ${collection.name}",
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
            if (collection.active_cover_image_id == null) {
                Text("No photos", style = MaterialTheme.typography.labelMedium, color = TextSecondary)
            }
        }
        Text(
            collection.name.ifBlank { "Untitled collection" },
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            style = MaterialTheme.typography.titleSmall,
            color = TextPrimary,
            modifier = Modifier.padding(top = 7.dp),
        )
        Text(
            "${collection.image_count} ${if (collection.image_count == 1) "photo" else "photos"}" +
                if (collection.smart) " · Smart" else "",
            style = MaterialTheme.typography.bodySmall,
            color = TextSecondary,
        )
    }
}

@Composable
private fun CollectionPhotosScreen(api: ArchiveApi, collection: ArchiveCollection, onClose: () -> Unit) {
    BackHandler(onBack = onClose)
    var detail by remember { mutableStateOf<CollectionDetail?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var viewerIndex by remember { mutableStateOf<Int?>(null) }

    LaunchedEffect(collection.id) {
        loading = true
        error = null
        runCatching { api.collection(collection.id) }
            .onSuccess { detail = it }
            .onFailure { error = it.message ?: "Couldn't load collection" }
        loading = false
    }

    val images = detail?.collection?.images.orEmpty()
    viewerIndex?.let { index ->
        HubViewer(api, images, index, onClose = { viewerIndex = null })
        return
    }

    Column(Modifier.fillMaxSize()) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            IconButton(onClick = onClose) {
                Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Back", tint = TextPrimary)
            }
            Column(Modifier.weight(1f).padding(end = 12.dp)) {
                Text(
                    collection.name.ifBlank { "Untitled collection" },
                    style = MaterialTheme.typography.titleMedium,
                    color = TextPrimary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text("${collection.image_count} photos", style = MaterialTheme.typography.bodySmall, color = TextSecondary)
            }
        }
        when {
            loading -> LoadingScreen()
            error != null -> MessageScreen(error!!)
            images.isEmpty() -> MessageScreen("No photos in this collection")
            else -> CollectionGrid(api, images) { viewerIndex = it }
        }
    }
}

@Composable
private fun CollectionGrid(api: ArchiveApi, images: List<ArchiveImage>, onClick: (Int) -> Unit) {
    LazyVerticalGrid(
        columns = GridCells.Fixed(3),
        verticalArrangement = Arrangement.spacedBy(2.dp),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        itemsIndexed(images, key = { _, image -> image.id }) { index, image ->
            AsyncImage(
                model = ImageRequest.Builder(LocalContext.current)
                    .data(api.thumbUrl(image))
                    .crossfade(false)
                    .build(),
                contentDescription = image.filename,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .aspectRatio(1f)
                    .clickable { onClick(index) },
            )
        }
    }
}

@Composable
private fun LoadingScreen() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
}

@Composable
private fun MessageScreen(message: String) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(message, color = TextSecondary, style = MaterialTheme.typography.bodyMedium)
    }
}
