package app.photoarchive.mobile;

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

    private Photo(
            int id,
            String filename,
            String dateTaken,
            String dateGroup,
            String camera,
            String lens,
            String fileExt,
            double aspectRatio
    ) {
        this.id = id;
        this.filename = filename;
        this.dateTaken = dateTaken;
        this.dateGroup = dateGroup;
        this.camera = camera;
        this.lens = lens;
        this.fileExt = fileExt;
        this.aspectRatio = aspectRatio;
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
                json.optDouble("aspect_ratio", 1.0)
        );
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
        if (line.length() == 0) {
            line.append(fileExt.isEmpty() ? "photoArchive image" : fileExt);
        }
        return line.toString();
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
