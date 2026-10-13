use crate::pet_windows::secondary::settings_window_url_for;

#[test]
fn control_center_uses_one_canonical_entry() {
    assert_eq!(settings_window_url_for(false), "control-center-lab.html");
    assert_eq!(
        settings_window_url_for(true),
        "control-center-lab.html?page=model"
    );
}
