# 浮动面板 UI 交接文档 v1

## 已完成状态（Slice 1 + 1.5）

### 文件清单

| 文件 | 状态 | 说明 |
|------|------|------|
| `desktop_pet_next/panel.html` | ✅ 已完成 | 面板 HTML 骨架 |
| `desktop_pet_next/src/panel.css` | ✅ 已完成 | 磨砂玻璃视觉，含 sliders/stop/quit 样式 |
| `desktop_pet_next/src/panel.js` | ✅ 已完成 | 完整逻辑：波形、状态接收、事件桥、进度插值 |
| `desktop_pet_next/vite.config.js` | ✅ 已完成 | 已注册 panel 入口 |
| `desktop_pet_next/src-tauri/src/main.rs` | ✅ 已完成 | `open_panel_window`，自动贴左/右，屏幕边界检测 |
| `desktop_pet_next/src-tauri/capabilities/default.json` | ✅ 已完成 | 已加 "panel" 到 windows 数组 |
| `desktop_pet_next/src/main.js` | ✅ 已完成 | 右键/toggle 按钮开面板，事件桥，状态推送，头像/音乐同步 |

### 当前功能

- 右键角色 / 点击 ✦ 按钮 → 面板从桌宠侧边滑出（自动判断左右）
- 磨砂玻璃效果：`rgba(255,255,255,0.68)` + `blur(18px) saturate(1.5)`，桌面壁纸透过来
- 头像显示当前角色情绪立绘
- 在线状态指示点（绿/灰）
- 音乐区：标题、艺术家、频谱波形动画、播放/上一首/下一首、本地进度插值（≤1s 误差）
- 快捷操作 2×2 格：新对话、手边、停止回复、声音开关
- 大小/透明度滑块 → 实时作用于桌宠本体
- 最近播放记录（换歌时自动更新，最多 5 条）
- 底部：完整设置、角色工坊、退出（带危险色）
- 面板内所有事件通过 `panel:action` Tauri 事件传递给主窗口，主窗口处理后状态通过 `panel:state-update` 推回面板
- 跨平台：无 Windows 专属代码，`-webkit-backdrop-filter` 已覆盖 macOS/Linux WebKit

### 数据流

```
主窗口 main.js
  ├─ emit("panel:state-update", payload)  →  面板接收渲染
  ├─ emit("panel:recent-update", tracks)  →  面板更新最近列表
  └─ listen("panel:action", handler)      →  处理面板发来的操作

面板 panel.js
  ├─ emit("panel:action", { action, ...args })  →  主窗口执行
  └─ listen("panel:state-update")               →  接收状态更新
```

**`panel:action` 已支持的操作：**
- `new-session` — 开始新对话
- `open-workspace` — 打开手边物品
- `open-workshop` — 打开角色工坊
- `toggle-mute` + `muted: boolean` — 切换 TTS
- `stop-reply` — 中断回复
- `quit` — 退出应用
- `set-scale` + `value: number` — 设置大小 (0.75–1.45)
- `set-opacity` + `value: number` — 设置透明度 (0.55–1.0)

---

## 待完成工作（交接给下一个执行者）

### Task 1：深色/多主题系统

**目标：** 面板支持浅色/深色主题切换，未来可扩展更多主题。

**实现方案：**

1. 在 `panel.css` 的 `:root` 后面追加深色主题覆盖：
```css
/* 在 panel.html 的 <html> 或 <body> 加 data-theme="dark" 即可激活 */
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

2. 在 `panel.js` 中加主题切换逻辑：
```js
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme || "light";
  localStorage.setItem("panel-theme", theme);
}
// 启动时读取
applyTheme(localStorage.getItem("panel-theme") || "light");
```

3. 在面板头部加一个小图标按钮（🌙/☀）触发切换。建议放在 `.close-btn` 左边。

4. （可选）从 `panel:state-update` 接收 `theme` 字段，让主窗口控制主题，保持和系统主题一致。

**验收：** 点击主题按钮，面板背景、文字颜色同步切换；关闭再打开面板，主题保持。

---

### Task 2：Slice 2 完整后端数据接入

**目标：** 面板"最近"区域展示真实的共听/对话记录，而非只有最近播放歌曲。

**背景：** 后端已有 co-listen API：
- `POST /capabilities/music/co_listen_summary` — 获取共听摘要（含最近一起听的歌、时间、歌词高亮片段）
- `GET /capabilities/music/control_permissions` — 获取当前音乐控制权限状态

**实现方案：**

1. 在 `panel.js` 中，面板 `panel:ready` 后向后端请求共听摘要：
```js
async function fetchCoListenSummary() {
  const data = await backendPost("/capabilities/music/co_listen_summary", {
    profile_user_id: "master",
    max_items: 5
  });
  if (data?.items?.length) {
    renderRecent(data.items.map(item =>
      `${item.title} · ${item.artist}（${item.timestamp_display}）`
    ));
  }
}
```

2. 在 `panel.js` 加 `backendPost` helper：
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

3. 面板"最近"区域改为混合显示：co-listen 记录 + 最近播放（fallback）。

**验收：** 面板打开后 1–2 秒内，最近区域显示共听记录（有时间戳），而不是"暂无记录"。

---

### Task 3：音乐控制权限状态显示

**目标：** 面板音乐区能显示当前控制权限（谁在控制：用户/模型），并允许切换。

**背景：** 后端已有 `GET/POST /capabilities/music/control_permissions`。

**实现方案：**

1. 拉取权限状态：`GET /capabilities/music/control_permissions`
2. 在音乐区右上角显示一个小标签：`用户控制` / `Akane 控制`
3. 点击可切换，调 `POST /capabilities/music/control_permissions` 传入新状态
4. 状态更新后同步 waveform 颜色（用户控制用蓝色，Akane 控制用粉色渐变）

---

### Task 4：能力快捷状态 chip

**目标：** 面板底部（recent 区域下方）加一行 capability chip，显示当前已激活的能力，单击可开关。

**背景：** 后端 Capability Adapter v1 已完成。已有：
- `GET /capabilities/adapter-registry/reload`
- 各 adapter 的 invoke/health 接口

**实现方案：**

1. 面板启动时拉取 `/capabilities/list`（如不存在，可从 `/desktop-pet/health` 的 `capabilities` 字段读取）
2. 将 `prompt_exposed: true` 的能力显示为 chip：`[AnySearch ✓]` `[屏幕感知 ●]`
3. chip 颜色：已启用绿色，未启用灰色
4. 点击 chip 触发对应能力的开关（通过 `panel:action` + `action: "toggle-capability"` 传给主窗口）

---

### Task 5：`systemMedia.positionSeconds` 字段核对 ✅ 已验证

**已核查（2026-06-17）：** `normalizeSystemMediaSnapshot`（main.js:3323–3324）确实输出 `positionSeconds` / `durationSeconds`，`buildPanelStatePayload`（main.js:2653–2654）字段名完全一致。无需修改。

---

## CSS 变量速查（主题扩展用）

```css
/* 当前亮色主题 (:root) */
--glass-bg: rgba(255, 255, 255, 0.68)   /* 面板背景 */
--glass-blur: blur(18px) saturate(1.5)   /* 模糊参数 */
--glass-border: rgba(255, 255, 255, 0.90) /* 边框 */
--blue: #3867d6                           /* 主色调 */
--pink: #d94f70                           /* 强调色 */
--text: #202a37                           /* 主文字 */
--text-soft: rgba(32,42,55,0.56)          /* 次要文字 */
--text-faint: rgba(32,42,55,0.36)         /* 弱文字 */
--line: rgba(32,42,55,0.07)               /* 分割线 */
--green: #23b274                          /* 在线/成功 */
--radius: 20px                            /* 圆角 */
```

## 事件协议完整参考

### 主窗口 → 面板

| 事件 | Payload | 时机 |
|------|---------|------|
| `panel:state-update` | `{ characterName, emotion, avatarSrc, musicPlaying, musicTitle, musicArtist, musicPosition, musicDuration, musicPositionAt, muted, scale, opacity }` | 状态变化时，或面板 ready 后 50ms |
| `panel:recent-update` | `string[]` (最多 5 条，最新在前) | 换歌时，或面板 ready 后（如有记录） |

### 面板 → 主窗口

| `panel:action` 的 `action` 值 | 附加字段 | 效果 |
|------|---------|------|
| `new-session` | — | 开始新对话 |
| `open-workspace` | — | 打开手边窗口 |
| `open-workshop` | — | 打开角色工坊 |
| `toggle-mute` | `muted: boolean` | 切换 TTS |
| `stop-reply` | — | 中断当前回复 |
| `quit` | — | 退出应用 |
| `set-scale` | `value: number` | 设置桌宠大小 |
| `set-opacity` | `value: number` | 设置桌宠透明度 |
