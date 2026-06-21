package app.photoarchive.mobile;

import android.content.Context;
import android.graphics.Typeface;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

final class BottomNav {
    private final Context context;
    private final AppTheme theme;
    private final TextView photos;
    private final TextView search;
    private final TextView importButton;
    private final TextView compare;

    BottomNav(Context context, AppTheme theme, Runnable onPhotos, Runnable onSearch, Runnable onImport, Runnable onCompare) {
        this.context = context;
        this.theme = theme;
        photos = button("Archive", onPhotos);
        search = button("Search", onSearch);
        importButton = button("Import", onImport);
        compare = button("Compare", onCompare);
    }

    LinearLayout view() {
        LinearLayout bottom = new LinearLayout(context);
        bottom.setGravity(Gravity.CENTER);
        bottom.setPadding(theme.dp(context, 12), theme.dp(context, 8), theme.dp(context, 12), theme.dp(context, 12));
        bottom.setBackgroundColor(android.graphics.Color.rgb(18, 19, 21));
        bottom.addView(photos, params());
        bottom.addView(search, params());
        bottom.addView(importButton, params());
        bottom.addView(compare, params());
        setActive("Archive");
        return bottom;
    }

    void setActive(String label) {
        mark(photos, "Archive".equals(label));
        mark(search, "Search".equals(label));
        mark(importButton, "Import".equals(label));
        mark(compare, "Compare".equals(label));
    }

    private TextView button(String text, Runnable action) {
        TextView button = new TextView(context);
        button.setText(text);
        button.setTextSize(13);
        button.setGravity(Gravity.CENTER);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setPadding(theme.dp(context, 8), theme.dp(context, 10), theme.dp(context, 8), theme.dp(context, 10));
        button.setOnClickListener(view -> action.run());
        return button;
    }

    private void mark(TextView view, boolean active) {
        view.setTextColor(active ? theme.accent : theme.text);
        view.setBackground(active
                ? theme.roundRect(android.graphics.Color.rgb(28, 34, 40), theme.dp(context, 12), android.graphics.Color.rgb(58, 69, 82), 1)
                : null);
    }

    private LinearLayout.LayoutParams params() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
        params.setMargins(theme.dp(context, 3), 0, theme.dp(context, 3), 0);
        return params;
    }
}
