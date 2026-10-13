use super::{
    files::{copy_to_staging, read_file_bounded},
    test_support::Sandbox,
};
use std::fs;

#[test]
fn actual_file_bytes_are_bounded_and_failed_staging_is_cleaned() {
    let sandbox = Sandbox::new();
    let source = sandbox.0.join("source.bin");
    let staging = sandbox.0.join("staging");
    fs::write(&source, b"12345").unwrap();
    assert!(read_file_bounded(&source, 4).is_err());
    assert_eq!(read_file_bounded(&source, 5).unwrap(), b"12345");
    assert!(copy_to_staging(&source, &staging, "bin", Some(4)).is_err());
    assert_eq!(fs::read_dir(&staging).unwrap().count(), 0);
    let (path, size) = copy_to_staging(&source, &staging, "bin", Some(5)).unwrap();
    assert_eq!(size, 5);
    assert_eq!(fs::read(path).unwrap(), b"12345");
}
