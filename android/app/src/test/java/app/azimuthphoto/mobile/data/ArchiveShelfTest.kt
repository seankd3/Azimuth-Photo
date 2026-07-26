package app.azimuthphoto.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Test

class ArchiveShelfTest {
    @Test
    fun collapsesChainsAndStopsAtDateFolders() {
        val folders = listOf(
            ArchiveFolder("Photos", 100, 0),
            ArchiveFolder("Photos/Library", 100, 1),
            ArchiveFolder("Photos/Library/Camera", 60, 2),
            ArchiveFolder("Photos/Library/Screenshots", 40, 2),
            ArchiveFolder("RAWS", 50, 0),
            ArchiveFolder("RAWS/2025", 30, 1),
            ArchiveFolder("RAWS/2026", 20, 1),
        )

        val result = collapseShelfFolders(folders)

        assertEquals(listOf("Camera", "RAWS", "Screenshots"), result.map { it.name })
    }
}
