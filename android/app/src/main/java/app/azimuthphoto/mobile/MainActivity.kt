package app.azimuthphoto.mobile

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
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
import androidx.compose.material.icons.outlined.Photo
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.rounded.Photo
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import app.azimuthphoto.mobile.backup.BackupScheduler
import app.azimuthphoto.mobile.backup.FreeUpSpace
import app.azimuthphoto.mobile.data.SettingsStore
import app.azimuthphoto.mobile.ui.ArchiveScreen
import app.azimuthphoto.mobile.ui.PhotoArchiveTheme
import app.azimuthphoto.mobile.ui.OnboardingScreen
import app.azimuthphoto.mobile.ui.SettingsScreen
import app.azimuthphoto.mobile.ui.SearchScreen
import app.azimuthphoto.mobile.ui.TimelineScreen
import app.azimuthphoto.mobile.ui.TrashScreen

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // Test hook (debug builds): adb shell am start ... --es server_url http://host:port
        if (BuildConfig.DEBUG) {
            intent?.getStringExtra("server_url")?.let { url ->
                lifecycleScope.launch { SettingsStore.setServerUrl(this@MainActivity, url) }
            }
        }
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
            PackageManager.PERMISSION_GRANTED &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.READ_MEDIA_VIDEO) ==
            PackageManager.PERMISSION_GRANTED
}

@Composable
private fun Root(
    hasMediaPermission: () -> Boolean,
    onPermissionGranted: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var granted by remember { mutableStateOf(hasMediaPermission()) }
    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        granted = results[Manifest.permission.READ_MEDIA_IMAGES] == true &&
            results[Manifest.permission.READ_MEDIA_VIDEO] == true
        if (granted) onPermissionGranted()
    }

    // Permission may be granted (or revoked) from system Settings — re-sync on resume.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) granted = hasMediaPermission()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val settings by SettingsStore.flow(context).collectAsState(initial = null)
    val currentSettings = settings
    if (currentSettings == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    if (!currentSettings.onboarded || !currentSettings.serverConfigured || !granted) {
        OnboardingScreen(
            hasMediaPermission = granted,
            onRequestPermissions = {
                launcher.launch(
                    arrayOf(
                        Manifest.permission.READ_MEDIA_IMAGES,
                        Manifest.permission.READ_MEDIA_VIDEO,
                        Manifest.permission.ACCESS_MEDIA_LOCATION,
                        Manifest.permission.POST_NOTIFICATIONS,
                    )
                )
            },
            onDone = { serverUrl, backupEnabled ->
                scope.launch {
                    SettingsStore.setServerUrl(context, serverUrl)
                    SettingsStore.setBackupEnabled(context, backupEnabled)
                    SettingsStore.setOnboarded(context, true)
                    onPermissionGranted()
                }
            },
        )
        return
    }

    var tab by rememberSaveable { mutableStateOf(0) }
    var showTrash by rememberSaveable { mutableStateOf(false) }
    var immersive by remember { mutableStateOf(false) }
    if (showTrash) {
        TrashScreen(onClose = { showTrash = false })
        return
    }
    BackHandler(enabled = tab != 0 && !immersive) { tab = 0 }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            if (immersive) return@Scaffold
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
                            if (tab == 1) Icons.Rounded.Search else Icons.Outlined.Search,
                            contentDescription = "Search",
                        )
                    },
                    label = { Text("Search") },
                )
                NavigationBarItem(
                    selected = tab == 2, onClick = { tab = 2 },
                    icon = {
                        Icon(
                            if (tab == 2) Icons.Rounded.Settings else Icons.Outlined.Settings,
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
                0 -> TimelineScreen(
                    onOpenSettings = { tab = 2 },
                    onOpenTrash = { showTrash = true },
                    onImmersive = { immersive = it },
                )
                1 -> SearchScreen(onImmersive = { immersive = it })
                else -> SettingsScreen(onOpenTrash = { showTrash = true })
            }
        }
    }
}

@Composable
private fun PermissionGate(onRequest: () -> Unit) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("Azimuth needs access to your photos", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(12.dp))
            Button(onClick = onRequest) { Text("Grant access") }
        }
    }
}
