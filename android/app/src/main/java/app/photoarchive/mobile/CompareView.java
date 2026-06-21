package app.photoarchive.mobile;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;
import java.util.Locale;

final class CompareView {

    interface ServerUrlProvider {
        String serverUrl();
    }

    private final Activity activity;
    private final PhotoArchiveClient client;
    private final ImageLoader imageLoader;
    private final AppTheme theme;
    private final ServerUrlProvider serverUrlProvider;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private FrameLayout root;
    private TextView statsView;
    private FrameLayout photoFrameA;
    private FrameLayout photoFrameB;
    private ImageView imageA;
    private ImageView imageB;
    private TextView overlayA;
    private TextView overlayB;
    private ProgressBar spinner;

    private Photo currentA;
    private Photo currentB;
    private int sessionCount = 0;
    private double lastWinnerElo = 0;
    private boolean submitting = false;

    CompareView(Activity activity, PhotoArchiveClient client, ImageLoader imageLoader,
                AppTheme theme, ServerUrlProvider serverUrlProvider) {
        this.activity = activity;
        this.client = client;
        this.imageLoader = imageLoader;
        this.theme = theme;
        this.serverUrlProvider = serverUrlProvider;
        buildLayout();
    }

    View getView() {
        return root;
    }

    void loadNextPair() {
        submitting = false;
        spinner.setVisibility(View.VISIBLE);
        photoFrameA.setVisibility(View.INVISIBLE);
        photoFrameB.setVisibility(View.INVISIBLE);

        String serverUrl = serverUrlProvider.serverUrl();
        client.fetchComparePair(serverUrl, new PhotoArchiveClient.CompareCallback() {
            @Override
            public void onSuccess(List<Photo[]> pairs) {
                spinner.setVisibility(View.GONE);
                if (pairs.isEmpty()) {
                    Toast.makeText(activity, "No more pairs available", Toast.LENGTH_SHORT).show();
                    return;
                }
                Photo[] pair = pairs.get(0);
                currentA = pair[0];
                currentB = pair[1];
                showPair();
            }

            @Override
            public void onError(String message) {
                spinner.setVisibility(View.GONE);
                Toast.makeText(activity, "Compare: " + message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void showPair() {
        String serverUrl = serverUrlProvider.serverUrl();

        imageLoader.loadInto(currentA.previewUrl(serverUrl), imageA, theme.tile);
        imageLoader.loadInto(currentB.previewUrl(serverUrl), imageB, theme.tile);

        String rankA = currentA.comparisons > 0
                ? String.format(Locale.US, "Rank %,d", (int) currentA.elo)
                : "Unranked";
        overlayA.setText(currentA.filename + "  ·  " + rankA);

        String rankB = currentB.comparisons > 0
                ? String.format(Locale.US, "Rank %,d", (int) currentB.elo)
                : "Unranked";
        overlayB.setText(currentB.filename + "  ·  " + rankB);

        photoFrameA.setVisibility(View.VISIBLE);
        photoFrameB.setVisibility(View.VISIBLE);

        photoFrameA.setForeground(null);
        photoFrameB.setForeground(null);

        updateStats();
    }

    private void pickWinner(Photo winner, Photo loser, FrameLayout winnerFrame, FrameLayout loserFrame) {
        if (submitting || currentA == null || currentB == null) return;
        submitting = true;

        winnerFrame.setForeground(flashOverlay(Color.argb(80, 76, 175, 80)));
        loserFrame.setForeground(flashOverlay(Color.argb(80, 244, 67, 54)));

        String serverUrl = serverUrlProvider.serverUrl();
        client.submitComparison(serverUrl, winner.id, loser.id, new PhotoArchiveClient.CompareResultCallback() {
            @Override
            public void onSuccess(double winnerElo, double loserElo) {
                sessionCount++;
                lastWinnerElo = winnerElo;
                updateStats();
                mainHandler.postDelayed(() -> loadNextPair(), 200);
            }

            @Override
            public void onError(String message) {
                submitting = false;
                photoFrameA.setForeground(null);
                photoFrameB.setForeground(null);
                Toast.makeText(activity, "Compare failed: " + message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void undoLast() {
        String serverUrl = serverUrlProvider.serverUrl();
        client.undoComparison(serverUrl, new PhotoArchiveClient.UndoCallback() {
            @Override
            public void onSuccess() {
                if (sessionCount > 0) sessionCount--;
                Toast.makeText(activity, "Undone", Toast.LENGTH_SHORT).show();
                loadNextPair();
            }

            @Override
            public void onError(String message) {
                Toast.makeText(activity, "Undo: " + message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void updateStats() {
        if (sessionCount == 0) {
            statsView.setText("Tap the better photo");
        } else if (lastWinnerElo > 0) {
            statsView.setText(String.format(Locale.US,
                    "%d comparison%s  ·  Last rank: %,d",
                    sessionCount, sessionCount == 1 ? "" : "s", (int) lastWinnerElo));
        } else {
            statsView.setText(String.format(Locale.US,
                    "%d comparison%s", sessionCount, sessionCount == 1 ? "" : "s"));
        }
    }

    private void buildLayout() {
        root = new FrameLayout(activity);
        root.setBackgroundColor(theme.background);

        LinearLayout content = new LinearLayout(activity);
        content.setOrientation(LinearLayout.VERTICAL);
        root.addView(content, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));

        // Stats bar
        statsView = new TextView(activity);
        statsView.setTextColor(theme.muted);
        statsView.setTextSize(13);
        statsView.setGravity(Gravity.CENTER);
        statsView.setPadding(dp(16), dp(10), dp(16), dp(6));
        statsView.setText("Tap the better photo");
        content.addView(statsView, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        // Photo A (top half)
        photoFrameA = buildPhotoFrame();
        imageA = (ImageView) photoFrameA.getChildAt(0);
        overlayA = (TextView) photoFrameA.getChildAt(1);
        photoFrameA.setOnClickListener(v -> {
            if (currentA != null && currentB != null) {
                pickWinner(currentA, currentB, photoFrameA, photoFrameB);
            }
        });
        LinearLayout.LayoutParams photoAParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1);
        photoAParams.setMargins(dp(4), dp(4), dp(4), dp(2));
        content.addView(photoFrameA, photoAParams);

        // Divider with "vs"
        TextView divider = new TextView(activity);
        divider.setText("vs");
        divider.setTextColor(theme.muted);
        divider.setTextSize(14);
        divider.setTypeface(Typeface.DEFAULT_BOLD);
        divider.setGravity(Gravity.CENTER);
        divider.setPadding(0, dp(2), 0, dp(2));
        content.addView(divider, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        // Photo B (bottom half)
        photoFrameB = buildPhotoFrame();
        imageB = (ImageView) photoFrameB.getChildAt(0);
        overlayB = (TextView) photoFrameB.getChildAt(1);
        photoFrameB.setOnClickListener(v -> {
            if (currentA != null && currentB != null) {
                pickWinner(currentB, currentA, photoFrameB, photoFrameA);
            }
        });
        LinearLayout.LayoutParams photoBParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1);
        photoBParams.setMargins(dp(4), dp(2), dp(4), dp(4));
        content.addView(photoFrameB, photoBParams);

        // Button row
        LinearLayout buttonRow = new LinearLayout(activity);
        buttonRow.setOrientation(LinearLayout.HORIZONTAL);
        buttonRow.setGravity(Gravity.CENTER);
        buttonRow.setPadding(dp(16), dp(6), dp(16), dp(10));

        Button undoButton = theme.actionButton(activity, "Undo");
        undoButton.setOnClickListener(v -> undoLast());
        LinearLayout.LayoutParams undoParams = new LinearLayout.LayoutParams(0, dp(42), 1);
        undoParams.setMargins(0, 0, dp(8), 0);
        buttonRow.addView(undoButton, undoParams);

        Button skipButton = theme.actionButton(activity, "Skip");
        skipButton.setOnClickListener(v -> loadNextPair());
        buttonRow.addView(skipButton, new LinearLayout.LayoutParams(0, dp(42), 1));

        content.addView(buttonRow, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        // Spinner overlay (centered)
        spinner = new ProgressBar(activity);
        spinner.setVisibility(View.GONE);
        FrameLayout.LayoutParams spinnerParams = new FrameLayout.LayoutParams(dp(48), dp(48));
        spinnerParams.gravity = Gravity.CENTER;
        root.addView(spinner, spinnerParams);
    }

    private FrameLayout buildPhotoFrame() {
        FrameLayout frame = new FrameLayout(activity);

        ImageView image = new ImageView(activity);
        image.setScaleType(ImageView.ScaleType.CENTER_CROP);
        image.setBackgroundColor(theme.tile);
        frame.addView(image, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));

        TextView overlay = new TextView(activity);
        overlay.setTextColor(Color.WHITE);
        overlay.setTextSize(12);
        overlay.setTypeface(Typeface.DEFAULT_BOLD);
        overlay.setPadding(dp(10), dp(6), dp(10), dp(6));
        overlay.setBackgroundColor(Color.argb(160, 0, 0, 0));
        overlay.setSingleLine(true);
        FrameLayout.LayoutParams overlayParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT);
        overlayParams.gravity = Gravity.BOTTOM;
        frame.addView(overlay, overlayParams);

        GradientDrawable clip = new GradientDrawable();
        clip.setCornerRadius(dp(8));
        clip.setColor(theme.tile);
        frame.setBackground(clip);
        frame.setClipToOutline(true);

        return frame;
    }

    private GradientDrawable flashOverlay(int color) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(8));
        return drawable;
    }

    private int dp(float value) {
        return theme.dp(activity, value);
    }
}
