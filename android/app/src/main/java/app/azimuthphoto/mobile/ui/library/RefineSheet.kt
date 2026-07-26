package app.azimuthphoto.mobile.ui.library

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material.icons.rounded.Star
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.FilterOptions
import app.azimuthphoto.mobile.data.Person
import app.azimuthphoto.mobile.data.SearchFilters
import app.azimuthphoto.mobile.ui.Accent
import app.azimuthphoto.mobile.ui.Ink
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.PanelHigh
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import coil.compose.AsyncImage
import java.util.Locale

private val MonthNames = listOf(
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

/** 48,246 → "48k"; under 1000 stays exact. Counts read thin, never shouty. */
private fun abbrev(count: Int): String =
    if (count >= 1000) "${count / 1000}k" else count.toString()

private fun formatCount(count: Long): String = String.format(Locale.US, "%,d", count)

/**
 * The "Refine" tool: stacks facet filters over the current search. Holds a draft
 * seeded from [initial]; every edit calls [onDraftChanged] so the host can debounce
 * a live-count query, and [onApply] commits the draft.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RefineSheet(
    initial: SearchFilters,
    options: FilterOptions?,
    people: List<Person>,
    liveCount: Long?,
    onDraftChanged: (SearchFilters) -> Unit,
    onApply: (SearchFilters) -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var draft by remember(initial) { mutableStateOf(initial) }

    fun update(next: SearchFilters) {
        draft = next
        onDraftChanged(next)
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = Panel,
    ) {
        Column(Modifier.padding(horizontal = 16.dp).padding(bottom = 24.dp)) {
            RefineHeader(
                liveCount = liveCount,
                showReset = draft.activeCount > 0,
                onReset = {
                    // Reset clears refinements only — the text query (q/deep) and sort survive.
                    update(
                        draft.copy(
                            people = emptyList(), tag = "", dateTaken = "", fileType = "",
                            camera = "", lens = "", minStars = 0, flag = "",
                            orientation = "", folders = emptyList(),
                        ),
                    )
                },
            )

            if (options == null) {
                Box(
                    Modifier.fillMaxWidth().height(180.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(color = TextSecondary)
                }
            } else {
                Column(
                    modifier = Modifier
                        .weight(1f, fill = false)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(18.dp),
                ) {
                    if (people.isNotEmpty()) {
                        PeopleSection(
                            people = people,
                            selected = draft.people,
                            onToggle = { id ->
                                val next = if (id in draft.people) draft.people - id
                                else draft.people + id
                                update(draft.copy(people = next))
                            },
                        )
                    }

                    if (options.years.isNotEmpty()) {
                        DateSection(
                            options = options,
                            dateTaken = draft.dateTaken,
                            onChange = { update(draft.copy(dateTaken = it)) },
                        )
                    }

                    if (options.file_types.isNotEmpty()) {
                        Section("Type") {
                            ChipFlow {
                                options.file_types.forEach { facet ->
                                    FacetChip(
                                        label = ".${facet.ext}",
                                        count = facet.count,
                                        selected = draft.fileType == facet.ext,
                                        onClick = {
                                            update(
                                                draft.copy(
                                                    fileType = if (draft.fileType == facet.ext) "" else facet.ext,
                                                ),
                                            )
                                        },
                                    )
                                }
                            }
                        }
                    }

                    if (options.cameras.isNotEmpty()) {
                        Section("Camera") {
                            ChipFlow {
                                options.cameras.sortedByDescending { it.count }.take(12).forEach { facet ->
                                    FacetChip(
                                        label = facet.camera,
                                        count = facet.count,
                                        selected = draft.camera == facet.camera,
                                        onClick = {
                                            update(
                                                draft.copy(
                                                    camera = if (draft.camera == facet.camera) "" else facet.camera,
                                                ),
                                            )
                                        },
                                    )
                                }
                            }
                        }
                    }

                    if (options.lenses.isNotEmpty()) {
                        Section("Lens") {
                            ChipFlow {
                                options.lenses.sortedByDescending { it.count }.take(12).forEach { facet ->
                                    FacetChip(
                                        label = facet.lens,
                                        count = facet.count,
                                        selected = draft.lens == facet.lens,
                                        onClick = {
                                            update(
                                                draft.copy(
                                                    lens = if (draft.lens == facet.lens) "" else facet.lens,
                                                ),
                                            )
                                        },
                                    )
                                }
                            }
                        }
                    }

                    Section("Rating") {
                        StarRow(
                            minStars = draft.minStars,
                            onChange = { update(draft.copy(minStars = it)) },
                        )
                    }

                    Section("More") {
                        ChipFlow {
                            listOf("picked" to "Picked", "unflagged" to "Unflagged", "rejected" to "Rejected")
                                .forEach { (value, label) ->
                                    FacetChip(
                                        label = label,
                                        count = null,
                                        selected = draft.flag == value,
                                        onClick = {
                                            update(draft.copy(flag = if (draft.flag == value) "" else value))
                                        },
                                    )
                                }
                            listOf("landscape" to "Landscape", "portrait" to "Portrait")
                                .forEach { (value, label) ->
                                    FacetChip(
                                        label = label,
                                        count = null,
                                        selected = draft.orientation == value,
                                        onClick = {
                                            update(
                                                draft.copy(
                                                    orientation = if (draft.orientation == value) "" else value,
                                                ),
                                            )
                                        },
                                    )
                                }
                        }
                    }

                    Section("Sort") {
                        ChipFlow {
                            listOf("date_taken" to "Newest", "elo" to "Best").forEach { (value, label) ->
                                FacetChip(
                                    label = label,
                                    count = null,
                                    selected = draft.sort == value,
                                    onClick = { update(draft.copy(sort = value)) },
                                )
                            }
                        }
                    }
                }
            }

            Spacer(Modifier.height(16.dp))
            Button(
                onClick = { onApply(draft) },
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = Ink),
            ) {
                Text(
                    text = liveCount?.let { "Show ${formatCount(it)} photos" } ?: "Show results",
                    style = MaterialTheme.typography.labelLarge,
                )
            }
        }
    }
}

// ---- Header ----

@Composable
private fun RefineHeader(liveCount: Long?, showReset: Boolean, onReset: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = "Refine",
            style = MaterialTheme.typography.titleMedium,
            color = TextPrimary,
        )
        Spacer(Modifier.weight(1f))
        if (liveCount == null) {
            CircularProgressIndicator(
                modifier = Modifier.size(14.dp),
                color = TextSecondary,
                strokeWidth = 1.5.dp,
            )
        } else {
            Text(
                text = "${formatCount(liveCount)} photos",
                style = MaterialTheme.typography.labelMedium,
                color = TextSecondary,
            )
        }
        if (showReset) {
            TextButton(onClick = onReset, modifier = Modifier.padding(start = 4.dp)) {
                Text("Reset", color = Accent, style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}

// ---- Section scaffolding ----

@Composable
private fun Section(label: String, content: @Composable () -> Unit) {
    Column {
        Text(
            text = label,
            style = MaterialTheme.typography.labelMedium,
            color = TextSecondary,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        content()
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChipFlow(content: @Composable () -> Unit) {
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        content()
    }
}

/** Compact facet chip: name in primary, count trailing in secondary. */
@Composable
private fun FacetChip(label: String, count: Int?, selected: Boolean, onClick: () -> Unit) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = {
            Text(
                text = buildAnnotatedString {
                    append(label)
                    if (count != null) {
                        withStyle(SpanStyle(color = TextSecondary)) {
                            append(" · ${abbrev(count)}")
                        }
                    }
                },
                style = MaterialTheme.typography.labelMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        },
        colors = FilterChipDefaults.filterChipColors(
            containerColor = PanelHigh,
            labelColor = TextPrimary,
            selectedContainerColor = Accent.copy(alpha = 0.22f),
            selectedLabelColor = TextPrimary,
        ),
        border = null,
    )
}

// ---- People ----

@Composable
private fun PeopleSection(people: List<Person>, selected: List<Long>, onToggle: (Long) -> Unit) {
    Section("People") {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            people.take(20).forEach { person ->
                PersonChip(
                    person = person,
                    selected = person.id in selected,
                    onClick = { onToggle(person.id) },
                )
            }
        }
    }
}

@Composable
private fun PersonChip(person: Person, selected: Boolean, onClick: () -> Unit) {
    Column(
        modifier = Modifier.width(64.dp).clickable(onClick = onClick),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        val ring = if (selected) {
            Modifier.border(2.dp, Accent, CircleShape)
        } else {
            Modifier
        }
        Box(
            modifier = Modifier
                .size(56.dp)
                .then(ring)
                .padding(if (selected) 3.dp else 0.dp)
                .clip(CircleShape)
                .background(PanelHigh),
            contentAlignment = Alignment.Center,
        ) {
            if (person.face_thumb_url.isBlank()) {
                Text(
                    text = person.displayName.take(1).uppercase(),
                    style = MaterialTheme.typography.titleMedium,
                    color = TextSecondary,
                )
            } else {
                AsyncImage(
                    model = person.face_thumb_url,
                    contentDescription = person.displayName,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }
        Text(
            text = person.displayName,
            style = MaterialTheme.typography.labelSmall,
            color = if (selected) TextPrimary else TextSecondary,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}

// ---- Date ----

@Composable
private fun DateSection(options: FilterOptions, dateTaken: String, onChange: (String) -> Unit) {
    val selectedYear = dateTaken.substringBefore("-").takeIf { it.isNotBlank() }
    val selectedMonth = dateTaken.substringAfter("-", "").takeIf { it.isNotBlank() }

    Section("Date") {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            ChipFlow {
                options.years.forEach { facet ->
                    FacetChip(
                        label = facet.year,
                        count = facet.count,
                        selected = selectedYear == facet.year,
                        onClick = {
                            onChange(if (selectedYear == facet.year) "" else facet.year)
                        },
                    )
                }
            }
            if (selectedYear != null) {
                ChipFlow {
                    MonthNames.forEachIndexed { index, name ->
                        val mm = String.format(Locale.US, "%02d", index + 1)
                        FacetChip(
                            label = name,
                            count = null,
                            selected = selectedMonth == mm,
                            onClick = {
                                // Tapping the selected month reverts to the bare year.
                                onChange(if (selectedMonth == mm) selectedYear else "$selectedYear-$mm")
                            },
                        )
                    }
                }
            }
        }
    }
}

// ---- Rating ----

@Composable
private fun StarRow(minStars: Int, onChange: (Int) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        (1..5).forEach { star ->
            val lit = star <= minStars
            Icon(
                imageVector = if (lit) Icons.Rounded.Star else Icons.Outlined.StarOutline,
                contentDescription = "$star stars and up",
                tint = if (lit) Accent else TextSecondary,
                modifier = Modifier
                    .size(32.dp)
                    .clip(CircleShape)
                    .clickable { onChange(if (minStars == star) 0 else star) }
                    .padding(4.dp),
            )
        }
        if (minStars > 0) {
            Text(
                text = "and up",
                style = MaterialTheme.typography.labelMedium,
                color = TextSecondary,
                modifier = Modifier.padding(start = 8.dp),
            )
        }
    }
}
