package app.azimuthphoto.mobile.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.R
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.SettingsStore
import kotlinx.coroutines.launch

@Composable
fun OnboardingScreen(
    hasMediaPermission: Boolean,
    onRequestPermissions: () -> Unit,
    onDone: (serverUrl: String, backupEnabled: Boolean) -> Unit,
) {
    val pagerState = rememberPagerState { 3 }
    val scope = rememberCoroutineScope()
    var serverUrl by remember { mutableStateOf(SettingsStore.DEFAULT_SERVER_URL) }
    var backupEnabled by remember { mutableStateOf(true) }
    var testResult by remember { mutableStateOf<String?>(null) }
    var testing by remember { mutableStateOf(false) }

    // Granting from the system dialog advances the flow without a second tap.
    LaunchedEffect(hasMediaPermission) {
        if (hasMediaPermission && pagerState.currentPage == 1) pagerState.animateScrollToPage(2)
    }

    // The app can answer "does this address work?" itself — probe on entering the
    // connect step and after edits settle, instead of making the user tap a button.
    LaunchedEffect(pagerState.currentPage, serverUrl) {
        if (pagerState.currentPage != 2 || serverUrl.isBlank()) {
            testResult = null
            testing = false
            return@LaunchedEffect
        }
        testing = true
        testResult = null
        kotlinx.coroutines.delay(500)
        testResult = runCatching {
            val count = ArchiveApi(serverUrl.trim().trimEnd('/')).stats().photoCount
            if (count == null) "✓ Connected" else "✓ ${"%,d".format(count)} photos"
        }.getOrElse { "× Can't reach this address" }
        testing = false
    }

    // Pages advance only through the gated buttons — no swiping past a step.
    HorizontalPager(
        state = pagerState,
        userScrollEnabled = false,
        modifier = Modifier.fillMaxSize(),
    ) { page ->
        Box(
            Modifier.fillMaxSize().padding(horizontal = 28.dp),
            contentAlignment = Alignment.Center,
        ) {
            when (page) {
                0 -> Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    Image(
                        painter = painterResource(R.drawable.ic_launcher_fg),
                        contentDescription = null,
                        modifier = Modifier.size(112.dp),
                    )
                    Text("Azimuth", style = MaterialTheme.typography.headlineLarge, color = TextPrimary)
                    Spacer(Modifier.height(18.dp))
                    Text(
                        "Your photos. Your server. Nobody else.",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextSecondary,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(42.dp))
                    Button(onClick = { scope.launch { pagerState.animateScrollToPage(1) } }) {
                        Text("Continue")
                    }
                }
                1 -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Keep your library close", style = MaterialTheme.typography.headlineSmall)
                    Spacer(Modifier.height(18.dp))
                    Text(
                        "Azimuth uses photo and video access for your timeline and backup. Location adds map details, and notifications show backup progress.",
                        color = TextSecondary,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(36.dp))
                    Button(onClick = {
                        if (hasMediaPermission) {
                            scope.launch { pagerState.animateScrollToPage(2) }
                        } else onRequestPermissions()
                    }) {
                        Text(if (hasMediaPermission) "Continue" else "Grant access")
                    }
                    if (hasMediaPermission) {
                        Spacer(Modifier.height(10.dp))
                        Text("Photo access granted", color = Positive, style = MaterialTheme.typography.bodySmall)
                    }
                }
                else -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Connect your archive", style = MaterialTheme.typography.headlineSmall)
                    Spacer(Modifier.height(22.dp))
                    OutlinedTextField(
                        value = serverUrl,
                        onValueChange = {
                            serverUrl = it
                            testResult = null
                        },
                        label = { Text("Server URL") },
                        placeholder = { Text("https://photos.example.com") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(10.dp))
                    // Live, self-checking status — no "test" button to remember to press.
                    Text(
                        text = when {
                            testing -> "Checking…"
                            testResult != null -> testResult!!
                            else -> " "
                        },
                        color = if (testResult?.startsWith("✓") == true) Positive else TextSecondary,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Spacer(Modifier.height(14.dp))
                    androidx.compose.foundation.layout.Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Back up automatically", modifier = Modifier.weight(1f))
                        Switch(checked = backupEnabled, onCheckedChange = { backupEnabled = it })
                    }
                    Spacer(Modifier.height(30.dp))
                    Button(
                        enabled = hasMediaPermission && serverUrl.isNotBlank(),
                        onClick = { onDone(serverUrl, backupEnabled) },
                    ) { Text("Done") }
                }
            }
        }
    }
}
