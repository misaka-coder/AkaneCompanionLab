use std::{
    fs,
    path::{Path, PathBuf},
};

use crate::runtime::config::{
    runtime_instance_id, APP_DIRECTORY_NAME, DATA_ROOT_ENV, LOCAL_DEFAULT_INSTANCE_ID,
};

pub(crate) fn desktop_runtime_cache_dir(category: &str) -> Result<PathBuf, String> {
    let path = desktop_runtime_cache_path(&akane_data_root()?, category)?;
    fs::create_dir_all(&path).map_err(|error| error.to_string())?;
    Ok(path)
}

pub(crate) fn desktop_runtime_cache_path(
    data_root: &Path,
    category: &str,
) -> Result<PathBuf, String> {
    let relative = match category {
        "attachments/audio" => PathBuf::from("attachments").join("audio"),
        "character_import" => PathBuf::from("character_import"),
        "export_staging" => PathBuf::from("export_staging"),
        // M66-D: workspace item staging for handle-based open/reveal/export
        "workspace_items" => PathBuf::from("workspace_items"),
        _ => return Err("invalid_desktop_cache_category".to_string()),
    };
    Ok(data_root.join("cache").join("desktop_pet").join(relative))
}

pub(crate) fn akane_data_root() -> Result<PathBuf, String> {
    let instance_id = runtime_instance_id()?;
    if let Some(explicit) = std::env::var_os(DATA_ROOT_ENV).filter(|value| !value.is_empty()) {
        return absolute_path(PathBuf::from(explicit));
    }

    if instance_id != LOCAL_DEFAULT_INSTANCE_ID {
        return Err("named_instance_requires_data_root".to_string());
    }

    #[cfg(windows)]
    {
        if let Some(base) = std::env::var_os("LOCALAPPDATA").or_else(|| std::env::var_os("APPDATA"))
        {
            return Ok(PathBuf::from(base).join(APP_DIRECTORY_NAME));
        }
        if let Some(home) = std::env::var_os("USERPROFILE") {
            return Ok(PathBuf::from(home)
                .join("AppData")
                .join("Local")
                .join(APP_DIRECTORY_NAME));
        }
    }

    #[cfg(target_os = "macos")]
    {
        if let Some(home) = std::env::var_os("HOME") {
            return Ok(PathBuf::from(home)
                .join("Library")
                .join("Application Support")
                .join(APP_DIRECTORY_NAME));
        }
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        if let Some(base) = std::env::var_os("XDG_DATA_HOME") {
            return Ok(PathBuf::from(base).join(APP_DIRECTORY_NAME));
        }
        if let Some(home) = std::env::var_os("HOME") {
            return Ok(PathBuf::from(home)
                .join(".local")
                .join("share")
                .join(APP_DIRECTORY_NAME));
        }
    }

    Err("无法定位 Akane 用户数据目录。".to_string())
}

pub(crate) fn absolute_path(path: PathBuf) -> Result<PathBuf, String> {
    if path.is_absolute() {
        return Ok(path);
    }
    std::env::current_dir()
        .map(|current| current.join(path))
        .map_err(|error| error.to_string())
}
