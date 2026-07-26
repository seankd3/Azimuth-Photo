package app.azimuthphoto.mobile

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.backup.BackupScheduler
import app.azimuthphoto.mobile.ui.AzimuthPhotoTheme
import kotlinx.coroutines.delay

/** Share target: receiving media starts a backup pass and briefly confirms it. */
class ShareActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val count = sharedItemCount()
        BackupScheduler.runNow(this)
        setContent {
            AzimuthPhotoTheme {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Surface(
                        shape = MaterialTheme.shapes.large,
                        tonalElevation = 6.dp,
                    ) {
                        Text(
                            "Backing up $count ${if (count == 1) "item" else "items"} to your archive",
                            style = MaterialTheme.typography.bodyLarge,
                            modifier = Modifier.padding(horizontal = 24.dp, vertical = 20.dp),
                        )
                    }
                }
                LaunchedEffect(Unit) {
                    delay(1_500)
                    finish()
                }
            }
        }
    }

    private fun sharedItemCount(): Int {
        intent.clipData?.let { return it.itemCount.coerceAtLeast(1) }
        val multiple = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
        return multiple?.size?.coerceAtLeast(1) ?: 1
    }
}
