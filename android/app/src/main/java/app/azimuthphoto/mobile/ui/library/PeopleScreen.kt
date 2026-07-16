package app.azimuthphoto.mobile.ui.library

import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.DriveFileRenameOutline
import androidx.compose.material.icons.outlined.Merge
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.data.Person
import app.azimuthphoto.mobile.ui.Accent
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.launch

@Composable
fun PeopleScreen(api: LibraryApi, onBack: () -> Unit, onOpenPerson: (Person) -> Unit) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var people by remember { mutableStateOf<List<Person>?>(null) }
    var selected by remember { mutableStateOf(setOf<Long>()) }
    var confirmingMerge by remember { mutableStateOf(false) }
    var naming by remember { mutableStateOf<Person?>(null) }
    var busy by remember { mutableStateOf(false) }

    suspend fun reload() {
        people = runCatching { api.people() }.getOrDefault(emptyList())
    }

    LaunchedEffect(Unit) { reload() }

    // Back exits selection mode first; only then leaves the screen.
    BackHandler(enabled = selected.isNotEmpty()) { selected = emptySet() }

    Column(Modifier.fillMaxSize().background(Ink)) {
        if (selected.isNotEmpty()) {
            val chosen = people.orEmpty().filter { it.id in selected }
            Row(
                Modifier.fillMaxWidth().background(Panel).statusBarsPadding(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = { selected = emptySet() }) {
                    Icon(Icons.Outlined.Close, contentDescription = "Clear selection", tint = TextPrimary)
                }
                Text(
                    "${selected.size} selected",
                    style = MaterialTheme.typography.titleMedium,
                    color = TextPrimary,
                    modifier = Modifier.weight(1f),
                )
                if (chosen.size == 1) {
                    IconButton(onClick = { naming = chosen.single() }) {
                        Icon(Icons.Outlined.DriveFileRenameOutline, contentDescription = "Name person", tint = TextPrimary)
                    }
                }
                if (chosen.size >= 2) {
                    IconButton(enabled = !busy, onClick = { confirmingMerge = true }) {
                        Icon(Icons.Outlined.Merge, contentDescription = "Merge people", tint = TextPrimary)
                    }
                }
            }
        } else {
            LibraryTopBar(title = "People", onBack = onBack)
        }
        when (val loaded = people) {
            null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = TextSecondary)
            }
            else -> LazyVerticalGrid(
                columns = GridCells.Adaptive(96.dp),
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                items(items = loaded, key = { it.id }) { person ->
                    PersonCard(
                        api = api,
                        person = person,
                        selected = person.id in selected,
                        onOpen = {
                            if (selected.isNotEmpty()) selected = selected.toggle(person.id)
                            else onOpenPerson(person)
                        },
                        onLongPress = { selected = selected.toggle(person.id) },
                    )
                }
            }
        }
    }

    if (confirmingMerge) {
        val chosen = people.orEmpty().filter { it.id in selected }
        // Merge into the person the library knows best: a named one first, then most photos.
        val target = chosen.sortedWith(
            compareByDescending<Person> { it.named }.thenByDescending { it.photo_count },
        ).first()
        val sources = chosen.filter { it.id != target.id }
        AlertDialog(
            onDismissRequest = { confirmingMerge = false },
            containerColor = Panel,
            title = { Text("Merge into ${target.displayName}?") },
            text = {
                Text(
                    "${sources.joinToString { it.displayName }} will become ${target.displayName}. " +
                        "All their photos move together.",
                )
            },
            confirmButton = {
                TextButton(
                    enabled = !busy,
                    onClick = {
                        confirmingMerge = false
                        busy = true
                        scope.launch {
                            val failed = sources.count { !api.mergePeople(it.id, target.id) }
                            busy = false
                            selected = emptySet()
                            if (failed > 0) {
                                Toast.makeText(context, "Couldn't merge $failed", Toast.LENGTH_SHORT).show()
                            }
                            reload()
                        }
                    },
                ) { Text("Merge", color = Accent) }
            },
            dismissButton = {
                TextButton(onClick = { confirmingMerge = false }) { Text("Cancel") }
            },
        )
    }

    naming?.let { person ->
        var draft by remember(person.id) { mutableStateOf(if (person.named) person.displayName else "") }
        AlertDialog(
            onDismissRequest = { naming = null },
            containerColor = Panel,
            title = { Text(if (person.named) "Rename person" else "Name person") },
            text = {
                OutlinedTextField(value = draft, onValueChange = { draft = it }, singleLine = true)
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val next = draft.trim()
                        naming = null
                        if (next.isNotBlank()) {
                            scope.launch {
                                if (api.labelPerson(person.id, next)) {
                                    selected = emptySet()
                                    reload()
                                } else {
                                    Toast.makeText(context, "Couldn't name person", Toast.LENGTH_SHORT).show()
                                }
                            }
                        }
                    },
                ) { Text("Save", color = Accent) }
            },
            dismissButton = {
                TextButton(onClick = { naming = null }) { Text("Cancel") }
            },
        )
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun PersonCard(
    api: LibraryApi,
    person: Person,
    selected: Boolean,
    onOpen: () -> Unit,
    onLongPress: () -> Unit,
) {
    val haptics = LocalHapticFeedback.current
    Column(
        modifier = Modifier.combinedClickable(
            onClick = onOpen,
            onLongClick = {
                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                onLongPress()
            },
        ),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current)
                .data(api.thumb(person.face_thumb_url))
                .crossfade(true)
                .size(256)
                .build(),
            contentDescription = person.displayName,
            contentScale = ContentScale.Crop,
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(1f)
                .clip(CircleShape)
                .background(Panel, CircleShape)
                .then(if (selected) Modifier.border(3.dp, Accent, CircleShape) else Modifier),
        )
        Text(
            text = person.displayName,
            style = MaterialTheme.typography.bodyMedium,
            color = if (selected) Accent else TextPrimary,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
        )
        Text(
            text = "${person.photo_count}",
            style = MaterialTheme.typography.labelSmall,
            color = TextSecondary,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth().padding(top = 2.dp),
        )
    }
}

private fun Set<Long>.toggle(id: Long): Set<Long> = if (id in this) this - id else this + id
