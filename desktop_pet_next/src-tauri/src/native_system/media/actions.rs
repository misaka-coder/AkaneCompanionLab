pub(crate) fn normalize_system_media_control_action(action: &str) -> String {
    match action.trim().to_ascii_lowercase().as_str() {
        "play" | "resume" => "play",
        "pause" => "pause",
        "stop" => "stop",
        "next" | "skip" => "next",
        "previous" | "prev" => "previous",
        _ => "",
    }
    .to_string()
}
