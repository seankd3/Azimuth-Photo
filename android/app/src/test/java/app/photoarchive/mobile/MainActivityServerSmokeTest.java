package app.photoarchive.mobile;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 30)
public class MainActivityServerSmokeTest {
    @Test
    public void launchesAgainstConfiguredPhotoArchiveServer() throws Exception {
        MainActivity activity = Robolectric.buildActivity(MainActivity.class).setup().get();

        String uiText = waitForUsefulState(activity);

        assertFalse(uiText, uiText.contains("Offline"));
        assertFalse(uiText, uiText.contains("Cannot reach photoArchive"));
        assertTrue(uiText, uiText.contains("Connected privately") || uiText.contains("photos on Omarchy"));
    }

    private String waitForUsefulState(MainActivity activity) throws Exception {
        String uiText = "";
        for (int i = 0; i < 80; i++) {
            org.robolectric.shadows.ShadowLooper.runUiThreadTasksIncludingDelayedTasks();
            uiText = collectText(activity.getWindow().getDecorView());
            if (uiText.contains("Offline")
                    || uiText.contains("Cannot reach photoArchive")
                    || uiText.contains("Connected privately")
                    || uiText.contains("photos on Omarchy")) {
                return uiText;
            }
            Thread.sleep(250);
        }
        return uiText;
    }

    private String collectText(View view) {
        StringBuilder text = new StringBuilder();
        collectText(view, text);
        return text.toString();
    }

    private void collectText(View view, StringBuilder text) {
        if (view instanceof TextView) {
            text.append(((TextView) view).getText()).append('\n');
        }
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                collectText(group.getChildAt(i), text);
            }
        }
    }
}
