use super::{
    archive_export::write_character_pack_archive,
    archive_reader::{detect_character_pack_root, read_character_archive},
    test_support::Sandbox,
};
use super::{
    archive_install::install_with_directories, archive_reader::read_character_archive_with_limit,
};
use std::{
    fs,
    io::{Cursor, Write},
};

#[test]
fn decompressed_payloads_are_bounded_independently_of_compressed_size() {
    let bytes = archive(&[("fixture/first", b"123456"), ("fixture/second", b"123456")]);
    assert!(read_character_archive_with_limit(&bytes, 11).is_err());
    assert_eq!(
        read_character_archive_with_limit(&bytes, 12).unwrap().len(),
        2
    );
}

#[test]
fn native_export_roundtrip_installs_assets_and_overwrites_without_importing_private_state() {
    let sandbox = Sandbox::new();
    let source = sandbox.0.join("source/fixture");
    fs::create_dir_all(source.join("assets/characters/default")).unwrap();
    fs::write(
        source.join("character.json"),
        crate::character_packs::constants::CHARACTER_PACK_TEMPLATE_JSON,
    )
    .unwrap();
    fs::write(
        source.join("assets/characters/default/normal.png"),
        b"fixture image",
    )
    .unwrap();
    let target = sandbox.0.join("portable.zip");
    write_character_pack_archive(&source, &target).unwrap();
    let bytes = fs::read(target).unwrap();
    let characters = sandbox.0.join("characters");
    let cache = sandbox.0.join("cache");
    let directories = || Ok((characters.clone(), cache.clone()));
    let result = install_with_directories(bytes.clone(), false, directories).unwrap();
    assert_eq!(result.pack_id, "fixture");
    assert!(!result.requires_restart);
    assert_eq!(
        fs::read(characters.join("fixture/assets/characters/default/normal.png")).unwrap(),
        b"fixture image"
    );
    assert!(install_with_directories(bytes.clone(), false, directories).is_err());
    fs::create_dir_all(characters.join("fixture/_local")).unwrap();
    fs::write(
        characters.join("fixture/_local/state.json"),
        b"owned local state",
    )
    .unwrap();
    install_with_directories(bytes, true, directories).unwrap();
    assert_eq!(
        fs::read(characters.join("fixture/_local/state.json")).unwrap(),
        b"owned local state"
    );
    assert_eq!(fs::read_dir(&characters).unwrap().count(), 1);
    assert_eq!(fs::read_dir(&cache).unwrap().count(), 0);
}

#[test]
fn invalid_import_leaves_the_existing_pack_and_cache_untouched() {
    let sandbox = Sandbox::new();
    let characters = sandbox.0.join("characters");
    let cache = sandbox.0.join("cache");
    fs::create_dir_all(characters.join("fixture")).unwrap();
    fs::write(characters.join("fixture/character.json"), b"old profile").unwrap();
    let broken = archive(&[("fixture/character.json", b"{}")]);
    assert!(
        install_with_directories(broken, true, || Ok((characters.clone(), cache.clone()))).is_err()
    );
    assert_eq!(
        fs::read(characters.join("fixture/character.json")).unwrap(),
        b"old profile"
    );
    assert_eq!(fs::read_dir(characters).unwrap().count(), 1);
    assert_eq!(fs::read_dir(cache).unwrap().count(), 0);
}

#[test]
fn concurrent_imports_use_independent_staging_and_never_remove_other_packs() {
    use std::{
        sync::{Arc, Barrier},
        thread,
    };
    let sandbox = Sandbox::new();
    let characters = sandbox.0.join("characters");
    let cache = sandbox.0.join("cache");
    let barrier = Arc::new(Barrier::new(8));
    let workers: Vec<_> = (0..8)
        .map(|index| {
            let bytes = archive(&[(
                &format!("fixture-{index}/character.json"),
                crate::character_packs::constants::CHARACTER_PACK_TEMPLATE_JSON.as_bytes(),
            )]);
            let characters = characters.clone();
            let cache = cache.clone();
            let barrier = barrier.clone();
            thread::spawn(move || {
                barrier.wait();
                install_with_directories(bytes, false, || Ok((characters, cache)))
            })
        })
        .collect();
    for worker in workers {
        worker.join().unwrap().unwrap();
    }
    assert_eq!(fs::read_dir(characters).unwrap().count(), 8);
    assert_eq!(fs::read_dir(cache).unwrap().count(), 0);
}

#[test]
fn duplicate_concurrent_imports_publish_once_without_overwriting() {
    use std::{
        sync::{Arc, Barrier},
        thread,
    };
    let sandbox = Sandbox::new();
    let characters = sandbox.0.join("characters");
    let cache = sandbox.0.join("cache");
    let barrier = Arc::new(Barrier::new(8));
    let workers: Vec<_> = (0..8)
        .map(|_| {
            let bytes = archive(&[(
                "fixture/character.json",
                crate::character_packs::constants::CHARACTER_PACK_TEMPLATE_JSON.as_bytes(),
            )]);
            let characters = characters.clone();
            let cache = cache.clone();
            let barrier = barrier.clone();
            thread::spawn(move || {
                barrier.wait();
                install_with_directories(bytes, false, || Ok((characters, cache)))
            })
        })
        .collect();
    let completed = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .filter(Result::is_ok)
        .count();
    assert_eq!(completed, 1);
    assert_eq!(fs::read_dir(characters).unwrap().count(), 1);
    assert_eq!(fs::read_dir(cache).unwrap().count(), 0);
}

fn archive(entries: &[(&str, &[u8])]) -> Vec<u8> {
    let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
    let options =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for (name, data) in entries {
        writer.start_file(*name, options).unwrap();
        writer.write_all(data).unwrap();
    }
    writer.finish().unwrap().into_inner()
}

#[test]
fn native_export_can_be_read_by_native_import_and_preserves_directory_entries() {
    let sandbox = Sandbox::new();
    let pack = sandbox.0.join("fixture");
    fs::create_dir_all(pack.join("assets/characters/default")).unwrap();
    fs::write(
        pack.join("character.json"),
        crate::character_packs::constants::CHARACTER_PACK_TEMPLATE_JSON,
    )
    .unwrap();
    fs::write(pack.join("persona.md"), "测试角色设定").unwrap();
    let target = sandbox.0.join("fixture.zip");
    let size = write_character_pack_archive(&pack, &target).unwrap();
    let bytes = fs::read(target).unwrap();
    assert_eq!(size, bytes.len() as u64);
    let entries = read_character_archive(&bytes).unwrap();
    assert_eq!(detect_character_pack_root(&entries).unwrap(), "fixture");
    assert!(entries
        .iter()
        .any(|entry| entry.name == "fixture/assets/characters/default/"));
    assert_eq!(
        entries
            .iter()
            .find(|entry| entry.name == "fixture/persona.md")
            .unwrap()
            .data,
        "测试角色设定".as_bytes()
    );
}

#[test]
fn crc_mismatch_does_not_silently_import_corrupted_files() {
    let marker = b"fixture-unique-text";
    let mut bytes = archive(&[("fixture/persona.md", marker)]);
    let offset = bytes
        .windows(marker.len())
        .position(|candidate| candidate == marker)
        .unwrap();
    bytes[offset] ^= 1;
    assert!(read_character_archive(&bytes).is_err());
}

#[test]
fn normalized_aliases_cannot_overwrite_another_archive_entry() {
    let bytes = archive(&[
        ("fixture/character.json", b"original"),
        ("fixture/./CHARACTER.json", b"replacement"),
    ]);
    assert!(read_character_archive(&bytes).is_err());
}

#[test]
fn archive_identification_requires_the_exact_character_filename() {
    let bytes = archive(&[
        ("fixture/character.json", b"{}"),
        ("fixture/mycharacter.json", b"{}"),
    ]);
    let entries = read_character_archive(&bytes).unwrap();
    assert_eq!(detect_character_pack_root(&entries).unwrap(), "fixture");
}

#[test]
fn portable_export_excludes_private_local_content() {
    let sandbox = Sandbox::new();
    let pack = sandbox.0.join("fixture");
    fs::create_dir_all(pack.join("_local")).unwrap();
    fs::create_dir_all(pack.join("nested/_LOCAL")).unwrap();
    fs::write(pack.join("character.json"), b"{}").unwrap();
    fs::write(pack.join("_local/private.txt"), b"private").unwrap();
    fs::write(pack.join("nested/_LOCAL/private.txt"), b"private").unwrap();
    let target = sandbox.0.join("fixture.zip");
    write_character_pack_archive(&pack, &target).unwrap();
    let bytes = fs::read(target).unwrap();
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).unwrap();
    for index in 0..zip.len() {
        assert!(!zip
            .by_index(index)
            .unwrap()
            .name()
            .to_ascii_lowercase()
            .contains("_local"));
    }
}

#[test]
fn traversal_and_truncated_archives_fail_before_extracting() {
    assert!(read_character_archive(&archive(&[("fixture/../escape", b"data")])).is_err());
    assert!(read_character_archive(b"broken zip").is_err());
}

#[test]
fn a_nested_folder_named_like_the_archive_root_is_not_flattened() {
    use super::archive_install::extract_character_pack_entries;
    let bytes = archive(&[
        ("fixture/character.json", b"{}"),
        ("fixture/context.md", b"top"),
        ("fixture/fixture/context.md", b"nested"),
    ]);
    let entries = read_character_archive(&bytes).unwrap();
    let sandbox = Sandbox::new();
    let target = sandbox.0.join("extracted");
    extract_character_pack_entries(&entries, "fixture", &target).unwrap();
    assert_eq!(fs::read(target.join("context.md")).unwrap(), b"top");
    assert_eq!(
        fs::read(target.join("fixture/context.md")).unwrap(),
        b"nested"
    );
}

#[test]
fn existing_export_staging_is_not_overwritten_or_removed() {
    let sandbox = Sandbox::new();
    let pack = sandbox.0.join("fixture");
    fs::create_dir(&pack).unwrap();
    let target = sandbox.0.join("owned.zip");
    fs::write(&target, b"another export owns this").unwrap();
    assert!(write_character_pack_archive(&pack, &target).is_err());
    assert_eq!(fs::read(target).unwrap(), b"another export owns this");
}

#[cfg(windows)]
#[test]
fn rejected_external_directory_links_leave_no_partial_export() {
    let sandbox = Sandbox::new();
    let outside = Sandbox::new();
    let pack = sandbox.0.join("fixture");
    fs::create_dir(&pack).unwrap();
    fs::write(outside.0.join("outside.png"), b"outside fixture").unwrap();
    let link = pack.join("linked-assets");
    let created = std::process::Command::new("cmd")
        .args(["/D", "/C", "mklink", "/J"])
        .arg(&link)
        .arg(&outside.0)
        .output()
        .unwrap();
    assert!(created.status.success());
    let target = sandbox.0.join("partial.zip");
    let result = write_character_pack_archive(&pack, &target);
    fs::remove_dir(link).unwrap();
    assert!(result.is_err());
    assert!(!target.exists());
    assert_eq!(
        fs::read(outside.0.join("outside.png")).unwrap(),
        b"outside fixture"
    );
}
