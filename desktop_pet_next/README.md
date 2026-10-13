# Akane Desktop Pet Next

Tauri/WebView2 是唯一桌宠主线；旧 Electron、后端提供的旧 Web 聊天与预览页已移除。开发仍使用本目录的 Vite、浏览器测试与 Tauri。清理范围见 [前置清理记录](../docs/frontend_cleanup_preparation_v1.md)。
先看 [开发入口](../docs/DEVELOPMENT.md)，交互插件参考 [五子棋完整链路](../examples/desktop_features/gomoku/README.md)。

## Quick Start

普通用户使用根目录 `启动_Akane.bat`。本目录命令用于源码开发：

```powershell
# 仓库根目录；另一个终端运行后端
python launch_akane_memory_v01.py

cd desktop_pet_next
npm install
npm run tauri -- dev
```

独立源码后端默认 `http://127.0.0.1:9999`。已运行的命名实例使用自己的端口，开发前确认连接的后端与当前实例/Bot 一致。
`npm run doctor` 检查 Node、Rust、WebView2 和后端可达性；具体功能接线检查见下文。

## 当前入口

| 需要修改什么 | 实现 |
|---|---|
| 主窗口初始化、控制器组合 | `src/main.js` → `src/pet/application.js` |
| 聊天、流式回复、工具轮 | `src/pet/chat.js` |
| 气泡、表情、语音表现 | `src/pet/bubble.js`、`voice.js`、`tts.js`、`src/visual-renderer.js` |
| 客户端状态、切换与旧响应 | `src/pet/state.js`、`scope.js`、`lifecycle.js` |
| 设置 | `control-center-lab.html` → `src/control-center-v2/`；主窗口命令在 `src/pet/settings.js` |
| 工作区 | `workspace.html` → `src/workspace/` |
| 菜单、独立面板 | `src/pet/ui.js`、`panel.js`、`src/panel.js` |
| 五子棋 | `gomoku.html`、`src/gomoku.js`、`src/pet/gomoku.js`、`plugins/akane_gomoku/` |
| 原生窗口、命令和桥接 | `src-tauri/src/main.rs`、`pet_windows/`、`backend/` |
| 多页打包、窗口权限 | `vite.config.js`、`src-tauri/capabilities/*.json` |

`settings.html` 和 `control-center-v2.html` 是兼容跳转，设置功能只进入 `control-center-lab.html`。
程序调用插件服务可用 `src/pet/plugin-service.js`；小型无边框窗口可用 `src-tauri/src/pet_windows/docked.rs`。

## 现有能力与边界

主窗口通过既有后端聊天链路处理文本、工具、语音、表情和记忆；当前模型输出由后端解析后交给表现层。
静态透明立绘配合 CSS 动效。角色资源优先由当前角色包提供，资源入口以 `src/pet/character.js` 为准。
实时语音、音乐与歌词、屏幕观察、主动搭话、工作区、商店和五子棋各有自己的控制器，新增交互复用这些入口。
浏览器预览能检查页面布局；原生权限、桌面操作、窗口和实际模型链路在 Tauri 中验收。

## Commands

```powershell
npm run doctor
npm run build
npm run tauri -- dev
npm run tauri -- build --no-bundle
npm run start:release
```

`npm run build` 生成前端。发布 EXE 用 Tauri 构建，把前端页面嵌入程序；单独 Cargo 编译仅适合 Rust 检查。
`start:release` 对缺失或早于源码的发布 EXE 自动重建，`-Rebuild` 可显式重建，`-NoBuild` 保留现有产物。
根目录普通启动器会复用已有产物，其更新行为与这个开发脚本不同。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/start-next.ps1 -Rebuild
```

源码目录、构建目录和运行目录可能是三处；诊断运行 EXE 与发布产物是否一致后，再确认进程是否重启。
修改插件后在设置中源码安装更新已安装版本。

## 按改动选择验证

| 范围 | 命令 |
|---|---|
| 控制器边界、组合运行时 | `npm run check:pet-runtime`、`npm run smoke:pet-runtime` |
| 主窗口浏览器交互 | `npm run smoke:pet-browser` |
| 五子棋与服务身份绑定 | `npm run test:gomoku` |
| 工作区 | `npm run test:workspace`、`npm run smoke:workspace-browser` |
| 实时语音 | `npm run test:realtime-voice`、`npm run test:pet-voice` |
| 音乐 | `npm run test:pet-music`、`npm run smoke:pet-music-browser` |
| 设置页面 | `npm run verify:control-center` |
| 原生编译 | `npm run tauri -- build --no-bundle` |

新交互窗口的只读诊断（仓库根目录）：

```powershell
python scripts/check_desktop_feature.py gomoku
python scripts/check_desktop_feature.py gomoku --backend-url http://127.0.0.1:12001 --running
```

多 Bot 追加 `--bot-id`。不指定后端时只检查源码/产物，运行状态显示 SKIP。

## Daily Smoke Test

按本次改动检查实际状态：

- 启动后立绘、菜单、输入和资源正常，连接到预期后端/Bot。
- 发消息后文本、表情与语音沿正常链路到达；工具操作以实际结果为准。
- 窗口能打开、拖动、关闭；重新打开显示当前状态。
- 慢请求、重复点击、失败、切换角色/会话时，旧响应不覆盖新界面。
- 修改原生或嵌入页面后，实际运行 EXE 已更新。

`git diff --check` 与相关检查通过后再交付。全套仓库验证使用 `scripts/verify_akane.ps1`，具体范围遵循当前任务与 AGENTS.md。
