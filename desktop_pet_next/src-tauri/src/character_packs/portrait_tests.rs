use super::appearance::save_calibration_with_directory;
use super::portrait_editing::rename_portrait_emotion_with_directory;
use super::portrait_images::{
    import_generated_portrait_image_with_directory, read_portrait_image_with_directory,
    upload_portrait_image_with_directory,
};
use super::{
    authoring::create_character_pack_with_directory, models::CreateCharacterPackRequest,
    test_support::Sandbox,
};
use std::fs;

const PNG: &[u8] = b"\x89PNG\r\n\x1a\nfixture png bytes";

#[test]
fn idle_blink_texture_is_contained_and_is_not_a_chat_expression() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let pack = sandbox.0.join("fixture");
    let animation = pack.join("assets/animations/default/normal-blink.png");
    fs::create_dir_all(animation.parent().unwrap()).unwrap();
    fs::write(&animation, PNG).unwrap();
    let profile = serde_json::json!({"appearance":{"idle_blink":{"asset":"animations/default/normal-blink.png"}}});
    let item = super::catalog::registry_item("fixture".into(), &pack, profile, "assets");
    assert_eq!(fs::canonicalize(item.idle_blink_asset.unwrap()).unwrap(), fs::canonicalize(&animation).unwrap());
    assert!(!item.outfits.iter().flat_map(|o| &o.emotions).any(|e| e.id.contains("blink")));
    for asset in ["../../../outside.png", "missing.png", "animations/default", "animations/default/file.txt", "C:/outside.png"] {
        let profile = serde_json::json!({"appearance":{"idle_blink":{"asset":asset}}});
        assert!(super::catalog::idle_blink_asset_path(&pack, "assets", &profile).is_none(), "{asset}");
    }
}

fn fixture(sandbox: &Sandbox) {
    create_character_pack_with_directory(
        CreateCharacterPackRequest {
            pack_id: "fixture".into(),
            name: "测试".into(),
            app_name: "".into(),
            user_title: "伙伴".into(),
        },
        || Ok(sandbox.0.clone()),
    )
    .unwrap();
}

#[test]
fn failed_upload_keeps_the_previous_image_format() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let outfit = sandbox.0.join("fixture/assets/characters/default");
    fs::write(outfit.join("normal.jpg"), b"old image").unwrap();
    fs::create_dir(outfit.join("normal.png")).unwrap();
    assert!(upload_portrait_image_with_directory(
        "fixture".into(),
        "default".into(),
        "normal".into(),
        PNG.to_vec(),
        || Ok(sandbox.0.clone())
    )
    .is_err());
    assert_eq!(fs::read(outfit.join("normal.jpg")).unwrap(), b"old image");
}

#[test]
fn successful_upload_replaces_other_formats_and_keeps_result_fields() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let outfit = sandbox.0.join("fixture/assets/characters/default");
    fs::write(outfit.join("normal.jpg"), b"old image").unwrap();
    let result = upload_portrait_image_with_directory(
        "fixture".into(),
        "default".into(),
        "normal".into(),
        PNG.to_vec(),
        || Ok(sandbox.0.clone()),
    )
    .unwrap();
    assert!(result.ok);
    assert_eq!(result.size_bytes, PNG.len() as u64);
    assert_eq!(fs::read(outfit.join("normal.png")).unwrap(), PNG);
    assert!(!outfit.join("normal.jpg").exists());
    let wire = serde_json::to_value(result).unwrap();
    assert!(wire.get("sizeBytes").is_some());
}

#[test]
fn generated_import_checks_metadata_and_explicit_overwrite() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let import = |overwrite| {
        import_generated_portrait_image_with_directory(
            "fixture".into(),
            "default".into(),
            "normal".into(),
            PNG.to_vec(),
            Some(".PNG".into()),
            Some("image/png; charset=binary".into()),
            overwrite,
            || Ok(sandbox.0.clone()),
        )
    };
    let assets = import(false).unwrap();
    assert_eq!(assets[0].emotions[0].id, "normal");
    assert!(import(false).is_err());
    assert!(import(true).is_ok());
    assert!(import_generated_portrait_image_with_directory(
        "fixture".into(),
        "default".into(),
        "normal".into(),
        PNG.to_vec(),
        Some("jpg".into()),
        Some("image/png".into()),
        true,
        || panic!("bad metadata must not reach the data root")
    )
    .is_err());
}

#[test]
fn native_read_returns_the_saved_binary_and_missing_images_return_errors() {
    use tauri::ipc::{InvokeResponseBody, IpcResponse};
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    upload_portrait_image_with_directory(
        "fixture".into(),
        "default".into(),
        "normal".into(),
        PNG.to_vec(),
        || Ok(sandbox.0.clone()),
    )
    .unwrap();
    let response = read_portrait_image_with_directory(
        "fixture".into(),
        "default".into(),
        "normal".into(),
        || Ok(sandbox.0.clone()),
    )
    .unwrap()
    .body()
    .unwrap();
    assert!(matches!(response, InvokeResponseBody::Raw(data) if data == PNG));
    assert!(read_portrait_image_with_directory(
        "fixture".into(),
        "default".into(),
        "missing".into(),
        || Ok(sandbox.0.clone())
    )
    .is_err());
}

#[test]
fn renaming_cannot_collide_with_an_emotion_in_another_format() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let outfit = sandbox.0.join("fixture/assets/characters/default");
    fs::write(outfit.join("old.jpg"), b"old").unwrap();
    fs::write(outfit.join("new.png"), b"new").unwrap();
    assert!(rename_portrait_emotion_with_directory(
        "fixture".into(),
        "default".into(),
        "old".into(),
        "new".into(),
        || Ok(sandbox.0.clone())
    )
    .is_err());
    assert_eq!(fs::read(outfit.join("old.jpg")).unwrap(), b"old");
    assert!(!outfit.join("new.jpg").exists());
}

#[test]
fn damaged_layout_returns_an_error_without_panicking_or_overwriting() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let path = sandbox.0.join("fixture/character.json");
    let mut profile: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    profile["layout"] = serde_json::json!("damaged layout");
    let before = serde_json::to_vec(&profile).unwrap();
    fs::write(&path, &before).unwrap();
    let result = std::panic::catch_unwind(|| {
        save_calibration_with_directory(
            "fixture".into(),
            "default".into(),
            serde_json::json!({"bubble_anchor":{"x":0.5,"y":0.2}}),
            || Ok(sandbox.0.clone()),
        )
    });
    assert!(
        result.is_ok(),
        "invalid on-disk layouts must return a real error"
    );
    assert!(result.unwrap().is_err());
    assert_eq!(fs::read(path).unwrap(), before);
}

#[test]
fn calibration_preserves_other_outfits_and_rejects_invalid_input_before_io() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let first = serde_json::json!({"bubble_anchor":{"x":0.5,"y":0.2}});
    let second = serde_json::json!({"portrait_scale":1.2});
    save_calibration_with_directory("fixture".into(), "default".into(), first.clone(), || {
        Ok(sandbox.0.clone())
    })
    .unwrap();
    save_calibration_with_directory("fixture".into(), "second".into(), second.clone(), || {
        Ok(sandbox.0.clone())
    })
    .unwrap();
    let profile: serde_json::Value =
        serde_json::from_slice(&fs::read(sandbox.0.join("fixture/character.json")).unwrap())
            .unwrap();
    assert_eq!(profile["layout"]["outfits"]["default"], first);
    assert_eq!(profile["layout"]["outfits"]["second"], second);
    assert!(save_calibration_with_directory(
        "fixture".into(),
        "default".into(),
        serde_json::Value::Null,
        || panic!("invalid calibration must not access the data root")
    )
    .is_err());
}

#[test]
fn native_asset_edits_and_default_selection_follow_the_same_saved_pack() {
    use super::{
        appearance::{set_default_emotion_with_directory, set_default_portrait_with_directory},
        portrait_editing::{
            create_portrait_outfit_with_directory, delete_portrait_image_with_directory,
            rename_portrait_outfit_with_directory,
        },
    };
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let directory = || Ok(sandbox.0.clone());
    let assets =
        create_portrait_outfit_with_directory("fixture".into(), "second".into(), directory)
            .unwrap();
    assert!(assets
        .iter()
        .any(|outfit| outfit.id == "second" && outfit.emotions.is_empty()));
    assert!(set_default_portrait_with_directory(
        "fixture".into(),
        "second".into(),
        "normal".into(),
        directory
    )
    .is_err());
    upload_portrait_image_with_directory(
        "fixture".into(),
        "second".into(),
        "normal".into(),
        PNG.to_vec(),
        directory,
    )
    .unwrap();
    rename_portrait_emotion_with_directory(
        "fixture".into(),
        "second".into(),
        "normal".into(),
        "smile".into(),
        directory,
    )
    .unwrap();
    rename_portrait_outfit_with_directory(
        "fixture".into(),
        "second".into(),
        "renamed".into(),
        directory,
    )
    .unwrap();
    set_default_portrait_with_directory(
        "fixture".into(),
        "renamed".into(),
        "smile".into(),
        directory,
    )
    .unwrap();
    set_default_emotion_with_directory(
        "fixture".into(),
        "music_emotion".into(),
        "smile".into(),
        directory,
    )
    .unwrap();
    let path = sandbox.0.join("fixture/character.json");
    let before = fs::read(&path).unwrap();
    let profile: serde_json::Value = serde_json::from_slice(&before).unwrap();
    assert_eq!(profile["appearance"]["default_outfit"], "renamed");
    assert_eq!(profile["appearance"]["default_emotion"], "smile");
    assert_eq!(profile["appearance"]["music_emotion"], "smile");
    assert!(set_default_emotion_with_directory(
        "fixture".into(),
        "identity".into(),
        "invalid".into(),
        directory
    )
    .is_err());
    assert_eq!(fs::read(path).unwrap(), before);
    let assets = delete_portrait_image_with_directory(
        "fixture".into(),
        "renamed".into(),
        "smile".into(),
        directory,
    )
    .unwrap();
    assert!(assets
        .iter()
        .any(|outfit| outfit.id == "renamed" && outfit.emotions.is_empty()));
    assert!(delete_portrait_image_with_directory(
        "fixture".into(),
        "renamed".into(),
        "smile".into(),
        directory
    )
    .is_err());
}

#[test]
fn legacy_null_calibration_containers_are_supported_and_scalar_outfits_are_rejected() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let path = sandbox.0.join("fixture/character.json");
    let mut profile: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    profile["layout"] = serde_json::Value::Null;
    fs::write(&path, serde_json::to_vec(&profile).unwrap()).unwrap();
    let layout = serde_json::json!({"portrait_scale":1.0});
    save_calibration_with_directory("fixture".into(), "default".into(), layout.clone(), || {
        Ok(sandbox.0.clone())
    })
    .unwrap();
    let mut profile: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    assert_eq!(profile["layout"]["outfits"]["default"], layout);
    profile["layout"]["outfits"] = serde_json::json!(false);
    let before = serde_json::to_vec(&profile).unwrap();
    fs::write(&path, &before).unwrap();
    assert!(
        save_calibration_with_directory("fixture".into(), "default".into(), layout, || Ok(sandbox
            .0
            .clone()))
        .is_err()
    );
    assert_eq!(fs::read(path).unwrap(), before);
}
