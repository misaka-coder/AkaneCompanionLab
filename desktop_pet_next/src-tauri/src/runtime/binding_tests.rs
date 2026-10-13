use crate::runtime::config::is_safe_instance_id;
use crate::runtime::paths::desktop_runtime_cache_path;
use std::path::Path;

#[test]
fn instance_ids_use_the_backend_safe_grammar() {
    assert!(is_safe_instance_id("local-default"));
    assert!(is_safe_instance_id("finance.prod_1"));
    assert!(!is_safe_instance_id("../finance"));
    assert!(!is_safe_instance_id(""));
}

#[test]
fn mutable_desktop_artifacts_resolve_below_the_instance_root() {
    let root_a = Path::new("instance-a-root");
    let root_b = Path::new("instance-b-root");
    for category in [
        "attachments/audio",
        "character_import",
        "export_staging",
        "workspace_items",
    ] {
        let path_a = desktop_runtime_cache_path(root_a, category).unwrap();
        let path_b = desktop_runtime_cache_path(root_b, category).unwrap();
        assert!(path_a.starts_with(root_a));
        assert!(path_b.starts_with(root_b));
        assert_ne!(path_a, path_b);
    }
}
