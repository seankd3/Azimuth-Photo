package app.azimuthphoto.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.rounded.PlayCircle
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import app.azimuthphoto.mobile.backup.BackupDb
import app.azimuthphoto.mobile.backup.BackupWorker
import app.azimuthphoto.mobile.data.DeviceMedia
import app.azimuthphoto.mobile.data.MediaItem
import java.time.LocalDate
import java.time.format.DateTimeFormatter

sealed class TimelineRow {
    data class Header(val day: LocalDate) : TimelineRow()
    data class Cell(val item: MediaItem) : TimelineRow()
}

@Composable
fun TimelineScreen() {
    val context = LocalContext.current
    var items by remember { mutableStateOf<List<MediaItem>?>(null) }
    var backupStates by remember { mutableStateOf<Map<Long, String>>(emptyMap()) }
    var viewerIndex by remember { mutableStateOf<Int?>(null) }
    val progress by BackupWorker.progress.collectAsState()

    LaunchedEffect(progress.running) {
        items = DeviceMedia.queryAll(context)
        backupStates = BackupDb.get(context).allStates()
    }

    val media = items
    if (media == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        return
    }

    viewerIndex?.let { index ->
        ViewerScreen(items = media, startIndex = index, onClose = { viewerIndex = null })
        return
    }

    val rows = remember(media) {
        buildList {
            var lastDay: LocalDate? = null
            media.forEach { item ->
                if (item.day != lastDay) {
                    add(TimelineRow.Header(item.day))
                    lastDay = item.day
                }
                add(TimelineRow.Cell(item))
            }
        }
    }
    val indexOf = remember(media) { media.withIndex().associate { it.value.id to it.index } }

    val gridState = rememberLazyGridState()
    LazyVerticalGrid(
        state = gridState,
        columns = GridCells.Fixed(4),
        modifier = Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(2.dp),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        items(
            items = rows,
            span = { row ->
                if (row is TimelineRow.Header) GridItemSpan(maxLineSpan) else GridItemSpan(1)
            },
            key = { row ->
                when (row) {
                    is TimelineRow.Header -> "h${row.day}"
                    is TimelineRow.Cell -> row.item.id
                }
            },
        ) { row ->
            when (row) {
                is TimelineRow.Header -> DayHeader(row.day)
                is TimelineRow.Cell -> MediaCell(
                    item = row.item,
                    backedUp = backupStates[row.item.id] == BackupDb.STATE_UPLOADED ||
                        backupStates[row.item.id] == BackupDb.STATE_PRESENT,
                    onClick = { viewerIndex = indexOf[row.item.id] },
                )
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

@Composable
private fun MediaCell(item: MediaItem, backedUp: Boolean, onClick: () -> Unit) {
    Box(
        Modifier
            .aspectRatio(1f)
            .background(Panel)
            .clickable(onClick = onClick)
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
        if (!backedUp) {
            // GPhotos-style: only the *not yet safe* items carry a mark.
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
    }
}

fun formatDuration(ms: Long): String {
    val totalSec = ms / 1000
    val m = totalSec / 60
    val s = totalSec % 60
    return "%d:%02d".format(m, s)
}
