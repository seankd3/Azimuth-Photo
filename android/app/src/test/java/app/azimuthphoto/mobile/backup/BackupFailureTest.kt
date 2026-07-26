package app.azimuthphoto.mobile.backup

import java.io.IOException
import org.junit.Assert.assertEquals
import org.junit.Test

class BackupFailureTest {
    @Test
    fun http422IsPermanent() {
        assertEquals(
            BackupFailure.PERMANENT,
            classifyBackupFailure(HubHttpException(422)),
        )
    }

    @Test
    fun ioFailureIsTransient() {
        assertEquals(
            BackupFailure.TRANSIENT,
            classifyBackupFailure(IOException("offline")),
        )
    }
}
