package app.azimuthphoto.mobile.ui.library

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.data.Tag
import app.azimuthphoto.mobile.ui.PanelHigh
import app.azimuthphoto.mobile.ui.TextPrimary

private const val MAX_TAGS = 24

/** A wrapping flow of the most-used tags, tap to open everything tagged that way. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TagsSection(api: LibraryApi, onOpenTag: (String) -> Unit) {
    var tags by remember { mutableStateOf<List<Tag>?>(null) }

    LaunchedEffect(Unit) {
        tags = runCatching { api.tags() }.getOrDefault(emptyList())
    }

    val loaded = tags
    if (loaded.isNullOrEmpty()) return

    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
        Text(
            text = "Things",
            style = MaterialTheme.typography.titleMedium,
            color = TextPrimary,
            modifier = Modifier.padding(top = 8.dp, bottom = 14.dp),
        )
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            loaded.take(MAX_TAGS).forEach { tag ->
                AssistChip(
                    onClick = { onOpenTag(tag.tag) },
                    label = {
                        Text(if (tag.count > 0) "${tag.tag} ${tag.count}" else tag.tag)
                    },
                    colors = AssistChipDefaults.assistChipColors(
                        containerColor = PanelHigh,
                        labelColor = TextPrimary,
                    ),
                    border = null,
                )
            }
        }
    }
}
