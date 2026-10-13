//! Serialized character-pack requests and results.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CharacterPackInstallResult {
    pub(crate) pack_id: String,
    pub(crate) character_id: String,
    pub(crate) character_name: String,
    pub(crate) installed_path: String,
    pub(crate) file_count: usize,
    pub(crate) requires_restart: bool,
    pub(crate) warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CharacterPackActivationResult {
    pub(crate) pack_id: String,
    pub(crate) character_name: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CharacterPackRegistryItem {
    pub(crate) id: String,
    pub(crate) source: String,
    pub(crate) installed_path: String,
    pub(crate) asset_count: usize,
    pub(crate) profile: serde_json::Value,
    pub(crate) outfits: Vec<CharacterPackOutfitAsset>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) idle_blink_asset: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CharacterPackOutfitAsset {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) emotions: Vec<CharacterPackEmotionAsset>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CharacterPackEmotionAsset {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) path: String,
    pub(crate) size_bytes: u64,
}

#[derive(Debug, Clone)]
pub(crate) struct ZipEntry {
    pub(crate) name: String,
    pub(crate) data: Vec<u8>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct CharacterPackJson {
    pub(crate) schema_version: String,
    pub(crate) identity: CharacterPackIdentity,
    #[serde(default)]
    pub(crate) persona_form: serde_json::Value,
    pub(crate) appearance: CharacterPackAppearance,
    pub(crate) dialogue: CharacterPackDialogue,
    #[serde(default)]
    pub(crate) emotion_aliases: serde_json::Value,
    #[serde(default)]
    pub(crate) layout: serde_json::Value,
    #[serde(default)]
    pub(crate) voice: serde_json::Value,
    #[serde(default)]
    pub(crate) assets: CharacterPackAssets,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SaveCharacterPackRequest {
    pub(crate) pack_id: String,
    pub(crate) identity: serde_json::Value,
    pub(crate) persona_form: serde_json::Value,
    #[serde(default)]
    pub(crate) dialogue: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CreateCharacterPackRequest {
    pub(crate) pack_id: String,
    pub(crate) name: String,
    pub(crate) app_name: String,
    pub(crate) user_title: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CreateCharacterContextLibraryRequest {
    pub(crate) pack_id: String,
    pub(crate) folder: String,
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) load_when: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SetCharacterVoiceProfileRequest {
    pub(crate) pack_id: String,
    pub(crate) provider: String,
    pub(crate) profile_id: String,
    pub(crate) notes: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct UploadPortraitResult {
    pub(crate) ok: bool,
    pub(crate) outfit: String,
    pub(crate) emotion: String,
    pub(crate) path: String,
    pub(crate) size_bytes: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct CharacterPackIdentity {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) app_name: String,
    #[serde(default)]
    pub(crate) self_reference: String,
    pub(crate) user_title: String,
    #[serde(default)]
    pub(crate) relationship: String,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct CharacterPackAppearance {
    pub(crate) default_outfit: String,
    pub(crate) default_emotion: String,
    #[serde(default)]
    pub(crate) required_emotions: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct CharacterPackDialogue {
    pub(crate) local_click_lines: Vec<CharacterPackClickLine>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct CharacterPackClickLine {
    pub(crate) text: String,
    pub(crate) emotion: String,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub(crate) struct CharacterPackAssets {
    pub(crate) asset_root: Option<String>,
}

#[derive(Debug)]
pub(crate) struct CharacterPackValidation {
    pub(crate) character_id: String,
    pub(crate) character_name: String,
    pub(crate) warnings: Vec<String>,
}

impl CharacterPackJson {
    pub(super) fn asset_root(&self) -> &str {
        self.assets
            .asset_root
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("assets")
    }
}
