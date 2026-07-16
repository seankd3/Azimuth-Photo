package app.azimuthphoto.mobile.ui.library

import androidx.compose.foundation.layout.Row
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import app.azimuthphoto.mobile.ui.TextSecondary

sealed interface LoadState<out T> {
    data object Loading : LoadState<Nothing>
    data class Ok<T>(val value: T) : LoadState<T>
    data object Error : LoadState<Nothing>
}

suspend fun <T> loadState(block: suspend () -> T): LoadState<T> = try {
    LoadState.Ok(block())
} catch (_: Exception) {
    LoadState.Error
}

@Composable
fun ArchiveOfflineRow(onRetry: () -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Outlined.CloudOff, contentDescription = null, tint = TextSecondary)
        Text("Archive offline", color = TextSecondary, style = MaterialTheme.typography.labelMedium)
        TextButton(onClick = onRetry) { Text("Retry") }
    }
}
