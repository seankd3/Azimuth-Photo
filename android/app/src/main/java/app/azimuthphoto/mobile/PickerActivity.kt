package app.azimuthphoto.mobile

import android.content.ClipData
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Close
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
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.ui.MediaGrid
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.AzimuthPhotoTheme

class PickerActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val allowMultiple = intent.getBooleanExtra(Intent.EXTRA_ALLOW_MULTIPLE, false)
        val accept = mimePredicate(intent)
        setContent {
            AzimuthPhotoTheme {
                PickerScreen(
                    allowMultiple = allowMultiple,
                    accept = accept,
                    onCancel = ::finish,
                    onResult = ::returnSelection,
                )
            }
        }
    }

    private fun returnSelection(uris: List<Uri>) {
        if (uris.isEmpty()) return
        val result = Intent().addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        // Persistable grant so ACTION_GET_CONTENT callers can takePersistableUriPermission.
        if (intent.action == Intent.ACTION_GET_CONTENT) {
            result.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        }
        // Always set data (first uri) too — many callers read getData() even for multi.
        result.data = uris.first()
        if (uris.size > 1) {
            result.clipData = ClipData.newUri(contentResolver, "Selected media", uris.first()).apply {
                uris.drop(1).forEach { addItem(ClipData.Item(it)) }
            }
        }
        setResult(RESULT_OK, result)
        finish()
    }

    /** Honour the requested MIME (type + EXTRA_MIME_TYPES); default to all media. */
    private fun mimePredicate(intent: Intent): (MediaItem) -> Boolean {
        val types = buildList {
            intent.type?.let { add(it) }
            intent.getStringArrayExtra(Intent.EXTRA_MIME_TYPES)?.let { addAll(it) }
        }
        val wantImages = types.isEmpty() || types.any { it.startsWith("image/") || it == "*/*" }
        val wantVideos = types.isEmpty() || types.any { it.startsWith("video/") || it == "*/*" }
        if (wantImages == wantVideos) return { true } // both (or neither specified) → all
        return { item -> if (item.isVideo) wantVideos else wantImages }
    }
}

@Composable
private fun PickerScreen(
    allowMultiple: Boolean,
    accept: (MediaItem) -> Boolean,
    onCancel: () -> Unit,
    onResult: (List<Uri>) -> Unit,
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    var items by remember { mutableStateOf<List<MediaItem>?>(null) }
    var selectedIds by rememberSaveable(
        stateSaver = listSaver(
            save = { it.toList() },
            restore = { it.toSet() },
        ),
    ) { mutableStateOf(emptySet<Long>()) }

    LaunchedEffect(Unit) {
        items = DeviceMedia.collapseRawPairs(DeviceMedia.queryAll(context)).filter(accept)
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier
                .fillMaxWidth()
                .background(Panel)
                .statusBarsPadding(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onCancel) {
                Icon(Icons.Outlined.Close, contentDescription = "Cancel")
            }
            Text(
                if (allowMultiple) "Select items" else "Select photo",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            if (allowMultiple) {
                IconButton(
                    enabled = selectedIds.isNotEmpty(),
                    onClick = {
                        onResult(items.orEmpty().filter { it.id in selectedIds }.map { it.uri })
                    },
                ) {
                    Icon(Icons.Outlined.Check, contentDescription = "Confirm selection")
                }
            }
        }

        val media = items
        if (media == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
        } else {
            MediaGrid(
                items = media,
                selectedIds = selectedIds,
                onTap = { item ->
                    if (allowMultiple) {
                        selectedIds = selectedIds.toggle(item.id)
                    } else {
                        onResult(listOf(item.uri))
                    }
                },
                onLongPress = { item ->
                    if (allowMultiple) selectedIds = selectedIds + item.id
                },
                modifier = Modifier.weight(1f),
            )
        }
    }
}

private fun Set<Long>.toggle(id: Long): Set<Long> =
    if (id in this) this - id else this + id
