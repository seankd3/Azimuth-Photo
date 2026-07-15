package app.azimuthphoto.mobile.data

import java.io.IOException
import java.time.LocalDate
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request

/** A single "on this day" flashback: every photo taken on today's date in a past year. */
data class Memory(val yearsAgo: Int, val year: Int, val images: List<ArchiveImage>)

/**
 * Builds "on this day" memories by asking the hub for the best-ranked photos taken on
 * today's month/day in each of the past years. Read-only; failures per year are swallowed
 * so one bad request never sinks the whole strip.
 */
object Memories {

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private val json = Json { ignoreUnknownKeys = true }

    suspend fun onThisDay(
        serverUrl: String,
        today: LocalDate,
        maxYears: Int = 12,
    ): List<Memory> = withContext(Dispatchers.IO) {
        val mm = "%02d".format(today.monthValue)
        val dd = "%02d".format(today.dayOfMonth)
        (1..maxYears).mapNotNull { yearsAgo ->
            val year = today.year - yearsAgo
            val images = runCatching { fetch(serverUrl, year, mm, dd) }.getOrDefault(emptyList())
            if (images.isEmpty()) null else Memory(yearsAgo = yearsAgo, year = year, images = images)
        }
    }

    private fun fetch(serverUrl: String, year: Int, mm: String, dd: String): List<ArchiveImage> {
        val url = "$serverUrl/api/rankings?sort=elo&date_taken=$year-$mm-$dd&limit=12"
        http.newCall(Request.Builder().url(url).build()).execute().use { response ->
            if (!response.isSuccessful) throw IOException("memories failed: HTTP ${response.code}")
            return json.decodeFromString<RankingsPage>(response.body!!.string()).images
        }
    }
}
