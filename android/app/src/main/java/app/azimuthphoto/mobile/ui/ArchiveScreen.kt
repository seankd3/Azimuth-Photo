package app.azimuthphoto.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import app.azimuthphoto.mobile.data.AppSettings
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.SettingsStore

/** The full hub library: every photo you own, browsable and searchable from the couch. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ArchiveScreen() {
    val context = LocalContext.current
    var settings by remember { mutableStateOf<AppSettings?>(null) }
    LaunchedEffect(Unit) { settings = SettingsStore.current(context) }
    val api = remember(settings?.serverUrl) { settings?.let { ArchiveApi(it.serverUrl) } } ?: return

    var query by remember { mutableStateOf("") }
    var activeQuery by remember { mutableStateOf("") }
    var images by remember { mutableStateOf<List<ArchiveImage>>(emptyList()) }
    var totalVisible by remember { mutableStateOf(0L) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var viewerIndex by remember { mutableStateOf<Int?>(null) }

    LaunchedEffect(activeQuery) {
        loading = true
        error = null
        try {
            val page = api.page(offset = 0, search = activeQuery)
            images = page.images
            totalVisible = page.visible_images
        } catch (e: Exception) {
            error = e.message ?: "Couldn't reach the archive"
        }
        loading = false
    }

    viewerIndex?.let { index ->
        ArchiveViewer(api = api, images = images, startIndex = index, onClose = { viewerIndex = null })
        return
    }

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            placeholder = { Text("Search the archive", color = TextSecondary) },
            leadingIcon = { Icon(Icons.Outlined.Search, null, tint = TextSecondary) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { activeQuery = query }),
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = Panel,
                unfocusedContainerColor = Panel,
                focusedBorderColor = PanelHigh,
                unfocusedBorderColor = Panel,
            ),
            shape = MaterialTheme.shapes.extraLarge,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
        )

        when {
            loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            error != null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(error!!, color = TextSecondary, style = MaterialTheme.typography.bodyMedium)
            }
            else -> {
                val gridState = rememberLazyGridState()
                LazyVerticalGrid(
                    state = gridState,
                    columns = GridCells.Fixed(4),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    itemsIndexed(images, key = { _, img -> img.id }) { index, image ->
                        Box(
                            Modifier
                                .aspectRatio(1f)
                                .background(Panel)
                                .clickable { viewerIndex = index }
                        ) {
                            AsyncImage(
                                model = ImageRequest.Builder(context)
                                    .data(api.thumbUrl(image))
                                    .crossfade(false)
                                    .build(),
                                contentDescription = image.filename,
                                contentScale = ContentScale.Crop,
                                modifier = Modifier.fillMaxSize(),
                            )
                        }
                        if (index >= images.size - 40) {
                            LaunchedEffect(images.size) {
                                runCatching {
                                    val next = api.page(offset = images.size, search = activeQuery)
                                    if (next.images.isNotEmpty()) images = images + next.images
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ArchiveViewer(
    api: ArchiveApi,
    images: List<ArchiveImage>,
    startIndex: Int,
    onClose: () -> Unit,
) {
    BackHandler(onBack = onClose)
    val pagerState = rememberPagerState(initialPage = startIndex) { images.size }
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        HorizontalPager(state = pagerState, key = { images[it].id }) { page ->
            val image = images[page]
            AsyncImage(
                model = ImageRequest.Builder(LocalContext.current)
                    .data(api.largeUrl(image))
                    .crossfade(true)
                    .build(),
                contentDescription = image.filename,
                contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}
