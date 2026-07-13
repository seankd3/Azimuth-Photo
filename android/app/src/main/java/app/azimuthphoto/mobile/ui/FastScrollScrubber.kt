package app.azimuthphoto.mobile.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

@Composable
fun FastScrollScrubber(
    state: LazyGridState,
    labelForIndex: (Int) -> String,
    modifier: Modifier = Modifier,
) {
    var dragging by remember { mutableStateOf(false) }
    var heightPx by remember { mutableIntStateOf(0) }
    var dragY by remember { mutableFloatStateOf(0f) }
    val scope = rememberCoroutineScope()
    val visibleAlpha by animateFloatAsState(
        targetValue = if (state.isScrollInProgress || dragging) 1f else 0f,
        label = "scrubberAlpha",
    )
    val total = state.layoutInfo.totalItemsCount
    val fraction = if (total <= 1) 0f else {
        state.firstVisibleItemIndex.toFloat() / (total - 1).toFloat()
    }.coerceIn(0f, 1f)
    val handleHeightPx = with(LocalDensity.current) { 48.dp.toPx() }

    fun scrollTo(y: Float) {
        if (heightPx <= 0 || total <= 0) return
        dragY = y.coerceIn(0f, heightPx.toFloat())
        val target = ((dragY / heightPx) * (total - 1)).roundToInt().coerceIn(0, total - 1)
        scope.launch { state.scrollToItem(target) }
    }

    Box(modifier.fillMaxHeight(), contentAlignment = Alignment.CenterEnd) {
        Box(
            Modifier
                .fillMaxHeight()
                .width(36.dp)
                .align(Alignment.CenterEnd)
                .onSizeChanged { heightPx = it.height }
                .pointerInput(total, heightPx) {
                    detectVerticalDragGestures(
                        onDragStart = {
                            dragging = true
                            scrollTo(it.y)
                        },
                        onDragEnd = { dragging = false },
                        onDragCancel = { dragging = false },
                        onVerticalDrag = { change, amount ->
                            change.consume()
                            scrollTo(dragY + amount)
                        },
                    )
                },
        )
        Box(
            Modifier
                .align(Alignment.TopEnd)
                .padding(end = 4.dp)
                .offset {
                    IntOffset(
                        0,
                        ((heightPx - handleHeightPx).coerceAtLeast(0f) * fraction).roundToInt(),
                    )
                }
                .size(width = 5.dp, height = 48.dp)
                .alpha(visibleAlpha)
                .background(TextSecondary.copy(alpha = 0.8f), MaterialTheme.shapes.small),
        )
        if (dragging) {
            Text(
                text = labelForIndex(state.firstVisibleItemIndex),
                color = Color.White,
                style = MaterialTheme.typography.titleSmall,
                modifier = Modifier
                    .align(Alignment.CenterEnd)
                    .padding(end = 46.dp)
                    .background(PanelHigh.copy(alpha = 0.96f), MaterialTheme.shapes.large)
                    .padding(horizontal = 14.dp, vertical = 9.dp),
            )
        }
    }
}
