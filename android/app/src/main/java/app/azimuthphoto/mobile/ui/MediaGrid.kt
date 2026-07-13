package app.azimuthphoto.mobile.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.PointerInputScope
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.MediaItem
import app.azimuthphoto.mobile.data.SettingsStore
import coil.compose.AsyncImage
import coil.request.ImageRequest
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.launch

private sealed class MediaGridRow {
    data class Header(val day: LocalDate) : MediaGridRow()
    data class Cell(val item: MediaItem) : MediaGridRow()
}

@Composable
fun MediaGrid(
    items: List<MediaItem>,
    selectedIds: Set<Long>,
    onTap: (MediaItem) -> Unit,
    onLongPress: (MediaItem) -> Unit,
    backedUpIds: Set<Long> = emptySet(),
    showBackupState: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val settings by SettingsStore.flow(context).collectAsState(initial = null)
    val columns = settings?.gridColumns ?: 4
    val scope = rememberCoroutineScope()
    val gridState = rememberLazyGridState()
    var zoomAccumulator by remember(columns) { mutableFloatStateOf(1f) }
    val rawShots = remember(items) {
        items.asSequence().filter { it.isRaw }.map { it.shotKey }.toHashSet()
    }
    val rows = remember(items) {
        buildList {
            var lastDay: LocalDate? = null
            items.forEach { item ->
                if (item.day != lastDay) {
                    add(MediaGridRow.Header(item.day))
                    lastDay = item.day
                }
                add(MediaGridRow.Cell(item))
            }
        }
    }
    val selectionMode = selectedIds.isNotEmpty()

    Box(modifier) {
        LazyVerticalGrid(
            state = gridState,
            columns = GridCells.Fixed(columns),
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(columns) {
                    detectPinchZoom { zoom ->
                        zoomAccumulator *= zoom
                        val next = when {
                            zoomAccumulator > 1.18f -> (columns - 1).coerceAtLeast(3)
                            zoomAccumulator < 0.84f -> (columns + 1).coerceAtMost(5)
                            else -> columns
                        }
                        if (next != columns) {
                            zoomAccumulator = 1f
                            scope.launch { SettingsStore.setGridColumns(context, next) }
                        }
                    }
                },
            verticalArrangement = Arrangement.spacedBy(2.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            items(
                items = rows,
                span = { row ->
                    if (row is MediaGridRow.Header) GridItemSpan(maxLineSpan) else GridItemSpan(1)
                },
                key = { row ->
                    when (row) {
                        is MediaGridRow.Header -> "h${row.day}"
                        is MediaGridRow.Cell -> row.item.id
                    }
                },
            ) { row ->
                when (row) {
                    is MediaGridRow.Header -> DayHeader(row.day)
                    is MediaGridRow.Cell -> MediaCell(
                        item = row.item,
                        selected = row.item.id in selectedIds,
                        selectionMode = selectionMode,
                        backedUp = row.item.id in backedUpIds,
                        showBackupState = showBackupState,
                        hasRaw = row.item.shotKey in rawShots,
                        onClick = { onTap(row.item) },
                        onLongClick = { onLongPress(row.item) },
                    )
                }
            }
        }
        FastScrollScrubber(
            state = gridState,
            labelForIndex = { index ->
                val day = when (val row = rows.getOrNull(index)) {
                    is MediaGridRow.Header -> row.day
                    is MediaGridRow.Cell -> row.item.day
                    null -> null
                }
                day?.format(DateTimeFormatter.ofPattern("MMM yyyy")).orEmpty()
            },
            modifier = Modifier.align(Alignment.CenterEnd),
        )
    }
}

private suspend fun PointerInputScope.detectPinchZoom(onZoom: (Float) -> Unit) {
    awaitEachGesture {
        awaitFirstDown(requireUnconsumed = false)
        while (true) {
            val event = awaitPointerEvent()
            val pressed = event.changes.filter { it.pressed }
            if (pressed.isEmpty()) break
            if (pressed.size >= 2) {
                onZoom(event.calculateZoom())
                event.changes.forEach { it.consume() }
            }
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
        else -> day.format(
            if (day.year == LocalDate.now().year) HEADER_FORMAT_THIS_YEAR else HEADER_FORMAT
        )
    }
    Text(
        text = label,
        style = MaterialTheme.typography.titleSmall,
        color = TextPrimary,
        modifier = Modifier.padding(start = 14.dp, top = 22.dp, bottom = 8.dp),
    )
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun MediaCell(
    item: MediaItem,
    selected: Boolean,
    selectionMode: Boolean,
    backedUp: Boolean,
    showBackupState: Boolean,
    hasRaw: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
) {
    Box(
        Modifier
            .aspectRatio(1f)
            .background(Panel)
            .combinedClickable(onClick = onClick, onLongClick = onLongClick)
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current)
                .data(item.uri)
                .crossfade(false)
                .size(256)
                .build(),
            contentDescription = item.displayName,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )
        if (hasRaw && !item.isVideo) {
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
        if (item.isVideo) {
            Row(
                Modifier
                    .align(Alignment.TopEnd)
                    .padding(4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (item.durationMs > 0) {
                    Text(
                        formatDuration(item.durationMs),
                        style = MaterialTheme.typography.labelSmall,
                        color = Color.White,
                        modifier = Modifier.padding(end = 3.dp),
                    )
                }
                Icon(
                    Icons.Rounded.PlayCircle,
                    contentDescription = "Video",
                    tint = Color.White,
                    modifier = Modifier.size(16.dp),
                )
            }
        }
        if (showBackupState && !backedUp) {
            Icon(
                Icons.Outlined.CloudUpload,
                contentDescription = "Not backed up",
                tint = Color.White,
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .padding(4.dp)
                    .size(14.dp)
                    .alpha(0.85f),
            )
        }
        if (selectionMode) {
            Icon(
                if (selected) Icons.Rounded.CheckCircle else Icons.Outlined.CheckCircle,
                contentDescription = if (selected) "Selected" else "Not selected",
                tint = if (selected) Accent else Color.White,
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .padding(6.dp)
                    .size(22.dp),
            )
        }
    }
}
