package app.azimuthphoto.mobile.ui.library

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
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
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.launch

private val RowShape = RoundedCornerShape(10.dp)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AddToCollectionSheet(
    api: LibraryApi,
    imageIds: List<Long>,
    onDismiss: () -> Unit,
    onAdded: () -> Unit = {},
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var collections by remember { mutableStateOf<List<Collection>?>(null) }
    var busy by remember { mutableStateOf(false) }
    var creating by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        collections = runCatching { api.collections() }.getOrDefault(emptyList())
    }

    fun addTo(id: Long, name: String) {
        if (busy) return
        busy = true
        scope.launch {
            api.addToCollection(id, imageIds)
            Toast.makeText(context, "Added to $name", Toast.LENGTH_SHORT).show()
            onAdded()
            busy = false
            onDismiss()
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = Panel,
    ) {
        Column(Modifier.padding(horizontal = 16.dp).padding(bottom = 24.dp)) {
            Text(
                text = "Add ${imageIds.size} to collection",
                style = MaterialTheme.typography.titleMedium,
                color = TextPrimary,
                modifier = Modifier.padding(bottom = 12.dp),
            )

            NewCollectionRow(onClick = { creating = true })

            val loaded = collections
            if (loaded == null) {
                Box(Modifier.fillMaxWidth().height(120.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = TextSecondary)
                }
            } else {
                LazyColumn(
                    modifier = Modifier.heightIn(max = 420.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    items(items = loaded, key = { it.id }) { collection ->
                        CollectionRow(
                            api = api,
                            collection = collection,
                            onClick = { addTo(collection.id, collection.name.ifBlank { "Untitled" }) },
                        )
                    }
                }
            }
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
                                val id = api.createCollection(name)
                                if (id != null) addTo(id, name)
                            }
                        }
                    },
                ) {
                    Text("Create and add", color = Accent)
                }
            },
            dismissButton = {
                TextButton(onClick = { creating = false }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun NewCollectionRow(onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RowShape)
            .clickable(onClick = onClick)
            .padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier.size(44.dp).clip(RowShape).background(Ink),
            contentAlignment = Alignment.Center,
        ) {
            Icon(Icons.Rounded.Add, contentDescription = null, tint = Accent)
        }
        Text(
            text = "New collection",
            style = MaterialTheme.typography.bodyLarge,
            color = TextPrimary,
            modifier = Modifier.padding(start = 14.dp),
        )
    }
}

@Composable
private fun CollectionRow(api: LibraryApi, collection: Collection, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RowShape)
            .clickable(onClick = onClick)
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(modifier = Modifier.size(44.dp).clip(RowShape).background(Ink)) {
            val cover = collection.cover_image_id
            if (cover != null) {
                AsyncImage(
                    model = ImageRequest.Builder(LocalContext.current)
                        .data(api.imageThumb(cover, "sm"))
                        .crossfade(true)
                        .size(128)
                        .build(),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }
        Column(Modifier.padding(start = 14.dp)) {
            Text(
                text = collection.name.ifBlank { "Untitled" },
                style = MaterialTheme.typography.bodyLarge,
                color = TextPrimary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = "${collection.image_count} photos",
                style = MaterialTheme.typography.labelSmall,
                color = TextSecondary,
            )
        }
    }
}
