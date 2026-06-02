package app.photoarchive.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Dialog;
import android.content.ClipData;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
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

    private final int colorBackground = Color.rgb(12, 13, 14);
    private final int colorPanel = Color.rgb(24, 25, 27);
    private final int colorTile = Color.rgb(34, 37, 40);
    private final int colorText = Color.rgb(242, 242, 242);
    private final int colorMuted = Color.rgb(160, 166, 173);
    private final int colorAccent = Color.rgb(144, 199, 255);

    private ServerSettings serverSettings;
    private PhotoArchiveClient client;
    private ImageLoader imageLoader;
    private PhotoAdapter adapter;

    private String serverUrl;
    private String activeQuery = "";
    private int offset = 0;
    private int totalImages = 0;
    private boolean loading = false;
    private boolean endReached = false;

    private TextView titleView;
    private TextView subtitleView;
    private TextView serverChip;
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
        adapter = new PhotoAdapter(this, imageLoader, () -> serverUrl, colorTile, colorMuted);

        getWindow().setStatusBarColor(colorBackground);
        getWindow().setNavigationBarColor(colorBackground);
        buildInterface();
        loadFresh();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        client.shutdown();
        imageLoader.shutdown();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != PICK_IMPORT_PHOTOS || resultCode != RESULT_OK || data == null) {
            return;
        }
        List<Uri> uris = selectedUris(data);
        if (uris.isEmpty()) {
            return;
        }
        importPhotos(uris);
    }

    private void buildInterface() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(colorBackground);
        setContentView(root);

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.VERTICAL);
        top.setPadding(dp(16), dp(14), dp(16), dp(10));
        root.addView(top, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        top.addView(titleRow, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        titleView = new TextView(this);
        titleView.setText("Photos");
        titleView.setTextColor(colorText);
        titleView.setTextSize(28);
        titleView.setTypeface(Typeface.DEFAULT_BOLD);
        titleRow.addView(titleView, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        serverChip = chip("Omarchy");
        serverChip.setOnClickListener(view -> showServerDialog());
        titleRow.addView(serverChip);

        subtitleView = new TextView(this);
        subtitleView.setTextColor(colorMuted);
        subtitleView.setTextSize(14);
        subtitleView.setPadding(0, dp(6), 0, dp(10));
        top.addView(subtitleView);

        LinearLayout searchRow = new LinearLayout(this);
        searchRow.setGravity(Gravity.CENTER_VERTICAL);
        searchRow.setPadding(0, 0, 0, dp(4));
        top.addView(searchRow, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        searchInput = new EditText(this);
        searchInput.setSingleLine(true);
        searchInput.setHint("Search people, places, filenames");
        searchInput.setHintTextColor(Color.rgb(118, 124, 131));
        searchInput.setTextColor(colorText);
        searchInput.setTextSize(15);
        searchInput.setInputType(InputType.TYPE_CLASS_TEXT);
        searchInput.setImeOptions(EditorInfo.IME_ACTION_SEARCH);
        searchInput.setPadding(dp(14), 0, dp(14), 0);
        searchInput.setBackground(roundRect(Color.rgb(22, 23, 25), dp(16), Color.rgb(48, 51, 55), 1));
        searchInput.setOnEditorActionListener((view, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEARCH) {
                loadFresh();
                return true;
            }
            return false;
        });
        searchRow.addView(searchInput, new LinearLayout.LayoutParams(0, dp(46), 1));

        Button searchButton = actionButton("Search");
        searchButton.setOnClickListener(view -> loadFresh());
        LinearLayout.LayoutParams searchButtonParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                dp(46)
        );
        searchButtonParams.setMargins(dp(8), 0, 0, 0);
        searchRow.addView(searchButton, searchButtonParams);

        FrameLayout content = new FrameLayout(this);
        root.addView(content, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1
        ));

        gridView = new GridView(this);
        gridView.setNumColumns(3);
        gridView.setStretchMode(GridView.STRETCH_COLUMN_WIDTH);
        gridView.setVerticalSpacing(dp(2));
        gridView.setHorizontalSpacing(dp(2));
        gridView.setPadding(dp(2), dp(2), dp(2), dp(96));
        gridView.setClipToPadding(false);
        gridView.setBackgroundColor(colorBackground);
        gridView.setAdapter(adapter);
        gridView.setOnItemClickListener((parent, view, position, id) -> openPhoto((Photo) adapter.getItem(position)));
        gridView.setOnScrollListener(new AbsListView.OnScrollListener() {
            @Override
            public void onScrollStateChanged(AbsListView view, int scrollState) {
                // No-op.
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

        emptyView = new TextView(this);
        emptyView.setTextColor(colorMuted);
        emptyView.setTextSize(16);
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

        LinearLayout bottom = new LinearLayout(this);
        bottom.setGravity(Gravity.CENTER);
        bottom.setPadding(dp(12), dp(8), dp(12), dp(12));
        bottom.setBackgroundColor(Color.rgb(18, 19, 21));
        root.addView(bottom, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        bottom.addView(bottomButton("Photos", () -> {
            searchInput.setText("");
            loadFresh();
        }), bottomButtonParams());
        bottom.addView(bottomButton("Search", () -> searchInput.requestFocus()), bottomButtonParams());
        bottom.addView(bottomButton("Import", this::choosePhotosToImport), bottomButtonParams());
        bottom.addView(bottomButton("Server", this::showServerDialog), bottomButtonParams());
    }

    private void loadFresh() {
        activeQuery = searchInput.getText().toString().trim();
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
                if (firstPage) {
                    adapter.clear();
                }
                adapter.addPhotos(page.photos);
                offset += page.photos.size();
                totalImages = page.totalImages;
                endReached = page.photos.isEmpty() || offset >= Math.max(page.totalImages, offset);
                prefetchNextFew(page.photos);
                updateSummary(null);
                if (adapter.getCount() == 0) {
                    showEmpty(activeQuery.isEmpty()
                            ? "No photos found on this server yet."
                            : "No matches for \"" + activeQuery + "\".");
                } else {
                    emptyView.setVisibility(TextView.GONE);
                }
            }

            @Override
            public void onError(String message) {
                loading = false;
                progressBar.setVisibility(ProgressBar.GONE);
                updateSummary(null);
                if (adapter.getCount() == 0) {
                    showEmpty("Cannot reach photoArchive.\n\n" + message);
                } else {
                    Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show();
                }
            }
        });
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

        LinearLayout info = new LinearLayout(this);
        info.setOrientation(LinearLayout.VERTICAL);
        info.setPadding(dp(18), dp(14), dp(18), dp(20));
        info.setBackgroundColor(Color.argb(210, 0, 0, 0));

        TextView name = new TextView(this);
        name.setText(photo.filename);
        name.setTextColor(Color.WHITE);
        name.setTextSize(17);
        name.setTypeface(Typeface.DEFAULT_BOLD);
        info.addView(name);

        TextView detail = new TextView(this);
        detail.setText(photo.detailLine());
        detail.setTextColor(Color.rgb(210, 214, 218));
        detail.setTextSize(13);
        detail.setPadding(0, dp(4), 0, dp(10));
        info.addView(detail);

        Button close = actionButton("Close");
        close.setOnClickListener(view -> dialog.dismiss());
        info.addView(close, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(42)));

        FrameLayout.LayoutParams infoParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
        );
        infoParams.gravity = Gravity.BOTTOM;
        frame.addView(info, infoParams);

        imageLoader.loadInto(photo.previewUrl(serverUrl), imageView, Color.BLACK, () -> spinner.setVisibility(ProgressBar.GONE));
        dialog.show();
    }

    private void choosePhotosToImport() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("image/*");
        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        startActivityForResult(Intent.createChooser(intent, "Choose photos for Omarchy"), PICK_IMPORT_PHOTOS);
    }

    private void importPhotos(List<Uri> uris) {
        progressBar.setVisibility(ProgressBar.VISIBLE);
        updateSummary("Importing " + uris.size() + " photo" + (uris.size() == 1 ? "" : "s") + " to Omarchy...");
        client.uploadImport(this, serverUrl, uris, new PhotoArchiveClient.ImportCallback() {
            @Override
            public void onSuccess(int imported, int skipped) {
                progressBar.setVisibility(ProgressBar.GONE);
                Toast.makeText(MainActivity.this, "Imported " + imported + " photo" + (imported == 1 ? "" : "s") + ".", Toast.LENGTH_LONG).show();
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
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(serverUrl);
        input.setSelectAllOnFocus(true);

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Self-hosted server")
                .setMessage("Use your Omarchy Tailscale URL. The app stores this URL locally and talks directly to photoArchive.")
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

    private TextView chip(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(colorAccent);
        view.setTextSize(12);
        view.setSingleLine(true);
        view.setPadding(dp(12), dp(7), dp(12), dp(7));
        view.setBackground(roundRect(Color.rgb(22, 25, 29), dp(18), Color.rgb(58, 69, 82), 1));
        return view;
    }

    private Button actionButton(String text) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        button.setTextColor(colorText);
        button.setTextSize(13);
        button.setPadding(dp(12), 0, dp(12), 0);
        button.setBackground(roundRect(Color.rgb(37, 39, 43), dp(10), Color.rgb(54, 57, 62), 1));
        return button;
    }

    private TextView bottomButton(String text, Runnable action) {
        TextView button = new TextView(this);
        button.setText(text);
        button.setTextColor(colorText);
        button.setTextSize(13);
        button.setGravity(Gravity.CENTER);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setPadding(dp(8), dp(10), dp(8), dp(10));
        button.setOnClickListener(view -> action.run());
        return button;
    }

    private LinearLayout.LayoutParams bottomButtonParams() {
        return new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
    }

    private GradientDrawable roundRect(int fill, int radius, int strokeColor, int strokeWidth) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(fill);
        drawable.setCornerRadius(radius);
        drawable.setStroke(strokeWidth, strokeColor);
        return drawable;
    }

    private int dp(float value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
