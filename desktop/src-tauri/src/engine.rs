//! Resolve and launch the bundled Azimuth Photo engine.
//!
//! Installed builds use the frozen onedir server bundled as a Tauri resource.
//! Developers can point at another frozen binary with an environment override;
//! customer installs never need Python, a checkout, or a configuration file.

use std::path::{Path, PathBuf};
use std::process::Command;

use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager};

const ENGINE_DIR: &str = "photoarchive-server";

fn engine_filename() -> &'static str {
    if cfg!(windows) {
        "photoarchive-server.exe"
    } else {
        "photoarchive-server"
    }
}

fn resource_engine_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .resolve(
            Path::new(ENGINE_DIR).join(engine_filename()),
            BaseDirectory::Resource,
        )
        .map_err(|error| format!("Azimuth Photo could not locate its local engine: {error}"))
}

fn developer_override() -> Option<PathBuf> {
    [
        "AZIMUTH_DESKTOP_ENGINE_PATH",
        "PHOTOARCHIVE_DESKTOP_ENGINE_PATH",
    ]
    .iter()
    .find_map(|key| std::env::var_os(key).filter(|value| !value.is_empty()))
    .map(PathBuf::from)
}

fn resolve_engine(app: &AppHandle) -> Result<PathBuf, String> {
    let path = developer_override()
        .map(Ok)
        .unwrap_or_else(|| resource_engine_path(app))?;
    if path.is_file() {
        Ok(path)
    } else {
        Err("Azimuth Photo's local engine is missing. Reinstall the app and try again.".into())
    }
}

pub fn command(app: &AppHandle) -> Result<Command, String> {
    let executable = resolve_engine(app)?;
    let working_directory = executable
        .parent()
        .ok_or_else(|| "Azimuth Photo's local engine path is incomplete.".to_string())?;
    let mut command = Command::new(&executable);
    command
        .args(["--host", "127.0.0.1", "--port", "8010"])
        .current_dir(working_directory)
        .env("AZIMUTH_MODE", "standalone")
        .env("AZIMUTH_PORT", "8010")
        // Compatibility keys remain frozen until the coordinated identifier migration.
        .env("PHOTOARCHIVE_MODE", "standalone")
        .env("PHOTOARCHIVE_PORT", "8010");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    Ok(command)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packaged_engine_name_stays_compatible() {
        let expected = if cfg!(windows) {
            "photoarchive-server.exe"
        } else {
            "photoarchive-server"
        };
        assert_eq!(engine_filename(), expected);
        assert_eq!(ENGINE_DIR, "photoarchive-server");
    }
}
