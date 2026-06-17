# Listening Together Demo v1 — T5 实施 Ticket（桌宠"伸手"动画 + SMTC 时序）

Updated: 2026-06-17
Parent design: `docs/listening_together_demo_v1.md`
Status: ready for small-model implementation
Preceding work: T1–T4 均已落地。T4 增加了"让她也能"权限开关（`music_control_store.py` + 两个 endpoint + 控制中心开关 UI）。

## 0. 这份 ticket 的位置

设计稿 §6.5 描述了"她主动执行 SMTC 时"的时序表：

| 时刻 | 事件 |
|------|------|
| T0.0 | 立绘开始"伸手"动画 |
| T+0.3s | 气泡前置文字出现 |
| T+0.6s | SMTC 命令发出 |
| T+1.2s | 动画复位；结果气泡替换 |

前几轮落地的是"她有没有权限"（T4）；本 ticket 落地的是"她执行时，用户看到什么"。

**核心问题：桌宠是用户自己上传的静态图，没有 Live2D，伸手动画怎么做？**

答案：不动图片像素，只动容器。CSS `transform + filter` 施加在 `.pet-hitbox img` 上，能让任意静态图产生"靠近"的感觉，与图片内容无关。Live2D 接入时，只需将 CSS class 换成 Live2D motion API 调用，接口不变。

## 1. 目标（v1 边界内）

当 `controlSystemMediaPlayback(action)` 被调用时（无论是模型 tool call 触发还是未来 CC 发起），：

1. **先播动画**：`.stage` 加上 `is-reaching` class → `.pet-hitbox img` 播 600ms 向右上靠近的 CSS 动画
2. **动画峰值时发命令**：600ms 后才调用 Tauri `control_system_media`
3. **1200ms 后动画复位**：class 移除，`img` 回到 idle 状态
4. **新增 `"smtcAction"` settings command**：控制中心未来可直接触发，经过同一套动画+SMTC 路径

兑现后用户感知：不再是"突然切了歌"，而是"她朝播放器方向动了一下，然后歌切了"。

## 2. 不做（避免范围蔓延）

| 不做 | 留给谁 |
|------|------|
| 控制中心新增"让她帮我切歌"按钮 | 待 UI 重构后再评估 |
| Live2D motion API 接入 | 等 Live2D 接入时换实现 |
| 不同 action（next/prev/pause）播不同动画 | v2+ |
| 动画期间 hitbox 禁用点击 | 1200ms 很短，v1 不做 |
| ASR / 语音触发路径 | 已通过 `applyPayloadActivity` 进同一函数，自然覆盖 |
| "她的心情"叙述 / 反向请求 | T6 |

## 3. 现有可复用模块

| 模块 | 路径 | 用途 |
|------|------|------|
| SMTC 入口 | `desktop_pet_next/src/main.js:controlSystemMediaPlayback` | 在这里加 `await triggerPetReachGesture()` |
| Motion 系统 | `desktop_pet_next/src/styles.css` — `data-motion` + `@keyframes` | 参考 `pet-speaking-bob`、`pet-thinking-focus` 写新 keyframe |
| Settings command bridge | `main.js:handleSettingsCommand` | 加 `"smtcAction"` case |
| 前端合约测试 | `tests/test_desktop_pet_frontend_contract.py` | 加字符串断言，验证关键函数和 class 存在 |

## 4. 不新建文件

本 ticket 只改现有文件，不创建新 Python 模块（无后端改动）。

## 5. 改动现有文件

| 文件 | 改什么 |
|------|------|
| `desktop_pet_next/src/styles.css` | 在现有 motion keyframe 区块后追加 `@keyframes pet-reach` + `.stage.is-reaching .pet-hitbox img` |
| `desktop_pet_next/src/main.js` | 新增 `triggerPetReachGesture()`；`controlSystemMediaPlayback` 内 `await triggerPetReachGesture()`；`handleSettingsCommand` 加 `"smtcAction"` case |
| `tests/test_desktop_pet_frontend_contract.py` | 加一个测试类，断言上面的关键 symbol 和 class 存在 |

## 6. 关键契约

### 6.1 CSS（追加到 `styles.css` 末尾，在现有 motion 规则之后）

```css
@keyframes pet-reach {
  0%   { transform: translate(0px, 0px)   scale(1.00); filter: brightness(1.00); }
  45%  { transform: translate(9px, -5px)  scale(1.05); filter: brightness(1.10); }
  55%  { transform: translate(10px, -6px) scale(1.06); filter: brightness(1.12); }
  100% { transform: translate(0px, 0px)   scale(1.00); filter: brightness(1.00); }
}

/* 放在文件末尾，通过 source-order 覆盖 data-motion 的 animation，不用 !important */
.stage.is-reaching .pet-hitbox img {
  animation: pet-reach 1200ms cubic-bezier(0.22, 1, 0.36, 1) both;
  pointer-events: none;
}
```

**为什么不用 `!important`**：把这两条放在文件末尾，specificity 相同时 source order 优先，CSS 动画自然覆盖 `data-motion` 的状态。

**为什么不走 `visualRenderer.setMotion("reaching")`**：`VALID_MOTIONS` 只认 4 个值，改动影响面大；`is-reaching` class 是正交的一次性覆盖，不需要纳入 motion 状态机。

### 6.2 `triggerPetReachGesture()` — 新增到 `main.js`

```js
/**
 * 播"伸手"CSS 动画并在峰值（600ms）时 resolve。
 * 1200ms 后 class 自动移除。
 * Live2D 接入时：用 Live2D motion API 替换 classList 操作，保持 Promise 接口不变。
 */
function triggerPetReachGesture() {
  const PEAK_MS = 600;
  const TOTAL_MS = 1200;
  return new Promise((resolve) => {
    const stage = els.stage;
    if (!stage) {
      resolve();
      return;
    }
    // 强制 reflow，让同一帧内连续触发也能重新播放
    stage.classList.remove("is-reaching");
    void stage.offsetWidth;
    stage.classList.add("is-reaching");
    window.setTimeout(resolve, PEAK_MS);
    window.setTimeout(() => stage.classList.remove("is-reaching"), TOTAL_MS);
  });
}
```

**放置位置**：紧接在 `controlSystemMediaPlayback` 函数定义之前。

### 6.3 修改 `controlSystemMediaPlayback(action)` — 加 `await triggerPetReachGesture()`

```js
async function controlSystemMediaPlayback(action) {
  if (!isTauriRuntime) {
    notifyMusicActivityUnavailable("系统媒体控制只在桌面端可用。");
    return false;
  }
  const normalized = normalizeSystemMediaControlAction(action);
  if (!["play", "pause", "stop", "next", "previous"].includes(normalized)) return false;

  await triggerPetReachGesture();   // ← 新增：动画峰值后再发命令

  const result = await tauriCall("control_system_media", { action: normalized }, { quiet: true });
  // ... 以下全部不变 ...
}
```

唯一改动：在 `tauriCall` 前插入 `await triggerPetReachGesture()`。函数签名、返回值、错误处理全部不变。

### 6.4 `handleSettingsCommand` 新增 case

在现有 switch 末尾（`default:` 之前）加：

```js
case "smtcAction":
  if (payload.action) {
    void controlSystemMediaPlayback(payload.action);
  }
  break;
```

**作用**：控制中心发 `{command: "smtcAction", action: "next"}` → pet window 走完整的动画+SMTC 路径。v1 的控制中心不新增按钮，这条 case 只做基础设施，为未来留口。

## 7. 测试矩阵

| # | 测试 | 方式 |
|---|------|------|
| T5-01 | `triggerPetReachGesture` 函数名存在于 main.js | `test_desktop_pet_frontend_contract.py` 字符串断言 |
| T5-02 | `controlSystemMediaPlayback` 调用 `triggerPetReachGesture` | 字符串断言（`assertIn("await triggerPetReachGesture()", source)`） |
| T5-03 | `is-reaching` class 存在于 styles.css | 字符串断言 |
| T5-04 | `pet-reach` keyframe 存在于 styles.css | 字符串断言 |
| T5-05 | `"smtcAction"` case 存在于 main.js | 字符串断言 |
| T5-06 | `controlSystemMediaPlayback` 在无 Tauri 时直接返回（不崩） | 手动 / 或字符串断言 `notifyMusicActivityUnavailable` 在 `triggerPetReachGesture` 之前 |

## 8. 验证命令

```bash
# 语法检查
python -m py_compile tests/test_desktop_pet_frontend_contract.py

# 跑测试
python -m unittest tests.test_desktop_pet_frontend_contract -v

# 目测 diff 是否只动了 3 个文件
git diff --stat HEAD
```

手动验证（如果有桌宠运行环境）：
1. 确认系统有音乐在播放（QQ Music / Spotify 等）
2. 对 Akane 说"帮我切下一首"
3. 观察：立绘是否向右上偏移后回弹，然后歌才切

## 9. 实施顺序

1. **先写 CSS**（`styles.css` 末尾追加 keyframe + class）
2. **写 `triggerPetReachGesture()`**，放在 `controlSystemMediaPlayback` 前
3. **改 `controlSystemMediaPlayback`**，加 `await triggerPetReachGesture()`
4. **改 `handleSettingsCommand`**，加 `"smtcAction"` case
5. **写测试**，断言 5 个字符串存在
6. **跑测试**，全绿
7. **目测 diff**，确认只改 3 个文件

## 10. Tripwire 自检

完成后回答这 3 个问题，全部通过才算落地：

- [ ] `triggerPetReachGesture()` 调用在 `tauriCall("control_system_media", ...)` 之前，而不是之后
- [ ] CSS 里的 `.stage.is-reaching .pet-hitbox img` 规则在所有 `[data-motion="..."] .pet-hitbox img` 规则之后（source order 覆盖）
- [ ] `notifyMusicActivityUnavailable` / 早退路径发生在 `triggerPetReachGesture()` 调用之前（没有 Tauri 时不播动画）

---

## 附：给小模型的执行提示词（可直接 copy-paste）

```
你是 AkaneCompanionLab 项目的实施工程师。请阅读并完整执行以下 ticket，不做任何超出范围的改动。

ticket 路径：docs/listening_together_demo_v1_t5_ticket.md

背景：
- desktop_pet_next/src/main.js —— 桌宠 Tauri 前端，已有 controlSystemMediaPlayback(action) 函数（第 4581 行附近）和 handleSettingsCommand switch（第 1009 行附近）
- desktop_pet_next/src/styles.css —— 已有 pet-idle-breathe / pet-thinking-focus / pet-speaking-bob 等 keyframe；现有 motion 规则用 .stage[data-motion="..."] 选择器
- tests/test_desktop_pet_frontend_contract.py —— 用 Python unittest + 字符串断言检查 JS 源码

实施顺序严格按 ticket §9：
1. styles.css 末尾追加 CSS（§6.1）
2. main.js 写 triggerPetReachGesture()（§6.2），放在 controlSystemMediaPlayback 定义之前
3. main.js 修改 controlSystemMediaPlayback（§6.3），仅加一行 await
4. main.js handleSettingsCommand 加 "smtcAction" case（§6.4）
5. tests/test_desktop_pet_frontend_contract.py 加测试类，断言 §7 中全部 5 个 symbol/class 存在
6. 跑 python -m unittest tests.test_desktop_pet_frontend_contract -v，全绿
7. 跑 git diff --stat HEAD，确认只动了 3 个文件

自检（§10 tripwire）全部通过后汇报：
- 改了什么（每个文件的行号范围）
- 验证结果
- 没做什么
```
