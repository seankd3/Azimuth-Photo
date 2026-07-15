package app.azimuthphoto.mobile.data

/**
 * One media identity for the full-screen viewer, so a phone shot and an
 * archive original get the exact same gestures, chrome, and actions.
 */
sealed class ViewerMedia {
    abstract val key: String
    abstract val isVideo: Boolean
    abstract val displayName: String

    /** Lives in MediaStore on this device. [rawTwin] is the hidden DNG of a RAW+JPEG pair. */
    data class Local(val item: MediaItem, val rawTwin: MediaItem? = null) : ViewerMedia() {
        override val key get() = "d${item.id}"
        override val isVideo get() = item.isVideo
        override val displayName get() = item.displayName
    }

    /** Lives only on the hub; rendered from server previews, actions via the API. */
    data class Remote(val image: ArchiveImage) : ViewerMedia() {
        override val key get() = "h${image.id}"
        override val isVideo get() = image.isVideo
        override val displayName get() = image.filename
    }
}
