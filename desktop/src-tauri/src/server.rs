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
/// Long enough to fold a large write log, short enough that quitting still feels instant.
const PREPARE_QUIT_TIMEOUT: Duration = Duration::from_secs(20);

static CHILD: Mutex<Option<Child>> = Mutex::new(None);

fn agent(timeout: Duration) -> ureq::Agent {
    ureq::Agent::new_with_config(
        ureq::Agent::config_builder()
            .timeout_global(Some(timeout))
            .build(),
    )
}

fn alive() -> bool {
    let response = agent(Duration::from_secs(2))
        .get(format!("{BASE_URL}/api/system/health").as_str())
        .call();
    let Ok(mut response) = response else {
        return false;
    };
    response
        .body_mut()
        .read_json::<serde_json::Value>()
        .ok()
        .is_some_and(|body| {
            body.get("product").and_then(|value| value.as_str()) == Some("azimuth-v2")
                && body.get("ready").and_then(|value| value.as_bool()) == Some(true)
        })
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

/// Ask the engine to put the library away, then stop it. Idempotent.
///
/// Killing the child outright is fine for the process and not fine for the
/// catalog: the engine never gets to fold its write log back in or record that
/// it closed in a known state, so the *next* launch treats an ordinary quit as
/// a crash and reads the whole catalog before it will answer anything —
/// measured at 9.1 seconds on a 2.1GB library, every single launch.
pub fn shutdown() {
    let _ = agent(PREPARE_QUIT_TIMEOUT)
        .post(format!("{BASE_URL}/api/system/prepare-quit").as_str())
        .send_empty();
    if let Some(mut child) = CHILD.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}
