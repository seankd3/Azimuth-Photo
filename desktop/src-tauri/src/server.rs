// Local satellite server lifecycle: spawn (unless one is already serving :8010),
// poll readiness behind the splash, navigate to the library, kill the child on exit.
//
// Paths + env come from %APPDATA%/photoarchive/shell.json when present
// ({ python, server_cwd, env }) with the constants below as fallbacks — see desktop/BUILD.md.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};

pub const BASE_URL: &str = "http://127.0.0.1:8010";
const LIBRARY_URL: &str = "http://127.0.0.1:8010/d";
const READY_TIMEOUT: Duration = Duration::from_secs(45);
const READY_POLL: Duration = Duration::from_millis(500);

const PYTHON: &str = r"C:\Users\smast\OneDrive\Desktop\Projects\photography\photoarchive-field\web\.venv\Scripts\python.exe";
const SERVER_CWD: &str =
    r"C:\Users\smast\OneDrive\Desktop\Projects\photography\photoarchive-field\web";

// Satellite profile per FIELD_README.md, pointed at the omarchy hub. No smoke
// mode: it skips init_db and all workers, so thumbnails never flush.
const SERVER_ENV: &[(&str, &str)] = &[
    ("PHOTOARCHIVE_MODE", "satellite"),
    ("PHOTOARCHIVE_HUB_URL", "http://100.102.150.104:8000"),
    ("PHOTOARCHIVE_HOME", r"C:\PhotoArchiveField"),
    (
        "PHOTOARCHIVE_THUMB_CACHE_DIR",
        r"C:\PhotoArchiveField\thumbs",
    ),
    (
        "PHOTOARCHIVE_DEVELOP_CACHE_DIR",
        r"C:\PhotoArchiveField\develop",
    ),
    ("PHOTOARCHIVE_EXPORT_DIR", r"C:\PhotoArchiveField\exports"),
    ("PHOTOARCHIVE_PORT", "8010"),
];

static CHILD: Mutex<Option<Child>> = Mutex::new(None);

struct ShellConfig {
    python: PathBuf,
    server_cwd: PathBuf,
    env: Vec<(String, String)>,
}

fn appdata_dir() -> Option<PathBuf> {
    std::env::var_os("APPDATA").map(PathBuf::from)
}

fn shell_config_path() -> Option<PathBuf> {
    Some(appdata_dir()?.join("photoarchive").join("shell.json"))
}

fn default_shell_config() -> ShellConfig {
    ShellConfig {
        python: PathBuf::from(PYTHON),
        server_cwd: PathBuf::from(SERVER_CWD),
        env: SERVER_ENV
            .iter()
            .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
            .collect(),
    }
}

fn load_shell_config() -> ShellConfig {
    let mut config = default_shell_config();
    let Some(path) = shell_config_path() else {
        return config;
    };
    let Ok(raw) = fs::read_to_string(&path) else {
        return config;
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return config;
    };
    if let Some(python) = value.get("python").and_then(|v| v.as_str()) {
        if !python.trim().is_empty() {
            config.python = PathBuf::from(python);
        }
    }
    if let Some(cwd) = value
        .get("server_cwd")
        .or_else(|| value.get("serverCwd"))
        .and_then(|v| v.as_str())
    {
        if !cwd.trim().is_empty() {
            config.server_cwd = PathBuf::from(cwd);
        }
    }
    if let Some(env_map) = value.get("env").and_then(|v| v.as_object()) {
        for (key, raw_value) in env_map {
            if let Some(text) = raw_value.as_str() {
                if let Some(existing) = config.env.iter_mut().find(|(k, _)| k == key) {
                    existing.1 = text.to_string();
                } else {
                    config.env.push((key.clone(), text.to_string()));
                }
            }
        }
    }
    let _ = Path::new(&config.python);
    config
}

pub fn agent(timeout: Duration) -> ureq::Agent {
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

fn spawn_server() -> std::io::Result<Child> {
    let config = load_shell_config();
    let mut cmd = Command::new(&config.python);
    cmd.args([
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8010",
    ])
    .current_dir(&config.server_cwd);
    for (key, value) in &config.env {
        cmd.env(key, value);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console flash
    }
    cmd.spawn()
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
        // A field server is already running externally — just connect to it.
        status("Connecting to the running library server…", false);
    } else {
        status("Starting the local library…", false);
        match spawn_server() {
            Ok(child) => *CHILD.lock().unwrap() = Some(child),
            Err(error) => {
                status(&format!("Couldn't start the library server: {error}"), true);
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
                    &format!("The library server exited during startup ({exit})."),
                    true,
                );
                return;
            }
        }
        std::thread::sleep(READY_POLL);
    }
    status(
        "The library server didn't come up within 45 seconds. Check photoarchive-field\\web.",
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
