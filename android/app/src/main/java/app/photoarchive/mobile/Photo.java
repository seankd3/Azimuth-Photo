package app.photoarchive.mobile;

import java.util.Locale;

import org.json.JSONObject;

final class Photo {
    final int id;
    final String filename;
    final String dateTaken;
    final String dateGroup;
    final String camera;
    final String lens;
    final String fileExt;
    final double aspectRatio;
    final double elo;
    final int comparisons;
    String flag;
    final int width;
    final int height;
    final long fileSize;

    private Photo(
            int id,
            String filename,
            String dateTaken,
            String dateGroup,
            String camera,
            String lens,
            String fileExt,
            double aspectRatio,
            double elo,
            int comparisons,
            String flag,
            int width,
            int height,
            long fileSize
    ) {
        this.id = id;
        this.filename = filename;
        this.dateTaken = dateTaken;
        this.dateGroup = dateGroup;
        this.camera = camera;
        this.lens = lens;
        this.fileExt = fileExt;
        this.aspectRatio = aspectRatio;
        this.elo = elo;
        this.comparisons = comparisons;
        this.flag = flag;
        this.width = width;
        this.height = height;
        this.fileSize = fileSize;
    }

    static Photo fromJson(JSONObject json) {
        String cameraMake = json.optString("camera_make", "");
        String cameraModel = json.optString("camera_model", "");
        String camera = (cameraMake + " " + cameraModel).trim();
        return new Photo(
                json.optInt("id"),
                json.optString("filename", "Photo"),
                json.optString("date_taken", ""),
                json.optString("date_group", ""),
                camera,
                json.optString("lens", ""),
                json.optString("file_ext", ""),
                json.optDouble("aspect_ratio", 1.0),
                json.optDouble("elo", 1200.0),
                json.optInt("comparisons", 0),
                json.optString("flag", "unflagged"),
                json.optInt("width", 0),
                json.optInt("height", 0),
                json.optLong("file_size", 0)
        );
    }

    String smallThumbUrl(String serverUrl) {
        return resolve(serverUrl, "/api/thumb/sm/" + id);
    }

    String thumbUrl(String serverUrl) {
        return resolve(serverUrl, "/api/thumb/md/" + id);
    }

    String previewUrl(String serverUrl) {
        return resolve(serverUrl, "/api/thumb/lg/" + id);
    }

    String monthLabel() {
        if (!dateGroup.isEmpty()) return dateGroup;
        if (dateTaken.length() >= 7) return dateTaken.substring(0, 7);
        return "";
    }

    String detailLine() {
        StringBuilder line = new StringBuilder();
        if (!dateTaken.isEmpty()) {
            line.append(dateTaken);
        }
        if (!camera.isEmpty()) {
            appendSep(line).append(camera);
        }
        if (!lens.isEmpty()) {
            appendSep(line).append(lens);
        }
        if (comparisons > 0) {
            appendSep(line).append("Rank ").append(String.format(Locale.US, "%,d", (int) elo));
        }
        if (line.length() == 0) {
            line.append(fileExt.isEmpty() ? "photoArchive image" : fileExt);
        }
        return line.toString();
    }

    String dimensionLine() {
        if (width <= 0 || height <= 0) return "";
        StringBuilder line = new StringBuilder();
        line.append(String.format(Locale.US, "%,d", width));
        line.append(" × ");
        line.append(String.format(Locale.US, "%,d", height));
        if (fileSize > 0) {
            line.append(" · ");
            line.append(humanFileSize(fileSize));
        }
        return line.toString();
    }

    private static String humanFileSize(long bytes) {
        if (bytes < 1024) return bytes + " B";
        double kb = bytes / 1024.0;
        if (kb < 1024) return String.format(Locale.US, "%.0f KB", kb);
        double mb = kb / 1024.0;
        if (mb < 100) return String.format(Locale.US, "%.1f MB", mb);
        return String.format(Locale.US, "%.0f MB", mb);
    }

    private static StringBuilder appendSep(StringBuilder builder) {
        if (builder.length() > 0) builder.append("  |  ");
        return builder;
    }

    private static String resolve(String serverUrl, String path) {
        String base = serverUrl.endsWith("/") ? serverUrl.substring(0, serverUrl.length() - 1) : serverUrl;
        return base + path;
    }
}
