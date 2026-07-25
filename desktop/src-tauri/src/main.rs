// Azimuth Photo desktop shell — starts the bundled local engine and opens the library.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod engine;
mod server;
mod tray;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

fn main() {
    tauri::Builder::default()
        // Must be first: a second launch pings this instance and exits.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.show();
                let _ = win.unminimize();
                let _ = win.set_focus();
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .setup(|app| {
            let win = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Azimuth Photo")
                .inner_size(1500.0, 950.0)
                .min_inner_size(900.0, 600.0)
                .disable_drag_drop_handler() // let the web app own drag & drop (publishing, masks)
                .on_navigation(|url| {
                    // Stay on the local archive (and the bundled splash page);
                    // anything external opens in the default browser instead.
                    let local = matches!(url.host_str(), Some("127.0.0.1") | Some("localhost"))
                        || url
                            .host_str()
                            .is_some_and(|h| h.ends_with("tauri.localhost"))
                        || url.scheme() == "tauri";
                    if !local && (url.scheme() == "http" || url.scheme() == "https") {
                        let _ = open::that_detached(url.to_string());
                        return false;
                    }
                    true
                })
                .build()?;
            #[cfg(debug_assertions)]
            win.open_devtools();
            let _ = win.set_focus();

            tray::setup(app)?;

            // Bring up (or attach to) the local engine off the main thread.
            let handle = app.handle().clone();
            std::thread::spawn(move || server::start(handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Azimuth Photo desktop")
        .run(|_app, event| {
            if let RunEvent::ExitRequested { .. } = event {
                server::shutdown();
            }
        });
}
