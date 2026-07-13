package app.azimuthphoto.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test
import java.time.ZoneId

class UnifiedTimelineTest {

    private fun hub(id: Long, name: String, size: Long?, date: String?) =
        ArchiveImage(id = id, filename = name, file_size = size, date_taken = date)

    private fun hubEntry(id: Long, date: String) =
        TimelineEntry.Hub(hub(id, "h$id.cr3", 1, date), UnifiedTimeline.hubMillis(date))

    @Test fun dedupKeyIgnoresPathAndCase() {
        assertEquals(
            UnifiedTimeline.dedupKey("PXL_1.JPG", 100),
            UnifiedTimeline.dedupKey("DCIM/Camera/pxl_1.jpg", 100),
        )
    }

    @Test fun dedupKeyDistinguishesSize() {
        assertFalse(
            UnifiedTimeline.dedupKey("a.jpg", 100) == UnifiedTimeline.dedupKey("a.jpg", 101)
        )
    }

    @Test fun hubMillisParsesTimestampAndDateOnly() {
        val z = ZoneId.of("UTC")
        assertEquals(
            java.time.LocalDate.parse("2026-07-11").atStartOfDay(z).toInstant().toEpochMilli(),
            UnifiedTimeline.hubMillis("2026-07-11", z),
        )
        assertTrue(UnifiedTimeline.hubMillis("2026-07-11 12:00:00", z) > UnifiedTimeline.hubMillis("2026-07-11", z))
        assertEquals(0L, UnifiedTimeline.hubMillis(null, z))
        assertEquals(0L, UnifiedTimeline.hubMillis("", z))
    }

    @Test fun hubEntriesDropDeviceTwins() {
        val deviceKeys = setOf(UnifiedTimeline.dedupKey("PXL_1.jpg", 500))
        val images = listOf(
            hub(1, "PXL_1.jpg", 500, "2026-07-11 10:00:00"), // twin -> dropped
            hub(2, "SKDA_9.cr3", 900, "2026-07-10 10:00:00"), // archive-only -> kept
        )
        val entries = UnifiedTimeline.hubEntries(images, deviceKeys)
        assertEquals(1, entries.size)
        assertEquals(2L, entries.first().image.id)
    }

    @Test fun mergeIsDateDescendingAcrossSources() {
        val a = listOf(hubEntry(1, "2026-07-11 00:00:00"), hubEntry(3, "2025-01-01 00:00:00"))
        val b = listOf(hubEntry(2, "2026-03-01 00:00:00"), hubEntry(4, "2024-01-01 00:00:00"))
        val merged = UnifiedTimeline.merge(a, b)
        val ms = merged.map { it.sortMs }
        assertEquals(ms.sortedDescending(), ms)
        assertEquals(listOf(1L, 2L, 3L, 4L), merged.map { (it as TimelineEntry.Hub).image.id })
    }
}
