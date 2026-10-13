//! Native character-pack and portrait services used by Tauri commands.
#[cfg(test)]
mod archive_tests;
#[cfg(test)]
mod authoring_tests;
pub(crate) mod models;
pub(crate) mod paths;
#[cfg(test)]
mod paths_tests;
#[cfg(test)]
mod portrait_tests;
pub(crate) mod storage;
#[cfg(test)]
mod storage_tests;
#[cfg(test)]
mod test_support;

pub(crate) mod authoring;
pub(crate) mod catalog;
pub(crate) mod context_libraries;
pub(crate) mod profile;
pub(crate) mod voice;

pub(crate) mod archive_export;
pub(crate) mod archive_install;
pub(crate) mod archive_reader;
pub(crate) mod archive_validation;

pub(crate) mod appearance;
pub(crate) mod constants;
pub(crate) mod portrait_editing;
pub(crate) mod portrait_formats;
pub(crate) mod portrait_images;
