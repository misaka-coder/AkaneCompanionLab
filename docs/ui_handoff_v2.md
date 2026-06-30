# UI 交接文档 v2 — 面板 + 控制中心后续任务

> 编写于 2026-06-17。面向执行者（小模型 / GPT）。请从头读完再动手。

---

## 执行前必读

- 工程根目录：当前 AkaneCompanionLab 仓库
- 桌宠前端：`desktop_pet_next/`（Tauri v2 + Vite + WebView2）
- 面板 JS：`desktop_pet_next/src/panel.js`
- 面板 CSS：`desktop_pet_next/src/panel.css`
- 面板 HTML：`desktop_pet_next/panel.html`
- 控制中心 JS：`desktop_pet_next/src/control-center-lab.js`
- 控制中心 CSS：`desktop_pet_next/src/control-center-lab.css`
- Rust 后端：`desktop_pet_next/src-tauri/src/main.rs`

**架构约束（违反会被拒绝）：**
1. UI 不直接调后端 HTTP API — 只通过 Tauri 事件 (`emit`/`listen`) 或 `invoke` 通信
2. 面板 → 主窗口：`emit("panel:action", { action, ...args })`
3. 主窗口 → 面板：`emit("panel:state-update", payload)` / `emit("panel:recent-update", items)`
4. 绝对路径、API key、`.env` 值不得出现在任何前端代码或日志里
5. 不写空实现、假成功、假状态

---

## 已完成（本次会话）

### ✅ 面板播放图标 Bug 修复

**文件：** `desktop_pet_next/src/panel.js`

**问题：** 播放按钮 SVG 硬编码为暂停条，不随播放状态切换。

**修复内容：**
- 新增 `updatePlayIcon(playing)` 函数，通过 `document.getElementById("mc-play-icon")` 动态切换 SVG 路径
  - 播放中（playing=true）：显示暂停双竖条
  - 已暂停（playing=false）：显示播放三角
  - 同时更新按钮 `title` 属性
- `renderMusic()` 现在在 `setWaveformState` 后调用 `updatePlayIcon(state.musicPlaying)`
- 播放按钮点击：乐观更新图标（`updatePlayIcon(!state.musicPlaying)`），等主窗口 `state-update` 修正

**验收：** 切换播放/暂停时，按钮图标正确切换；初始加载时也显示正确图标。

---

## 待执行任务

---

### Task A：面板深色主题

**优先级：** 中
**文件：** `desktop_pet_next/src/panel.css`、`desktop_pet_next/src/panel.js`、`desktop_pet_next/panel.html`

#### CSS 变更（`panel.css`）

在文件末尾追加：

```css
[data-theme="dark"] {
  --glass-bg: rgba(14, 17, 24, 0.76);
  --glass-border: rgba(255, 255, 255, 0.10);
  --glass-shadow: 0 12px 36px rgba(0, 0, 0, 0.28), 0 2px 8px rgba(0, 0, 0, 0.16);
  --glass-inner: inset 0 1px 0 rgba(255, 255, 255, 0.08);
  --text: #e8edf5;
  --text-soft: rgba(232, 237, 245, 0.60);
  --text-faint: rgba(232, 237, 245, 0.36);
  --line: rgba(255, 255, 255, 0.07);
  --blue-soft: rgba(100, 140, 255, 0.15);
  --pink-soft: rgba(240, 100, 140, 0.15);
}
```

#### JS 变更（`panel.js`）

在 `init()` 之前加：

```js
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme || "light";
  localStorage.setItem("panel-theme", theme);
}
```

在 `init()` 函数体最前面加：

```js
applyTheme(localStorage.getItem("panel-theme") || "light");
```

#### HTML 变更（`panel.html`）

在 `.close-btn` 按钮左边（同一行）插入主题切换按钮：

```html
<button id="theme-toggle-btn" class="theme-btn" title="切换主题" type="button">
  <svg id="theme-icon" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
    <!-- 月亮图标，浅色模式下显示 -->
    <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 12.5A5.5 5.5 0 1 1 8 2.5a5.5 5.5 0 0 1 0 11z"/>
  </svg>
</button>
```

在 `wireButtons()` 中加：

```js
document.getElementById("theme-toggle-btn")?.addEventListener("click", () => {
  const current = document.documentElement.dataset.theme || "light";
  applyTheme(current === "light" ? "dark" : "light");
});
```

**验收：**
- 点击按钮，面板背景/文字同步切换深浅
- 关闭面板再打开，主题保持（localStorage）

---

### Task B：共听记录接入

**优先级：** 中
**文件：** `desktop_pet_next/src/panel.js`

**注意：** 这里调后端需通过 `tauriFetch`，不能用浏览器 `fetch`（已有 import：`import { fetch as tauriFetch } from "@tauri-apps/plugin-http"`）。

在 `DEFAULT_BACKEND_URL` 下方加 helper：

```js
async function backendPost(path, body) {
  try {
    const res = await tauriFetch(`${DEFAULT_BACKEND_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      connectTimeout: 5000,
    });
    if (res.ok) return await res.json();
  } catch {}
  return null;
}
```

在 `init()` 末尾加：

```js
if (isTauri) {
  fetchCoListenSummary();
}
```

在文件任意位置加函数：

```js
async function fetchCoListenSummary() {
  const data = await backendPost("/capabilities/music/co_listen_summary", {
    profile_user_id: "master",
    max_items: 5,
  });
  if (data?.items?.length) {
    renderRecent(
      data.items.map(
        (item) => `${item.title} · ${item.artist}（${item.timestamp_display}）`
      )
    );
  }
}
```

**验收：** 后端运行时，面板打开后 2 秒内显示带时间戳的共听记录（不是"暂无记录"）。后端不运行时，降级显示"暂无记录"，不报错。

---

### Task C：音乐控制权限状态显示

**优先级：** 低
**文件：** `desktop_pet_next/src/panel.js`、`desktop_pet_next/panel.html`

**API：**
- `GET /capabilities/music/control_permissions` → `{ controller: "user" | "model" }`
- `POST /capabilities/music/control_permissions` + body `{ controller: "user" | "model" }`

在音乐区（`#music-section`）右上角加一个标签按钮（HTML）：

```html
<button id="music-controller-badge" class="controller-badge" type="button" title="点击切换控制权">
  用户控制
</button>
```

JS 中加拉取逻辑：

```js
async function fetchMusicController() {
  try {
    const res = await tauriFetch(`${DEFAULT_BACKEND_URL}/capabilities/music/control_permissions`, {
      method: "GET",
      connectTimeout: 3000,
    });
    if (res.ok) {
      const data = await res.json();
      updateControllerBadge(data.controller);
    }
  } catch {}
}

function updateControllerBadge(controller) {
  const badge = document.getElementById("music-controller-badge");
  if (!badge) return;
  badge.textContent = controller === "model" ? "Akane 控制" : "用户控制";
  badge.dataset.controller = controller || "user";
}
```

切换点击：

```js
document.getElementById("music-controller-badge")?.addEventListener("click", async () => {
  const badge = document.getElementById("music-controller-badge");
  const next = badge?.dataset.controller === "model" ? "user" : "model";
  try {
    const res = await tauriFetch(`${DEFAULT_BACKEND_URL}/capabilities/music/control_permissions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ controller: next }),
      connectTimeout: 3000,
    });
    if (res.ok) updateControllerBadge(next);
  } catch {}
});
```

**验收：** 标签显示当前控制方，点击后切换并持久化到后端。

---

### Task D：控制中心音乐页简化

**优先级：** 中
**文件：** `desktop_pet_next/src/control-center-lab.js`

**背景：** 当前控制中心音乐页（`music` 页面）有一个完整播放器（进度条、歌名、控制按钮）。这与面板功能重复。控制中心应只保留"配置"功能。

**要删除的区域：** 找到 `renderMusicPage()` 函数，删除或注释掉：
- 播放控制按钮组（prev/play/next）
- 进度条 + 时间显示
- 当前播放歌名/艺术家

**要保留的区域：**
- 播放模式设置（循环/随机/单曲）
- 音量标准化开关
- 其他配置项

如果函数内无法确定边界，只删除按钮 DOM 和相关事件绑定即可，不要删整个函数。

**验收：** 音乐页不再有播放控件；仍可修改播放模式和音量设置。

---

### Task E：控制中心概览页迷你播放器移除

**优先级：** 低
**文件：** `desktop_pet_next/src/control-center-lab.js`

**背景：** 概览页（`overview`）有一个迷你播放器区块，和面板重复。

**操作：** 找到 `renderOverviewPage()` 中的音乐/播放器相关 HTML 块，将其整段注释掉或删除。确认其余概览内容（角色状态、快捷按钮）不受影响。

**验收：** 概览页不再展示迷你播放器；其余内容正常显示。

---

### Task F：macOS / Linux 音乐控制（Rust 层）

**优先级：** 低
**文件：** `desktop_pet_next/src-tauri/src/main.rs`

**背景：** 当前 `control_system_media` 命令仅 Windows 实现（SMTC API）；macOS/Linux 返回 `"system_media_control_unavailable"`。

**macOS 方案（AppleScript）：**

```rust
#[cfg(target_os = "macos")]
#[tauri::command]
async fn control_system_media(action: String) -> String {
  let script = match action.as_str() {
    "play"     => r#"tell application "Music" to play"#,
    "pause"    => r#"tell application "Music" to pause"#,
    "next"     => r#"tell application "Music" to next track"#,
    "previous" => r#"tell application "Music" to previous track"#,
    _          => return "unknown_action".to_string(),
  };
  let output = std::process::Command::new("osascript")
    .arg("-e")
    .arg(script)
    .output();
  match output {
    Ok(o) if o.status.success() => "ok".to_string(),
    _ => "system_media_control_unavailable".to_string(),
  }
}
```

注意：这只控制 Apple Music。通用跨播放器控制需要 `MediaRemote.framework`（私有框架），实现复杂，暂不要求。

**Linux 方案（MPRIS via D-Bus）：**

需要在 `Cargo.toml` 加 `[dependencies] mpris = "2"` 或用 `dbus` crate，复杂度较高。暂标记为 `// TODO: Linux MPRIS`，维持返回 `"system_media_control_unavailable"`。

**验收（macOS）：** 在 macOS 上，面板播放控制能控制 Apple Music；其他播放器不受影响，不报错。

---

## 事件协议速查

### 主窗口 → 面板

| 事件 | Payload 字段 | 触发时机 |
|------|-------------|---------|
| `panel:state-update` | `characterName, emotion, avatarSrc, musicPlaying, musicTitle, musicArtist, musicPosition, musicDuration, musicPositionAt, muted, scale, opacity` | 状态变化时 |
| `panel:recent-update` | `string[]`（最新在前，最多 5 条） | 换歌时 |

### 面板 → 主窗口（`panel:action`）

| `action` | 附加字段 | 效果 |
|----------|---------|------|
| `new-session` | — | 开始新对话 |
| `open-workspace` | — | 打开手边窗口 |
| `open-workshop` | — | 打开角色工坊 |
| `toggle-mute` | `muted: boolean` | 切换 TTS |
| `stop-reply` | — | 中断当前回复 |
| `quit` | — | 退出应用 |
| `set-scale` | `value: number` | 设置桌宠大小 (0.75–1.45) |
| `set-opacity` | `value: number` | 设置透明度 (0.55–1.0) |

---

## 验收清单

执行完某个 Task 后，在下方打勾：

- [ ] Task A：深色主题 — 切换 + localStorage 保持
- [ ] Task B：共听记录 — 后端运行时显示真实记录
- [ ] Task C：音乐控制权限 — 标签显示 + 点击切换
- [ ] Task D：控制中心音乐页简化 — 无播放控件，保留设置
- [ ] Task E：概览迷你播放器移除 — 页面干净
- [ ] Task F：macOS AppleScript 控制 — Apple Music 响应

---

## 提交说明

- 只提交 `desktop_pet_next/src/` 和 `desktop_pet_next/src-tauri/src/main.rs` 内的改动
- 不提交 `node_modules/`、`dist/`、`target/`、`.env`
- 每个 Task 独立 commit，commit message 格式：`feat(panel): Task A — 深色主题`
