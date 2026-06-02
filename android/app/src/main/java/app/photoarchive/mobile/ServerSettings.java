package app.photoarchive.mobile;

import android.content.Context;
import android.content.SharedPreferences;

final class ServerSettings {
    static final String DEFAULT_SERVER_URL = "http://omarchy.tail0eeded.ts.net:8000";

    private static final String PREFS = "photoarchive-mobile";
    private static final String KEY_SERVER_URL = "server_url";

    private final SharedPreferences preferences;

    ServerSettings(Context context) {
        preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    String load() {
        return normalize(preferences.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL));
    }

    void save(String serverUrl) {
        preferences.edit().putString(KEY_SERVER_URL, normalize(serverUrl)).apply();
    }

    static String normalize(String rawUrl) {
        if (rawUrl == null) return "";
        String url = rawUrl.trim();
        if (url.isEmpty()) return "";
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        return url;
    }
}
