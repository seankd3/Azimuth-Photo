package app.azimuthphoto.mobile.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.data.Tag
import app.azimuthphoto.mobile.ui.PanelHigh
import app.azimuthphoto.mobile.ui.TextPrimary
import coil.compose.AsyncImage

private const val MAX_TAGS = 24

// Same stale-while-revalidate idea as LibraryHome's shelves: paint the
// last-known tags instantly, revalidate in the background.
private var tagsCache: List<Tag>? = null
private val thumbCache = mutableMapOf<String, Long?>()

/** A row of thumbnail cards for the most-used tags, tap to open everything tagged that way. */
@Composable
fun TagsSection(api: LibraryApi, onOpenTag: (String) -> Unit) {
    var tags by remember { mutableStateOf(tagsCache) }

    LaunchedEffect(Unit) {
        runCatching { api.tags() }.onSuccess { tags = it; tagsCache = it }
        if (tags == null) tags = emptyList()
    }

    val loaded = tags
    if (loaded.isNullOrEmpty()) return

    Column(Modifier.fillMaxWidth()) {
        Text(
            text = "Things",
            style = MaterialTheme.typography.titleMedium,
            color = TextPrimary,
            modifier = Modifier.padding(start = 16.dp, top = 8.dp, bottom = 14.dp),
        )
        LazyRow(
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            contentPadding = PaddingValues(horizontal = 16.dp),
        ) {
            items(loaded.take(MAX_TAGS), key = { it.tag }) { tag ->
                TagCard(api = api, tag = tag, onClick = { onOpenTag(tag.tag) })
            }
        }
    }
}

/** One square card — a representative photo behind the tag name, fetched lazily on first composition. */
@Composable
private fun TagCard(api: LibraryApi, tag: Tag, onClick: () -> Unit) {
    var thumbId by remember(tag.tag) { mutableStateOf(thumbCache[tag.tag]) }
    LaunchedEffect(tag.tag) {
        if (thumbId == null && tag.tag !in thumbCache) {
            thumbId = runCatching { api.tagPhotos(tag.tag, 0, 1).firstOrNull()?.id }.getOrNull()
            thumbCache[tag.tag] = thumbId
        }
    }
    Box(
        Modifier
            .size(104.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(PanelHigh)
            .clickable(onClick = onClick),
    ) {
        thumbId?.let { id ->
            AsyncImage(
                model = api.imageThumb(id, "sm"),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
        }
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        0.45f to Color.Transparent,
                        1f to Color.Black.copy(alpha = 0.75f),
                    ),
                ),
        )
        Column(Modifier.align(Alignment.BottomStart).padding(8.dp)) {
            Text(
                text = tag.tag,
                style = MaterialTheme.typography.labelLarge,
                color = Color.White,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (tag.count > 0) {
                Text(
                    text = "%,d".format(tag.count),
                    style = MaterialTheme.typography.labelSmall,
                    color = Color.White.copy(alpha = 0.75f),
                )
            }
        }
    }
}
