package app.azimuthphoto.mobile.ui

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.PointerInputScope
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import app.azimuthphoto.mobile.data.SettingsStore
import kotlinx.coroutines.launch

@Composable
fun rememberGridColumns(): Int {
    val context = LocalContext.current
    val settings by SettingsStore.flow(context).collectAsState(initial = null)
    return settings?.gridColumns ?: 4
}

@Composable
fun Modifier.gridDensityPinch(columns: Int): Modifier {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var zoomAccumulator by remember(columns) { mutableFloatStateOf(1f) }
    return pointerInput(columns) {
        detectGridDensityPinch { zoom ->
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
    }
}

private suspend fun PointerInputScope.detectGridDensityPinch(onZoom: (Float) -> Unit) {
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
