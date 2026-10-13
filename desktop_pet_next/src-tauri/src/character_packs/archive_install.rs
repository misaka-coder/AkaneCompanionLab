//! Stage, validate and publish imported character packs.
use super::archive_reader::{detect_character_pack_root, read_character_archive};
use super::archive_validation::validate_imported_character_pack;
use super::models::CharacterPackInstallResult;
use super::models::ZipEntry;
use super::paths::*;
use crate::character_packs::constants::{MAX_CHARACTER_PACK_ZIP_BYTES, PRIVATE_LOCAL_DIRECTORY};
use crate::local_files::system_open::open_path_in_file_manager;
use crate::runtime::paths::desktop_runtime_cache_dir;
use crate::runtime::time::current_time_millis;
use std::{
    fs,
    path::{Path, PathBuf},
};
use tauri::AppHandle;

#[tauri::command]
pub(crate) async fn install_character_pack_zip_file(
    _app: AppHandle,
    path: String,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        install_character_pack_zip_file_blocking(path, overwrite)
    })
    .await
    .map_err(|error| format!("角色包导入任务失败：{error}"))?
}

pub(crate) fn install_character_pack_zip_file_blocking(
    path: String,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    let zip_path = PathBuf::from(path.trim());
    if !zip_path.is_file() {
        return Err("请选择一个角色包 zip 文件。".to_string());
    }
    if !zip_path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .eq_ignore_ascii_case("zip")
    {
        return Err("角色包文件需要是 .zip。".to_string());
    }
    let metadata = fs::metadata(&zip_path).map_err(|error| error.to_string())?;
    if metadata.len() == 0 {
        return Err("这个 zip 文件是空的。".to_string());
    }
    if metadata.len() as usize > MAX_CHARACTER_PACK_ZIP_BYTES {
        return Err("角色包 zip 暂时请控制在 300MB 以内。".to_string());
    }

    let bytes = fs::read(&zip_path).map_err(|error| error.to_string())?;
    install_character_pack_zip(bytes, overwrite)
}

#[tauri::command]
pub(crate) async fn install_character_pack_zip_bytes(
    _app: AppHandle,
    file_name: String,
    bytes: Vec<u8>,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        install_character_pack_zip_bytes_blocking(file_name, bytes, overwrite)
    })
    .await
    .map_err(|error| format!("角色包导入任务失败：{error}"))?
}

pub(crate) fn install_character_pack_zip_bytes_blocking(
    file_name: String,
    bytes: Vec<u8>,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    if !file_name.trim().to_ascii_lowercase().ends_with(".zip") {
        return Err("角色包文件需要是 .zip。".to_string());
    }
    if bytes.is_empty() {
        return Err("这个 zip 文件是空的。".to_string());
    }
    if bytes.len() > MAX_CHARACTER_PACK_ZIP_BYTES {
        return Err("角色包 zip 暂时请控制在 300MB 以内。".to_string());
    }

    install_character_pack_zip(bytes, overwrite)
}

pub(crate) fn install_character_pack_zip(
    bytes: Vec<u8>,
    overwrite: bool,
) -> Result<CharacterPackInstallResult, String> {
    install_with_directories(bytes, overwrite, || {
        let cache = desktop_runtime_cache_dir("character_import")?;
        Ok((creator_kit_characters_dir()?, cache))
    })
}

pub(super) fn install_with_directories(
    bytes: Vec<u8>,
    overwrite: bool,
    directories: impl FnOnce() -> Result<(PathBuf, PathBuf), String>,
) -> Result<CharacterPackInstallResult, String> {
    let entries = read_character_archive(&bytes)?;
    let root = detect_character_pack_root(&entries)?;
    let pack_id = sanitize_pack_id(
        Path::new(&root)
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or(root.as_str()),
    );
    if pack_id.is_empty() {
        return Err("角色包 zip 缺少有效的包名。".into());
    }
    let (characters_dir, cache) = directories()?;
    let destination = safe_child_path(&characters_dir, &pack_id)?;
    if destination.exists() && !overwrite {
        return Err(format!("角色包 {pack_id} 已存在。勾选覆盖同名后再导入。"));
    }
    let temp_root = new_import_directory(&cache)?;
    let temp_pack_dir = temp_root.join(&pack_id);
    let nonce = temp_root.file_name().unwrap().to_string_lossy();
    let backup_name = format!(".{pack_id}.backup_{nonce}");
    let mut has_backup = false;
    let mut published = false;
    let install_result = (|| {
        let backup = safe_child_path(&characters_dir, &backup_name)?;
        extract_character_pack_entries(&entries, &root, &temp_pack_dir)?;
        let mut validation = validate_imported_character_pack(&temp_pack_dir)?;
        // Distinct imports can decode and stage concurrently. Publishing one
        // destination and carrying its _local state must complete together.
        static PUBLICATION: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _publication = PUBLICATION
            .lock()
            .map_err(|_| "角色包安装状态不可用。".to_string())?;
        fs::create_dir_all(&characters_dir).map_err(|error| error.to_string())?;
        if destination.exists() {
            if !overwrite {
                return Err(format!("角色包 {pack_id} 已存在。勾选覆盖同名后再导入。"));
            }
            assert_safe_remove_target(&characters_dir, &destination)?;
            fs::rename(&destination, &backup).map_err(|error| error.to_string())?;
            has_backup = true;
        }
        let publication = (|| {
            fs::rename(&temp_pack_dir, &destination).map_err(|error| error.to_string())?;
            published = true;
            if has_backup {
                let previous_local = safe_child_path(&backup, PRIVATE_LOCAL_DIRECTORY)?;
                if previous_local.exists() {
                    let installed_local = safe_child_path(&destination, PRIVATE_LOCAL_DIRECTORY)?;
                    // Imported archives never contribute _local, but retain the
                    // containment check if another local operation created it.
                    if installed_local.exists() {
                        assert_safe_remove_target(&destination, &installed_local)?;
                        fs::remove_dir_all(&installed_local).map_err(|error| error.to_string())?;
                    }
                    fs::rename(&previous_local, &installed_local)
                        .map_err(|error| format!("保留角色本机私有数据失败：{error}"))?;
                }
            }
            Ok::<(), String>(())
        })();
        if let Err(error) = publication {
            let rollback = (|| {
                if published {
                    assert_safe_remove_target(&characters_dir, &destination)?;
                    fs::remove_dir_all(&destination).map_err(|error| error.to_string())?;
                }
                if has_backup {
                    fs::rename(&backup, &destination).map_err(|error| error.to_string())?;
                }
                Ok::<(), String>(())
            })();
            return Err(match rollback {
                Ok(()) => error,
                Err(reason) => format!("{error}；恢复原角色包失败：{reason}"),
            });
        }
        if has_backup {
            assert_safe_remove_target(&characters_dir, &backup)?;
            if let Err(error) = fs::remove_dir_all(&backup) {
                validation
                    .warnings
                    .push(format!("旧角色包备份未清理：{error}"));
            }
        }
        let prefix = format!("{root}/");
        Ok(CharacterPackInstallResult {
            pack_id,
            character_id: validation.character_id,
            character_name: validation.character_name,
            installed_path: destination.to_string_lossy().to_string(),
            file_count: entries
                .iter()
                .filter(|entry| entry.name.starts_with(&prefix) && !entry.name.ends_with('/'))
                .filter(|entry| {
                    !is_private_local_relative_path(entry.name.strip_prefix(&prefix).unwrap())
                })
                .count(),
            requires_restart: false,
            warnings: validation.warnings,
        })
    })();
    if assert_safe_remove_target(&cache, &temp_root).is_ok() {
        let _ = fs::remove_dir_all(&temp_root);
    }
    install_result
}

fn new_import_directory(cache: &Path) -> Result<PathBuf, String> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT_IMPORT: AtomicU64 = AtomicU64::new(0);
    fs::create_dir_all(cache).map_err(|error| error.to_string())?;
    loop {
        let name = format!(
            "import_{}-{}-{}",
            current_time_millis(),
            std::process::id(),
            NEXT_IMPORT.fetch_add(1, Ordering::Relaxed)
        );
        let path = safe_child_path(cache, &name)?;
        match fs::create_dir(&path) {
            Ok(()) => return Ok(path),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.to_string()),
        }
    }
}

pub(crate) fn extract_character_pack_entries(
    entries: &[ZipEntry],
    root: &str,
    target_dir: &Path,
) -> Result<(), String> {
    let prefix = if root.is_empty() {
        String::new()
    } else {
        format!("{root}/")
    };
    for entry in entries {
        if entry.name.ends_with('/') {
            continue;
        }
        if !prefix.is_empty() && !entry.name.starts_with(&prefix) {
            continue;
        }
        let relative = if prefix.is_empty() {
            entry.name.as_str()
        } else {
            entry.name.strip_prefix(&prefix).unwrap()
        };
        if relative.is_empty() {
            continue;
        }
        if is_private_local_relative_path(relative) {
            continue;
        }
        let target = safe_child_path(target_dir, relative)?;
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        fs::write(target, &entry.data).map_err(|error| error.to_string())?;
    }
    Ok(())
}

pub(crate) fn is_private_local_relative_path(relative: &str) -> bool {
    relative
        .replace('\\', "/")
        .split('/')
        .next()
        .map(|part| part.eq_ignore_ascii_case(PRIVATE_LOCAL_DIRECTORY))
        .unwrap_or(false)
}

#[tauri::command]
pub(crate) fn open_character_packs_folder() -> Result<(), String> {
    let characters_dir = creator_kit_characters_dir()?;
    fs::create_dir_all(&characters_dir).map_err(|error| error.to_string())?;
    open_path_in_file_manager(&characters_dir)
}
