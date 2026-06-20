package app.photoarchive.mobile;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.widget.Button;
import android.widget.TextView;

final class AppTheme {
    final int background = Color.rgb(12, 13, 14);
    final int panel = Color.rgb(24, 25, 27);
    final int tile = Color.rgb(34, 37, 40);
    final int text = Color.rgb(242, 242, 242);
    final int muted = Color.rgb(160, 166, 173);
    final int accent = Color.rgb(144, 199, 255);
    final int good = Color.rgb(126, 217, 160);

    TextView chip(Context context, String text) {
        TextView view = new TextView(context);
        view.setText(text);
        view.setTextColor(accent);
        view.setTextSize(12);
        view.setSingleLine(true);
        view.setPadding(dp(context, 12), dp(context, 7), dp(context, 12), dp(context, 7));
        view.setBackground(roundRect(Color.rgb(22, 25, 29), dp(context, 18), Color.rgb(58, 69, 82), 1));
        return view;
    }

    Button actionButton(Context context, String text) {
        Button button = new Button(context);
        button.setText(text);
        button.setAllCaps(false);
        button.setTextColor(this.text);
        button.setTextSize(13);
        button.setPadding(dp(context, 12), 0, dp(context, 12), 0);
        button.setBackground(roundRect(Color.rgb(37, 39, 43), dp(context, 10), Color.rgb(54, 57, 62), 1));
        return button;
    }

    TextView label(Context context, String text, int size, int color, boolean bold) {
        TextView view = new TextView(context);
        view.setText(text);
        view.setTextColor(color);
        view.setTextSize(size);
        if (bold) {
            view.setTypeface(Typeface.DEFAULT_BOLD);
        }
        return view;
    }

    GradientDrawable roundRect(int fill, int radius, int strokeColor, int strokeWidth) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(fill);
        drawable.setCornerRadius(radius);
        drawable.setStroke(strokeWidth, strokeColor);
        return drawable;
    }

    int dp(Context context, float value) {
        return (int) (value * context.getResources().getDisplayMetrics().density + 0.5f);
    }
}
