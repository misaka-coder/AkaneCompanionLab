import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_desktop_feature import catalog_checks, runtime_check, source_checks


class DesktopFeatureDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.desktop = self.root / "desktop_pet_next"
        for path, text in {
            "gomoku.html": '<script type="module" src="/src/gomoku.js"></script>',
            "src/gomoku.js": 'emitTo("main", "gomoku:request", {}); listen("gomoku:state", () => {});',
            "src/pet/application.js": "import { createGomokuController } from './gomoku.js';\nconst gomokuPort = {};\nwire(gomokuPort, createGomokuController({}));\ngomokuPort.registerBridge();",
            "src/pet/gomoku.js": 'export function createGomokuController() { platform.listen("gomoku:request", () => {}); platform.emitTo("gomoku", "gomoku:state", {}); }',
            "src/pet/ui.js": 'platform.tauriCall("open_gomoku_window", {});',
            "src/pet/panel.js": 'platform.tauriCall("open_gomoku_window", {});',
            "vite.config.js": 'input: { gomoku: resolve(__dirname, "gomoku.html") }',
            "src-tauri/src/main.rs": 'generate_handler![pet_windows::gomoku::open_gomoku_window])',
            "src-tauri/src/pet_windows/mod.rs": "pub(crate) mod gomoku;",
            "src-tauri/src/pet_windows/gomoku.rs": '#[tauri::command]\nasync fn open_gomoku_window() {}',
        }.items():
            target = self.desktop / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.permissions = self.desktop / "src-tauri/capabilities/default.json"
        self.permissions.parent.mkdir(parents=True, exist_ok=True)
        self.permission_data = {"windows": ["main", "gomoku"], "permissions": ["core:default",
            "core:window:allow-close", "core:window:allow-start-dragging"]}
        self.save_permissions()

    def save_permissions(self):
        self.permissions.write_text(json.dumps(self.permission_data), encoding="utf-8")

    def failures(self):
        return {check.name for check in source_checks(self.root, "gomoku") if check.level == "FAIL"}

    def test_complete_wiring_has_no_source_failures(self):
        self.assertEqual(self.failures(), set())

    def test_window_missing_from_permissions_is_detected_despite_compilable_sources(self):
        self.permission_data["windows"] = ["main"]
        self.save_permissions()
        self.assertIn("窗口授权", self.failures())
        self.assertIn("拖动", self.failures())

    def test_missing_handler_registration_is_detected(self):
        (self.desktop / "src-tauri/src/main.rs").write_text("generate_handler![])", encoding="utf-8")
        self.assertIn("命令注册", self.failures())

    def test_missing_controller_import_composition_and_bridge_registration_are_detected(self):
        application = self.desktop / "src/pet/application.js"
        original = application.read_text(encoding="utf-8")
        application.write_text(
            original.replace(
                "import { createGomokuController } from './gomoku.js';",
                "// import { createGomokuController } from './gomoku.js';",
            ),
            encoding="utf-8",
        )
        self.assertIn("主窗口控制器导入", self.failures())

        application.write_text(
            original.replace(
                "wire(gomokuPort, createGomokuController({}));",
                "// wire(gomokuPort, createGomokuController({}));",
            ),
            encoding="utf-8",
        )
        failures = self.failures()
        self.assertIn("主窗口控制器组合", failures)
        self.assertIn("主窗口桥接注册", failures)

        application.write_text(
            original.replace("gomokuPort.registerBridge();", "// gomokuPort.registerBridge();"),
            encoding="utf-8",
        )
        self.assertIn("主窗口桥接注册", self.failures())

    def test_request_and_response_events_must_exist_on_both_window_sides(self):
        controller = self.desktop / "src/pet/gomoku.js"
        original_controller = controller.read_text(encoding="utf-8")
        controller.write_text(
            original_controller.replace('platform.listen("gomoku:request"', 'platform.listen("other:request"'),
            encoding="utf-8",
        )
        self.assertIn("请求事件接线", self.failures())

        controller.write_text(original_controller, encoding="utf-8")
        page = self.desktop / "src/gomoku.js"
        original_page = page.read_text(encoding="utf-8")
        page.write_text(
            original_page.replace('listen("gomoku:state"', 'listen("other:state"'),
            encoding="utf-8",
        )
        self.assertIn("响应事件接线", self.failures())

    def test_ui_and_panel_open_commands_are_checked_without_counting_comments(self):
        ui = self.desktop / "src/pet/ui.js"
        ui.write_text('// platform.tauriCall("open_gomoku_window", {});', encoding="utf-8")
        self.assertIn("主 UI 打开入口", self.failures())

        panel = self.desktop / "src/pet/panel.js"
        panel.write_text('// platform.tauriCall("open_gomoku_window", {});', encoding="utf-8")
        self.assertIn("面板打开入口", self.failures())

    def test_nonconventional_window_layout_is_explicitly_skipped(self):
        checks = source_checks(self.root, "workshop")
        controller = next(check for check in checks if check.name == "主窗口控制器接线")
        self.assertEqual(controller.level, "SKIP")
        self.assertIn("手工定位", controller.detail)

    def test_opening_a_nonconventional_page_does_not_require_a_same_named_controller(self):
        (self.desktop / "src/pet/application.js").write_text("// composed elsewhere", encoding="utf-8")
        (self.desktop / "src/pet/gomoku.js").write_text("export default {};", encoding="utf-8")
        checks = source_checks(self.root, "gomoku")
        controller = next(check for check in checks if check.name == "主窗口控制器接线")
        self.assertEqual(controller.level, "SKIP")
        self.assertFalse(any(check.level == "FAIL" for check in checks))

    def test_controller_helpers_are_not_misreported_as_broken_wiring(self):
        application = self.desktop / "src/pet/application.js"
        application.write_text(application.read_text(encoding="utf-8").replace(
            "gomokuPort.registerBridge()", "gomokuPort.registerGomokuBridge()"), encoding="utf-8")
        controller = self.desktop / "src/pet/gomoku.js"
        controller.write_text(controller.read_text(encoding="utf-8").replace(
            'platform.emitTo("gomoku", "gomoku:state", {})',
            'sendState("gomoku:state"); function sendState(eventName) { platform.emitTo("gomoku", eventName, {}); }'),
            encoding="utf-8")
        (self.desktop / "src/pet/ui.js").write_text('gomokuPort.openGomokuWindow();', encoding="utf-8")
        checks = source_checks(self.root, "gomoku")
        self.assertFalse(any(check.level == "FAIL" for check in checks))
        response = next(check for check in checks if check.name == "响应事件接线")
        self.assertEqual(response.level, "SKIP")
        self.assertIn("手工核对", response.detail)

    def test_missing_html_build_entry_and_script_are_detected(self):
        (self.desktop / "vite.config.js").write_text("input: {}", encoding="utf-8")
        (self.desktop / "src/gomoku.js").unlink()
        self.assertIn("打包入口", self.failures())
        self.assertIn("页面脚本", self.failures())

    def test_malformed_permission_file_reports_the_file_instead_of_success(self):
        self.permissions.write_text("{broken", encoding="utf-8")
        self.assertIn("窗口权限文件", self.failures())

    def test_active_plugin_with_old_contributions_is_not_reported_ready(self):
        catalog = {"plugins": [{"plugin_id": "akane.gomoku", "enabled": True, "runtime_status": "active",
            "version": "0.1.0", "contributions": {"capabilities": [], "services": []}}]}
        checks = catalog_checks(catalog, "akane.gomoku", ["akane.gomoku.place_piece"], [("board", 1, "control")])
        self.assertEqual({check.name for check in checks if check.level == "FAIL"}, {"运行工具目录", "运行程序服务"})
        identity = next(check for check in checks if check.name == "源码一致性")
        self.assertEqual(identity.level, "WARN")
        self.assertIn("尚未核实", identity.detail)

    def test_missing_and_inactive_plugins_preserve_unavailable_reason(self):
        self.assertEqual(catalog_checks({"plugins": []}, "game", [], [])[0].level, "FAIL")
        item = {"plugin_id": "game", "enabled": True, "runtime_status": "waiting_dependency", "reason": "provider_missing"}
        check = catalog_checks({"plugins": [item]}, "game", [], [])[0]
        self.assertEqual(check.level, "FAIL")
        self.assertIn("provider_missing", check.detail)

    def test_matching_active_contributions_do_not_claim_source_content_identity(self):
        catalog = {"plugins": [{
            "plugin_id": "akane.gomoku",
            "enabled": True,
            "runtime_status": "active",
            "version": "0.1.0",
            "generation": 12,
            "contributions": {
                "capabilities": ["akane.gomoku.place_piece"],
                "services": [{"service_id": "board", "version": 1, "methods": [{"name": "control"}]}],
            },
        }]}
        checks = catalog_checks(catalog, "akane.gomoku", ["akane.gomoku.place_piece"], [("board", 1, "control")])
        self.assertFalse(any(check.level == "FAIL" for check in checks))
        identity = next(check for check in checks if check.name == "源码一致性")
        self.assertEqual(identity.level, "WARN")
        self.assertIn("尚未核实", identity.detail)
        self.assertIn("不代表源码内容相同", next(check for check in checks if check.name == "运行工具目录").detail)

    def test_running_old_binary_is_detected_even_when_a_new_build_exists(self):
        release, running = self.root / "release.exe", self.root / "running.exe"
        release.write_bytes(b"new version")
        running.write_bytes(b"old version")
        self.assertEqual(runtime_check(release, running).level, "FAIL")
        running.write_bytes(release.read_bytes())
        self.assertEqual(runtime_check(release, running).level, "OK")
