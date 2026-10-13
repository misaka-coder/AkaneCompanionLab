use super::{
    authoring::*, context_libraries::create_character_context_library_with_directory, models::*,
    test_support::Sandbox,
};
use std::fs;

fn fixture(fixture: &Sandbox) -> CharacterPackRegistryItem {
    create_character_pack_with_directory(
        CreateCharacterPackRequest {
            pack_id: "fixture".into(),
            name: "测试角色".into(),
            app_name: "".into(),
            user_title: "伙伴".into(),
        },
        || Ok(fixture.0.clone()),
    )
    .unwrap()
}

fn edit(fixture: &Sandbox) -> Result<CharacterPackRegistryItem, String> {
    save_character_pack_with_directory(
        SaveCharacterPackRequest {
            pack_id: "fixture".into(),
            identity: serde_json::json!({"name":"新名字"}),
            persona_form: serde_json::json!({"extra_setting":"保留的设定"}),
            dialogue: serde_json::Value::Null,
        },
        || Ok(fixture.0.clone()),
    )
}

#[test]
fn manual_persona_notes_with_original_headers_survive_saving() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let path = sandbox.0.join("fixture/persona.md");
    let manual = "# 我的角色\n## Voice\n手工设定的说话方式\n## Relationship Boundary\n手工边界\n## World Notes\n手工背景\n";
    fs::write(&path, manual).unwrap();
    let edited = edit(&sandbox).unwrap();
    assert_eq!(edited.profile["identity"]["name"], "新名字");
    assert_eq!(fs::read_to_string(path).unwrap(), manual);
}

#[test]
fn generated_persona_is_updated_and_missing_persona_is_recreated() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let path = sandbox.0.join("fixture/persona.md");
    edit(&sandbox).unwrap();
    assert_eq!(
        fs::read_to_string(&path).unwrap(),
        persona_md_text("新名字", "伙伴")
    );
    fs::remove_file(&path).unwrap();
    edit(&sandbox).unwrap();
    assert_eq!(
        fs::read_to_string(&path).unwrap(),
        persona_md_text("新名字", "伙伴")
    );
}

#[test]
fn nested_persona_changes_preserve_dialogue_and_other_profile_fields() {
    let sandbox = Sandbox::new();
    let original = fixture(&sandbox);
    let edited = edit(&sandbox).unwrap();
    assert_eq!(edited.profile["dialogue"], original.profile["dialogue"]);
    assert_eq!(
        edited.profile["persona_form"]["extra_setting"],
        "保留的设定"
    );
    assert_eq!(edited.profile["voice"], original.profile["voice"]);
    assert_eq!(edited.id, "fixture");
    assert_eq!(edited.asset_count, 0);
    let wire = serde_json::to_value(edited).unwrap();
    assert!(wire.get("installedPath").is_some());
    assert!(wire.get("assetCount").is_some());
}

#[test]
fn invalid_edits_leave_disk_content_unchanged() {
    let sandbox = Sandbox::new();
    fixture(&sandbox);
    let path = sandbox.0.join("fixture/character.json");
    let before = fs::read(&path).unwrap();
    let result = save_character_pack_with_directory(
        SaveCharacterPackRequest {
            pack_id: "fixture".into(),
            identity: serde_json::json!([]),
            persona_form: serde_json::Value::Null,
            dialogue: serde_json::Value::Null,
        },
        || Ok(sandbox.0.clone()),
    );
    assert!(result.is_err());
    assert_eq!(fs::read(path).unwrap(), before);
}

#[test]
fn invalid_create_does_not_resolve_or_write_the_data_root() {
    let result = create_character_pack_with_directory(
        CreateCharacterPackRequest {
            pack_id: "..".into(),
            name: "测试".into(),
            app_name: "".into(),
            user_title: "".into(),
        },
        || panic!("invalid requests must not access the data root"),
    );
    assert!(result.is_err());
}

#[test]
fn context_library_registration_preserves_profile_and_rejects_duplicates_and_private_names() {
    let sandbox = Sandbox::new();
    let original = fixture(&sandbox);
    let request = |folder: &str| CreateCharacterContextLibraryRequest {
        pack_id: "fixture".into(),
        folder: folder.into(),
        name: "人物资料".into(),
        description: "背景资料".into(),
        load_when: "需要背景时".into(),
    };
    let created = create_character_context_library_with_directory(request("资料"), || {
        Ok(sandbox.0.clone())
    })
    .unwrap();
    assert!(sandbox.0.join("fixture/资料").is_dir());
    assert_eq!(created.profile["identity"], original.profile["identity"]);
    assert_eq!(created.profile["context_libraries"][0]["folder"], "资料");
    let before = fs::read(sandbox.0.join("fixture/character.json")).unwrap();
    for name in ["资料", "_LOCAL", "assets"] {
        assert!(
            create_character_context_library_with_directory(request(name), || Ok(sandbox
                .0
                .clone()))
            .is_err()
        );
    }
    assert_eq!(
        fs::read(sandbox.0.join("fixture/character.json")).unwrap(),
        before
    );
}
