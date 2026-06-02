package app.photoarchive.mobile;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Handler;
import android.os.Looper;
import android.util.LruCache;
import android.widget.ImageView;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class ImageLoader {
    private final LruCache<String, Bitmap> cache;
    private final ExecutorService executor = Executors.newFixedThreadPool(4);
    private final Handler main = new Handler(Looper.getMainLooper());

    ImageLoader() {
        int maxMemoryKb = (int) (Runtime.getRuntime().maxMemory() / 1024L);
        cache = new LruCache<String, Bitmap>(Math.max(8 * 1024, maxMemoryKb / 6)) {
            @Override
            protected int sizeOf(String key, Bitmap value) {
                return value.getByteCount() / 1024;
            }
        };
    }

    void loadInto(String url, ImageView target, int placeholderColor) {
        loadInto(url, target, placeholderColor, null);
    }

    void loadInto(String url, ImageView target, int placeholderColor, Runnable onDone) {
        target.setTag(url);
        Bitmap cached = cache.get(url);
        if (cached != null) {
            target.setImageBitmap(cached);
            if (onDone != null) onDone.run();
            return;
        }
        target.setImageDrawable(null);
        target.setBackgroundColor(placeholderColor);
        executor.execute(() -> {
            Bitmap bitmap = null;
            try {
                bitmap = download(url);
                if (bitmap != null) {
                    cache.put(url, bitmap);
                }
            } catch (Exception ignored) {
                bitmap = null;
            }
            Bitmap result = bitmap;
            main.post(() -> {
                Object tag = target.getTag();
                if (url.equals(tag) && result != null) {
                    target.setImageBitmap(result);
                }
                if (onDone != null) onDone.run();
            });
        });
    }

    void prefetch(String url) {
        if (cache.get(url) != null) return;
        executor.execute(() -> {
            try {
                Bitmap bitmap = download(url);
                if (bitmap != null) {
                    cache.put(url, bitmap);
                }
            } catch (Exception ignored) {
                // Prefetch is opportunistic.
            }
        });
    }

    void shutdown() {
        executor.shutdownNow();
    }

    private Bitmap download(String urlString) throws Exception {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(urlString);
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(20_000);
            connection.setUseCaches(true);
            connection.setRequestProperty("Accept", "image/*");
            int code = connection.getResponseCode();
            if (code < 200 || code >= 300) return null;
            try (InputStream input = connection.getInputStream()) {
                BitmapFactory.Options options = new BitmapFactory.Options();
                options.inPreferredConfig = Bitmap.Config.RGB_565;
                return BitmapFactory.decodeStream(input, null, options);
            }
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }
}
