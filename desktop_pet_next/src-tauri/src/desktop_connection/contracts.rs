use crate::{computer_use, personal_browser};

pub(crate) const SATELLITE_PROTOCOL_VERSION: u64 = 1;

pub(crate) const OPEN_BROWSER_TOOL_ID: &str = "open_browser";

pub(crate) const OPEN_BROWSER_SPEC_VERSION: &str = "1.0.0";

pub(crate) const OPEN_BROWSER_SCHEMA_VERSION: u64 = 1;

pub(crate) const OPEN_BROWSER_SCHEMA_HASH: &str =
    "sha256:e92907fe6f7b3309ab5c681f2b4cf2e0f676c5e3255141ab69d10bdc7aa310f5";

pub(crate) const DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID: &str = "desktop_context_snapshot";

pub(crate) const DESKTOP_CONTEXT_SNAPSHOT_SPEC_VERSION: &str = "1.0.0";

pub(crate) const DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_VERSION: u64 = 1;

pub(crate) const DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_HASH: &str =
    "sha256:9f5b78b6381353be3f145341888682323c72dc0efa61edad246beb8c51a1011d";

pub(crate) const SYSTEM_MEDIA_SNAPSHOT_TOOL_ID: &str = "system_media_snapshot";

pub(crate) const SYSTEM_MEDIA_SNAPSHOT_SPEC_VERSION: &str = "1.0.0";

pub(crate) const SYSTEM_MEDIA_SNAPSHOT_SCHEMA_VERSION: u64 = 1;

pub(crate) const SYSTEM_MEDIA_SNAPSHOT_SCHEMA_HASH: &str =
    "sha256:b21d05e8f80871ef9908f2e8d3daf2f6fbcd19d1fd57a20eba34d9f6807f8bd7";

pub(crate) const SYSTEM_MEDIA_CONTROL_TOOL_ID: &str = "system_media_control";

pub(crate) const SYSTEM_MEDIA_CONTROL_SPEC_VERSION: &str = "1.0.0";

pub(crate) const SYSTEM_MEDIA_CONTROL_SCHEMA_VERSION: u64 = 1;

pub(crate) const SYSTEM_MEDIA_CONTROL_SCHEMA_HASH: &str =
    "sha256:70f940d83faeabbf820e2794ee3effa95015eb8f632490521ab26c24662c4906";

pub(crate) const SYSTEM_PROCESS_SNAPSHOT_TOOL_ID: &str = "system_process_snapshot";

pub(crate) const SYSTEM_PROCESS_SNAPSHOT_SPEC_VERSION: &str = "1.0.0";

pub(crate) const SYSTEM_PROCESS_SNAPSHOT_SCHEMA_VERSION: u64 = 1;

pub(crate) const SYSTEM_PROCESS_SNAPSHOT_SCHEMA_HASH: &str =
    "sha256:a0f3c803980ed44d799ac4fc6e69d5861042688aa5dc13862e8ae9c1af3b2900";

pub(crate) const SYSTEM_PROCESS_TERMINATE_TOOL_ID: &str = "system_process_terminate";

pub(crate) const SYSTEM_PROCESS_TERMINATE_SPEC_VERSION: &str = "1.0.0";

pub(crate) const SYSTEM_PROCESS_TERMINATE_SCHEMA_VERSION: u64 = 1;

pub(crate) const SYSTEM_PROCESS_TERMINATE_SCHEMA_HASH: &str =
    "sha256:5b121827fe5365b1c75611cb34a73ab0fe3fdeca2cd3bc50ea165d81835aab75";

pub(crate) const SYSTEM_VOLUME_TOOL_ID: &str = "system_volume";

pub(crate) const SYSTEM_VOLUME_SPEC_VERSION: &str = "1.0.0";

pub(crate) const SYSTEM_VOLUME_SCHEMA_VERSION: u64 = 1;

pub(crate) const SYSTEM_VOLUME_SCHEMA_HASH: &str =
    "sha256:af6c04d18e548326eaa141b2041c0e1a8f495cd742db1eeead6a62bf4ea158d5";

pub(crate) const DESKTOP_SCREENSHOT_TOOL_ID: &str = "desktop_screenshot";

pub(crate) const DESKTOP_SCREENSHOT_SPEC_VERSION: &str = "1.0.0";

pub(crate) const DESKTOP_SCREENSHOT_SCHEMA_VERSION: u64 = 1;

pub(crate) const DESKTOP_SCREENSHOT_SCHEMA_HASH: &str =
    "sha256:812eba3513348950d4b9ec65f56adea26d4f3a68307aa248e020531220e06e94";

pub(crate) const SATELLITE_OFFER_TOOL_IDS: [&str; 10] = [
    personal_browser::TOOL_ID,
    computer_use::protocol::TOOL_ID,
    DESKTOP_SCREENSHOT_TOOL_ID,
    OPEN_BROWSER_TOOL_ID,
    DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID,
    SYSTEM_MEDIA_SNAPSHOT_TOOL_ID,
    SYSTEM_MEDIA_CONTROL_TOOL_ID,
    SYSTEM_PROCESS_SNAPSHOT_TOOL_ID,
    SYSTEM_PROCESS_TERMINATE_TOOL_ID,
    SYSTEM_VOLUME_TOOL_ID,
];

pub(crate) fn satellite_tool_contract(tool_id: &str) -> Option<(&'static str, u64, &'static str)> {
    match tool_id {
        personal_browser::TOOL_ID => Some((
            personal_browser::SPEC_VERSION,
            personal_browser::SCHEMA_VERSION,
            personal_browser::SCHEMA_HASH,
        )),
        computer_use::protocol::TOOL_ID => Some((
            computer_use::protocol::SPEC_VERSION,
            computer_use::protocol::SCHEMA_VERSION,
            computer_use::protocol::SCHEMA_HASH,
        )),
        DESKTOP_SCREENSHOT_TOOL_ID => Some((
            DESKTOP_SCREENSHOT_SPEC_VERSION,
            DESKTOP_SCREENSHOT_SCHEMA_VERSION,
            DESKTOP_SCREENSHOT_SCHEMA_HASH,
        )),
        OPEN_BROWSER_TOOL_ID => Some((
            OPEN_BROWSER_SPEC_VERSION,
            OPEN_BROWSER_SCHEMA_VERSION,
            OPEN_BROWSER_SCHEMA_HASH,
        )),
        DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID => Some((
            DESKTOP_CONTEXT_SNAPSHOT_SPEC_VERSION,
            DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_VERSION,
            DESKTOP_CONTEXT_SNAPSHOT_SCHEMA_HASH,
        )),
        SYSTEM_MEDIA_SNAPSHOT_TOOL_ID => Some((
            SYSTEM_MEDIA_SNAPSHOT_SPEC_VERSION,
            SYSTEM_MEDIA_SNAPSHOT_SCHEMA_VERSION,
            SYSTEM_MEDIA_SNAPSHOT_SCHEMA_HASH,
        )),
        SYSTEM_MEDIA_CONTROL_TOOL_ID => Some((
            SYSTEM_MEDIA_CONTROL_SPEC_VERSION,
            SYSTEM_MEDIA_CONTROL_SCHEMA_VERSION,
            SYSTEM_MEDIA_CONTROL_SCHEMA_HASH,
        )),
        SYSTEM_PROCESS_SNAPSHOT_TOOL_ID => Some((
            SYSTEM_PROCESS_SNAPSHOT_SPEC_VERSION,
            SYSTEM_PROCESS_SNAPSHOT_SCHEMA_VERSION,
            SYSTEM_PROCESS_SNAPSHOT_SCHEMA_HASH,
        )),
        SYSTEM_PROCESS_TERMINATE_TOOL_ID => Some((
            SYSTEM_PROCESS_TERMINATE_SPEC_VERSION,
            SYSTEM_PROCESS_TERMINATE_SCHEMA_VERSION,
            SYSTEM_PROCESS_TERMINATE_SCHEMA_HASH,
        )),
        SYSTEM_VOLUME_TOOL_ID => Some((
            SYSTEM_VOLUME_SPEC_VERSION,
            SYSTEM_VOLUME_SCHEMA_VERSION,
            SYSTEM_VOLUME_SCHEMA_HASH,
        )),
        _ => None,
    }
}

pub(super) fn registration_frame(
    instance_id: &str,
    bot_id: &str,
    desktop_ui: bool,
    presentation_only: bool,
) -> Result<serde_json::Value, &'static str> {
    let offers = if presentation_only {
        Vec::new()
    } else {
        SATELLITE_OFFER_TOOL_IDS
            .iter()
            .map(|tool_id| {
                let (spec_version, schema_version, schema_hash) =
                    satellite_tool_contract(tool_id).ok_or("satellite_contract_unavailable")?;
                Ok(serde_json::json!({
                    "tool_id": tool_id,
                    "spec_version": spec_version,
                    "schema_version": schema_version,
                    "schema_hash": schema_hash,
                }))
            })
            .collect::<Result<Vec<_>, &'static str>>()?
    };
    Ok(serde_json::json!({
        "type": "register",
        "protocol_version": SATELLITE_PROTOCOL_VERSION,
        "instance_id": instance_id,
        "bot_id": bot_id,
        "desktop_ui": desktop_ui,
        "offers": offers,
    }))
}
