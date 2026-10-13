"""Read-only wiring/build/runtime diagnostics for a desktop feature; never invokes it."""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.is_file() else ""


def strip_js_comments(source: str) -> str:
    """Remove comments while preserving strings used for static references."""
    result: list[str] = []
    index = 0
    quote = ""
    escaped = False
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            result.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            while index < len(source) and source[index] not in "\r\n":
                result.append(" ")
                index += 1
            continue
        if char == "/" and following == "*":
            result.extend((" ", " "))
            index += 2
            while index < len(source):
                if source[index] == "*" and index + 1 < len(source) and source[index + 1] == "/":
                    result.extend((" ", " "))
                    index += 2
                    break
                result.append("\n" if source[index] == "\n" else " ")
                index += 1
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _event_listeners(source: str) -> set[str]:
    return {
        event
        for _quote, event in re.findall(r"(?:\.\s*)?\blisten\s*\(\s*(['\"])([^'\"]+)\1", source)
    }


def _event_emits(source: str, *, target: str | None = None) -> set[str]:
    if target is None:
        target_pattern = r"(?:\.\s*)?\bemit\s*\(\s*(['\"])([^'\"]+)\1"
        return {match[1] for match in re.findall(target_pattern, source)}
    pattern = (
        r"(?:\.\s*)?\bemitTo\s*\(\s*(['\"])" + re.escape(target)
        + r"\1\s*,\s*(['\"])([^'\"]+)\2"
    )
    return {match[2] for match in re.findall(pattern, source)}


def _camel_window_name(window: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[-_]", window) if part)


def source_checks(root: Path, window: str) -> list[Check]:
    desktop = root / "desktop_pet_next"
    page = f"{window}.html"
    checks = []

    def record(ok: bool, name: str, success: str, failure: str) -> None:
        checks.append(Check("OK" if ok else "FAIL", name, success if ok else failure))

    html = read(desktop / page)
    record(bool(html), "页面", page, f"缺少 {page}")
    scripts = re.findall(r'<script\b[^>]*\bsrc=[\"\']([^\"\']+)[\"\']', html)
    script_paths = [(desktop / path.lstrip("/")).resolve() for path in scripts]
    record(bool(script_paths) and all(path.is_relative_to(desktop.resolve()) and path.is_file() for path in script_paths),
           "页面脚本", "脚本入口存在", "页面脚本缺失或不在桌宠目录")
    record(bool(re.search(r'[\"\']' + re.escape(page) + r'[\"\']', read(desktop / "vite.config.js"))),
           "打包入口", "Vite 包含该页面", f"在 vite.config.js 的 input 中加入 {page}")

    permissions = set()
    for path in (desktop / "src-tauri/capabilities").glob("*.json"):
        try:
            data = json.loads(read(path))
        except json.JSONDecodeError:
            checks.append(Check("FAIL", "窗口权限文件", f"{path.name} 不是有效 JSON"))
            continue
        if any(fnmatchcase(window, pattern) for pattern in data.get("windows", [])):
            permissions.update(item if isinstance(item, str) else item.get("identifier", "")
                               for item in data.get("permissions", []))
    record(bool(permissions), "窗口授权", "window label 已绑定权限", f"capabilities 中未授权窗口 {window}")
    for suffix, label in (("close", "关闭"), ("start-dragging", "拖动")):
        permission = f"core:window:allow-{suffix}"
        record(permission in permissions, label, permission, f"窗口 {window} 缺少 {permission}")
    events = "core:default" in permissions or "core:event:default" in permissions or {
        "core:event:allow-listen", "core:event:allow-emit-to",
    }.issubset(permissions)
    record(events, "跨窗口事件", "监听与定向发送已授权", "缺少事件监听/定向发送权限")

    command = f"open_{window}_window"
    native = desktop / "src-tauri/src"
    modules = [path for path in (native / "pet_windows").glob("*.rs")
               if re.search(r"\bfn\s+" + re.escape(command) + r"\b", read(path))]
    record(bool(modules) and any("#[tauri::command]" in read(path) for path in modules),
           "窗口命令", command, f"缺少带 tauri::command 的 {command}")
    main = read(native / "main.rs")
    handler = re.search(r"generate_handler!\[(.*?)\]\)", main, re.DOTALL)
    record(bool(handler and re.search(r"\b" + re.escape(command) + r"\b", handler.group(1))),
           "命令注册", "generate_handler 已注册", f"在 main.rs 的 generate_handler 中加入 {command}")
    module_text = read(native / "pet_windows/mod.rs")
    record(any(re.search(r"\bmod\s+" + re.escape(path.stem) + r"\s*;", module_text) for path in modules),
           "Rust 模块", "窗口实现已接入模块树", "在 pet_windows/mod.rs 中注册窗口实现模块")

    app = strip_js_comments(read(desktop / "src/pet/application.js"))
    controller_path = desktop / "src/pet" / f"{window}.js"
    page_entry = desktop / "src" / f"{window}.js"
    ui = strip_js_comments(read(desktop / "src/pet/ui.js"))
    panel = strip_js_comments(read(desktop / "src/pet/panel.js"))
    open_command = f"open_{window}_window"
    command_ref = rf"\btauriCall\s*\(\s*['\"]{re.escape(open_command)}['\"]"
    factory = f"create{_camel_window_name(window)}Controller"
    controller_imported = re.search(
        rf"\bimport\s*\{{[^}}]*\b{re.escape(factory)}\b[^}}]*\}}\s*from\s*['\"]\./{re.escape(window)}\.js['\"]",
        app,
    )
    controller = strip_js_comments(read(controller_path))
    conventional_controller = (
        bool(controller_imported)
        or bool(re.search(rf"\b{re.escape(factory)}\b", app))
        or bool(re.search(rf"\b{re.escape(factory)}\b", controller))
    )
    if not conventional_controller:
        checks.append(Check(
            "SKIP",
            "主窗口控制器接线",
            f"未发现 src/pet/{window}.js 与 application.js 的同名组合约定；请从页面脚本和实际桥接入口手工定位。",
        ))
    else:
        record(controller_path.is_file(), "主窗口控制器文件", f"src/pet/{window}.js", f"缺少 src/pet/{window}.js")
        record(bool(controller_imported), "主窗口控制器导入",
               f"application.js 导入 {factory}", f"在 src/pet/application.js 从 './{window}.js' 导入 {factory}")
        record(bool(re.search(rf"\bfunction\s+{re.escape(factory)}\s*\(", controller)),
               "主窗口控制器定义", factory, f"在 src/pet/{window}.js 导出 {factory}")
        composition = re.search(
            rf"\bwire\s*\(\s*(\w+)\s*,\s*{re.escape(factory)}\s*\(", app
        )
        record(bool(composition), "主窗口控制器组合", f"application.js 组合 {factory}",
               f"在 src/pet/application.js 用 wire(port, {factory}(...)) 组合控制器")
        port_name = composition.group(1) if composition else ""
        bridge_methods = ("registerBridge", f"register{_camel_window_name(window)}Bridge")
        registered = next((method for method in bridge_methods if port_name and re.search(
            rf"\b{re.escape(port_name)}\.{re.escape(method)}\s*\(", app)), "")
        record(bool(registered), "主窗口桥接注册", f"{port_name}.{registered}() 已接入 application.js" if registered else "",
               "在 src/pet/application.js 调用组合端口实际提供的桥接注册方法")

        page = strip_js_comments(read(page_entry))
        page_sends = _event_emits(page, target="main") | _event_emits(page)
        main_receives = _event_listeners(controller)
        request_events = {event for event in page_sends | main_receives if event.startswith(f"{window}:")}
        if request_events:
            paired = request_events.issubset(page_sends) and request_events.issubset(main_receives)
            detail = (
                "页面发送与主窗口监听已配对：" + ", ".join(sorted(request_events))
                if paired
                else "请求事件缺少一侧源码引用；页面需发送、主窗口控制器需监听：" + ", ".join(sorted(request_events))
            )
            checks.append(Check("OK" if paired else "FAIL", "请求事件接线", detail))
        else:
            checks.append(Check("SKIP", "请求事件接线", "未发现窗口前缀的请求事件；请确认该窗口是否使用跨窗交互。"))

        main_sends = _event_emits(controller, target=window) | _event_emits(controller)
        page_receives = _event_listeners(page)
        response_events = {event for event in main_sends | page_receives if event.startswith(f"{window}:")}
        if response_events:
            paired = response_events.issubset(main_sends) and response_events.issubset(page_receives)
            detail = (
                "主窗口发送与子窗口监听已配对：" + ", ".join(sorted(response_events))
                if paired
                else "响应事件缺少一侧源码引用；主窗口需发送、页面需监听：" + ", ".join(sorted(response_events))
            )
            indirect_emit = re.search(
                rf"\bemitTo\s*\(\s*['\"]{re.escape(window)}['\"]\s*,\s*[A-Za-z_$]",
                controller,
            ) or re.search(r"\bemit\s*\(\s*[A-Za-z_$]", controller)
            if not paired and indirect_emit:
                checks.append(Check("SKIP", "响应事件接线", "发送事件名经变量或助手传递；请沿发送助手手工核对："
                                    + ", ".join(sorted(response_events))))
            else:
                checks.append(Check("OK" if paired else "FAIL", "响应事件接线", detail))
        else:
            checks.append(Check("SKIP", "响应事件接线", "未发现窗口前缀的响应事件；请确认该窗口是否使用跨窗交互。"))

        open_method_ref = rf"\b\w+\.open{re.escape(_camel_window_name(window))}Window\s*\("
        record(bool(re.search(command_ref, ui) or re.search(open_method_ref, ui)), "主 UI 打开入口",
               f"ui.js 引用 {open_command} 或控制器打开方法", f"在 src/pet/ui.js 接入窗口打开入口")
        record(bool(re.search(command_ref, panel) or re.search(open_method_ref, panel)), "面板打开入口",
               f"panel.js 引用 {open_command} 或控制器打开方法", f"在 src/pet/panel.js 接入窗口打开入口")
    return checks


def source_inputs(desktop: Path, *, native: bool) -> list[Path]:
    inputs = list(desktop.glob("*.html")) + list((desktop / "src").rglob("*"))
    inputs += [desktop / name for name in ("package.json", "package-lock.json", "vite.config.js")]
    if native:
        inputs += list((desktop / "src-tauri/src").rglob("*"))
        inputs += list((desktop / "src-tauri/capabilities").glob("*.json"))
        inputs += [desktop / "src-tauri" / name for name in ("Cargo.toml", "Cargo.lock", "tauri.conf.json", "build.rs")]
    return [path for path in inputs if path.is_file()]


def build_checks(root: Path, window: str) -> list[Check]:
    desktop = root / "desktop_pet_next"
    checks = []
    for path, native, label, command in (
        (desktop / "dist" / f"{window}.html", False, "前端产物", "npm run build"),
        (desktop / "src-tauri/target/release/akane_desktop_pet_next.exe", True, "发布产物", "npm run tauri -- build --no-bundle"),
    ):
        if not path.is_file():
            checks.append(Check("WARN", label, f"未发现产物；运行 {command}"))
        elif any(item.stat().st_mtime > path.stat().st_mtime for item in source_inputs(desktop, native=native)):
            checks.append(Check("WARN", label, f"源码比产物新；运行 {command}"))
        else:
            checks.append(Check("OK", label, "存在，时间不早于源码（不代替原生验收）"))
    return checks


def plugin_contract(root: Path, window: str) -> tuple[str, list[str], list[tuple[str, int, str]]]:
    directory = root / "plugins" / f"akane_{window}"
    manifest = directory / "pyproject.toml"
    if not manifest.is_file():
        return "", [], []
    project = tomllib.loads(read(manifest))["project"]
    plugin_id = next(iter(project.get("entry-points", {}).get("akane.plugins.v1", {})), "")
    tools = []
    for path in (directory / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(read(path))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "plugin" and target.attr == "tool":
                    name = node.name
                    if isinstance(decorator, ast.Call):
                        for keyword in decorator.keywords:
                            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                                name = keyword.value.value
                    tools.append(f"{plugin_id}.{name}")
    controller = read(root / "desktop_pet_next/src/pet" / f"{window}.js")
    services = [(name, int(version), method) for name, version, method in re.findall(
        re.escape(plugin_id) + r"\.service\.([A-Za-z0-9_-]+)\.v([0-9]+)\.([A-Za-z0-9_]+)", controller)] if plugin_id else []
    return plugin_id, tools, services


def catalog_checks(catalog: dict, plugin_id: str, tools: list[str], services: list[tuple[str, int, str]]) -> list[Check]:
    if catalog.get("ok") is False:
        return [Check("FAIL", "运行插件目录", str(catalog.get("reason") or catalog.get("status") or "不可用"))]
    plugin = next((item for item in catalog.get("plugins", []) if item.get("plugin_id") == plugin_id), None)
    if plugin is None:
        return [Check("FAIL", "运行插件", f"{plugin_id} 未安装到这个 Bot；在插件设置中 stage 并安装源码，然后启用插件")]
    if not plugin.get("enabled") or plugin.get("runtime_status") != "active":
        return [Check("FAIL", "运行插件", f"{plugin_id}: {plugin.get('runtime_status', 'unknown')} {plugin.get('reason', '')}；检查插件设置中的启用状态和依赖")]
    checks = [Check("OK", "运行插件", f"{plugin_id} active，运行目录版本 {plugin.get('version', '?')}")]
    checks.append(Check(
        "WARN",
        "源码一致性",
        "已核对 active 状态和贡献标识；运行代码是否与当前源码一致尚未核实。相同版本号、generation 或工具名不能证明代码相同；请结合安装返回的 digest 与激活结果确认部署。",
    ))
    contributions = plugin.get("contributions", {})
    missing = set(tools) - set(contributions.get("capabilities", []))
    checks.append(Check(
        "FAIL" if missing else "OK",
        "运行工具目录",
        ", ".join(sorted(missing)) + " 未声明；检查插件源码后 stage/install 并激活" if missing
        else "源码工具对应的 capability 声明已在运行目录出现；不代表源码内容相同",
    ))
    for name, version, method in services:
        found = any(item.get("service_id") == name and item.get("version") == version and
                    any(member.get("name") == method for member in item.get("methods", []))
                    for item in contributions.get("services", []))
        checks.append(Check(
            "OK" if found else "FAIL",
            "运行程序服务",
            f"{name}.v{version}.{method}" + (
                " 声明已在运行目录出现；不代表源码内容相同"
                if found
                else " 未声明；检查服务定义后 stage/install 插件源码并激活"
            ),
        ))
    return checks


def runtime_check(release: Path, running: Path) -> Check:
    if not running.is_file() or not release.is_file():
        return Check("FAIL", "运行 EXE", "运行文件或本机构建文件不存在")
    # Close files before a launcher can replace them on Windows.
    with running.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    with release.open("rb") as handle:
        expected = hashlib.file_digest(handle, "sha256").hexdigest()
    return Check("OK" if actual == expected else "FAIL", "运行 EXE", "与本机发布产物一致" if actual == expected else "正在使用旧/不同 EXE；重新部署并重启桌宠")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("window", help="窗口 label；约定页面为 <label>.html，命令为 open_<label>_window")
    parser.add_argument("--backend-url", default=os.environ.get("AKANE_BACKEND_URL", ""), help="只读检查这个后端的插件目录")
    parser.add_argument("--bot-id", default="", help="多 Bot 时指定目标 Bot")
    parser.add_argument("--running", action="store_true", help="Windows：比对当前桌宠进程的 EXE")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", args.window):
        parser.error("window 必须是简单窗口 label")
    try:
        checks = source_checks(ROOT, args.window) + build_checks(ROOT, args.window)
        plugin_id, tools, services = plugin_contract(ROOT, args.window)
    except (OSError, ValueError, SyntaxError, KeyError, TypeError) as error:
        print(f"[FAIL] 源码诊断: {type(error).__name__}: {error}")
        return 1
    if plugin_id and args.backend_url:
        prefix = f"/api/bots/{quote(args.bot_id, safe='')}" if args.bot_id else ""
        try:
            with urlopen(args.backend_url.rstrip("/") + prefix + "/plugins/catalog", timeout=3) as response:
                catalog = json.load(response)
            checks += catalog_checks(catalog, plugin_id, tools, services)
        except Exception as error:
            checks.append(Check("FAIL", "运行插件目录", f"无法读取：{type(error).__name__}；检查后端地址和 Bot"))
    else:
        checks.append(Check("SKIP", "运行插件目录", "未指定后端或该窗口没有同名插件；未验证运行状态"))
    if args.running:
        if os.name != "nt":
            checks.append(Check("SKIP", "运行 EXE", "当前系统不是 Windows"))
        else:
            try:
                command = "Get-CimInstance Win32_Process -Filter \"Name='akane_desktop_pet_next.exe'\" | Select-Object -ExpandProperty ExecutablePath | ConvertTo-Json -Compress"
                result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
                paths = json.loads(result.stdout) if result.stdout.strip() else []
                paths = [paths] if isinstance(paths, str) else paths
                release = ROOT / "desktop_pet_next/src-tauri/target/release/akane_desktop_pet_next.exe"
                checks += [runtime_check(release, Path(path)) for path in paths if path]
                if not paths:
                    checks.append(Check("WARN", "运行 EXE", "没有运行中的桌宠"))
            except Exception as error:
                checks.append(Check("FAIL", "运行 EXE", f"无法读取进程：{type(error).__name__}"))
    else:
        checks.append(Check("SKIP", "运行 EXE", "未指定 --running；未验证正在运行的版本"))
    for check in checks:
        print(f"[{check.level}] {check.name}: {check.detail}")
    print("诊断只读取接线、产物和目录状态；原生拖动/关闭、真实模型调用请按示例验收。")
    return int(any(check.level == "FAIL" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
