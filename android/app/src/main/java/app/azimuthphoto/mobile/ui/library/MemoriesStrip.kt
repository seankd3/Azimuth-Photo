package app.azimuthphoto.mobile.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.Memories
import app.azimuthphoto.mobile.data.Memory
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import coil.compose.AsyncImage
import coil.request.ImageRequest
import java.time.LocalDate

/**
 * "On this day" flashback strip: past years' best photos taken on today's date.
 * Renders nothing when there are no memories, so it never leaves an empty header behind.
 */
@Composable
fun MemoriesStrip(serverUrl: String, onOpenPhotos: (List<ArchiveImage>, Int) -> Unit) {
    var memories by remember { mutableStateOf<List<Memory>?>(null) }

    LaunchedEffect(serverUrl) {
        val today = LocalDate.now()
        memories = runCatching { Memories.onThisDay(serverUrl, today) }.getOrDefault(emptyList())
    }

    val loaded = memories ?: emptyList()
    if (loaded.isEmpty()) {
        Spacer(Modifier.height(0.dp))
        return
    }

    Column(Modifier.fillMaxWidth()) {
        Text(
            text = "Memories",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            color = TextPrimary,
            modifier = Modifier.padding(start = 16.dp, end = 16.dp, bottom = 12.dp),
        )
        LazyRow(
            modifier = Modifier.fillMaxWidth(),
            contentPadding = PaddingValues(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            items(items = loaded, key = { it.year }) { memory ->
                MemoryCard(
                    serverUrl = serverUrl,
                    memory = memory,
                    onOpen = { onOpenPhotos(memory.images, 0) },
                )
            }
        }
    }
}

@Composable
private fun MemoryCard(serverUrl: String, memory: Memory, onOpen: () -> Unit) {
    val shape = RoundedCornerShape(16.dp)
    val cover = memory.images.first()
    val label = "${memory.yearsAgo} year${if (memory.yearsAgo == 1) "" else "s"} ago"

    Box(
        modifier = Modifier
            .width(140.dp)
            .height(180.dp)
            .clip(shape)
            .background(Panel, shape)
            .clickable(onClick = onOpen),
    ) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current)
                .data("$serverUrl/api/thumb/md/${cover.id}")
                .crossfade(true)
                .build(),
            contentDescription = label,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )
        // A soft bottom scrim keeps the label legible over any cover photo.
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        0.55f to Color.Transparent,
                        1f to Color(0xCC000000),
                    ),
                ),
        )
        Column(
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(12.dp),
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
                color = TextPrimary,
            )
            Text(
                text = "${memory.year}",
                style = MaterialTheme.typography.labelSmall,
                color = TextSecondary,
            )
        }
    }
}
