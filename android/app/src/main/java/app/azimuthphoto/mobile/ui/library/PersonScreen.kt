package app.azimuthphoto.mobile.ui.library

import android.widget.Toast
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
import androidx.compose.material.icons.rounded.MoreVert
import androidx.compose.material3.AlertDialog
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
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.data.Person
import app.azimuthphoto.mobile.ui.Accent
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.PhotoGrid
import app.azimuthphoto.mobile.ui.TextPrimary
import kotlinx.coroutines.launch

private const val PAGE_SIZE = 200

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PersonScreen(
    api: LibraryApi,
    person: Person,
    onBack: () -> Unit,
    onOpenPhotos: (List<ArchiveImage>, Int) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var name by remember(person.id) { mutableStateOf(person.displayName) }
    var images by remember(person.id) { mutableStateOf<List<ArchiveImage>>(emptyList()) }
    var offset by remember(person.id) { mutableStateOf(0) }
    var loading by remember(person.id) { mutableStateOf(false) }
    var pageError by remember(person.id) { mutableStateOf(false) }
    var reachedEnd by remember(person.id) { mutableStateOf(false) }
    var menuOpen by remember { mutableStateOf(false) }
    var renaming by remember { mutableStateOf(false) }
    val context = LocalContext.current

    suspend fun loadMore() {
        if (loading || reachedEnd) return
        loading = true
        pageError = false
        runCatching { api.personPhotos(person.id, offset, PAGE_SIZE) }
            .onSuccess { page ->
                images = images + page
                offset += page.size
                if (page.size < PAGE_SIZE) reachedEnd = true
            }
            .onFailure { pageError = true }
        loading = false
    }

    LaunchedEffect(person.id) { loadMore() }

    BackHandler(onBack = onBack)

    Scaffold(
        containerColor = Ink,
        topBar = {
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
                    Text(
                        text = name,
                        style = MaterialTheme.typography.titleLarge,
                        modifier = Modifier.clickable { renaming = true },
                    )
                },
                actions = {
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
                            text = { Text("Hide person") },
                            onClick = {
                                menuOpen = false
                                scope.launch {
                                    if (api.ignorePerson(person.id)) {
                                        onBack()
                                    } else {
                                        Toast.makeText(context, "Couldn't hide person", Toast.LENGTH_SHORT).show()
                                    }
                                }
                            },
                        )
                    }
                },
            )
        },
    ) { padding ->
        PhotoGrid(
            images = images,
            thumbModel = { api.imageThumb(it.id, "sm") },
            onOpen = { index -> onOpenPhotos(images, index) },
            onNearEnd = { scope.launch { loadMore() } },
            modifier = Modifier.fillMaxSize().background(Ink).padding(padding),
        )
    }

    if (renaming) {
        var draft by remember { mutableStateOf(name) }
        AlertDialog(
            onDismissRequest = { renaming = false },
            title = { Text("Rename person") },
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
                                if (!api.labelPerson(person.id, next)) {
                                    name = previous
                                    Toast.makeText(context, "Couldn't rename person", Toast.LENGTH_SHORT).show()
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
}
