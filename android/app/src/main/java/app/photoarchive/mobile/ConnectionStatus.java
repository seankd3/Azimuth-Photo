package app.photoarchive.mobile;

final class ConnectionStatus {
    final int totalImages;
    final boolean stale;

    ConnectionStatus(int totalImages, boolean stale) {
        this.totalImages = totalImages;
        this.stale = stale;
    }
}
