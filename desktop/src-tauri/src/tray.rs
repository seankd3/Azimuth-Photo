// System tray: Open / live sync-status readout / Pause-Resume sync / Quit.
// Status polls GET /api/sync/status every 30s; toggle POSTs /api/sync/pause|resume.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{App, AppHandle, Manager, Wry};

use crate::server;

const TRAY_ID: &str = "photoarchive";
const POLL_INTERVAL: Duration = Duration::from_secs(30);
const HTTP_TIMEOUT: Duration = Duration::from_secs(5);

/// Last known paused state, so the toggle knows which endpoint to hit.
static PAUSED: AtomicBool = AtomicBool::new(false);

struct SyncStatus {
    paused: bool,
    queue_depth: u64,
}

pub fn setup(app: &App) -> tauri::Result<()> {
    let open_item = MenuItem::with_id(app, "open", "Open photoArchive", true, None::<&str>)?;
    let status_item =
        MenuItem::with_id(app, "sync-status", "Sync: starting…", false, None::<&str>)?;
    let toggle_item = MenuItem::with_id(app, "sync-toggle", "Pause sync", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &open_item,
            &PredefinedMenuItem::separator(app)?,
            &status_item,
            &toggle_item,
            &PredefinedMenuItem::separator(app)?,
            &quit_item,
        ],
    )?;

    let (status_for_menu, toggle_for_menu) = (status_item.clone(), toggle_item.clone());
    let tray = TrayIconBuilder::with_id(TRAY_ID)
        .icon(
            app.default_window_icon()
                .expect("bundled window icon")
                .clone(),
        )
        .tooltip("photoArchive")
        .menu(&menu)
        .on_menu_event(move |app, event| match event.id.as_ref() {
            "open" => show_main_window(app),
            "sync-toggle" => {
                let path = if PAUSED.load(Ordering::Relaxed) {
                    "/api/sync/resume"
                } else {
                    "/api/sync/pause"
                };
                let _ = server::agent(HTTP_TIMEOUT)
                    .post(format!("{}{}", server::BASE_URL, path).as_str())
                    .send_empty();
                if let Some(tray) = app.tray_by_id(TRAY_ID) {
                    refresh(&status_for_menu, &toggle_for_menu, &tray);
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;

    std::thread::spawn(move || loop {
        refresh(&status_item, &toggle_item, &tray);
        std::thread::sleep(POLL_INTERVAL);
    });
    Ok(())
}

fn fetch_status() -> Option<SyncStatus> {
    let mut response = server::agent(HTTP_TIMEOUT)
        .get(format!("{}/api/sync/status", server::BASE_URL).as_str())
        .call()
        .ok()?;
    let value: serde_json::Value = response.body_mut().read_json().ok()?;
    Some(SyncStatus {
        paused: value
            .get("paused")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        queue_depth: value
            .get("queue_depth")
            .and_then(|v| v.as_u64())
            .unwrap_or(0),
    })
}

fn refresh(status_item: &MenuItem<Wry>, toggle_item: &MenuItem<Wry>, tray: &TrayIcon<Wry>) {
    let Some(status) = fetch_status() else {
        let _ = status_item.set_text("Sync: server offline");
        let _ = tray.set_tooltip(Some("photoArchive — server offline"));
        return;
    };
    PAUSED.store(status.paused, Ordering::Relaxed);
    let label = if status.paused {
        format!("Sync: paused ({} queued)", status.queue_depth)
    } else if status.queue_depth > 0 {
        format!("Sync: {} queued", status.queue_depth)
    } else {
        "Sync: up to date".to_string()
    };
    let _ = status_item.set_text(&label);
    let _ = toggle_item.set_text(if status.paused {
        "Resume sync"
    } else {
        "Pause sync"
    });
    let _ = tray.set_tooltip(Some(format!("photoArchive — {label}")));
}

fn show_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}
