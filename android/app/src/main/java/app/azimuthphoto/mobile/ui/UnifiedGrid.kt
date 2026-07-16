package app.azimuthphoto.mobile.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.PlayCircle
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.data.TimelineEntry
import coil.compose.AsyncImage
import coil.request.ImageRequest
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.flow.distinctUntilChanged

private sealed class Row {
    data class Header(val day: LocalDate) : Row()
    data class Cell(val entry: TimelineEntry) : Row()
}

/** One grid over the merged device+hub stream. Device cells select; hub cells don't. */
@Composable
fun UnifiedGrid(
    entries: List<TimelineEntry>,
    api: ArchiveApi,
    selectedIds: Set<Long>,
    onTapEntry: (TimelineEntry) -> Unit,
    onLongPressDevice: (MediaItem) -> Unit,
    onNearEnd: () -> Unit,
    contentPadding: PaddingValues = PaddingValues(0.dp),
    modifier: Modifier = Modifier,
) {
    val columns = rememberGridColumns()
    val gridState = rememberLazyGridState()
    val selectionMode = selectedIds.isNotEmpty()

    val rows = remember(entries) {
        buildList {
            var lastDay: LocalDate? = null
            entries.forEach { entry ->
                if (entry.day != lastDay) {
                    add(Row.Header(entry.day))
                    lastDay = entry.day
                }
                add(Row.Cell(entry))
            }
        }
    }

    LaunchedEffect(gridState, rows.size) {
        snapshotFlow { gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }
            .distinctUntilChanged()
            .collect { last -> if (rows.isNotEmpty() && last >= rows.size - 40) onNearEnd() }
    }

    Box(modifier) {
        LazyVerticalGrid(
            state = gridState,
            columns = GridCells.Fixed(columns),
            modifier = Modifier
                .fillMaxSize()
                .gridDensityPinch(columns),
            verticalArrangement = Arrangement.spacedBy(2.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp),
            contentPadding = contentPadding,
        ) {
            items(
                items = rows,
                span = { row -> if (row is Row.Header) GridItemSpan(maxLineSpan) else GridItemSpan(1) },
                key = { row ->
                    when (row) {
                        is Row.Header -> "h${row.day}"
                        is Row.Cell -> row.entry.gridKey
                    }
                },
            ) { row ->
                when (row) {
                    is Row.Header -> DayHeader(row.day)
                    is Row.Cell -> UnifiedCell(
                        entry = row.entry,
                        api = api,
                        selectionMode = selectionMode,
                        selected = (row.entry as? TimelineEntry.Device)?.item?.id in selectedIds,
                        onClick = { onTapEntry(row.entry) },
                        onLongClick = {
                            (row.entry as? TimelineEntry.Device)?.let { onLongPressDevice(it.item) }
                        },
                    )
                }
            }
        }
        FastScrollScrubber(
            state = gridState,
            labelForIndex = { index ->
                val day = when (val row = rows.getOrNull(index)) {
                    is Row.Header -> row.day
                    is Row.Cell -> row.entry.day
                    null -> null
                }
                day?.format(DateTimeFormatter.ofPattern("MMM yyyy")).orEmpty()
            },
            modifier = Modifier.align(Alignment.CenterEnd),
        )
    }
}

/**
 * The timeline's grid grammar — day headers, pinch-zoom columns (3–5), and the
 * fast-scroll scrubber — over a plain list of hub images. Library leaf screens
 * (person, collection, tag, similar) render through this so leaving the timeline
 * never drops you into a lesser grid.
 */
@Composable
fun PhotoGrid(
    images: List<ArchiveImage>,
    thumbModel: (ArchiveImage) -> Any,
    onOpen: (Int) -> Unit,
    modifier: Modifier = Modifier,
    contentPadding: PaddingValues = PaddingValues(0.dp),
    onNearEnd: () -> Unit = {},
    // Optional multi-select: long-press starts it, checkmarks render while any
    // id is selected. Hosts decide what tap does in selection mode via onOpen.
    selectedIds: Set<Long> = emptySet(),
    onLongPress: ((ArchiveImage) -> Unit)? = null,
) {
    val columns = rememberGridColumns()
    val gridState = rememberLazyGridState()

    // Rows carry the source index so a tap maps back to the caller's list position.
    val rows = remember(images) {
        buildList {
            var lastDay: LocalDate? = null
            images.forEachIndexed { index, image ->
                val day = image.localDate()
                if (day != null && day != lastDay) {
                    add(ImageRow.Header(day))
                    lastDay = day
                }
                add(ImageRow.Cell(image, index))
            }
        }
    }

    LaunchedEffect(gridState, rows.size) {
        snapshotFlow { gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }
            .distinctUntilChanged()
            .collect { last -> if (rows.isNotEmpty() && last >= rows.size - 40) onNearEnd() }
    }

    Box(modifier) {
        LazyVerticalGrid(
            state = gridState,
            columns = GridCells.Fixed(columns),
            modifier = Modifier
                .fillMaxSize()
                .gridDensityPinch(columns),
            verticalArrangement = Arrangement.spacedBy(2.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp),
            contentPadding = contentPadding,
        ) {
            items(
                items = rows,
                span = { row -> if (row is ImageRow.Header) GridItemSpan(maxLineSpan) else GridItemSpan(1) },
                key = { row ->
                    when (row) {
                        is ImageRow.Header -> "h${row.day}"
                        is ImageRow.Cell -> row.image.id
                    }
                },
            ) { row ->
                when (row) {
                    is ImageRow.Header -> DayHeader(row.day)
                    is ImageRow.Cell -> PhotoCell(
                        model = thumbModel(row.image),
                        isVideo = row.image.isVideo,
                        hasRaw = row.image.isRaw,
                        durationMs = 0,
                        notBackedUp = false,
                        showSelection = onLongPress != null && selectedIds.isNotEmpty(),
                        selected = row.image.id in selectedIds,
                        onClick = { onOpen(row.index) },
                        onLongClick = { onLongPress?.invoke(row.image) },
                    )
                }
            }
        }
        FastScrollScrubber(
            state = gridState,
            labelForIndex = { index ->
                val day = when (val row = rows.getOrNull(index)) {
                    is ImageRow.Header -> row.day
                    is ImageRow.Cell -> row.image.localDate()
                    null -> null
                }
                day?.format(DateTimeFormatter.ofPattern("MMM yyyy")).orEmpty()
            },
            modifier = Modifier.align(Alignment.CenterEnd),
        )
    }
}

private sealed class ImageRow {
    data class Header(val day: LocalDate) : ImageRow()
    data class Cell(val image: ArchiveImage, val index: Int) : ImageRow()
}

private fun ArchiveImage.localDate(): LocalDate? =
    date_taken?.take(10)?.let { runCatching { LocalDate.parse(it) }.getOrNull() }

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun UnifiedCell(
    entry: TimelineEntry,
    api: ArchiveApi,
    selectionMode: Boolean,
    selected: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
) {
    val model: Any
    val isVideo: Boolean
    val durationMs: Long
    val hasRaw: Boolean
    val notBackedUp: Boolean
    val selectable: Boolean
    when (entry) {
        is TimelineEntry.Device -> {
            model = entry.item.uri
            isVideo = entry.item.isVideo
            durationMs = entry.item.durationMs
            hasRaw = entry.hasRaw
            notBackedUp = !entry.backedUp
            selectable = true
        }
        is TimelineEntry.Hub -> {
            model = api.thumbUrl(entry.image)
            isVideo = entry.image.isVideo
            durationMs = 0
            hasRaw = entry.image.isRaw
            notBackedUp = false
            selectable = false
        }
    }
    PhotoCell(
        model = model,
        isVideo = isVideo,
        hasRaw = hasRaw,
        durationMs = durationMs,
        notBackedUp = notBackedUp,
        showSelection = selectionMode && selectable,
        selected = selected,
        onClick = onClick,
        onLongClick = onLongClick,
    )
}

/** The one photo-tile visual used by every grid — timeline and library alike. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun PhotoCell(
    model: Any,
    isVideo: Boolean,
    hasRaw: Boolean,
    durationMs: Long,
    notBackedUp: Boolean,
    showSelection: Boolean,
    selected: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
) {
    val haptics = LocalHapticFeedback.current
    Box(
        Modifier
            .aspectRatio(1f)
            .background(Panel)
            .combinedClickable(
                onClick = onClick,
                onLongClick = {
                    haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                    onLongClick()
                },
            ),
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current).data(model).crossfade(false).size(256).build(),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )
        // Top scrim so white RAW/video glyphs stay legible on bright thumbnails.
        if (hasRaw || isVideo) {
            Box(
                Modifier
                    .fillMaxSize()
                    .background(
                        Brush.verticalGradient(
                            0f to Color.Black.copy(alpha = 0.28f),
                            0.28f to Color.Transparent,
                        ),
                    ),
            )
        }
        if (hasRaw) {
            Text(
                "RAW",
                style = MaterialTheme.typography.labelSmall,
                color = Color.White,
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .padding(4.dp)
                    .background(Color.Black.copy(alpha = 0.45f), MaterialTheme.shapes.extraSmall)
                    .padding(horizontal = 4.dp, vertical = 1.dp)
                    .alpha(0.9f),
            )
        }
        if (isVideo) {
            Row(
                Modifier.align(Alignment.TopEnd).padding(4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (durationMs > 0) {
                    Text(
                        formatDuration(durationMs),
                        style = MaterialTheme.typography.labelSmall,
                        color = Color.White,
                        modifier = Modifier.padding(end = 3.dp),
                    )
                }
                Icon(Icons.Rounded.PlayCircle, null, tint = Color.White, modifier = Modifier.size(16.dp))
            }
        }
        if (notBackedUp) {
            Icon(
                Icons.Outlined.CloudUpload,
                contentDescription = "Not backed up",
                tint = Color.White,
                modifier = Modifier.align(Alignment.BottomEnd).padding(4.dp).size(14.dp).alpha(0.85f),
            )
        }
        if (showSelection) {
            Icon(
                if (selected) Icons.Rounded.CheckCircle else Icons.Outlined.CheckCircle,
                contentDescription = if (selected) "Selected" else "Not selected",
                tint = if (selected) Accent else Color.White,
                modifier = Modifier.align(Alignment.BottomStart).padding(6.dp).size(22.dp),
            )
        }
    }
}

private val HEADER_FORMAT = DateTimeFormatter.ofPattern("EEE, MMM d, yyyy")
private val HEADER_FORMAT_THIS_YEAR = DateTimeFormatter.ofPattern("EEE, MMM d")

@Composable
private fun DayHeader(day: LocalDate) {
    val label = when (day) {
        LocalDate.now() -> "Today"
        LocalDate.now().minusDays(1) -> "Yesterday"
        else -> day.format(if (day.year == LocalDate.now().year) HEADER_FORMAT_THIS_YEAR else HEADER_FORMAT)
    }
    Text(
        text = label,
        style = MaterialTheme.typography.titleSmall,
        color = TextPrimary,
        modifier = Modifier.padding(start = 14.dp, top = 22.dp, bottom = 8.dp),
    )
}
