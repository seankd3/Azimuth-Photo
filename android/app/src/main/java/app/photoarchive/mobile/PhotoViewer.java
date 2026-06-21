package app.photoarchive.mobile;

import android.app.Activity;
import android.app.Dialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.util.List;
import java.util.Locale;

final class PhotoViewer {

    interface ServerUrlProvider {
        String serverUrl();
    }

    private final Activity activity;
    private final List<Photo> photos;
    private final ServerUrlProvider serverUrlProvider;
    private final ImageLoader imageLoader;
    private final AppTheme theme;

    private int position;
    private Dialog dialog;
    private ZoomableImageView imageView;
    private ProgressBar spinner;
    private TextView counter;
    private LinearLayout infoPanel;
    private TextView nameView;
    private TextView detailView;
    private boolean chromeVisible = true;

    PhotoViewer(Activity activity, List<Photo> photos, int position,
                ServerUrlProvider serverUrlProvider, ImageLoader imageLoader, AppTheme theme) {
        this.activity = activity;
        this.photos = photos;
        this.position = Math.max(0, Math.min(position, photos.size() - 1));
        this.serverUrlProvider = serverUrlProvider;
        this.imageLoader = imageLoader;
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
        dialog.setOnKeyListener((d, keyCode, event) -> {
            if (keyCode == KeyEvent.KEYCODE_BACK && event.getAction() == KeyEvent.ACTION_UP) {
                dialog.dismiss();
                return true;
            }
            return false;
        });

        loadPhoto();
        dialog.show();
    }

    private LinearLayout buildInfoPanel() {
        LinearLayout info = new LinearLayout(activity);
        info.setOrientation(LinearLayout.VERTICAL);
        info.setPadding(dp(18), dp(14), dp(18), dp(20));
        info.setBackgroundColor(Color.argb(210, 0, 0, 0));

        nameView = theme.label(activity, "", 17, Color.WHITE, true);
        info.addView(nameView);

        detailView = theme.label(activity, "", 13, Color.rgb(210, 214, 218), false);
        detailView.setPadding(0, dp(4), 0, dp(10));
        info.addView(detailView);

        Button close = theme.actionButton(activity, "Close");
        close.setOnClickListener(view -> dialog.dismiss());
        info.addView(close, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, dp(42)));
        return info;
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

        prefetchAdjacent();
    }

    private void navigateNext() {
        if (position < photos.size() - 1) {
            position++;
            loadPhoto();
        }
    }

    private void navigatePrev() {
        if (position > 0) {
            position--;
            loadPhoto();
        }
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
