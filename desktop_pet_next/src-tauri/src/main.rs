#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod audio_assets;
mod backend;
mod backend_restart;
mod character_packs;
mod character_stories;
mod computer_use;
mod control_lease;
mod desktop_capture;
mod desktop_connection;
mod device_control;
mod device_preferences;
mod local_files;
mod local_qq_setup;
mod native_system;
mod personal_browser;
mod pet_state;
mod pet_windows;
mod runtime;
mod scene_window;
mod startup;
mod workspace_files;

use tauri::Manager;

fn main() {
    device_preferences::initialize();
    if std::env::args().any(|arg| arg == "--device-only") {
        desktop_connection::device_runtime::run();
        return;
    }
    tauri::Builder::default()
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = pet_windows::lifecycle::close_pet_app(window.app_handle().clone());
                }
            }
        })
        .setup(startup::setup)
        .invoke_handler(tauri::generate_handler![
            personal_browser::configure_personal_browser,
            personal_browser::get_personal_browser_status,
            computer_use::stop_computer_use,
            computer_use::get_computer_use_status,
            computer_use::get_computer_use_targets,
            computer_use::configure_computer_use_target,
            computer_use::configure_computer_use,
            computer_use::resume_computer_use,
            pet_state::persistence::load_pet_state,
            pet_state::persistence::get_client_launch_binding,
            runtime::launch::report_ui_ready,
            desktop_connection::plugin_events::list_pending_plugin_agent_event_frames,
            desktop_connection::plugin_events::acknowledge_plugin_agent_event_frame,
            pet_state::persistence::save_pet_state,
            backend::binding::verify_backend_instance,
            backend::admin::backend_admin_request,
            local_qq_setup::local_qq_setup,
            backend_restart::memory_backend_restart,
            backend::projects::manage_project_workspaces,
            backend::projects::bind_project_directory,
            backend::plugin_picker::pick_local_plugin_path,
            pet_state::persistence::activate_character_pack,
            native_system::media::commands::get_current_system_media,
            native_system::media::commands::control_system_media,
            audio_assets::prepare::prepare_audio_asset,
            character_packs::catalog::list_character_packs,
            character_packs::archive_install::install_character_pack_zip_file,
            character_packs::archive_install::install_character_pack_zip_bytes,
            character_packs::archive_install::open_character_packs_folder,
            local_files::system_open::open_managed_skills_folder,
            local_files::system_open::open_local_file,
            local_files::system_open::show_item_in_folder,
            local_files::system_open::open_external_url,
            local_files::exports::export_file_to_desktop,
            workspace_files::downloads::open_workspace_item,
            workspace_files::imports::import_dropped_files,
            workspace_files::imports::choose_workspace_files,
            pet_windows::geometry::apply_window_state,
            pet_windows::geometry::set_visual_scale,
            pet_windows::flags::set_always_on_top,
            pet_windows::flags::set_taskbar_visible,
            pet_windows::flags::set_click_through,
            pet_windows::flags::set_hit_test_enabled,
            pet_windows::flags::update_hit_regions,
            pet_windows::geometry::reset_window_geometry,
            pet_windows::geometry::move_window_by,
            pet_windows::lifecycle::close_window,
            pet_windows::lifecycle::close_pet_app,
            pet_windows::lifecycle::quit_akane,
            pet_windows::secondary::open_settings_window,
            pet_windows::panel::open_panel_window,
            pet_windows::gomoku::open_gomoku_window,
            pet_windows::secondary::open_workspace_window,
            pet_windows::secondary::open_shop_window,
            scene_window::open_scene_window,
            scene_window::claim_scene_playback,
            scene_window::ack_scene_playback,
            scene_window::release_scene_playback,
            scene_window::scene_playback_active,
            pet_windows::secondary::open_workshop_window,
            pet_windows::geometry::get_window_geometry,
            character_packs::authoring::save_character_pack,
            character_packs::voice::set_character_voice_profile,
            character_packs::voice::clear_character_voice_profile,
            character_packs::authoring::create_character_pack,
            character_packs::context_libraries::create_character_context_library,
            character_packs::portrait_editing::create_portrait_outfit,
            character_packs::portrait_images::upload_portrait_image,
            character_packs::portrait_images::import_generated_portrait_image,
            character_packs::portrait_images::read_portrait_image,
            character_packs::catalog::list_pack_assets,
            character_packs::portrait_editing::delete_portrait_image,
            character_packs::portrait_editing::rename_portrait_emotion,
            character_packs::portrait_editing::rename_portrait_outfit,
            character_packs::appearance::set_default_emotion,
            character_packs::appearance::set_default_portrait,
            character_packs::appearance::save_calibration,
            pet_windows::geometry::resize_pet_window,
            character_packs::archive_export::export_character_pack,
            character_stories::open_character_stories_folder,
            character_stories::list_character_stories,
            character_stories::create_character_story_template
        ])
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_dialog::init())
        .run(tauri::generate_context!())
        .expect("error while running Akane Desktop Pet Next");
}
