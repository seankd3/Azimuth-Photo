package app.azimuthphoto.mobile.ui

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.awaitPointerEvent
import androidx.compose.ui.input.pointer.pointerInput
import kotlin.math.hypot

/** Google Photos-style grid density: pinch out for fewer, larger photos. */
const val MIN_GRID_COLUMNS = 2
const val MAX_GRID_COLUMNS = 8

fun Modifier.pinchToChangeGridDensity(
    columns: Int,
    onColumnsChange: (Int) -> Unit,
): Modifier = pointerInput(columns) {
    awaitEachGesture {
        var referenceDistance = 0f
        while (true) {
            val event = awaitPointerEvent()
            val pointers = event.changes.filter { it.pressed }
            if (pointers.size >= 2) {
                val first = pointers[0]
                val second = pointers[1]
                val distance = hypot(
                    first.position.x - second.position.x,
                    first.position.y - second.position.y,
                )
                if (referenceDistance == 0f) {
                    referenceDistance = distance
                } else if (distance > 0f) {
                    val ratio = distance / referenceDistance
                    when {
                        ratio > 1.16f && columns > MIN_GRID_COLUMNS -> {
                            onColumnsChange(columns - 1)
                            referenceDistance = distance
                        }
                        ratio < 0.86f && columns < MAX_GRID_COLUMNS -> {
                            onColumnsChange(columns + 1)
                            referenceDistance = distance
                        }
                    }
                }
            }
            if (event.changes.none { it.pressed }) break
        }
    }
}
