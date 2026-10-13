use super::storage::{write_bytes_atomic, write_bytes_atomic_with_cleanup, write_text_atomic};
use super::test_support::Sandbox;
use std::{
    fs,
    sync::{Arc, Barrier},
    thread,
};

#[test]
fn existing_temporary_files_are_never_overwritten() {
    let fixture = Sandbox::new();
    let old_temporary = fixture.0.join(".character.json.tmp");
    fs::write(&old_temporary, b"another writer owns this").unwrap();
    write_text_atomic(&fixture.0.join("character.json"), "new profile").unwrap();
    assert_eq!(
        fs::read(old_temporary).unwrap(),
        b"another writer owns this"
    );
}

#[test]
fn failed_replacement_preserves_destination_and_retires_its_temporary_file() {
    let fixture = Sandbox::new();
    let target = fixture.0.join("normal.png");
    fs::create_dir(&target).unwrap();
    fs::write(target.join("old.txt"), b"old content").unwrap();
    let old_image = fixture.0.join("normal.jpg");
    fs::write(&old_image, b"old image").unwrap();
    assert!(write_bytes_atomic_with_cleanup(&target, b"new image", &[old_image.clone()]).is_err());
    assert_eq!(fs::read(old_image).unwrap(), b"old image");
    assert_eq!(fs::read(target.join("old.txt")).unwrap(), b"old content");
    assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 2);
}

#[test]
fn replacing_existing_files_and_cleaning_other_formats_is_supported_on_windows() {
    let fixture = Sandbox::new();
    let target = fixture.0.join("normal.png");
    let old_image = fixture.0.join("normal.jpg");
    fs::write(&target, b"old png").unwrap();
    fs::write(&old_image, b"old jpg").unwrap();
    write_bytes_atomic_with_cleanup(&target, b"new png", &[old_image.clone(), target.clone()])
        .unwrap();
    assert_eq!(fs::read(target).unwrap(), b"new png");
    assert!(!old_image.exists());
    assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 1);
}

#[test]
fn concurrent_updates_publish_complete_files_without_sharing_temporary_paths() {
    let fixture = Sandbox::new();
    let target = fixture.0.join("character.json");
    let barrier = Arc::new(Barrier::new(8));
    let workers: Vec<_> = (0..8u8)
        .map(|value| {
            let barrier = barrier.clone();
            let target = target.clone();
            thread::spawn(move || {
                barrier.wait();
                write_bytes_atomic(&target, &vec![value; 128 * 1024])
            })
        })
        .collect();
    for worker in workers {
        worker.join().unwrap().unwrap();
    }
    let saved = fs::read(&target).unwrap();
    assert_eq!(saved.len(), 128 * 1024);
    assert!(saved.iter().all(|value| *value == saved[0]));
    assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 1);
}
