use crate::computer_use;
use crate::desktop_connection::contracts::{
    DESKTOP_CONTEXT_SNAPSHOT_TOOL_ID, DESKTOP_SCREENSHOT_TOOL_ID, OPEN_BROWSER_TOOL_ID,
    SATELLITE_OFFER_TOOL_IDS, SYSTEM_MEDIA_CONTROL_TOOL_ID, SYSTEM_MEDIA_SNAPSHOT_TOOL_ID,
    SYSTEM_PROCESS_SNAPSHOT_TOOL_ID, SYSTEM_PROCESS_TERMINATE_TOOL_ID, SYSTEM_VOLUME_TOOL_ID,
};
use crate::personal_browser;

#[test]
fn satellite_offer_id_retention_covers_every_registered_contract() {
    assert_eq!(SATELLITE_OFFER_TOOL_IDS.len(), 10);
    for tool_id in [
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
    ] {
        assert!(
            SATELLITE_OFFER_TOOL_IDS.contains(&tool_id),
            "missing offer id for {tool_id}"
        );
    }
}
