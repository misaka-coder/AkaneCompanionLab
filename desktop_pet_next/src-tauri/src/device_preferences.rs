//! Instance-local capability choices. Session evidence and stops are never persisted.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::{Mutex, OnceLock}};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Choices {
    version: u32,
    instance_id: String,
    computer_input_enabled: bool,
    personal_chrome_enabled: bool,
}
struct Store { path: PathBuf, choices: Choices, error: Option<&'static str> }
static STORE: OnceLock<Mutex<Store>> = OnceLock::new();

impl Store {
    fn load(path: PathBuf, instance_id: String) -> Self {
        let defaults = Choices { version: 1, instance_id, computer_input_enabled: true, personal_chrome_enabled: true };
        let loaded = match std::fs::read(&path) {
            Ok(bytes) => serde_json::from_slice::<Choices>(&bytes).ok()
                .filter(|v| v.version == 1 && v.instance_id == defaults.instance_id)
                .ok_or("device_preferences_invalid"),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(defaults.clone()),
            Err(_) => Err("device_preferences_read_failed"),
        };
        match loaded {
            Ok(choices) => Self { path, choices, error: None },
            Err(reason) => Self { path, choices: Choices { computer_input_enabled: false, personal_chrome_enabled: false, ..defaults }, error: Some(reason) },
        }
    }
    fn save(&mut self, action: &str, enabled: bool) -> Result<(), &'static str> {
        let mut next = self.choices.clone();
        match action {
            "configure" => next.computer_input_enabled = enabled,
            "chrome_configure" => next.personal_chrome_enabled = enabled,
            _ => return Err("invalid_control_arguments"),
        }
        let raw = serde_json::to_string_pretty(&next).map_err(|_| "device_preferences_save_failed")?;
        if crate::write_text_atomic(&self.path, &raw).is_err() {
            self.error = Some("device_preferences_save_failed");
            return Err("device_preferences_save_failed");
        }
        self.choices = next;
        self.error = None;
        Ok(())
    }
}

pub fn initialize() {
    // Presentation processes must never overwrite the execution owner's choices.
    if crate::env_flag_enabled(crate::EXTERNAL_DEVICE_EXECUTOR_ENV) && !crate::is_device_only_mode() { return; }
    let (Ok(root), Ok(instance)) = (crate::akane_data_root(), crate::runtime_instance_id()) else { return; };
    let store = STORE.get_or_init(|| Mutex::new(Store::load(root.join("config/device-capabilities.json"), instance)));
    if let Ok(store) = store.lock() {
        // False is already the runtime default. Calling configure(false) here would
        // stop the shared lease and accidentally block the other enabled capability.
        if store.choices.computer_input_enabled { crate::computer_use::local_control("configure", &json!({"enabled":true})); }
        if store.choices.personal_chrome_enabled { crate::personal_browser::local_control("chrome_configure", &json!({"enabled":true})); }
    }
}

pub fn configure(action: &str, args: &Value, apply: impl FnOnce() -> Value) -> Value {
    let Some(store) = STORE.get() else { return apply(); };
    let Ok(mut store) = store.try_lock() else { return json!({"ok":false,"reason":"device_preferences_busy"}); };
    let mut result = apply();
    if result["ok"] == true {
        if let Some(enabled) = args["enabled"].as_bool() {
            if let Err(reason) = store.save(action, enabled) {
                // Report the live state truthfully even when persistence failed.
                result["ok"] = json!(false);
                result["reason"] = json!(reason);
                result["preference_saved"] = json!(false);
            } else { result["preference_saved"] = json!(true); }
        }
    }
    result
}

pub fn annotate(result: &mut Value) {
    if let Some(store) = STORE.get() {
        if let Ok(store) = store.try_lock() {
            result["preference_error"] = json!(store.error.unwrap_or(""));
        }
    }
}

#[cfg(test)] mod tests {
    use super::*;
    fn root() -> PathBuf {
        std::env::temp_dir().join(format!("akane-preferences-{}-{}", std::process::id(), crate::current_time_millis()))
    }
    #[test] fn remembers_both_choices_across_reload_and_separates_instances() {
        let root = root(); let path = root.join("choices.json");
        let mut store = Store::load(path.clone(), "one".into());
        assert!(store.choices.computer_input_enabled && store.choices.personal_chrome_enabled);
        store.save("configure", false).unwrap();
        let mut store = Store::load(path.clone(), "one".into());
        assert!(!store.choices.computer_input_enabled && store.choices.personal_chrome_enabled);
        store.save("chrome_configure", false).unwrap();
        store.save("configure", true).unwrap();
        let store = Store::load(path.clone(), "one".into());
        assert!(store.choices.computer_input_enabled && !store.choices.personal_chrome_enabled);
        let wrong = Store::load(path, "two".into());
        assert_eq!(wrong.error, Some("device_preferences_invalid"));
        assert!(!wrong.choices.computer_input_enabled && !wrong.choices.personal_chrome_enabled);
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test] fn damaged_preferences_are_reported_and_can_be_replaced_explicitly() {
        let root = root(); std::fs::create_dir_all(&root).unwrap(); let path = root.join("choices.json");
        std::fs::write(&path, b"{broken").unwrap();
        let mut store = Store::load(path.clone(), "one".into());
        assert_eq!(store.error, Some("device_preferences_invalid"));
        assert!(!store.choices.computer_input_enabled && !store.choices.personal_chrome_enabled);
        store.save("chrome_configure", true).unwrap();
        assert!(Store::load(path, "one".into()).choices.personal_chrome_enabled);
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test] fn failed_save_does_not_claim_a_persisted_choice() {
        let root = root(); std::fs::create_dir_all(&root).unwrap();
        let path = root.join("not-a-directory"); std::fs::write(&path, b"fixture").unwrap();
        let mut store = Store::load(path.join("choices.json"), "one".into());
        let before = store.choices.computer_input_enabled;
        assert_eq!(store.save("configure", !before), Err("device_preferences_save_failed"));
        assert_eq!(store.choices.computer_input_enabled, before);
        std::fs::remove_dir_all(root).unwrap();
    }
}
