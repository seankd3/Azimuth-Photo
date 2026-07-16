package app.azimuthphoto.mobile.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.Public
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.Collection
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.ui.Accent
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.PanelHigh
import app.azimuthphoto.mobile.ui.Positive
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.launch

private val CardShape = RoundedCornerShape(12.dp)

@Composable
fun CollectionsScreen(
    api: LibraryApi,
    onOpenCollection: (Collection) -> Unit,
    onCreate: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var collections by remember { mutableStateOf<List<Collection>?>(null) }
    var loadFailed by remember { mutableStateOf(false) }
    var creating by remember { mutableStateOf(false) }

    suspend fun refresh() {
        runCatching { api.collections() }
            .onSuccess {
                collections = it
                loadFailed = false
            }
            .onFailure { loadFailed = true }
    }

    LaunchedEffect(Unit) { refresh() }

    val loaded = collections
    if (loaded == null) {
        Box(Modifier.fillMaxSize().background(Ink), contentAlignment = Alignment.Center) {
            if (loadFailed) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "Couldn't load collections",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextSecondary,
                    )
                    TextButton(onClick = { scope.launch { refresh() } }) {
                        Text("Retry", color = TextSecondary)
                    }
                }
            } else {
                CircularProgressIndicator(color = TextSecondary)
            }
        }
        return
    }

    LazyVerticalGrid(
        columns = GridCells.Adaptive(160.dp),
        modifier = Modifier.fillMaxSize().background(Ink),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item(key = "__new") {
            NewCollectionCard(
                onClick = {
                    onCreate()
                    creating = true
                },
            )
        }
        items(items = loaded, key = { it.id }) { collection ->
            CollectionCard(api = api, collection = collection, onOpen = { onOpenCollection(collection) })
        }
    }

    if (creating) {
        var draft by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { creating = false },
            title = { Text("New collection") },
            text = {
                OutlinedTextField(
                    value = draft,
                    onValueChange = { draft = it },
                    singleLine = true,
                    placeholder = { Text("Collection name") },
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val name = draft.trim()
                        creating = false
                        if (name.isNotBlank()) {
                            scope.launch {
                                api.createCollection(name)
                                refresh()
                            }
                        }
                    },
                ) {
                    Text("Create", color = Accent)
                }
            },
            dismissButton = {
                TextButton(onClick = { creating = false }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun NewCollectionCard(onClick: () -> Unit) {
    Column(modifier = Modifier.clickable(onClick = onClick)) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(1f)
                .clip(CardShape)
                .background(Panel)
                .border(1.dp, PanelHigh, CardShape),
            contentAlignment = Alignment.Center,
        ) {
            Icon(Icons.Rounded.Add, contentDescription = null, tint = TextSecondary)
        }
        Text(
            text = "New collection",
            style = MaterialTheme.typography.bodyMedium,
            color = TextPrimary,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
        )
    }
}

@Composable
private fun CollectionCard(api: LibraryApi, collection: Collection, onOpen: () -> Unit) {
    Column(modifier = Modifier.clickable(onClick = onOpen)) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(1f)
                .clip(CardShape)
                .background(Panel),
        ) {
            val cover = collection.cover_image_id
            if (cover != null) {
                AsyncImage(
                    model = ImageRequest.Builder(LocalContext.current)
                        .data(api.imageThumb(cover, "md"))
                        .crossfade(true)
                        .size(384)
                        .build(),
                    contentDescription = collection.name,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
            if (collection.published) {
                Icon(
                    Icons.Rounded.Public,
                    contentDescription = "Published",
                    tint = Positive,
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(8.dp)
                        .clip(CardShape)
                        .background(Ink.copy(alpha = 0.55f))
                        .padding(4.dp),
                )
            }
        }
        Text(
            text = collection.name.ifBlank { "Untitled" },
            style = MaterialTheme.typography.bodyMedium,
            color = TextPrimary,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
        )
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth().padding(top = 2.dp),
        ) {
            if (collection.smart) {
                // Smart = a saved search kept in sync with the library.
                Icon(
                    Icons.Outlined.AutoAwesome,
                    contentDescription = "Smart collection",
                    tint = Accent.copy(alpha = 0.8f),
                    modifier = Modifier.padding(end = 4.dp).size(14.dp),
                )
            }
            Text(
                text = "${collection.image_count} photos",
                style = MaterialTheme.typography.labelSmall,
                color = TextSecondary,
            )
        }
    }
}
