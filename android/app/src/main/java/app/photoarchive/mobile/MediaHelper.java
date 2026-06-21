package app.photoarchive.mobile;

import android.app.Activity;
import android.content.ContentValues;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;
import android.widget.Toast;

import java.io.OutputStream;

final class MediaHelper {

    static Uri saveToMediaStore(Activity activity, String filename, String mimeType, byte[] data) {
        try {
            ContentValues values = new ContentValues();
            values.put(MediaStore.Images.Media.DISPLAY_NAME, filename);
            values.put(MediaStore.Images.Media.MIME_TYPE, mimeType);
            if (Build.VERSION.SDK_INT >= 29) {
                values.put(MediaStore.Images.Media.IS_PENDING, 1);
            }
            Uri imageUri = activity.getContentResolver().insert(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
            if (imageUri == null) return null;

            try (OutputStream out = activity.getContentResolver().openOutputStream(imageUri)) {
                if (out == null) return null;
                out.write(data);
            }

            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues update = new ContentValues();
                update.put(MediaStore.Images.Media.IS_PENDING, 0);
                activity.getContentResolver().update(imageUri, update, null, null);
            }
            return imageUri;
        } catch (Exception e) {
            return null;
        }
    }

    static String mimeFromContentType(String contentType) {
        if (contentType == null) return "image/jpeg";
        int semicolon = contentType.indexOf(';');
        String mime = semicolon >= 0 ? contentType.substring(0, semicolon).trim() : contentType.trim();
        return mime.isEmpty() ? "image/jpeg" : mime;
    }

    static void sharePhoto(Activity activity, PhotoArchiveClient client, Photo photo, String serverUrl) {
        Toast.makeText(activity, "Preparing share...", Toast.LENGTH_SHORT).show();
        client.downloadImage(photo.previewUrl(serverUrl), new PhotoArchiveClient.DownloadCallback() {
            @Override
            public void onSuccess(byte[] data, String contentType) {
                String mime = mimeFromContentType(contentType);
                Uri uri = saveToMediaStore(activity, photo.filename, mime, data);
                if (uri == null) {
                    Toast.makeText(activity, "Could not prepare image", Toast.LENGTH_SHORT).show();
                    return;
                }
                Intent share = new Intent(Intent.ACTION_SEND);
                share.setType(mime);
                share.putExtra(Intent.EXTRA_STREAM, uri);
                share.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                activity.startActivity(Intent.createChooser(share, "Share photo"));
            }

            @Override
            public void onError(String message) {
                Toast.makeText(activity, "Share failed: " + message, Toast.LENGTH_SHORT).show();
            }
        });
    }
}
