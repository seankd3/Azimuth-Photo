package app.azimuthphoto.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import coil.compose.AsyncImage
import app.azimuthphoto.mobile.ui.PhotoArchiveTheme

/** Handles ACTION_VIEW so photoArchive can be the phone's default photo opener. */
class ViewerActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val uri = intent?.data
        if (uri == null) { finish(); return }
        setContent {
            PhotoArchiveTheme {
                AsyncImage(
                    model = uri,
                    contentDescription = null,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize().background(Color.Black),
                )
            }
        }
    }
}
