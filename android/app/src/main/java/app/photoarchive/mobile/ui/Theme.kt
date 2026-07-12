package app.photoarchive.mobile.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Ink = Color(0xFF0B0B0D)
val Panel = Color(0xFF141418)
val PanelHigh = Color(0xFF1D1D23)
val TextPrimary = Color(0xFFE8E8EC)
val TextSecondary = Color(0xFF9B9BA6)
val Accent = Color(0xFF7C9CFF)
val Positive = Color(0xFF6FCF97)

private val DarkScheme = darkColorScheme(
    primary = Accent,
    onPrimary = Ink,
    background = Ink,
    onBackground = TextPrimary,
    surface = Panel,
    onSurface = TextPrimary,
    surfaceVariant = PanelHigh,
    onSurfaceVariant = TextSecondary,
    secondaryContainer = PanelHigh,
    onSecondaryContainer = TextPrimary,
)

@Composable
fun PhotoArchiveTheme(content: @Composable () -> Unit) {
    // The archive is dark-first by design, matching the desktop app.
    MaterialTheme(colorScheme = DarkScheme, content = content)
}
