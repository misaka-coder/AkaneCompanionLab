use std::path::{Path, PathBuf};

use crate::runtime::time::current_time_millis;

pub(crate) fn workspace_export_file_name(source_path: &Path, suggested_name: &str) -> String {
    let fallback = source_path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("akane-output");
    let mut file_name = sanitize_file_name(if suggested_name.trim().is_empty() {
        fallback
    } else {
        suggested_name.trim()
    });
    if Path::new(&file_name).extension().is_none() {
        if let Some(ext) = source_path.extension().and_then(|value| value.to_str()) {
            if !ext.trim().is_empty() {
                file_name.push('.');
                file_name.push_str(ext.trim());
            }
        }
    }
    file_name
}

pub(crate) fn sanitize_file_name(value: &str) -> String {
    let mut name: String = value
        .chars()
        .map(|ch| {
            if ch.is_control() || matches!(ch, '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*')
            {
                '_'
            } else {
                ch
            }
        })
        .collect();
    name = name.trim().trim_matches('.').trim().to_string();
    if name.is_empty() {
        return "akane-output".to_string();
    }
    if name.chars().count() > 160 {
        name = name.chars().take(160).collect();
        name = name.trim().trim_matches('.').trim().to_string();
    }
    if name.is_empty() {
        "akane-output".to_string()
    } else {
        name
    }
}

pub(crate) fn unique_child_file_path(directory: &Path, file_name: &str) -> PathBuf {
    let first = directory.join(file_name);
    if !first.exists() {
        return first;
    }

    let path = Path::new(file_name);
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("akane-output");
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("");
    for index in 2..1000 {
        let candidate_name = if extension.is_empty() {
            format!("{stem} ({index})")
        } else {
            format!("{stem} ({index}).{extension}")
        };
        let candidate = directory.join(candidate_name);
        if !candidate.exists() {
            return candidate;
        }
    }
    directory.join(format!("{}_{}", current_time_millis(), file_name))
}
