// Bundled local engine lifecycle: start, wait behind the splash, open the library,
// and stop the child when the desktop app exits.

use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};

use crate::engine;

const BASE_URL: &str = "http://127.0.0.1:8010";
const LIBRARY_URL: &str = "http://127.0.0.1:8010/d";
const READY_TIMEOUT: Duration = Duration::from_secs(45);
const READY_POLL: Duration = Duration::from_millis(500);

static CHILD: Mutex<Option<Child>> = Mutex::new(None);

fn agent(timeout: Duration) -> ureq::Agent {
    ureq::Agent::new_with_config(
        ureq::Agent::config_builder()
            .timeout_global(Some(timeout))
            .build(),
    )
}

fn alive() -> bool {
    agent(Duration::from_secs(2))
        .get(format!("{BASE_URL}/").as_str())
        .call()
        .is_ok()
}

fn spawn_server(app: &AppHandle) -> Result<Child, String> {
    let mut command: Command = engine::command(app)?;
    command
        .spawn()
        .map_err(|error| format!("Azimuth Photo could not start its local engine: {error}"))
}

/// Runs on a background thread: ensure the server is up, then leave the splash.
pub fn start(app: AppHandle) {
    let win = app.get_webview_window("main");
    let status = |message: &str, is_error: bool| {
        if let Some(win) = &win {
            let text = serde_json::to_string(message).unwrap_or_default();
            let _ = win.eval(format!(
                "window.__paStatus && window.__paStatus({text}, {is_error})"
            ));
        }
    };

    if alive() {
        status("Opening your library…", false);
    } else {
        status("Preparing your library…", false);
        match spawn_server(&app) {
            Ok(child) => *CHILD.lock().unwrap() = Some(child),
            Err(error) => {
                status(&error, true);
                return;
            }
        }
    }

    let deadline = Instant::now() + READY_TIMEOUT;
    while Instant::now() < deadline {
        if alive() {
            if let (Some(win), Ok(url)) = (&win, LIBRARY_URL.parse()) {
                let _ = win.navigate(url);
            }
            return;
        }
        if let Some(child) = CHILD.lock().unwrap().as_mut() {
            if let Ok(Some(exit)) = child.try_wait() {
                status(
                    &format!("Azimuth Photo closed while opening your library ({exit})."),
                    true,
                );
                return;
            }
        }
        std::thread::sleep(READY_POLL);
    }
    status(
        "Your library took too long to open. Quit Azimuth Photo and try again.",
        true,
    );
}

/// Kill the child server, if we spawned one. Idempotent.
pub fn shutdown() {
    if let Some(mut child) = CHILD.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}
