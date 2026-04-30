from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class DesktopPetFrontendContractTests(unittest.TestCase):
    def test_activity_runtime_keeps_audio_playback_from_message_interruption(self) -> None:
        source = _read("desktop_pet/renderer/services/ActivityRuntime.js")

        self.assertIn("interruptForUserMessage()", source)
        self.assertIn('this._current?.type === "vocal_performance"', source)
        self.assertIn('this._current.status === "running"', source)
        self.assertIn('nextStatus: "interrupted"', source)
        self.assertNotIn('this._current?.type === "audio_playback" && this._current.status === "running"', source)

    def test_activity_runtime_confirms_playback_and_reports_failures(self) -> None:
        source = _read("desktop_pet/renderer/services/ActivityRuntime.js")

        self.assertIn("PLAY_RETRY_DELAYS_MS", source)
        self.assertIn("_playWithConfirmation", source)
        self.assertIn("_waitForPlaybackProgress", source)
        self.assertIn("audio_play_no_progress", source)
        self.assertIn("音频播放动作失败了", source)
        self.assertIn("this._audioEl.muted = false", source)
        self.assertIn("this._audioEl.volume = 1", source)

    def test_workspace_panel_opens_local_location_instead_of_browser_download(self) -> None:
        source = _read("desktop_pet/renderer/ui/WorkspacePanel.js")

        self.assertIn("fetchWorkspaceItemLocation", source)
        self.assertIn("showItemInFolder", source)
        self.assertNotIn("openExternal", source)
        self.assertNotIn("open-external", source)
        self.assertIn('action: "clear_files"', source)
        self.assertIn("清理了", source)
        self.assertIn("源文件和生成文件", source)

    def test_presentation_controller_keeps_formal_reply_priority_and_segment_precedence(self) -> None:
        source = _read("desktop_pet/renderer/services/PresentationController.js")

        self.assertIn("const SOURCE_PRIORITY", source)
        self.assertIn("formal: 3", source)
        self.assertIn('source !== "formal" && this.isFormalActive()', source)
        self.assertIn("this.canAcceptPassive()", source)
        self.assertIn("queue: true", source)
        self.assertLess(
            source.index("const segments = this._normalizeSegments"),
            source.index("const speech = String"),
        )
        self.assertIn('source: "speech_segments"', source)

    def test_settings_defaults_are_safe_for_productized_desktop_pet(self) -> None:
        source = _read("desktop_pet/main/settings-store.js")

        self.assertIn('backendUrl: "http://127.0.0.1:9999"', source)
        self.assertIn('outfit: "猫娘"', source)
        self.assertIn("voiceEnabled: false", source)
        self.assertIn("voiceInputEnabled: true", source)
        self.assertIn("clipboardContextEnabled: false", source)
        self.assertIn('["水手服", "睡衣"].includes(outfit)', source)

    def test_pet_scale_is_persisted_and_drives_window_and_renderer_layout(self) -> None:
        settings_store = _read("desktop_pet/main/settings-store.js")
        window_source = _read("desktop_pet/main/window.js")
        app_source = _read("desktop_pet/renderer/app.js")
        settings_page = _read("desktop_pet/renderer/settings.js")
        context_menu = _read("desktop_pet/renderer/ui/ContextMenu.js")
        styles = _read("desktop_pet/renderer/styles.css")

        self.assertIn("petScale: 1", settings_store)
        self.assertIn("PET_SCALE_VALUES", settings_store)
        self.assertIn("normalizePetScale", settings_store)
        self.assertIn("getPetWindowSize", window_source)
        self.assertIn("applyPetScaleBounds", window_source)
        self.assertIn("resolveWindowBounds(settings.windowBounds, settings.petScale)", window_source)

        self.assertIn('style.setProperty("--pet-scale"', app_source)
        self.assertIn("chatInput.refreshLayout", app_source)
        self.assertIn('"set-pet-scale"', app_source)
        self.assertIn("petScale: currentPetScale", app_source)

        self.assertIn("大小与占用", settings_page)
        self.assertIn('data-field="petScale"', settings_page)
        self.assertIn("normalizePetScale", settings_page)
        self.assertIn('data-action="set-pet-scale"', context_menu)

        self.assertIn("--pet-scale", styles)
        self.assertIn("--chat-input-min-height", styles)
        self.assertIn("calc(318px * var(--pet-scale))", styles)
        self.assertIn("calc(324px * var(--pet-scale))", styles)

    def test_next_music_queue_has_visible_management_surfaces(self) -> None:
        main_source = _read("desktop_pet_next/src/main.js")
        settings_source = _read("desktop_pet_next/src/settings.js")
        workspace_source = _read("desktop_pet_next/src/workspace.js")
        settings_html = _read("desktop_pet_next/settings.html")
        workspace_html = _read("desktop_pet_next/workspace.html")
        tauri_source = _read("desktop_pet_next/src-tauri/src/main.rs")

        self.assertIn("playMusicTrackBySourceId", main_source)
        self.assertIn("removeMusicTrackBySourceId", main_source)
        self.assertIn("parseLrcText", main_source)
        self.assertIn("buildCurrentLyricSnapshot", main_source)
        self.assertIn("lyric_current", main_source)
        self.assertIn("scheduleBackendMusicTimeline", main_source)
        self.assertIn("uploadMusicTrackForTimeline", main_source)
        self.assertIn("prepareBackendMusicTimeline", main_source)
        self.assertIn("/desktop-pet/attachments/audio", main_source)
        self.assertIn("/desktop-pet/music-timeline/prepare", main_source)
        self.assertIn("importDroppedFilesToWorkspace", main_source)
        self.assertIn("/desktop-pet/workspace/import-local", main_source)
        self.assertIn('const BASE_CAPABILITIES = ["speech_segments", "tts", "file_drop", "tool_actions"]', main_source)
        self.assertIn("function getProfileUserId()", main_source)
        self.assertIn("real_user_id: getProfileUserId()", main_source)
        self.assertNotIn("real_user_id: PROFILE_USER_ID", main_source)
        self.assertIn("WORKSPACE_REFRESH_EVENT", main_source)
        self.assertIn("showFileDropHint", main_source)
        self.assertIn("scheduleWorkspaceTaskWatch", main_source)
        self.assertIn("announceWorkspaceTaskChanges", main_source)
        self.assertIn("/desktop-pet/workspace/summary", main_source)
        self.assertIn("timelineLyricLineCount", main_source)
        self.assertIn('"playMusicTrack"', main_source)
        self.assertIn('"removeMusicTrack"', main_source)
        self.assertIn('id="music-lyric"', settings_html)
        self.assertIn('id="music-queue"', settings_html)
        self.assertIn("renderMusicLyric", settings_source)
        self.assertIn("renderMusicQueue", settings_source)
        self.assertIn('id="workspace-music"', workspace_html)
        self.assertIn("renderMusicPanel", workspace_source)
        self.assertIn("buildMusicLyricText", workspace_source)
        self.assertIn("WORKSPACE_REFRESH_EVENT", workspace_source)
        self.assertIn("scheduleWorkspaceRefresh(120)", workspace_source)
        self.assertIn("TASK_AUTO_REFRESH_MS", workspace_source)
        self.assertIn("resolveItemStatusGroup", workspace_source)
        self.assertIn("status_group", workspace_source)
        self.assertIn("SETTINGS_COMMAND_EVENT", workspace_source)
        self.assertIn("openWorkspaceItem", workspace_source)
        self.assertIn("revealWorkspaceItem", workspace_source)
        self.assertIn("copyWorkspaceItemPath", workspace_source)
        self.assertIn("exportWorkspaceItemToDesktop", workspace_source)
        self.assertIn("resolveWorkspaceItemPath", workspace_source)
        self.assertIn('invoke("open_local_file"', workspace_source)
        self.assertIn('invoke("show_item_in_folder"', workspace_source)
        self.assertIn('invoke("export_file_to_desktop"', workspace_source)
        self.assertIn("fn open_local_file", tauri_source)
        self.assertIn("fn show_item_in_folder", tauri_source)
        self.assertIn("fn export_file_to_desktop", tauri_source)
        self.assertIn("Akane Outputs", tauri_source)

    def test_next_settings_can_install_creator_kit_character_pack_zips(self) -> None:
        settings_source = _read("desktop_pet_next/src/settings.js")
        settings_html = _read("desktop_pet_next/settings.html")
        tauri_source = _read("desktop_pet_next/src-tauri/src/main.rs")
        main_source = _read("desktop_pet_next/src/main.js")

        self.assertIn('id="character-pack-zip"', settings_html)
        self.assertIn('id="choose-character-pack-zip"', settings_html)
        self.assertIn("importCharacterPackZipFile", settings_source)
        self.assertIn("install_character_pack_zip_bytes", settings_source)
        self.assertIn("install_character_pack_zip_file", settings_source)
        self.assertIn("open_character_packs_folder", settings_source)
        self.assertIn('"refreshCharacterPacks"', settings_source)
        self.assertIn('id="open-character-packs-folder"', settings_html)
        self.assertIn('id="copy-character-pack-path"', settings_html)
        self.assertIn("onDragDropEvent", settings_source)
        self.assertIn("fn install_character_pack_zip", tauri_source)
        self.assertIn("fn list_character_packs", tauri_source)
        self.assertIn("list_character_packs", tauri_source)
        self.assertIn("install_character_pack_zip_bytes", tauri_source)
        self.assertIn("install_character_pack_zip_file", tauri_source)
        self.assertIn("open_path_in_file_manager", tauri_source)
        self.assertIn("DEFAULT_CHARACTER_PACK_ID", tauri_source)
        self.assertIn('"refreshCharacterPacks"', main_source)
        self.assertIn("setRuntimeCharacterPacks", main_source)
        self.assertIn("character_pack_id: getCurrentCharacterPackId()", main_source)
        self.assertIn("client: CLIENT_MODE", main_source)
        self.assertIn("function getCurrentCharacterPackId()", main_source)


if __name__ == "__main__":
    unittest.main()
