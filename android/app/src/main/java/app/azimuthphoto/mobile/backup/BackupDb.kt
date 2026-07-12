package app.azimuthphoto.mobile.backup

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

/** Local record of which MediaStore items have been backed up to the hub. */
class BackupDb private constructor(context: Context) :
    SQLiteOpenHelper(context.applicationContext, "backup.db", null, 1) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """CREATE TABLE items (
                media_id INTEGER PRIMARY KEY,
                content_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                state TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        db.execSQL("CREATE INDEX idx_items_state ON items(state)")
        db.execSQL("CREATE INDEX idx_items_hash ON items(content_hash)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun stateFor(mediaId: Long): String? =
        readableDatabase.rawQuery(
            "SELECT state FROM items WHERE media_id = ?", arrayOf(mediaId.toString())
        ).use { if (it.moveToFirst()) it.getString(0) else null }

    /** media_id → state, for badging the timeline in one query. */
    fun allStates(): Map<Long, String> {
        val map = HashMap<Long, String>(4096)
        readableDatabase.rawQuery("SELECT media_id, state FROM items", null).use { c ->
            while (c.moveToNext()) map[c.getLong(0)] = c.getString(1)
        }
        return map
    }

    fun upsert(mediaId: Long, contentHash: String, sizeBytes: Long, state: String) {
        val values = ContentValues().apply {
            put("media_id", mediaId)
            put("content_hash", contentHash)
            put("size_bytes", sizeBytes)
            put("state", state)
            put("updated_at", System.currentTimeMillis())
        }
        writableDatabase.insertWithOnConflict("items", null, values, SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun countByState(): Map<String, Int> {
        val map = HashMap<String, Int>()
        readableDatabase.rawQuery("SELECT state, COUNT(*) FROM items GROUP BY state", null).use { c ->
            while (c.moveToNext()) map[c.getString(0)] = c.getInt(1)
        }
        return map
    }

    companion object {
        const val STATE_UPLOADED = "uploaded"
        const val STATE_PRESENT = "present" // hub already had it (matched by hash)
        const val STATE_FAILED = "failed"

        @Volatile private var instance: BackupDb? = null
        fun get(context: Context): BackupDb =
            instance ?: synchronized(this) {
                instance ?: BackupDb(context).also { instance = it }
            }
    }
}
