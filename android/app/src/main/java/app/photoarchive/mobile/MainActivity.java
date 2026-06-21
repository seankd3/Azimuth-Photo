package app.photoarchive.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Dialog;
import android.content.ClipData;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.inputmethod.EditorInfo;
import android.widget.AbsListView;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.GridView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final int PAGE_SIZE = 90;
    private static final int PICK_IMPORT_PHOTOS = 3001;
    private static final int[] RECONNECT_DELAYS_MS = {1_500, 3_000, 6_000, 12_000, 20_000, 30_000};

    private final AppTheme theme = new AppTheme();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private ServerSettings serverSettings;
    private PhotoArchiveClient client;
    private ImageLoader imageLoader;
    private PhotoAdapter adapter;
    private BottomNav bottomNav;

    private String serverUrl;
    private String activeQuery = "";
    private int offset = 0;
    private int totalImages = 0;
    private boolean loading = false;
    private boolean endReached = false;
    private boolean reconnectScheduled = false;
    private int reconnectAttempts = 0;

    private TextView titleView;
    private TextView subtitleView;
    private TextView serverChip;
    private TextView statusLine;
    private TextView emptyView;
    private ProgressBar progressBar;
    private EditText searchInput;
    private GridView gridView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        serverSettings = new ServerSettings(this);
        serverUrl = serverSettings.load();
        client = new PhotoArchiveClient();
        imageLoader = new ImageLoader();
        adapter = new PhotoAdapter(this, imageLoader, () -> serverUrl, theme.tile, theme.muted);

        getWindow().setStatusBarColor(theme.background);
        getWindow().setNavigationBarColor(theme.background);
        buildInterface();
        checkServer();
        loadFresh();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        mainHandler.removeCallbacksAndMessages(null);
        client.shutdown();
        imageLoader.shutdown();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != PICK_IMPORT_PHOTOS || resultCode != RESULT_OK || data == null) return;
        List<Uri> uris = selectedUris(data);
        if (!uris.isEmpty()) confirmImport(uris);
    }

    private void buildInterface() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(theme.background);
        setContentView(root);

        root.addView(buildHeader(), new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));
        root.addView(buildContent(), new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1
        ));

        bottomNav = new BottomNav(
                this,
                theme,
                this::showArchive,
                this::focusSearch,
                this::choosePhotosToImport,
                this::showServerDialog
        );
        root.addView(bottomNav.view(), new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));
    }

    private LinearLayout buildHeader() {
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.VERTICAL);
        top.setPadding(dp(16), dp(14), dp(16), dp(10));

        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        top.addView(titleRow);

        titleView = theme.label(this, "Archive", 28, theme.text, true);
        titleRow.addView(titleView, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        serverChip = theme.chip(this, "Omarchy");
        serverChip.setOnClickListener(view -> showServerDialog());
        titleRow.addView(serverChip);

        subtitleView = theme.label(this, "Self-hosted through photoArchive", 14, theme.muted, false);
        subtitleView.setPadding(0, dp(6), 0, dp(6));
        top.addView(subtitleView);

        statusLine = theme.label(this, "Checking server...", 12, theme.muted, false);
        statusLine.setPadding(0, 0, 0, dp(10));
        top.addView(statusLine);

        top.addView(buildSearchRow());
        return top;
    }

    private LinearLayout buildSearchRow() {
        LinearLayout searchRow = new LinearLayout(this);
        searchRow.setGravity(Gravity.CENTER_VERTICAL);

        searchInput = new EditText(this);
        searchInput.setSingleLine(true);
        searchInput.setHint("People, places, filenames");
        searchInput.setHintTextColor(Color.rgb(118, 124, 131));
        searchInput.setTextColor(theme.text);
        searchInput.setTextSize(15);
        searchInput.setInputType(InputType.TYPE_CLASS_TEXT);
        searchInput.setImeOptions(EditorInfo.IME_ACTION_SEARCH);
        searchInput.setPadding(dp(14), 0, dp(14), 0);
        searchInput.setBackground(theme.roundRect(Color.rgb(22, 23, 25), dp(16), Color.rgb(48, 51, 55), 1));
        searchInput.setOnEditorActionListener((view, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEARCH) {
                bottomNav.setActive("Search");
                loadFresh();
                return true;
            }
            return false;
        });
        searchRow.addView(searchInput, new LinearLayout.LayoutParams(0, dp(46), 1));

        Button searchButton = theme.actionButton(this, "Search");
        searchButton.setOnClickListener(view -> {
            bottomNav.setActive("Search");
            loadFresh();
        });
        LinearLayout.LayoutParams buttonParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(46));
        buttonParams.setMargins(dp(8), 0, 0, 0);
        searchRow.addView(searchButton, buttonParams);
        return searchRow;
    }

    private FrameLayout buildContent() {
        FrameLayout content = new FrameLayout(this);

        gridView = new GridView(this);
        gridView.setNumColumns(3);
        gridView.setStretchMode(GridView.STRETCH_COLUMN_WIDTH);
        gridView.setVerticalSpacing(dp(2));
        gridView.setHorizontalSpacing(dp(2));
        gridView.setPadding(dp(2), dp(2), dp(2), dp(96));
        gridView.setClipToPadding(false);
        gridView.setBackgroundColor(theme.background);
        gridView.setAdapter(adapter);
        gridView.setOnItemClickListener((parent, view, position, id) -> openPhoto((Photo) adapter.getItem(position)));
        gridView.setOnScrollListener(new AbsListView.OnScrollListener() {
            @Override
            public void onScrollStateChanged(AbsListView view, int scrollState) {
            }

            @Override
            public void onScroll(AbsListView view, int firstVisibleItem, int visibleItemCount, int totalItemCount) {
                if (totalItemCount > 0 && firstVisibleItem + visibleItemCount >= totalItemCount - 12) {
                    loadNextPage(false);
                }
            }
        });
        content.addView(gridView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        emptyView = theme.label(this, "", 16, theme.muted, false);
        emptyView.setGravity(Gravity.CENTER);
        emptyView.setPadding(dp(32), dp(32), dp(32), dp(32));
        emptyView.setVisibility(TextView.GONE);
        content.addView(emptyView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        progressBar = new ProgressBar(this);
        progressBar.setVisibility(ProgressBar.GONE);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(dp(48), dp(48));
        progressParams.gravity = Gravity.CENTER;
        content.addView(progressBar, progressParams);
        return content;
    }

    private void showArchive() {
        titleView.setText("Archive");
        bottomNav.setActive("Archive");
        searchInput.setText("");
        loadFresh();
    }

    private void focusSearch() {
        titleView.setText("Search");
        bottomNav.setActive("Search");
        searchInput.requestFocus();
    }

    private void loadFresh() {
        activeQuery = searchInput.getText().toString().trim();
        titleView.setText(activeQuery.isEmpty() ? "Archive" : "Search");
        offset = 0;
        totalImages = 0;
        endReached = false;
        adapter.clear();
        emptyView.setVisibility(TextView.GONE);
        gridView.setSelection(0);
        loadNextPage(true);
    }

    private void loadNextPage(boolean firstPage) {
        if (loading || endReached) return;
        loading = true;
        progressBar.setVisibility(ProgressBar.VISIBLE);
        updateSummary("Loading from Omarchy...");

        client.fetchPhotos(serverUrl, activeQuery, offset, PAGE_SIZE, new PhotoArchiveClient.PhotosCallback() {
            @Override
            public void onSuccess(PhotoPage page) {
                loading = false;
                progressBar.setVisibility(ProgressBar.GONE);
                resetConnectionState();
                if (firstPage) adapter.clear();
                adapter.addPhotos(page.photos);
                offset += page.photos.size();
                totalImages = page.totalImages;
                endReached = page.photos.isEmpty() || offset >= Math.max(page.totalImages, offset);
                prefetchNextFew(page.photos);
                updateSummary(null);
                showEmptyIfNeeded();
            }

            @Override
            public void onError(String message) {
                loading = false;
                progressBar.setVisibility(ProgressBar.GONE);
                updateSummary(null);
                showReconnectingState();
                if (adapter.getCount() == 0) {
                    showEmpty("Connecting to Omarchy...");
                    scheduleReconnect();
                } else {
                    scheduleReconnect();
                }
            }
        });
    }

    private void checkServer() {
        statusLine.setTextColor(theme.muted);
        statusLine.setText("Checking server...");
        client.checkConnection(serverUrl, new PhotoArchiveClient.StatusCallback() {
            @Override
            public void onSuccess(ConnectionStatus status) {
                resetConnectionState();
                statusLine.setTextColor(status.stale ? theme.muted : theme.good);
                statusLine.setText(String.format(Locale.US, "Connected privately. %,d photos available.", status.totalImages));
            }

            @Override
            public void onError(String message) {
                showReconnectingState();
                scheduleReconnect();
            }
        });
    }

    private void showReconnectingState() {
        statusLine.setTextColor(theme.muted);
        statusLine.setText("Connecting to Omarchy...");
        if (adapter.getCount() == 0) {
            subtitleView.setText("Self-hosted through photoArchive");
        }
    }

    private void scheduleReconnect() {
        if (reconnectScheduled) return;
        int index = Math.min(reconnectAttempts, RECONNECT_DELAYS_MS.length - 1);
        int delayMs = RECONNECT_DELAYS_MS[index];
        reconnectAttempts++;
        reconnectScheduled = true;
        mainHandler.postDelayed(() -> {
            reconnectScheduled = false;
            checkServer();
            if (adapter.getCount() == 0) {
                loadFresh();
            } else {
                loadNextPage(false);
            }
        }, delayMs);
    }

    private void resetConnectionState() {
        reconnectAttempts = 0;
        reconnectScheduled = false;
        mainHandler.removeCallbacksAndMessages(null);
    }

    private void openPhoto(Photo photo) {
        Dialog dialog = new Dialog(this, android.R.style.Theme_Black_NoTitleBar_Fullscreen);
        FrameLayout frame = new FrameLayout(this);
        frame.setBackgroundColor(Color.BLACK);
        dialog.setContentView(frame);

        ImageView imageView = new ImageView(this);
        imageView.setScaleType(ImageView.ScaleType.FIT_CENTER);
        imageView.setBackgroundColor(Color.BLACK);
        frame.addView(imageView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        ProgressBar spinner = new ProgressBar(this);
        FrameLayout.LayoutParams spinnerParams = new FrameLayout.LayoutParams(dp(48), dp(48));
        spinnerParams.gravity = Gravity.CENTER;
        frame.addView(spinner, spinnerParams);

        frame.addView(photoInfoPanel(photo, dialog), infoPanelParams());
        imageLoader.loadInto(photo.previewUrl(serverUrl), imageView, Color.BLACK, () -> spinner.setVisibility(ProgressBar.GONE));
        dialog.show();
    }

    private LinearLayout photoInfoPanel(Photo photo, Dialog dialog) {
        LinearLayout info = new LinearLayout(this);
        info.setOrientation(LinearLayout.VERTICAL);
        info.setPadding(dp(18), dp(14), dp(18), dp(20));
        info.setBackgroundColor(Color.argb(210, 0, 0, 0));

        TextView name = theme.label(this, photo.filename, 17, Color.WHITE, true);
        info.addView(name);

        TextView detail = theme.label(this, photo.detailLine(), 13, Color.rgb(210, 214, 218), false);
        detail.setPadding(0, dp(4), 0, dp(10));
        info.addView(detail);

        Button close = theme.actionButton(this, "Close");
        close.setOnClickListener(view -> dialog.dismiss());
        info.addView(close, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(42)));
        return info;
    }

    private FrameLayout.LayoutParams infoPanelParams() {
        FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
        );
        params.gravity = Gravity.BOTTOM;
        return params;
    }

    private void choosePhotosToImport() {
        titleView.setText("Import");
        bottomNav.setActive("Import");
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("image/*");
        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        startActivityForResult(Intent.createChooser(intent, "Choose photos for Omarchy"), PICK_IMPORT_PHOTOS);
    }

    private void confirmImport(List<Uri> uris) {
        String count = uris.size() + " photo" + (uris.size() == 1 ? "" : "s");
        new AlertDialog.Builder(this)
                .setTitle("Import to Omarchy")
                .setMessage("Send " + count + " into photoArchive. Originals stay on this phone; the server receives a copy.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Import", (dialog, which) -> importPhotos(uris))
                .show();
    }

    private void importPhotos(List<Uri> uris) {
        progressBar.setVisibility(ProgressBar.VISIBLE);
        updateSummary("Importing " + uris.size() + " photo" + (uris.size() == 1 ? "" : "s") + " to Omarchy...");
        client.uploadImport(this, serverUrl, uris, new PhotoArchiveClient.ImportCallback() {
            @Override
            public void onSuccess(int imported, int skipped) {
                progressBar.setVisibility(ProgressBar.GONE);
                String skippedText = skipped > 0 ? " " + skipped + " already existed." : "";
                Toast.makeText(MainActivity.this, "Imported " + imported + " photo" + (imported == 1 ? "" : "s") + "." + skippedText, Toast.LENGTH_LONG).show();
                checkServer();
                loadFresh();
            }

            @Override
            public void onError(String message) {
                progressBar.setVisibility(ProgressBar.GONE);
                updateSummary(null);
                Toast.makeText(MainActivity.this, "Import failed: " + message, Toast.LENGTH_LONG).show();
            }
        });
    }

    private List<Uri> selectedUris(Intent data) {
        List<Uri> uris = new ArrayList<>();
        ClipData clipData = data.getClipData();
        if (clipData != null) {
            for (int i = 0; i < clipData.getItemCount(); i++) {
                Uri uri = clipData.getItemAt(i).getUri();
                if (uri != null) uris.add(uri);
            }
        } else if (data.getData() != null) {
            uris.add(data.getData());
        }
        return uris;
    }

    private void showServerDialog() {
        titleView.setText("Server");
        bottomNav.setActive("Server");
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(serverUrl);
        input.setSelectAllOnFocus(true);

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Self-hosted server")
                .setMessage("Use your Omarchy Tailscale URL. The app stores only this URL and talks directly to photoArchive.")
                .setView(input)
                .setNegativeButton("Cancel", null)
                .setNeutralButton("Use Omarchy", null)
                .setPositiveButton("Save", (view, which) -> saveServer(input.getText().toString()))
                .create();

        dialog.setOnShowListener(view -> dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(button -> {
            input.setText(ServerSettings.DEFAULT_SERVER_URL);
            saveServer(input.getText().toString());
            dialog.dismiss();
        }));
        dialog.show();
    }

    private void saveServer(String rawUrl) {
        String normalized = ServerSettings.normalize(rawUrl);
        if (normalized.isEmpty()) {
            Toast.makeText(this, "Enter a photoArchive server URL.", Toast.LENGTH_SHORT).show();
            return;
        }
        serverUrl = normalized;
        serverSettings.save(serverUrl);
        adapter.notifyDataSetChanged();
        checkServer();
        loadFresh();
    }

    private void updateSummary(String override) {
        if (override != null) {
            subtitleView.setText(override);
        } else if (!activeQuery.isEmpty()) {
            subtitleView.setText(totalImages + " result" + (totalImages == 1 ? "" : "s") + " for \"" + activeQuery + "\"");
        } else if (totalImages > 0) {
            subtitleView.setText(String.format(Locale.US, "%,d photos on Omarchy", totalImages));
        } else {
            subtitleView.setText("Self-hosted through photoArchive");
        }
        serverChip.setText(serverUrl.replace("http://", "").replace("https://", ""));
    }

    private void showEmptyIfNeeded() {
        if (adapter.getCount() == 0) {
            showEmpty(activeQuery.isEmpty()
                    ? "No photos found on this server yet."
                    : "No matches for \"" + activeQuery + "\".");
        } else {
            emptyView.setVisibility(TextView.GONE);
        }
    }

    private void showEmpty(String message) {
        emptyView.setText(message);
        emptyView.setVisibility(TextView.VISIBLE);
    }

    private void prefetchNextFew(List<Photo> photos) {
        int count = Math.min(photos.size(), 12);
        for (int i = 0; i < count; i++) {
            imageLoader.prefetch(photos.get(i).thumbUrl(serverUrl));
        }
    }

    private int dp(float value) {
        return theme.dp(this, value);
    }
}
