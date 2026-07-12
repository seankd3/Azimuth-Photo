package app.photoarchive.mobile

import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import app.photoarchive.mobile.backup.BackupScheduler

/**
 * Share target: receiving media just kicks a backup pass — anything shared in
 * is already in MediaStore, and the worker sweeps it up to the hub.
 */
class ShareActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        BackupScheduler.runNow(this)
        Toast.makeText(this, "Backing up to photoArchive", Toast.LENGTH_SHORT).show()
        finish()
    }
}
