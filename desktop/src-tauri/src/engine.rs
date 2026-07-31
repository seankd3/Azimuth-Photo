//! Resolve and launch the bundled Azimuth Photo engine.
//!
//! Installed builds use the frozen onedir server bundled as a Tauri resource.
//! Developers can point at another frozen binary with an environment override;
//! customer installs never need Python, a checkout, or a configuration file.
//!
//! A person who already has an Azimuth Photo library keeps it: an optional
//! pointer file in the user configuration folder names that library, and the
//! app opens it instead of making a new one. A first install has no pointer
//! file and keeps its own private data.

use std::path::{Path, PathBuf};
use std::process::Command;

use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager};

const ENGINE_DIR: &str = "azimuth-server";

/// User configuration folder entry that names an existing library.
const LIBRARY_POINTER: [&str; 2] = ["Azimuth Photo", "library.json"];

/// Pointer field -> engine environment variable. Fields left out keep the
/// packaged default, so a partial pointer file stays valid.
const LIBRARY_FIELDS: [(&str, &str); 3] = [
    ("data_root", "AZIMUTH_HOME"),
    ("mode", "AZIMUTH_MODE"),
    ("preview_root", "AZIMUTH_THUMB_CACHE_DIR"),
];

fn engine_filename() -> &'static str {
    if cfg!(windows) {
        "azimuth-server.exe"
    } else {
        "azimuth-server"
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
    std::env::var_os("AZIMUTH_DESKTOP_ENGINE_PATH")
        .filter(|value| !value.is_empty())
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

/// Read the pointer file. Absent or unreadable means "use the packaged
/// defaults" — a missing pointer is the normal first-install condition.
fn library_document(app: &AppHandle) -> Option<String> {
    let [folder, file] = LIBRARY_POINTER;
    let path = app
        .path()
        .resolve(Path::new(folder).join(file), BaseDirectory::Config)
        .ok()?;
    std::fs::read_to_string(path).ok()
}

/// Turn a pointer document into engine environment overrides. Unreadable or
/// unexpected content gives no overrides.
fn library_overrides(document: &str) -> Vec<(&'static str, String)> {
    let parsed = match serde_json::from_str::<serde_json::Value>(document) {
        Ok(value) => value,
        Err(_) => return Vec::new(),
    };
    LIBRARY_FIELDS
        .iter()
        .filter_map(|(field, variable)| {
            let value = parsed.get(field)?.as_str()?.trim();
            if value.is_empty() {
                return None;
            }
            Some((*variable, value.to_string()))
        })
        .collect()
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
        .env("AZIMUTH_PORT", "8010");
    if let Some(document) = library_document(app) {
        for (variable, value) in library_overrides(&document) {
            command.env(variable, value);
        }
    }
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
    fn packaged_engine_name_is_canonical() {
        let expected = if cfg!(windows) {
            "azimuth-server.exe"
        } else {
            "azimuth-server"
        };
        assert_eq!(engine_filename(), expected);
        assert_eq!(ENGINE_DIR, "azimuth-server");
    }

    #[test]
    fn pointer_names_an_existing_library() {
        let overrides = library_overrides(
            r#"{"data_root": "D:\\Photos", "mode": "satellite", "preview_root": " "}"#,
        );
        assert_eq!(
            overrides,
            vec![
                ("AZIMUTH_HOME", "D:\\Photos".to_string()),
                ("AZIMUTH_MODE", "satellite".to_string()),
            ]
        );
    }

    #[test]
    fn unusable_pointer_keeps_the_packaged_defaults() {
        assert!(library_overrides("").is_empty());
        assert!(library_overrides("{}").is_empty());
        assert!(library_overrides(r#"{"data_root": 7}"#).is_empty());
    }
}
