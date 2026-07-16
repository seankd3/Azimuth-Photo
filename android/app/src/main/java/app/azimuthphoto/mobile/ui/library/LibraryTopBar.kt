package app.azimuthphoto.mobile.ui.library

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.ui.TextPrimary

/**
 * One back+title header for every library leaf route, so navigation grammar is
 * identical everywhere and no screen leaves you without a way out.
 */
@Composable
fun LibraryTopBar(title: String, onBack: () -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier
            .fillMaxWidth()
            .statusBarsPadding()
            .padding(end = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back", tint = TextPrimary)
        }
        Text(title, color = TextPrimary, style = MaterialTheme.typography.titleLarge)
    }
}
