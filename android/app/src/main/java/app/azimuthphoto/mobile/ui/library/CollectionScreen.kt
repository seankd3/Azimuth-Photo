package app.azimuthphoto.mobile.ui.library

import android.content.Intent
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.outlined.RemoveCircleOutline
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.IosShare
import androidx.compose.material.icons.rounded.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.Collection
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.ui.Accent
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.PhotoGrid
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import kotlinx.coroutines.launch

private fun Set<Long>.toggle(id: Long): Set<Long> = if (id in this) this - id else this + id

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CollectionScreen(
    api: LibraryApi,
    collection: Collection,
    onBack: () -> Unit,
    onOpenPhotos: (List<ArchiveImage>, Int) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var name by remember(collection.id) { mutableStateOf(collection.name) }
    var photos by remember(collection.id) { mutableStateOf<List<ArchiveImage>?>(null) }
    var sharing by remember { mutableStateOf(false) }
    var shareUrl by remember { mutableStateOf<String?>(null) }
    var menuOpen by remember { mutableStateOf(false) }
    var renaming by remember { mutableStateOf(false) }
    var confirmingDelete by remember { mutableStateOf(false) }
    var selected by remember(collection.id) { mutableStateOf(setOf<Long>()) }
    var confirmingRemove by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf(false) }

    suspend fun reload() {
        photos = runCatching { api.collectionPhotos(collection.id) }.getOrDefault(emptyList())
    }

    LaunchedEffect(collection.id) { reload() }

    // Back exits selection mode first; only then leaves the screen.
    BackHandler {
        if (selected.isNotEmpty()) selected = emptySet() else onBack()
    }

    Scaffold(
        containerColor = Ink,
        topBar = {
            if (selected.isNotEmpty()) {
                // Selection bar — replaces the normal bar while photos are selected.
                TopAppBar(
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = Ink,
                        titleContentColor = TextPrimary,
                        navigationIconContentColor = TextPrimary,
                        actionIconContentColor = TextPrimary,
                    ),
                    navigationIcon = {
                        IconButton(onClick = { selected = emptySet() }) {
                            Icon(Icons.Rounded.Close, contentDescription = "Clear selection")
                        }
                    },
                    title = {
                        Text(
                            text = "${selected.size} selected",
                            style = MaterialTheme.typography.titleLarge,
                        )
                    },
                    actions = {
                        IconButton(enabled = !removing, onClick = { confirmingRemove = true }) {
                            Icon(
                                Icons.Outlined.RemoveCircleOutline,
                                contentDescription = "Remove from collection",
                            )
                        }
                    },
                )
            } else {
                TopAppBar(
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = Ink,
                        titleContentColor = TextPrimary,
                        navigationIconContentColor = TextPrimary,
                        actionIconContentColor = TextPrimary,
                    ),
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back")
                        }
                    },
                    title = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                text = name.ifBlank { "Untitled" },
                                style = MaterialTheme.typography.titleLarge,
                            )
                            if (collection.smart) {
                                // Smart = a saved search the hub keeps in sync with the library.
                                Text(
                                    text = "Smart",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = TextSecondary,
                                    modifier = Modifier
                                        .padding(start = 8.dp)
                                        .background(Panel, RoundedCornerShape(6.dp))
                                        .padding(horizontal = 6.dp, vertical = 2.dp),
                                )
                            }
                        }
                    },
                    actions = {
                        IconButton(
                            enabled = !sharing,
                            onClick = {
                                sharing = true
                                scope.launch {
                                    // Share only creates a private share link — never
                                    // publish (make public) as a side effect of tapping share.
                                    val link = api.share(collection.id)
                                    sharing = false
                                    if (link != null) shareUrl = link
                                    else Toast.makeText(
                                        context, "Couldn't create a share link", Toast.LENGTH_SHORT,
                                    ).show()
                                }
                            },
                        ) {
                            if (sharing) {
                                CircularProgressIndicator(
                                    color = TextSecondary,
                                    strokeWidth = 2.dp,
                                    modifier = Modifier.size(20.dp),
                                )
                            } else {
                                Icon(Icons.Rounded.IosShare, contentDescription = "Share")
                            }
                        }
                        IconButton(onClick = { menuOpen = true }) {
                            Icon(Icons.Rounded.MoreVert, contentDescription = "More")
                        }
                        DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                            DropdownMenuItem(
                                text = { Text("Rename") },
                                onClick = {
                                    menuOpen = false
                                    renaming = true
                                },
                            )
                            DropdownMenuItem(
                                text = { Text("Delete collection") },
                                onClick = {
                                    menuOpen = false
                                    confirmingDelete = true
                                },
                            )
                        }
                    },
                )
            }
        },
    ) { padding ->
        val loaded = photos
        if (loaded == null) {
            Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) {
                CircularProgressIndicator(color = TextSecondary)
            }
        } else if (loaded.isEmpty()) {
            Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) {
                Text("No photos in this collection yet.", color = TextSecondary)
            }
        } else {
            PhotoGrid(
                images = loaded,
                thumbModel = { api.imageThumb(it.id, "sm") },
                onOpen = { index ->
                    // In selection mode a tap toggles; otherwise it opens the viewer.
                    if (selected.isNotEmpty()) selected = selected.toggle(loaded[index].id)
                    else onOpenPhotos(loaded, index)
                },
                modifier = Modifier.fillMaxSize().background(Ink).padding(padding),
                selectedIds = selected,
                // Smart collections track a search — membership isn't hand-editable.
                onLongPress = if (collection.smart) null else ({ img -> selected = selected.toggle(img.id) }),
            )
        }
    }

    if (renaming) {
        var draft by remember { mutableStateOf(name) }
        AlertDialog(
            onDismissRequest = { renaming = false },
            containerColor = Panel,
            title = { Text("Rename collection") },
            text = {
                OutlinedTextField(
                    value = draft,
                    onValueChange = { draft = it },
                    singleLine = true,
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val next = draft.trim()
                        renaming = false
                        if (next.isNotBlank() && next != name) {
                            val previous = name
                            name = next
                            scope.launch {
                                if (!api.renameCollection(collection.id, next)) {
                                    name = previous
                                    Toast.makeText(context, "Couldn't rename collection", Toast.LENGTH_SHORT).show()
                                }
                            }
                        }
                    },
                ) {
                    Text("Save", color = Accent)
                }
            },
            dismissButton = {
                TextButton(onClick = { renaming = false }) { Text("Cancel") }
            },
        )
    }

    if (confirmingDelete) {
        AlertDialog(
            onDismissRequest = { confirmingDelete = false },
            containerColor = Panel,
            title = { Text("Delete collection?") },
            text = {
                Text(
                    "Deletes the collection — photos stay in your library.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary,
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmingDelete = false
                        scope.launch {
                            if (api.deleteCollection(collection.id)) onBack()
                            else Toast.makeText(context, "Couldn't delete collection", Toast.LENGTH_SHORT).show()
                        }
                    },
                ) {
                    Text("Delete", color = Accent)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmingDelete = false }) { Text("Cancel") }
            },
        )
    }

    if (confirmingRemove && selected.isNotEmpty()) {
        val count = selected.size
        AlertDialog(
            onDismissRequest = { confirmingRemove = false },
            containerColor = Panel,
            title = { Text(if (count == 1) "Remove 1 photo?" else "Remove $count photos?") },
            text = {
                Text(
                    "Removes them from this collection — photos stay in your library.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary,
                )
            },
            confirmButton = {
                TextButton(
                    enabled = !removing,
                    onClick = {
                        confirmingRemove = false
                        removing = true
                        val ids = selected.toList()
                        scope.launch {
                            val ok = api.removeFromCollection(collection.id, ids)
                            if (ok) {
                                selected = emptySet()
                                reload()
                            } else {
                                Toast.makeText(context, "Couldn't remove photos", Toast.LENGTH_SHORT).show()
                            }
                            removing = false
                        }
                    },
                ) {
                    Text("Remove", color = Accent)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmingRemove = false }) { Text("Cancel") }
            },
        )
    }

    val url = shareUrl
    if (url != null) {
        val clipboard = LocalClipboardManager.current
        AlertDialog(
            onDismissRequest = { shareUrl = null },
            containerColor = Panel,
            title = { Text("Share link") },
            text = {
                Text(
                    text = url,
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary,
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val send = Intent(Intent.ACTION_SEND).apply {
                            type = "text/plain"
                            putExtra(Intent.EXTRA_TEXT, url)
                        }
                        context.startActivity(Intent.createChooser(send, "Share collection"))
                        shareUrl = null
                    },
                ) {
                    Text("Share", color = Accent)
                }
            },
            dismissButton = {
                TextButton(
                    onClick = { clipboard.setText(AnnotatedString(url)) },
                ) {
                    Text("Copy link")
                }
            },
        )
    }
}
