package app.photoarchive.mobile;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AbsListView;
import android.widget.BaseAdapter;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

final class PhotoAdapter extends BaseAdapter {
    interface ServerUrlProvider {
        String serverUrl();
    }

    private final Context context;
    private final ImageLoader imageLoader;
    private final ServerUrlProvider serverUrlProvider;
    private final int tileColor;
    private final int mutedColor;
    private final List<Photo> photos = new ArrayList<>();

    PhotoAdapter(
            Context context,
            ImageLoader imageLoader,
            ServerUrlProvider serverUrlProvider,
            int tileColor,
            int mutedColor
    ) {
        this.context = context;
        this.imageLoader = imageLoader;
        this.serverUrlProvider = serverUrlProvider;
        this.tileColor = tileColor;
        this.mutedColor = mutedColor;
    }

    void clear() {
        photos.clear();
        notifyDataSetChanged();
    }

    void addPhotos(List<Photo> newPhotos) {
        photos.addAll(newPhotos);
        notifyDataSetChanged();
    }

    @Override
    public int getCount() {
        return photos.size();
    }

    @Override
    public Object getItem(int position) {
        return photos.get(position);
    }

    @Override
    public long getItemId(int position) {
        return photos.get(position).id;
    }

    @Override
    public View getView(int position, View convertView, ViewGroup parent) {
        Holder holder;
        if (convertView == null) {
            FrameLayout frame = new FrameLayout(context);
            frame.setBackgroundColor(tileColor);

            ImageView image = new ImageView(context);
            image.setScaleType(ImageView.ScaleType.CENTER_CROP);
            frame.addView(image, new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT
            ));

            TextView month = new TextView(context);
            month.setTextColor(Color.WHITE);
            month.setTextSize(11);
            month.setTypeface(Typeface.DEFAULT_BOLD);
            month.setPadding(dp(7), dp(4), dp(7), dp(4));
            month.setBackground(pill());
            FrameLayout.LayoutParams monthParams = new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT
            );
            monthParams.gravity = Gravity.START | Gravity.TOP;
            monthParams.setMargins(dp(6), dp(6), dp(6), dp(6));
            frame.addView(month, monthParams);

            TextView name = new TextView(context);
            name.setTextColor(mutedColor);
            name.setTextSize(10);
            name.setSingleLine(true);
            name.setPadding(dp(6), dp(4), dp(6), dp(4));
            name.setBackgroundColor(Color.argb(160, 0, 0, 0));
            FrameLayout.LayoutParams nameParams = new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT
            );
            nameParams.gravity = Gravity.BOTTOM;
            frame.addView(name, nameParams);

            holder = new Holder(image, month, name);
            frame.setTag(holder);
            convertView = frame;
        } else {
            holder = (Holder) convertView.getTag();
        }

        int tileSize = Math.max(dp(112), parent.getWidth() > 0 ? parent.getWidth() / 3 : dp(124));
        convertView.setLayoutParams(new AbsListView.LayoutParams(
                AbsListView.LayoutParams.MATCH_PARENT,
                tileSize
        ));

        Photo photo = photos.get(position);
        holder.name.setText(photo.filename);
        holder.month.setText(showMonth(position) ? photo.monthLabel() : "");
        holder.month.setVisibility(showMonth(position) && !photo.monthLabel().isEmpty() ? View.VISIBLE : View.GONE);
        holder.image.setContentDescription(photo.filename);
        imageLoader.loadInto(photo.thumbUrl(serverUrlProvider.serverUrl()), holder.image, tileColor);
        return convertView;
    }

    private boolean showMonth(int position) {
        if (position == 0) return true;
        String current = photos.get(position).monthLabel();
        String previous = photos.get(position - 1).monthLabel();
        return !current.equals(previous);
    }

    private GradientDrawable pill() {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(Color.argb(190, 0, 0, 0));
        drawable.setCornerRadius(dp(12));
        return drawable;
    }

    private int dp(float value) {
        return (int) (value * context.getResources().getDisplayMetrics().density + 0.5f);
    }

    private static final class Holder {
        final ImageView image;
        final TextView month;
        final TextView name;

        Holder(ImageView image, TextView month, TextView name) {
            this.image = image;
            this.month = month;
            this.name = name;
        }
    }
}
