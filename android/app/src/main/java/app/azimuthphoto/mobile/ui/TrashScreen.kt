package app.azimuthphoto.mobile.ui

import android.provider.MediaStore
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.DeleteForever
import androidx.compose.material.icons.outlined.RestoreFromTrash
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.MediaItem

@Composable
fun TrashScreen(onClose: () -> Unit) {
    val context = LocalContext.current
    var items by remember { mutableStateOf<List<MediaItem>?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    var selectedIds by rememberSaveable(
        stateSaver = listSaver(
            save = { it.toList() },
            restore = { it.toSet() },
        ),
    ) { mutableStateOf(emptySet<Long>()) }
    val requestLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartIntentSenderForResult(),
    ) {
        selectedIds = emptySet()
        refresh++
    }

    LaunchedEffect(refresh) { items = DeviceMedia.queryTrashed(context) }
    BackHandler {
        if (selectedIds.isNotEmpty()) selectedIds = emptySet() else onClose()
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier
                .fillMaxWidth()
                .background(Panel)
                .statusBarsPadding(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = {
                if (selectedIds.isNotEmpty()) selectedIds = emptySet() else onClose()
            }) {
                Icon(
                    if (selectedIds.isEmpty()) Icons.AutoMirrored.Rounded.ArrowBack
                    else Icons.Outlined.Close,
                    contentDescription = if (selectedIds.isEmpty()) "Back" else "Clear selection",
                )
            }
            Text(
                if (selectedIds.isEmpty()) "Trash" else "${selectedIds.size} selected",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            if (selectedIds.isNotEmpty()) {
                IconButton(onClick = {
                    val uris = items.orEmpty().filter { it.id in selectedIds }.map { it.uri }
                    val pending = MediaStore.createTrashRequest(context.contentResolver, uris, false)
                    requestLauncher.launch(IntentSenderRequest.Builder(pending).build())
                }) {
                    Icon(Icons.Outlined.RestoreFromTrash, contentDescription = "Restore")
                }
                IconButton(onClick = {
                    val uris = items.orEmpty().filter { it.id in selectedIds }.map { it.uri }
                    val pending = MediaStore.createDeleteRequest(context.contentResolver, uris)
                    requestLauncher.launch(IntentSenderRequest.Builder(pending).build())
                }) {
                    Icon(Icons.Outlined.DeleteForever, contentDescription = "Delete forever")
                }
            }
        }
        Text(
            "Items are removed forever after 30 days",
            color = TextSecondary,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
        )
        when (val media = items) {
            null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            emptyList<MediaItem>() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("Trash is empty", color = TextSecondary)
            }
            else -> MediaGrid(
                items = media,
                selectedIds = selectedIds,
                onTap = { item ->
                    selectedIds = if (selectedIds.isEmpty()) setOf(item.id)
                    else selectedIds.toggleTrash(item.id)
                },
                onLongPress = { item -> selectedIds = selectedIds + item.id },
                modifier = Modifier.weight(1f),
            )
        }
    }
}

private fun Set<Long>.toggleTrash(id: Long): Set<Long> =
    if (id in this) this - id else this + id
