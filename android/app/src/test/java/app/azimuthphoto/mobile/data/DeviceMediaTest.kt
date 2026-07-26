package app.azimuthphoto.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceMediaTest {
    @Test
    fun rawPairCollapsesToRenderedJpeg() {
        val jpeg = TestMedia("PXL_20260713_120000.RAW-01.jpg")
        val raw = TestMedia("PXL_20260713_120000.RAW-02.ORIGINAL.dng")

        val result = collapseRawPairsBy(listOf(jpeg, raw), TestMedia::isRaw, TestMedia::shotKey)

        assertEquals(listOf(jpeg), result)
    }

    @Test
    fun soloDngRemainsVisible() {
        val raw = TestMedia("PXL_20260713_120000.dng")

        assertEquals(
            listOf(raw),
            collapseRawPairsBy(listOf(raw), TestMedia::isRaw, TestMedia::shotKey),
        )
    }

    @Test
    fun topShotPatternCollapses() {
        val jpeg = TestMedia("PXL_20260713_120000.TS-001-01.jpg")
        val raw = TestMedia("PXL_20260713_120000.TS-001-02.ORIGINAL.dng")

        val result = collapseRawPairsBy(listOf(jpeg, raw), TestMedia::isRaw, TestMedia::shotKey)

        assertEquals(1, result.size)
        assertTrue(result.single().name.endsWith(".jpg"))
    }

    private data class TestMedia(val name: String) {
        val isRaw: Boolean get() = name.endsWith(".dng", ignoreCase = true)
        val shotKey: String get() = rawShotKey("camera", name)
    }
}
