package app.azimuthphoto.mobile.data

import android.net.Uri
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * One chronological stream over both sources, Google-Photos style: device photos
 * (always available, offline-safe) merged with hub photos that are NOT on the
 * device (older DSLR work, exported edits, freed-up shots). A backed-up phone
 * photo appears once — from the device — because hub items are deduplicated
 * against the device by filename + byte size.
 */
sealed class TimelineEntry {
    abstract val sortMs: Long
    abstract val gridKey: String
    val day: LocalDate get() = Instant.ofEpochMilli(sortMs).atZone(ZoneId.systemDefault()).toLocalDate()

    data class Device(
        val item: MediaItem,
        val backedUp: Boolean,
        val hasRaw: Boolean,
    ) : TimelineEntry() {
        override val sortMs get() = item.dateTakenMs
        override val gridKey get() = "d${item.id}"
    }

    data class Hub(
        val image: ArchiveImage,
        override val sortMs: Long,
    ) : TimelineEntry() {
        override val gridKey get() = "h${image.id}"
    }
}

object UnifiedTimeline {

    /** Identity a hub row shares with its on-device twin: base filename + exact size. */
    fun dedupKey(filename: String, sizeBytes: Long): String =
        "${filename.substringAfterLast('/').lowercase()}|$sizeBytes"

    private val HUB_FORMATS = listOf(
        DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"),
        DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss"),
    )

    /** Parse the hub's date_taken string to epoch millis; 0 when unknown (sorts last). */
    fun hubMillis(dateTaken: String?, zone: ZoneId = ZoneId.systemDefault()): Long {
        val raw = dateTaken?.trim().orEmpty()
        if (raw.isEmpty()) return 0L
        for (fmt in HUB_FORMATS) {
            runCatching {
                return LocalDateTime.parse(raw.take(19), fmt).atZone(zone).toInstant().toEpochMilli()
            }
        }
        runCatching {
            return LocalDate.parse(raw.take(10)).atStartOfDay(zone).toInstant().toEpochMilli()
        }
        return 0L
    }

    /**
     * Merge device entries with the filtered hub images into one date-descending
     * stream. Both inputs are assumed already sorted newest-first; this is a
     * linear merge so appending later hub pages stays cheap.
     */
    fun merge(a: List<TimelineEntry>, b: List<TimelineEntry>): List<TimelineEntry> {
        val out = ArrayList<TimelineEntry>(a.size + b.size)
        var i = 0
        var j = 0
        while (i < a.size && j < b.size) {
            if (a[i].sortMs >= b[j].sortMs) out.add(a[i++]) else out.add(b[j++])
        }
        while (i < a.size) out.add(a[i++])
        while (j < b.size) out.add(b[j++])
        return out
    }

    /** Hub images not already present on the device, as Hub entries. */
    fun hubEntries(images: List<ArchiveImage>, deviceKeys: Set<String>): List<TimelineEntry.Hub> =
        images.asSequence()
            .filter { dedupKey(it.filename, it.file_size ?: -1) !in deviceKeys }
            .map { TimelineEntry.Hub(it, hubMillis(it.date_taken)) }
            .toList()

    /** Dedup keys for every device item, so hub twins collapse away. */
    fun deviceKeys(items: List<MediaItem>): Set<String> =
        items.mapTo(HashSet(items.size)) { dedupKey(it.displayName, it.sizeBytes) }
}
