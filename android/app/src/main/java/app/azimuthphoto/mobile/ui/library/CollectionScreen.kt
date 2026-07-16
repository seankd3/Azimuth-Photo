package app.azimuthphoto.mobile.ui.library

import android.content.Intent
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.IosShare
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.layout.ContentScale
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
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import app.azimuthphoto.mobile.ui.gridDensityPinch
import app.azimuthphoto.mobile.ui.rememberGridColumns
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CollectionScreen(
    api: LibraryApi,
    collection: Collection,
    onBack: () -> Unit,
    onOpenPhotos: (List<ArchiveImage>, Int) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val toastContext = LocalContext.current
    var photos by remember(collection.id) { mutableStateOf<LoadState<List<ArchiveImage>>>(LoadState.Loading) }
    var reloads by remember(collection.id) { mutableStateOf(0) }
    var sharing by remember { mutableStateOf(false) }
    var shareUrl by remember { mutableStateOf<String?>(null) }
    val columns = rememberGridColumns()

    LaunchedEffect(collection.id, reloads) {
        photos = LoadState.Loading
        photos = loadState { api.collectionPhotos(collection.id) }
    }

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
                        text = collection.name.ifBlank { "Untitled" },
                        style = MaterialTheme.typography.titleLarge,
                    )
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
                                else android.widget.Toast.makeText(
                                    toastContext, "Couldn't create a share link", android.widget.Toast.LENGTH_SHORT,
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
                },
            )
        },
    ) { padding ->
        val state = photos
        if (state is LoadState.Loading) {
            Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) {
                CircularProgressIndicator(color = TextSecondary)
            }
        } else if (state is LoadState.Error) {
            Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) {
                ArchiveOfflineRow(onRetry = { reloads++ })
            }
        } else if ((state as LoadState.Ok<List<ArchiveImage>>).value.isEmpty()) {
            Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) {
                Text("No photos in this collection yet.", color = TextSecondary)
            }
        } else {
            val loaded = state.value
            LazyVerticalGrid(
                columns = GridCells.Fixed(columns),
                modifier = Modifier.fillMaxSize().background(Ink).padding(padding).gridDensityPinch(columns),
                verticalArrangement = Arrangement.spacedBy(2.dp),
                horizontalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                itemsIndexed(items = loaded, key = { _, image -> image.id }) { index, image ->
                    Box(
                        Modifier
                            .aspectRatio(1f)
                            .background(Panel)
                            .clickable { onOpenPhotos(loaded, index) },
                    ) {
                        AsyncImage(
                            model = ImageRequest.Builder(LocalContext.current)
                                .data(api.imageThumb(image.id, "sm"))
                                .crossfade(false)
                                .size(256)
                                .build(),
                            contentDescription = image.filename,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier.fillMaxSize(),
                        )
                    }
                }
            }
        }
    }

    val url = shareUrl
    if (url != null) {
        val context = LocalContext.current
        val clipboard = LocalClipboardManager.current
        AlertDialog(
            onDismissRequest = { shareUrl = null },
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
