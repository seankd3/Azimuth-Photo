package app.photoarchive.mobile;

import java.util.List;

final class PhotoPage {
    final List<Photo> photos;
    final int totalImages;
    final boolean stale;

    PhotoPage(List<Photo> photos, int totalImages, boolean stale) {
        this.photos = photos;
        this.totalImages = totalImages;
        this.stale = stale;
    }
}
