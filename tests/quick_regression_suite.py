from __future__ import annotations

import unittest

# 一键体检 —— 覆盖多端（QQ / 桌宠 / Web scene）关键回归路径。
# 运行：python -m tests.quick_regression_suite

QUICK_TESTS = [
    # === Repository hygiene ===
    "tests.test_repository_hygiene",

    # === Full module 回归（已有，保留） ===
    # 资源可见性契约：材料工作台提示词、来源标签、生成文件工作台、任务工作区
    "tests.test_resource_visibility_contract",
    # 桌宠前端契约：file_drop / tool_actions / 工作台入口、桌面本地文件交付
    "tests.test_desktop_pet_frontend_contract",
    # 桌宠后端契约：health / resource manifest / diagnostics
    "tests.test_desktop_pet_backend_contract",
    # 桌面 Activity Runtime、Workspace Panel、Scene 前端
    "tests.test_desktop_activity_runtime_contract",
    "tests.test_desktop_workspace_panel",
    "tests.test_scene_frontend_contract",

    # === QQ 网关：消息解析、附件、文件发送意图、非 QQ 文件事件隔离 ===
    "tests.test_qq_gateway.QQGatewayTests.test_render_reply_messages_prefers_speech_segments",
    "tests.test_qq_gateway.QQGatewayTests.test_extracts_image_and_file_attachments_from_segments",
    "tests.test_qq_gateway.QQGatewayTests.test_extracts_raw_cq_attachment_fallbacks",
    "tests.test_qq_gateway.QQGatewayTests.test_file_delivery_intent_respects_negative_request",
    "tests.test_qq_gateway.QQGatewayTests.test_send_generated_files_uses_onebot_upload_action",
    "tests.test_qq_gateway.QQGatewayTests.test_send_generated_files_blocks_without_current_delivery_intent",
    "tests.test_qq_gateway.QQGatewayTests.test_send_generated_files_ignores_desktop_client_file_events",
    "tests.test_qq_gateway.QQGatewayTests.test_duplicate_message_id_is_ignored",

    # === QQ / 桌宠 / Web 工具挂载不串线 ===
    "tests.test_vn_extensions.EngineExtensionTests.test_capability_registry_declares_client_tool_layers",
    "tests.test_vn_extensions.EngineExtensionTests.test_tool_prompt_context_is_filtered_by_client_mode",
    "tests.test_vn_extensions.EngineExtensionTests.test_web_scene_excludes_all_media_workbench_tools",
    "tests.test_vn_extensions.EngineExtensionTests.test_web_scene_prompt_excludes_media_preset_routing",

    # === 媒体任务预设路由 V1 ===
    "tests.test_vn_extensions.EngineExtensionTests.test_media_preset_routing_appears_in_prompt_for_chat_clients",

    # === 材料工作台提示词和来源标签 ===
    "tests.test_attachment_inbox.AttachmentInboxTests.test_service_prompt_renders_detail_index_and_pending",
    "tests.test_attachment_inbox.AttachmentInboxTests.test_inspect_attachment_requests_confirmation_for_ambiguous_target",

    # === send_file / desktop delivery / QQ 不吃桌宠 delivery_action ===
    "tests.test_generated_files.GeneratedFileTests.test_send_file_supports_generated_and_attachment_targets",
    "tests.test_generated_files.GeneratedFileTests.test_send_file_requests_confirmation_for_ambiguous_generated_name",
    "tests.test_generated_files.GeneratedFileTests.test_send_file_keeps_generated_exact_send_when_attachment_target_is_ambiguous",
    "tests.test_generated_files.GeneratedFileTests.test_send_file_tool_handler_carries_desktop_delivery_action",
    "tests.test_generated_files.GeneratedFileTests.test_send_file_tool_handler_ignores_desktop_delivery_action_for_qq",
]


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for name in QUICK_TESTS:
        suite.addTests(loader.loadTestsFromName(name))
    return suite


if __name__ == "__main__":
    unittest.main()
