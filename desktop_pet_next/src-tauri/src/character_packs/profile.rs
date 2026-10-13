//! Shared configuration validation and profile I/O.

pub(crate) fn require_text(value: &str, label: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        Err(format!("{label} 不能为空。"))
    } else {
        Ok(())
    }
}

pub(crate) fn require_optional_object(
    value: &serde_json::Value,
    label: &str,
) -> Result<(), String> {
    if value.is_null() || value.is_object() {
        Ok(())
    } else {
        Err(format!("{label} 必须是对象。"))
    }
}
