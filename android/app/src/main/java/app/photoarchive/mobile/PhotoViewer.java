package app.photoarchive.mobile;

import android.app.Activity;
import android.app.Dialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;
import java.util.Locale;
import java.util.Map;

final class PhotoViewer {

    interface ServerUrlProvider {
        String serverUrl();
    }

    interface OnDismissListener {
        void onDismiss();
    }

    private final Activity activity;
    private final List<Photo> photos;
    private final ServerUrlProvider serverUrlProvider;
    private final ImageLoader imageLoader;
    private final PhotoArchiveClient client;
    private final AppTheme theme;

    private int position;
    private Dialog dialog;
    private ZoomableImageView imageView;
    private ProgressBar spinner;
    private TextView counter;
    private LinearLayout infoPanel;
    private TextView nameView;
    private TextView detailView;
    private TextView dimensionView;
    private Button rejectButton;
    private Button pickButton;
    private LinearLayout exifPanel;
    private boolean exifVisible = false;
    private Map<String, String> cachedExif;
    private boolean chromeVisible = true;
    private OnDismissListener dismissListener;
    private final android.os.Handler mainHandler = new android.os.Handler(android.os.Looper.getMainLooper());

    void setOnDismissListener(OnDismissListener l) {
        this.dismissListener = l;
    }

    PhotoViewer(Activity activity, List<Photo> photos, int position,
                ServerUrlProvider serverUrlProvider, ImageLoader imageLoader,
                PhotoArchiveClient client, AppTheme theme) {
        this.activity = activity;
        this.photos = photos;
        this.position = Math.max(0, Math.min(position, photos.size() - 1));
        this.serverUrlProvider = serverUrlProvider;
        this.imageLoader = imageLoader;
        this.client = client;
        this.theme = theme;
    }

    void show() {
        if (photos.isEmpty()) return;

        dialog = new Dialog(activity, android.R.style.Theme_Black_NoTitleBar_Fullscreen);
        FrameLayout frame = new FrameLayout(activity);
        frame.setBackgroundColor(Color.BLACK);

        imageView = new ZoomableImageView(activity);
        imageView.setBackgroundColor(Color.BLACK);
        frame.addView(imageView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        spinner = new ProgressBar(activity);
        FrameLayout.LayoutParams spinnerParams = new FrameLayout.LayoutParams(dp(48), dp(48));
        spinnerParams.gravity = Gravity.CENTER;
        frame.addView(spinner, spinnerParams);

        counter = new TextView(activity);
        counter.setTextColor(Color.WHITE);
        counter.setTextSize(13);
        counter.setTypeface(Typeface.DEFAULT_BOLD);
        counter.setPadding(dp(8), dp(6), dp(8), dp(6));
        GradientDrawable counterBg = new GradientDrawable();
        counterBg.setColor(Color.argb(160, 0, 0, 0));
        counterBg.setCornerRadius(dp(14));
        counter.setBackground(counterBg);
        FrameLayout.LayoutParams counterParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
        );
        counterParams.gravity = Gravity.TOP | Gravity.END;
        counterParams.setMargins(dp(12), dp(12), dp(12), dp(12));
        frame.addView(counter, counterParams);

        infoPanel = buildInfoPanel();
        FrameLayout.LayoutParams infoPanelParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
        );
        infoPanelParams.gravity = Gravity.BOTTOM;
        frame.addView(infoPanel, infoPanelParams);

        imageView.setOnTapListener(this::toggleChrome);
        imageView.setOnSwipeListener(new ZoomableImageView.SwipeListener() {
            @Override
            public void onSwipeLeft() {
                navigateNext();
            }

            @Override
            public void onSwipeRight() {
                navigatePrev();
            }

            @Override
            public void onSwipeDown() {
                dialog.dismiss();
            }
        });

        dialog.setContentView(frame);
        dialog.setOnDismissListener(d -> {
            if (dismissListener != null) dismissListener.onDismiss();
        });
        dialog.setOnKeyListener((d, keyCode, event) -> {
            if (keyCode == KeyEvent.KEYCODE_BACK && event.getAction() == KeyEvent.ACTION_UP) {
                dialog.dismiss();
                return true;
            }
            return false;
        });

        loadPhoto();
        dialog.show();

        dialog.getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        );
    }

    private LinearLayout buildInfoPanel() {
        LinearLayout info = new LinearLayout(activity);
        info.setOrientation(LinearLayout.VERTICAL);
        info.setPadding(dp(18), dp(14), dp(18), dp(20));
        info.setBackgroundColor(Color.argb(210, 0, 0, 0));

        nameView = theme.label(activity, "", 17, Color.WHITE, true);
        nameView.setOnClickListener(view -> toggleExif());
        info.addView(nameView);

        detailView = theme.label(activity, "", 13, Color.rgb(210, 214, 218), false);
        detailView.setPadding(0, dp(4), 0, 0);
        info.addView(detailView);

        dimensionView = theme.label(activity, "", 12, theme.muted, false);
        dimensionView.setPadding(0, dp(2), 0, dp(10));
        info.addView(dimensionView);

        exifPanel = new LinearLayout(activity);
        exifPanel.setOrientation(LinearLayout.VERTICAL);
        exifPanel.setPadding(0, dp(6), 0, dp(10));
        exifPanel.setVisibility(View.GONE);
        info.addView(exifPanel);

        LinearLayout buttonRow = new LinearLayout(activity);
        buttonRow.setOrientation(LinearLayout.HORIZONTAL);
        buttonRow.setGravity(Gravity.CENTER_VERTICAL);

        rejectButton = theme.actionButton(activity, "✕ Reject");
        rejectButton.setOnClickListener(view -> flagPhoto("rejected"));
        LinearLayout.LayoutParams rejectParams = new LinearLayout.LayoutParams(0, dp(42), 1);
        rejectParams.setMargins(0, 0, dp(6), 0);
        buttonRow.addView(rejectButton, rejectParams);

        pickButton = theme.actionButton(activity, "♡ Pick");
        pickButton.setOnClickListener(view -> flagPhoto("picked"));
        LinearLayout.LayoutParams pickParams = new LinearLayout.LayoutParams(0, dp(42), 1);
        pickParams.setMargins(0, 0, dp(6), 0);
        buttonRow.addView(pickButton, pickParams);

        Button shareButton = theme.actionButton(activity, "Share");
        shareButton.setOnClickListener(view -> sharePhoto());
        LinearLayout.LayoutParams shareParams = new LinearLayout.LayoutParams(0, dp(42), 1);
        shareParams.setMargins(0, 0, dp(6), 0);
        buttonRow.addView(shareButton, shareParams);

        Button saveButton = theme.actionButton(activity, "Save");
        saveButton.setOnClickListener(view -> savePhotoToGallery());
        LinearLayout.LayoutParams saveParams = new LinearLayout.LayoutParams(0, dp(42), 1);
        saveParams.setMargins(0, 0, dp(6), 0);
        buttonRow.addView(saveButton, saveParams);

        Button close = theme.actionButton(activity, "Close");
        close.setOnClickListener(view -> dialog.dismiss());
        buttonRow.addView(close, new LinearLayout.LayoutParams(0, dp(42), 1));

        info.addView(buttonRow, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        return info;
    }

    private void flagPhoto(String flag) {
        if (rejectButton != null) {
            rejectButton.performHapticFeedback(android.view.HapticFeedbackConstants.CONTEXT_CLICK);
        }
        Photo photo = photos.get(position);
        String currentFlag = photo.flag;
        String newFlag = currentFlag.equals(flag) ? "unflagged" : flag;
        String serverUrl = serverUrlProvider.serverUrl();

        client.setFlag(serverUrl, photo.id, newFlag, new PhotoArchiveClient.FlagCallback() {
            @Override
            public void onSuccess(String resultFlag) {
                photo.flag = resultFlag;
                updateFlagButtons(resultFlag);
            }

            @Override
            public void onError(String message) {
                android.widget.Toast.makeText(activity, "Flag failed: " + message,
                        android.widget.Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void updateFlagButtons(String flag) {
        if ("rejected".equals(flag)) {
            rejectButton.setBackground(theme.roundRect(
                    Color.rgb(220, 80, 80), dp(10), Color.rgb(220, 80, 80), 0));
            rejectButton.setTextColor(Color.WHITE);
            pickButton.setBackground(theme.roundRect(
                    Color.rgb(37, 39, 43), dp(10), Color.rgb(54, 57, 62), 1));
            pickButton.setTextColor(theme.text);
        } else if ("picked".equals(flag)) {
            pickButton.setBackground(theme.roundRect(
                    theme.good, dp(10), theme.good, 0));
            pickButton.setTextColor(Color.BLACK);
            rejectButton.setBackground(theme.roundRect(
                    Color.rgb(37, 39, 43), dp(10), Color.rgb(54, 57, 62), 1));
            rejectButton.setTextColor(theme.text);
        } else {
            rejectButton.setBackground(theme.roundRect(
                    Color.rgb(37, 39, 43), dp(10), Color.rgb(54, 57, 62), 1));
            rejectButton.setTextColor(theme.text);
            pickButton.setBackground(theme.roundRect(
                    Color.rgb(37, 39, 43), dp(10), Color.rgb(54, 57, 62), 1));
            pickButton.setTextColor(theme.text);
        }
    }

    private void toggleExif() {
        if (exifVisible) {
            exifPanel.setVisibility(View.GONE);
            exifVisible = false;
            return;
        }

        if (cachedExif != null) {
            showExifData(cachedExif);
            return;
        }

        Photo photo = photos.get(position);
        String serverUrl = serverUrlProvider.serverUrl();
        client.fetchExif(serverUrl, photo.id, new PhotoArchiveClient.ExifCallback() {
            @Override
            public void onSuccess(Map<String, String> exif) {
                cachedExif = exif;
                showExifData(exif);
            }

            @Override
            public void onError(String message) {
                android.widget.Toast.makeText(activity, "EXIF: " + message,
                        android.widget.Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void showExifData(Map<String, String> exif) {
        exifPanel.removeAllViews();

        addExifRow(exif, "aperture", "Aperture", "shutter_speed", "Shutter", "iso", "ISO", "focal_length", "Focal Length");
        addExifRow(exif, "dimensions", "Dimensions", "file_size", "File Size");
        addExifRow(exif, "camera_make", "Camera Make", "camera_model", "Camera Model", "lens", "Lens");

        String lat = exif.get("latitude");
        String lon = exif.get("longitude");
        if (lat != null && lon != null && !"0".equals(lat) && !"0.0".equals(lat)) {
            LinearLayout gpsRow = new LinearLayout(activity);
            gpsRow.setOrientation(LinearLayout.HORIZONTAL);
            gpsRow.setPadding(0, dp(4), 0, 0);
            addExifPair(gpsRow, "GPS", lat + ", " + lon);
            exifPanel.addView(gpsRow);
        }

        exifPanel.setVisibility(View.VISIBLE);
        exifVisible = true;
    }

    private void addExifRow(Map<String, String> exif, String... keysAndLabels) {
        LinearLayout row = new LinearLayout(activity);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, dp(4), 0, 0);
        boolean hasValues = false;

        for (int i = 0; i < keysAndLabels.length; i += 2) {
            String key = keysAndLabels[i];
            String label = keysAndLabels[i + 1];
            String value = exif.get(key);
            if (value != null && !value.isEmpty() && !"null".equals(value)) {
                addExifPair(row, label, value);
                hasValues = true;
            }
        }

        if (hasValues) {
            exifPanel.addView(row);
        }
    }

    private void addExifPair(LinearLayout row, String label, String value) {
        LinearLayout pair = new LinearLayout(activity);
        pair.setOrientation(LinearLayout.VERTICAL);
        pair.setPadding(0, 0, dp(16), 0);

        TextView labelView = new TextView(activity);
        labelView.setText(label);
        labelView.setTextColor(theme.muted);
        labelView.setTextSize(11);
        pair.addView(labelView);

        TextView valueView = new TextView(activity);
        valueView.setText(value);
        valueView.setTextColor(theme.text);
        valueView.setTextSize(13);
        pair.addView(valueView);

        row.addView(pair);
    }

    private void sharePhoto() {
        Photo photo = photos.get(position);
        MediaHelper.sharePhoto(activity, client, photo, serverUrlProvider.serverUrl());
    }

    private void savePhotoToGallery() {
        Photo photo = photos.get(position);
        String serverUrl = serverUrlProvider.serverUrl();
        String fullUrl = serverUrl + "/api/full/" + photo.id;
        Toast.makeText(activity, "Downloading full resolution...", Toast.LENGTH_SHORT).show();

        client.downloadImage(fullUrl, new PhotoArchiveClient.DownloadCallback() {
            @Override
            public void onSuccess(byte[] data, String contentType) {
                Uri uri = MediaHelper.saveToMediaStore(activity, photo.filename,
                        MediaHelper.mimeFromContentType(contentType), data);
                if (uri != null) {
                    Toast.makeText(activity, "Saved to gallery", Toast.LENGTH_SHORT).show();
                } else {
                    Toast.makeText(activity, "Could not save image",
                            Toast.LENGTH_SHORT).show();
                }
            }

            @Override
            public void onError(String message) {
                Toast.makeText(activity, "Save failed: " + message,
                        Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void loadPhoto() {
        Photo photo = photos.get(position);
        spinner.setVisibility(View.VISIBLE);
        imageView.resetZoom();
        imageLoader.loadInto(photo.previewUrl(serverUrlProvider.serverUrl()), imageView, Color.BLACK,
                () -> spinner.setVisibility(View.GONE));

        counter.setText(String.format(Locale.US, "%,d of %,d", position + 1, photos.size()));
        nameView.setText(photo.filename);
        detailView.setText(photo.detailLine());

        String dimLine = photo.dimensionLine();
        dimensionView.setText(dimLine);
        dimensionView.setVisibility(dimLine.isEmpty() ? View.GONE : View.VISIBLE);

        updateFlagButtons(photo.flag);

        cachedExif = null;
        exifVisible = false;
        exifPanel.removeAllViews();
        exifPanel.setVisibility(View.GONE);

        prefetchAdjacent();
    }

    private void navigateNext() {
        if (position < photos.size() - 1) {
            position++;
            loadPhoto();
        } else {
            showEdgeBounce();
        }
    }

    private void navigatePrev() {
        if (position > 0) {
            position--;
            loadPhoto();
        } else {
            showEdgeBounce();
        }
    }

    private void showEdgeBounce() {
        counter.setTextColor(theme.accent);
        mainHandler.postDelayed(() -> counter.setTextColor(Color.WHITE), 300);
        imageView.performHapticFeedback(android.view.HapticFeedbackConstants.REJECT);
    }

    private void toggleChrome() {
        chromeVisible = !chromeVisible;
        int visibility = chromeVisible ? View.VISIBLE : View.GONE;
        counter.setVisibility(visibility);
        infoPanel.setVisibility(visibility);
    }

    private void prefetchAdjacent() {
        String serverUrl = serverUrlProvider.serverUrl();
        if (position > 0) {
            imageLoader.prefetch(photos.get(position - 1).previewUrl(serverUrl));
        }
        if (position < photos.size() - 1) {
            imageLoader.prefetch(photos.get(position + 1).previewUrl(serverUrl));
        }
    }

    private int dp(float value) {
        return theme.dp(activity, value);
    }
}
