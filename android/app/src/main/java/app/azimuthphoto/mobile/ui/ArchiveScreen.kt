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
import android.content.Intent
import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material.icons.outlined.StarBorder
import androidx.compose.material3.IconButton
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.core.content.FileProvider
import kotlinx.coroutines.launch
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.ui.unit.Dp

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

    if (viewerIndex != null) {
        val navBottomInset = WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding()
        ArchiveViewer(
            api = api,
            images = images,
            startIndex = viewerIndex!!.coerceIn(0, images.lastIndex.coerceAtLeast(0)),
            onClose = { viewerIndex = null },
            bottomInset = navBottomInset,
            onTrash = { img ->
                images = images.filterNot { it.id == img.id }
                totalVisible = (totalVisible - 1).coerceAtLeast(0)
                if (images.isEmpty()) viewerIndex = null
            },
        )
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ArchiveViewer(
    api: ArchiveApi,
    images: List<ArchiveImage>,
    startIndex: Int,
    onClose: () -> Unit,
    onTrash: (ArchiveImage) -> Unit,
    bottomInset: Dp = 0.dp,
) {
    if (images.isEmpty()) {
        LaunchedEffect(Unit) { onClose() }
        return
    }
    Dialog(
        onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        val context = LocalContext.current
        val scope = rememberCoroutineScope()
        val pagerState = rememberPagerState(initialPage = startIndex.coerceIn(0, images.lastIndex)) { images.size }
        var chromeVisible by remember { mutableStateOf(true) }
        var infoFor by remember { mutableStateOf<ArchiveImage?>(null) }
        val flagOverrides = remember { mutableStateMapOf<Long, String>() }

        BackHandler(onBack = onClose)

        val page = pagerState.currentPage.coerceIn(0, images.lastIndex)
        val current = images[page]
        val isFav = (flagOverrides[current.id] ?: current.flag) == "picked"

        Box(Modifier.fillMaxSize().background(Color.Black)) {
            HorizontalPager(
                state = pagerState,
                key = { images[it].id },
                modifier = Modifier.fillMaxSize(),
            ) { p ->
                ZoomableArchiveImage(api, images[p], onTap = { chromeVisible = !chromeVisible })
            }

            rawLabel(current.file_ext)?.let { label ->
                AnimatedVisibility(
                    visible = chromeVisible,
                    enter = fadeIn(),
                    exit = fadeOut(),
                    modifier = Modifier.align(Alignment.TopCenter).statusBarsPadding().padding(top = 64.dp),
                ) {
                    FormatChip(label)
                }
            }

            AnimatedVisibility(
                visible = chromeVisible,
                enter = fadeIn(),
                exit = fadeOut(),
                modifier = Modifier.align(Alignment.TopCenter),
            ) {
                ViewerTopBar(
                    current = current,
                    isFav = isFav,
                    onClose = onClose,
                    onToggleFav = {
                        val next = if (isFav) "unflagged" else "picked"
                        flagOverrides[current.id] = next
                        scope.launch { runCatching { api.setFlag(current.id, next) } }
                    },
                    onInfo = { infoFor = current },
                )
            }

            AnimatedVisibility(
                visible = chromeVisible,
                enter = fadeIn(),
                exit = fadeOut(),
                modifier = Modifier.align(Alignment.BottomCenter),
            ) {
                Column(
                    Modifier
                        .fillMaxWidth()
                        .background(Brush.verticalGradient(listOf(Color.Transparent, Color.Black.copy(alpha = 0.88f)))),
                ) {
                    Filmstrip(
                        images = images,
                        currentIndex = page,
                        api = api,
                        onSelect = { idx -> scope.launch { pagerState.animateScrollToPage(idx) } },
                    )
                    ViewerActionBar(
                        bottomInset = bottomInset,
                        onShare = {
                            scope.launch {
                                runCatching {
                                    val file = api.downloadToCache(context, current)
                                    val uri = FileProvider.getUriForFile(
                                        context, "${context.packageName}.fileprovider", file,
                                    )
                                    val send = Intent(Intent.ACTION_SEND).apply {
                                        type = "image/*"
                                        putExtra(Intent.EXTRA_STREAM, uri)
                                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                    }
                                    context.startActivity(
                                        Intent.createChooser(send, null).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                                    )
                                }.onFailure {
                                    Toast.makeText(context, "Couldn't share photo", Toast.LENGTH_SHORT).show()
                                }
                            }
                        },
                        onInfo = { infoFor = current },
                        onTrash = {
                            val target = current
                            scope.launch {
                                val ok = runCatching { api.trashImage(target.id) }.getOrDefault(false)
                                if (ok) {
                                    Toast.makeText(context, "Moved to Trash", Toast.LENGTH_SHORT).show()
                                    onTrash(target)
                                } else {
                                    Toast.makeText(context, "Couldn't move to Trash", Toast.LENGTH_SHORT).show()
                                }
                            }
                        },
                    )
                }
            }

            infoFor?.let { image ->
                Box(
                    Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = 0.55f))
                        .pointerInput(Unit) { detectTapGestures(onTap = { infoFor = null }) },
                )
                ArchiveInfoCard(image = image, bottomInset = bottomInset, modifier = Modifier.align(Alignment.BottomCenter))
            }
        }
    }
}

@Composable
private fun ViewerTopBar(
    current: ArchiveImage,
    isFav: Boolean,
    onClose: () -> Unit,
    onToggleFav: () -> Unit,
    onInfo: () -> Unit,
) {
    val (day, time) = formatViewerDate(current.date_taken)
    Row(
        Modifier
            .fillMaxWidth()
            .background(Brush.verticalGradient(listOf(Color.Black.copy(alpha = 0.6f), Color.Transparent)))
            .statusBarsPadding()
            .padding(horizontal = 4.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onClose) {
            Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Back", tint = Color.White)
        }
        Column(Modifier.weight(1f).padding(start = 4.dp)) {
            Text(
                day.ifEmpty { current.filename },
                color = Color.White,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            val sub = listOfNotNull(
                time.takeIf { it.isNotBlank() },
                current.camera_model?.takeIf { it.isNotBlank() },
            ).joinToString("  ·  ")
            if (sub.isNotEmpty()) {
                Text(
                    sub,
                    color = Color.White.copy(alpha = 0.75f),
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        IconButton(onClick = onToggleFav) {
            Icon(
                if (isFav) Icons.Outlined.Star else Icons.Outlined.StarBorder,
                "Favorite",
                tint = if (isFav) Accent else Color.White,
            )
        }
        IconButton(onClick = onInfo) {
            Icon(Icons.Outlined.MoreVert, "Details", tint = Color.White)
        }
    }
}

@Composable
private fun Filmstrip(
    images: List<ArchiveImage>,
    currentIndex: Int,
    api: ArchiveApi,
    onSelect: (Int) -> Unit,
) {
    val listState = rememberLazyListState()
    LaunchedEffect(currentIndex) {
        if (currentIndex in images.indices) listState.animateScrollToItem(currentIndex)
    }
    LazyRow(
        state = listState,
        modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
        contentPadding = PaddingValues(horizontal = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        items(images.size, key = { images[it].id }) { index ->
            val img = images[index]
            val selected = index == currentIndex
            Box(
                Modifier
                    .size(if (selected) 60.dp else 48.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .border(
                        width = if (selected) 2.dp else 0.dp,
                        color = if (selected) Accent else Color.Transparent,
                        shape = RoundedCornerShape(8.dp),
                    )
                    .clickable { onSelect(index) },
            ) {
                AsyncImage(
                    model = ImageRequest.Builder(LocalContext.current).data(api.thumbUrl(img)).crossfade(false).build(),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }
    }
}

@Composable
private fun ViewerActionBar(
    onShare: () -> Unit,
    onInfo: () -> Unit,
    onTrash: () -> Unit,
    bottomInset: Dp = 0.dp,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp, vertical = 4.dp)
            .padding(bottom = bottomInset),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ViewerAction(Icons.Outlined.Share, "Share", onShare)
        ViewerAction(Icons.Outlined.Info, "Info", onInfo)
        ViewerAction(Icons.Outlined.Delete, "Trash", onTrash)
    }
}

@Composable
private fun ViewerAction(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    onClick: () -> Unit,
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .clip(RoundedCornerShape(12.dp))
            .clickable { onClick() }
            .padding(horizontal = 20.dp, vertical = 8.dp),
    ) {
        Icon(icon, label, tint = Color.White)
        Spacer(Modifier.height(4.dp))
        Text(label, color = Color.White, style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
private fun FormatChip(label: String) {
    Text(
        label,
        color = Color.White,
        style = MaterialTheme.typography.labelMedium,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .background(Color.Black.copy(alpha = 0.5f))
            .padding(horizontal = 12.dp, vertical = 5.dp),
    )
}

@Composable
private fun ArchiveInfoCard(image: ArchiveImage, bottomInset: Dp = 0.dp, modifier: Modifier = Modifier) {
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
            .background(Panel)
            .padding(20.dp)
            .padding(bottom = bottomInset),
    ) {
        Text(
            image.filename,
            color = TextPrimary,
            style = MaterialTheme.typography.titleMedium,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Spacer(Modifier.height(12.dp))
        val (day, time) = formatViewerDate(image.date_taken)
        if (day.isNotEmpty()) InfoRow("Taken", listOf(day, time).filter { it.isNotBlank() }.joinToString("  ·  "))
        image.camera_model?.takeIf { it.isNotBlank() }?.let { InfoRow("Camera", it) }
        image.lens?.takeIf { it.isNotBlank() }?.let { InfoRow("Lens", it) }
        if ((image.width ?: 0) > 0 && (image.height ?: 0) > 0) InfoRow("Dimensions", "${image.width} × ${image.height}")
        image.file_ext?.takeIf { it.isNotBlank() }?.let { InfoRow("Format", it.trimStart('.').uppercase()) }
        (image.file_size ?: 0L).takeIf { it > 0 }?.let { InfoRow("Size", formatBytes(it)) }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
        Text(label, color = TextSecondary, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(0.35f))
        Text(value, color = TextPrimary, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(0.65f))
    }
}

@Composable
private fun ZoomableArchiveImage(api: ArchiveApi, image: ArchiveImage, onTap: () -> Unit) {
    var scale by remember(image.id) { mutableStateOf(1f) }
    var offsetX by remember(image.id) { mutableStateOf(0f) }
    var offsetY by remember(image.id) { mutableStateOf(0f) }
    Box(
        Modifier
            .fillMaxSize()
            .pointerInput(image.id) {
                detectTapGestures(
                    onTap = { onTap() },
                    onDoubleTap = {
                        if (scale > 1f) {
                            scale = 1f; offsetX = 0f; offsetY = 0f
                        } else scale = 2.5f
                    },
                )
            }
            .pointerInput(image.id) {
                detectTransformGestures { _, pan, zoom, _ ->
                    scale = (scale * zoom).coerceIn(1f, 8f)
                    if (scale > 1f) {
                        offsetX += pan.x; offsetY += pan.y
                    } else {
                        offsetX = 0f; offsetY = 0f
                    }
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current).data(api.largeUrl(image)).crossfade(true).build(),
            contentDescription = image.filename,
            contentScale = ContentScale.Fit,
            modifier = Modifier
                .fillMaxSize()
                .graphicsLayer {
                    scaleX = scale
                    scaleY = scale
                    translationX = offsetX
                    translationY = offsetY
                },
        )
    }
}

private val RAW_EXTS = setOf("cr2", "cr3", "arw", "nef", "raf", "dng", "orf", "rw2", "raw", "srw", "pef", "nrw")

private fun rawLabel(ext: String?): String? {
    val e = ext?.trimStart('.')?.lowercase() ?: return null
    return if (e in RAW_EXTS) e.uppercase() else null
}

private fun formatViewerDate(raw: String?): Pair<String, String> {
    if (raw.isNullOrBlank()) return "" to ""
    return try {
        val dt = java.time.LocalDateTime.parse(raw.trim().replace(' ', 'T'))
        dt.format(java.time.format.DateTimeFormatter.ofPattern("EEE, MMM d")) to
            dt.format(java.time.format.DateTimeFormatter.ofPattern("h:mm a"))
    } catch (e: Exception) {
        raw to ""
    }
}
