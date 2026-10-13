# Desktop Pet Control Center V2

Status: production control center, cut over on 2026-08-21. It covers Overview, Chat, Character & Appearance, Voice & Wake, Model Service, Abilities & Permissions, and System & Diagnostics.

The canonical settings entry is `desktop_pet_next/control-center-lab.html`. It loads the componentized V2 implementation directly. `settings.html` and `control-center-v2.html` are compatibility redirects and own no settings behavior.

The implementation has a small component/store/bridge boundary, reads only real backend and Tauri snapshots, and renders honest connecting, empty, failed, and unavailable states. Model settings use the existing admin bridge without returning saved keys. Bot switching rebinds the data source only after the desktop snapshot confirms the requested Bot. Voice preview and character changes also require observed runtime confirmation. System & Diagnostics never invents a log timeline. Device-local presentation preferences never rewrite character assets or compete with workshop calibration.

## Files

- `contract.md` — product boundary, information architecture, runtime data contract, interaction states, and migration gates.
- `production_gap_audit.md` — V2/legacy coverage, prohibited mock migration, replacement gates, and cutover order.
- `prototype/index.html` — overview, character/appearance, and chat-history prototype.
- `prototype/styles.css` — glass shell, responsive layout, motion, and theme tokens.
- `prototype/app.js` — local-only light/dark themes, framing editor, chat simulation, navigation, and honest simulated action states.
- `../../desktop_pet_next/control-center-lab.html` — canonical Vite/Tauri settings entry.
- `../../desktop_pet_next/control-center-v2.html` — stale-link compatibility redirect to the canonical entry.
- `../../desktop_pet_next/src/control-center-v2/` — componentized shell, Overview, Character & Appearance, shared action primitive, store, host bridge, and real-snapshot view model.
- `../control_center_view_model_split_v1.md` — domain projection owners, shared command matching, and failure/scope regression checks.
- `../control_center_app_split_v1.md` — application controllers, async form/draft ownership, render policy and lifecycle checks.
- `../control_center_bridge_split_v1.md` — host boundary, scoped reads, independent refreshes, action confirmation and subscription cleanup.

## Local preview

From the repository root:

```powershell
python -m http.server 4177
```

Open:

```text
http://127.0.0.1:4177/docs/desktop_pet_control_center_v2/prototype/
```

The prototype uses existing sample assets from `desktop_pet_next` to demonstrate slots. Selecting a local image only previews it in the current browser session; it does not edit a character pack.

## Production V2 preview

From `desktop_pet_next`:

```powershell
npm run dev
```

Open `http://127.0.0.1:1420/control-center-lab.html`. When the local backend is not running, the page deliberately shows a reconnectable empty state instead of sample data. In Tauri it also subscribes to the existing settings snapshot event for current activity, expression, and music state.

## Real Tauri path

From `desktop_pet_next`:

```powershell
npm run tauri -- dev
```

This starts the normal desktop-pet development runtime. The production frameless `settings` window uses the established `settings` label, so snapshot events, commands, folder entrances, the character workshop, music control, and window controls use the production bridge.

There is no implementation selector. `AKANE_OPEN_MODEL_SETTINGS=1` selects `control-center-lab.html?page=model`; all other settings launches select `control-center-lab.html`.

Manual acceptance for this stage:

1. V2 opens automatically and can be dragged, minimized, maximized/restored, and closed.
2. The connection chip and activity state follow the running desktop pet; no sample character or fake activity appears while disconnected.
3. “新对话” reports confirmation after a settings snapshot shows a session change; “停止回复” and “停止语音” confirm when matching completion receipt and stopped state are received (without requiring an artificial active-to-idle transition if already stopped).
4. “打开工作区”, “角色工坊”, and “角色包目录” open their real host targets.
5. Music controls report failure or unconfirmed execution honestly when no controllable session exists.
6. Character & Appearance reads the current pack, outfit, expressions, portrait, and resource health from real snapshots; device-local theme/framing changes provide immediate preview and report whether persistence succeeded.
7. Pack, outfit, and expression changes confirm only after the desktop-pet snapshot reports the requested value; otherwise the UI reports an unconfirmed execution.
8. Abilities & Permissions reads the real capability catalog, shows the current safety boundary, and changes approval mode only through the profile approval-policy route.
9. Full authoring, uploads, and framing calibration remain in the character workshop instead of creating a second character-pack authority.
10. Voice toggles, volume, speed, wake settings, preview, and stop use the existing settings-command bridge; preview reports success only after the live snapshot shows playback.
11. System & Diagnostics distinguishes backend health from desktop live-state availability, exposes no sensitive paths, and omits recent events until a real event source exists.
12. Chat loads older history through the read-only session cursor, preserves the visible reading position after prepending rows, and keeps newly arriving tail messages behind an unread affordance when the reader is away from the bottom.

The 2026-08-21 acceptance pass covered real Tauri connection loss, window chrome, drag/minimize/maximize/restore, workspace/workshop/folder entrances, repeated action pending state, slow model discovery, model secret masking, model deep-link startup, and canonical entry startup.

## Settings Action Development Guide

以现有“设置语音音量”动作（`voice.setVolume`）为例，说明一条本机主窗口动作的完整流转：

1. **按钮发出 action ID**：页面事件调用 `actions.runAction("voice.setVolume", { value: 0.8 })`（见 `src/control-center-v2/app/events.js`）；`actions` 由 `app/actions.js:createActionController` 创建，再调用 `bridge.runAction`。
2. **动作说明指定命令与确认**：在 `src/control-center/settings-command-contract.js` 统一声明：
   ```javascript
   [CONTROL_CENTER_ACTIONS.voiceSetVolume]: { command: "setVoiceVolume", observed: true }
   ```
   由此自动生成 `SETTINGS_COMMAND_BY_ACTION_ID`（映射到主窗口命令 `"setVoiceVolume"`）、`OBSERVED_SETTINGS_ACTION_IDS` 及超时配置（默认 1800ms）。无需在多个文件重复手工登记。
   新增 action ID 时先在 `src/control-center/action-router.js:CONTROL_CENTER_ACTIONS` 声明；只有走主窗口命令链的动作才在上述契约中增加 spec。`CONTROL_CENTER_BRIDGED_ACTION_IDS` 从动作声明派生，不手工维护。
3. **主窗口调用领域方法**：事件经 `data-source/tauri.js` 发布 `settingsCommand`，进入主窗口 `src/pet/settings.js:executeSettingsCommand`，调用 `voicePort.setVoiceVolume(Number(payload.value))`。
4. **统一补齐回执**：`executeSettingsCommand` 退出边界由宿主统一补齐 `command`、`operationId` 与时间戳；若业务处理器返回了对象（包含失败或非终态），其真实结果被原样保留，遗漏 `operationId` 不会把失败伪造成成功。
5. **确认与反馈**：`src/control-center-v2/view-model/action-results.js:isObservedActionConfirmation` 确认快照状态匹配（对于已空闲的语音和回复再次停止，匹配成功回执与已停止状态即可确认，不再死等超时）。
6. **验证测试**：
   - 合约与回执回归：`node --test scripts/settings-command-results.test.mjs`
   - 全动作 smoke：`node scripts/control-center-action-bridge-smoke.mjs`

### 其它入口类型（不走主窗口事件链）

并非所有设置动作都需要登记进主窗口事件。开发者应按业务归属选择实际专用入口：

- **直接窗口 / 宿主 invoke**：如 `workspace.open`（打开工作区）、`character.openWorkshop`（工坊）、`window.close`（关闭窗口），由 `src/control-center/data-source/tauri.js` 直接调用 Tauri IPC 或窗口 API，不向主窗口发布 `settingsCommand`。
- **后端业务动作**：如模型服务配置（`abilities.provider.*`）、MCP 管理（`abilities.mcp.*`）、插件（`abilities.plugin.*`）、记忆设置（`memory.*`）和 QQ 接入（`qq.setup.*`），走各自专用 HTTP 路由或业务管理器（如 `model-service.js`、`qq-setup.js`），保持清晰的边界与失败反馈。
