package app.photoarchive.mobile;

import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.webkit.MimeTypeMap;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedWriter;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class PhotoArchiveClient {
    interface PhotosCallback {
        void onSuccess(PhotoPage page);
        void onError(String message);
    }

    interface ImportCallback {
        void onSuccess(int imported, int skipped);
        void onError(String message);
    }

    interface StatusCallback {
        void onSuccess(ConnectionStatus status);
        void onError(String message);
    }

    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    void fetchPhotos(String serverUrl, String query, int offset, int limit, PhotosCallback callback) {
        executor.execute(() -> {
            try {
                PhotoPage page = requestPhotos(serverUrl, query, offset, limit);
                runOnMain(() -> callback.onSuccess(page));
            } catch (Exception error) {
                runOnMain(() -> callback.onError(cleanMessage(error)));
            }
        });
    }

    void uploadImport(Context context, String serverUrl, List<Uri> uris, ImportCallback callback) {
        executor.execute(() -> {
            try {
                ImportResult result = postImport(context, serverUrl, uris);
                runOnMain(() -> callback.onSuccess(result.imported, result.skipped));
            } catch (Exception error) {
                runOnMain(() -> callback.onError(cleanMessage(error)));
            }
        });
    }

    void checkConnection(String serverUrl, StatusCallback callback) {
        executor.execute(() -> {
            try {
                ConnectionStatus status = requestStatus(serverUrl);
                runOnMain(() -> callback.onSuccess(status));
            } catch (Exception error) {
                runOnMain(() -> callback.onError(cleanMessage(error)));
            }
        });
    }

    void shutdown() {
        executor.shutdownNow();
    }

    private PhotoPage requestPhotos(String serverUrl, String query, int offset, int limit) throws Exception {
        StringBuilder url = new StringBuilder(normalize(serverUrl));
        url.append("/api/rankings?sort=date_taken");
        url.append("&limit=").append(limit);
        url.append("&offset=").append(offset);
        if (query != null && !query.trim().isEmpty()) {
            url.append("&q=").append(URLEncoder.encode(query.trim(), "UTF-8"));
        }

        JSONObject json = new JSONObject(get(url.toString()));
        JSONArray images = json.optJSONArray("images");
        List<Photo> photos = new ArrayList<>();
        if (images != null) {
            for (int i = 0; i < images.length(); i++) {
                JSONObject item = images.optJSONObject(i);
                if (item != null) {
                    photos.add(Photo.fromJson(item));
                }
            }
        }
        int total = json.optInt("total_images", photos.size() + offset);
        return new PhotoPage(photos, total, json.optBoolean("status_stale", false));
    }

    private ConnectionStatus requestStatus(String serverUrl) throws Exception {
        JSONObject json = new JSONObject(get(normalize(serverUrl) + "/api/rankings?sort=date_taken&limit=1&offset=0"));
        return new ConnectionStatus(json.optInt("total_images", 0), json.optBoolean("status_stale", false));
    }

    private ImportResult postImport(Context context, String serverUrl, List<Uri> uris) throws Exception {
        String boundary = "photoarchive-android-" + System.currentTimeMillis();
        HttpURLConnection connection = null;
        try {
            URL url = new URL(normalize(serverUrl) + "/api/imports");
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15_000);
            connection.setReadTimeout(120_000);
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
            connection.setRequestProperty("Accept", "application/json");

            try (OutputStream output = connection.getOutputStream()) {
                writeField(output, boundary, "destination_mode", "date_shoot");
                writeField(output, boundary, "shoot_date", new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date()));
                writeField(output, boundary, "shoot_name", "Android Import");
                writeField(output, boundary, "preserve_structure", "false");

                for (Uri uri : uris) {
                    String filename = displayName(context, uri);
                    writeField(output, boundary, "relative_paths", filename);
                }
                for (Uri uri : uris) {
                    writeFilePart(context, output, boundary, uri);
                }
                output.write(("--" + boundary + "--\r\n").getBytes("UTF-8"));
            }

            int code = connection.getResponseCode();
            String body = readAll(code >= 400 ? connection.getErrorStream() : connection.getInputStream());
            if (code < 200 || code >= 300) {
                throw new IllegalStateException(body.isEmpty() ? "Server returned " + code : body);
            }
            JSONObject json = new JSONObject(body);
            return new ImportResult(json.optInt("imported_files"), json.optInt("skipped_files"));
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private String get(String urlString) throws Exception {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(urlString).openConnection();
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(30_000);
            connection.setRequestProperty("Accept", "application/json");
            int code = connection.getResponseCode();
            String body = readAll(code >= 400 ? connection.getErrorStream() : connection.getInputStream());
            if (code < 200 || code >= 300) {
                throw new IllegalStateException(body.isEmpty() ? "Server returned " + code : body);
            }
            return body;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private void writeField(OutputStream output, String boundary, String name, String value) throws Exception {
        BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(output, "UTF-8"));
        writer.write("--" + boundary + "\r\n");
        writer.write("Content-Disposition: form-data; name=\"" + name + "\"\r\n");
        writer.write("\r\n");
        writer.write(value == null ? "" : value);
        writer.write("\r\n");
        writer.flush();
    }

    private void writeFilePart(Context context, OutputStream output, String boundary, Uri uri) throws Exception {
        String filename = displayName(context, uri);
        String mimeType = context.getContentResolver().getType(uri);
        if (mimeType == null || mimeType.trim().isEmpty()) {
            mimeType = mimeTypeFromName(filename);
        }
        BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(output, "UTF-8"));
        writer.write("--" + boundary + "\r\n");
        writer.write("Content-Disposition: form-data; name=\"files\"; filename=\"" + safeHeader(filename) + "\"\r\n");
        writer.write("Content-Type: " + mimeType + "\r\n");
        writer.write("\r\n");
        writer.flush();

        try (InputStream input = context.getContentResolver().openInputStream(uri)) {
            if (input == null) {
                throw new IllegalStateException("Cannot open " + filename);
            }
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                if (read > 0) output.write(buffer, 0, read);
            }
        }
        output.write("\r\n".getBytes("UTF-8"));
    }

    private String displayName(Context context, Uri uri) {
        String name = null;
        try (Cursor cursor = context.getContentResolver().query(uri, null, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) {
                    name = cursor.getString(index);
                }
            }
        } catch (Exception ignored) {
            name = null;
        }
        if (name == null || name.trim().isEmpty()) {
            name = "android-photo-" + System.currentTimeMillis() + ".jpg";
        }
        return name.replace("/", "_").replace("\\", "_");
    }

    private String mimeTypeFromName(String filename) {
        String extension = "";
        int dot = filename.lastIndexOf('.');
        if (dot >= 0 && dot < filename.length() - 1) {
            extension = filename.substring(dot + 1).toLowerCase(Locale.US);
        }
        String type = MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension);
        return type == null ? "image/jpeg" : type;
    }

    private String safeHeader(String value) {
        return value.replace("\"", "'").replace("\r", "").replace("\n", "");
    }

    private String readAll(InputStream input) throws Exception {
        if (input == null) return "";
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[16 * 1024];
            int read;
            while ((read = stream.read(buffer)) >= 0) {
                if (read > 0) output.write(buffer, 0, read);
            }
            return output.toString("UTF-8");
        }
    }

    private String normalize(String serverUrl) {
        return ServerSettings.normalize(serverUrl);
    }

    private void runOnMain(Runnable runnable) {
        android.os.Handler handler = new android.os.Handler(android.os.Looper.getMainLooper());
        handler.post(runnable);
    }

    private String cleanMessage(Exception error) {
        String message = error.getMessage();
        if (message == null || message.trim().isEmpty()) {
            return "Unexpected connection problem";
        }
        return message.length() > 220 ? message.substring(0, 220) : message;
    }

    private static final class ImportResult {
        final int imported;
        final int skipped;

        ImportResult(int imported, int skipped) {
            this.imported = imported;
            this.skipped = skipped;
        }
    }
}
