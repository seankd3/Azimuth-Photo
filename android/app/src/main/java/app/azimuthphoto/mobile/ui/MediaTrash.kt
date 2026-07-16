package app.azimuthphoto.mobile.ui

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.MediaStore
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext

/**
 * Trashes device media. With the "Manage media" special access granted, Android
 * skips its per-item confirmation and the move is instant; without it, the first
 * delete offers to turn that on (and otherwise falls back to the system dialog).
 * [onTrashed] fires only when media actually moved to trash.
 */
@Composable
fun rememberMediaTrash(onTrashed: () -> Unit): (List<Uri>) -> Unit {
    val context = LocalContext.current
    var promptUris by remember { mutableStateOf<List<Uri>?>(null) }

    val trashLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartIntentSenderForResult(),
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) onTrashed()
    }
    // Returning from the Manage-media settings screen: carry out the deferred
    // trash — silent now if the toggle was flipped, dialog-once if not.
    val manageLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        promptUris?.let { launchTrash(context, it, trashLauncher) }
        promptUris = null
    }

    promptUris?.takeIf { !MediaStore.canManageMedia(context) }?.let { uris ->
        AlertDialog(
            // Dismissing (back / tap outside) cancels — it must not delete.
            onDismissRequest = { promptUris = null },
            containerColor = Panel,
            title = { Text("Delete without asking each time?") },
            text = {
                Text(
                    "Turn on Manage media and Azimuth can move photos to trash instantly, " +
                        "without Android confirming every time. Trashed photos still restore for 30 days.",
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    manageLauncher.launch(
                        Intent(Settings.ACTION_REQUEST_MANAGE_MEDIA)
                            .setData(Uri.parse("package:${context.packageName}")),
                    )
                }) { Text("Turn on") }
            },
            dismissButton = {
                TextButton(onClick = {
                    launchTrash(context, uris, trashLauncher)
                    promptUris = null
                }) { Text("Just this once") }
            },
        )
    }

    return { uris ->
        if (uris.isNotEmpty()) {
            if (MediaStore.canManageMedia(context)) launchTrash(context, uris, trashLauncher)
            else promptUris = uris
        }
    }
}

private fun launchTrash(
    context: Context,
    uris: List<Uri>,
    launcher: ActivityResultLauncher<IntentSenderRequest>,
) {
    val pending = MediaStore.createTrashRequest(context.contentResolver, uris, true)
    launcher.launch(IntentSenderRequest.Builder(pending).build())
}
