use super::{
    paths::{assert_safe_remove_target, normalize_zip_path, safe_child_path},
    test_support::Sandbox,
};
use std::fs;

#[test]
fn resource_paths_reject_traversal_absolute_paths_and_stream_names() {
    for path in [
        "../escape",
        "assets/../escape",
        "C:/escape",
        "/escape",
        "\\\\server\\share",
        "assets/file:stream",
    ] {
        assert!(normalize_zip_path(path).is_err(), "{path}");
    }
    assert_eq!(
        normalize_zip_path("assets\\characters/默认/./正常.png").unwrap(),
        "assets/characters/默认/正常.png"
    );
}

#[test]
fn missing_descendants_can_be_created_inside_the_selected_root() {
    let sandbox = Sandbox::new();
    let target = safe_child_path(&sandbox.0, "assets/characters/默认/正常.png").unwrap();
    fs::create_dir_all(target.parent().unwrap()).unwrap();
    fs::write(&target, b"fixture").unwrap();
    assert_eq!(fs::read(target).unwrap(), b"fixture");
    assert!(assert_safe_remove_target(&sandbox.0, &sandbox.0).is_err());
    let other = Sandbox::new();
    assert!(assert_safe_remove_target(&sandbox.0, &other.0).is_err());
}

#[cfg(windows)]
fn junction(link: &std::path::Path, destination: &std::path::Path) {
    let result = std::process::Command::new("cmd")
        .args(["/D", "/C", "mklink", "/J"])
        .arg(link)
        .arg(destination)
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "cannot create isolated junction fixture"
    );
}

#[cfg(windows)]
#[test]
fn existing_junction_targets_cannot_escape_the_pack_root() {
    let sandbox = Sandbox::new();
    let outside = Sandbox::new();
    let link = sandbox.0.join("link");
    junction(&link, &outside.0);
    let result = safe_child_path(&sandbox.0, "link");
    fs::remove_dir(link).unwrap();
    assert!(result.is_err());
}

#[cfg(windows)]
#[test]
fn nonexistent_parents_under_junctions_cannot_escape_the_pack_root() {
    let sandbox = Sandbox::new();
    let outside = Sandbox::new();
    let link = sandbox.0.join("link");
    junction(&link, &outside.0);
    let result = safe_child_path(&sandbox.0, "link/not-created/normal.png");
    fs::remove_dir(link).unwrap();
    assert!(result.is_err());
    assert!(!outside.0.join("not-created").exists());
}
