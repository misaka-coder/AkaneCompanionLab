pub(crate) const CHARACTER_PACK_TEMPLATE_JSON: &str =
    include_str!("../../../../desktop_pet_creator_kit/templates/character_pack/character.json");

pub(crate) const MAX_CHARACTER_PACK_ZIP_BYTES: usize = 300 * 1024 * 1024;

pub(crate) const MAX_PORTRAIT_IMAGE_BYTES: usize = 20 * 1024 * 1024;

pub(crate) const PRIVATE_LOCAL_DIRECTORY: &str = "_local";

pub(crate) const SUPPORTED_PORTRAIT_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "webp"];
