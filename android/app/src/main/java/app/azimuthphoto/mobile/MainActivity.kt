package app.azimuthphoto.mobile

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Cloud
import androidx.compose.material.icons.outlined.Collections
import androidx.compose.material.icons.outlined.Photo
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.rounded.Cloud
import androidx.compose.material.icons.rounded.Collections
import androidx.compose.material.icons.rounded.Photo
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import app.azimuthphoto.mobile.backup.BackupScheduler
import app.azimuthphoto.mobile.backup.FreeUpSpace
import app.azimuthphoto.mobile.ui.ArchiveScreen
import app.azimuthphoto.mobile.ui.CollectionsScreen
import app.azimuthphoto.mobile.ui.PhotoArchiveTheme
import app.azimuthphoto.mobile.ui.SettingsScreen
import app.azimuthphoto.mobile.ui.TimelineScreen

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            PhotoArchiveTheme {
                Root(
                    hasMediaPermission = ::hasMediaPermission,
                    onPermissionGranted = {
                        BackupScheduler.ensureScheduled(this)
                        BackupScheduler.runNow(this)
                    },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        if (hasMediaPermission()) {
            // Quietly age backed-up media off the device (no-op unless enabled).
            lifecycleScope.launch {
                runCatching { FreeUpSpace.run(this@MainActivity) }
            }
        }
    }

    private fun hasMediaPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.READ_MEDIA_IMAGES) ==
            PackageManager.PERMISSION_GRANTED
}

@Composable
private fun Root(
    hasMediaPermission: () -> Boolean,
    onPermissionGranted: () -> Unit,
) {
    var granted by remember { mutableStateOf(hasMediaPermission()) }
    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        granted = results[Manifest.permission.READ_MEDIA_IMAGES] == true
        if (granted) onPermissionGranted()
    }

    LaunchedEffect(Unit) {
        if (!granted) {
            launcher.launch(
                arrayOf(
                    Manifest.permission.READ_MEDIA_IMAGES,
                    Manifest.permission.READ_MEDIA_VIDEO,
                    Manifest.permission.ACCESS_MEDIA_LOCATION,
                    Manifest.permission.POST_NOTIFICATIONS,
                )
            )
        } else {
            onPermissionGranted()
        }
    }

    if (!granted) {
        PermissionGate { launcher.launch(arrayOf(Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO)) }
        return
    }

    var tab by rememberSaveable { mutableStateOf(0) }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                NavigationBarItem(
                    selected = tab == 0, onClick = { tab = 0 },
                    icon = {
                        Icon(
                            if (tab == 0) Icons.Rounded.Photo else Icons.Outlined.Photo,
                            contentDescription = "Photos",
                        )
                    },
                    label = { Text("Photos") },
                )
                NavigationBarItem(
                    selected = tab == 1, onClick = { tab = 1 },
                    icon = {
                        Icon(
                            if (tab == 1) Icons.Rounded.Cloud else Icons.Outlined.Cloud,
                            contentDescription = "Archive",
                        )
                    },
                    label = { Text("Archive") },
                )
                NavigationBarItem(
                    selected = tab == 2, onClick = { tab = 2 },
                    icon = {
                        Icon(
                            if (tab == 2) Icons.Rounded.Collections else Icons.Outlined.Collections,
                            contentDescription = "Collections",
                        )
                    },
                    label = { Text("Collections") },
                )
                NavigationBarItem(
                    selected = tab == 3, onClick = { tab = 3 },
                    icon = {
                        Icon(
                            if (tab == 3) Icons.Rounded.Settings else Icons.Outlined.Settings,
                            contentDescription = "Settings",
                        )
                    },
                    label = { Text("Settings") },
                )
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding)) {
            when (tab) {
                0 -> TimelineScreen()
                1 -> ArchiveScreen()
                2 -> CollectionsScreen()
                else -> SettingsScreen()
            }
        }
    }
}

@Composable
private fun PermissionGate(onRequest: () -> Unit) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("photoArchive needs access to your photos", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(12.dp))
            Button(onClick = onRequest) { Text("Grant access") }
        }
    }
}
