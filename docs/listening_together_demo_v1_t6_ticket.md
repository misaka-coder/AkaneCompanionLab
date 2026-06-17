# Listening Together Demo v1 — T6 实施 Ticket（她的心情叙述 + 反向请求能力注入）

Updated: 2026-06-17
Parent design: `docs/listening_together_demo_v1.md`
Status: ready for small-model implementation
Preceding work: T1–T5 均已落地。T5 加了"伸手"动画；T4 的权限开关中已有 `recommend` 控制位。

## 0. 这份 ticket 的位置

设计稿 §5.3 把"我们的共听"卡片定为 4 节：

| 节 | 状态 |
|----|------|
| 现在听的 | ✅ T3 落地 |
| 她的心情 | ⬛ **本 ticket（T6A）** |
| 我们的共听 | ✅ T3 落地 |
| 让她也能 | ✅ T4 落地 |

本 ticket 分两件事：

**T6A（前端纯改动）**：在"我们的共听"卡片的"现在听的"区块内，加一行她当前心情的友好描述。数据来自已在 snapshot 中的 `currentExpression.name`，不需要任何后端改动。

**T6B（后端小改动）**：当 `recommend` 权限开启且音乐上下文存在时，`build_co_listen_memory_prompt` 输出一条明确的能力正向提示，让模型知道它主动提议推荐是被允许且被鼓励的——而不是只在权限被关闭时才说"你不能推荐"。

## 1. 目标（v1 边界内）

### T6A — 心情一行

**表现目标**：用户在控制中心打开"我们的共听"卡片，在当前歌曲信息下看到一行简短的心情描述，例如：

```
晴天  ·  周杰伦  ·  QQ音乐
已经一起听过 3 次，上次是 2 天前。
她好像挺开心的～
```

**实现方式**：
- `latestRuntimeSnapshot.currentExpression.name` 已在 snapshot 中（见 `buildCurrentExpressionSnapshot()`），不需要任何后端改动
- 新增 `moodPhraseFromEmotion(name)` 函数，将情绪 ID 映射到中文短语
- 修改 `renderListeningTogetherCard()`，在 `nowBlock` 的 story text 之后追加心情行
- 仅在 `hasNow && latestRuntimeSnapshot?.currentExpression?.name` 有值时输出

### T6B — 推荐提议能力注入

**表现目标**：模型收到 prompt 后，知道当 `recommend` 权限开启时，它**可以主动说**"要我推荐一首吗？"或者"换首歌？"——不只是等用户来问。

**实现方式**：
- 修改 `companion_v01/music_context.py:build_co_listen_memory_prompt()`
- 在所有控制都开着（默认状态）时，仍然在"共听记忆"block 末尾追加一行能力正向提示
- 在任何控制被关时，维持现有的"她当前被限制了…"逻辑

**不做 UI**：反向请求（她说"要我推荐吗？"）的载体是她的气泡 + TTS，这已经是现有路径，不需要新的 UI 组件。

## 2. 不做（避免范围蔓延）

| 不做 | 留给谁 |
|------|------|
| 情绪来源改为"从 LLM 输出实时解析文本情感" | v2+ |
| 控制中心新增"她说的话"实时显示区域 | 待 UI 重构 |
| "要我推荐吗？"对应的一键确认按钮 | 口头回复即可，不做 UI |
| 不同 action 对应不同心情显示（如暂停→"她有点不舍"） | v2+ |
| 情绪从桌宠窗口 push 到控制中心（WebSocket/事件） | snapshot poll 已够 |

## 3. 现有可复用模块

| 模块 | 路径 | 用途 |
|------|------|------|
| currentExpression | `main.js:buildCurrentExpressionSnapshot()` | 已有；包含 `.name` |
| 控制中心 snapshot | `control-center-lab.js:latestRuntimeSnapshot` | 直接读 `.currentExpression.name` |
| 控制中心卡片 | `control-center-lab.js:renderListeningTogetherCard()` | 在 `nowBlock` 末追加心情行 |
| co-listen prompt 构建 | `companion_v01/music_context.py:build_co_listen_memory_prompt()` | 追加推荐提示行 |
| 已有测试 | `tests/test_music_context.py:CoListenPromptTests` | 加断言 |

## 4. 不新建文件

只改现有文件。

## 5. 改动现有文件

| 文件 | 改什么 |
|------|------|
| `desktop_pet_next/src/control-center-lab.js` | 新增 `moodPhraseFromEmotion(name)` 函数；`renderListeningTogetherCard` 的 `nowBlock` 追加心情行 |
| `companion_v01/music_context.py` | `build_co_listen_memory_prompt()` 追加推荐能力正向提示 |
| `tests/test_music_context.py` | `CoListenPromptTests` 加 2 个测试 |

## 6. 关键契约

### 6.1 `moodPhraseFromEmotion(name)` — 新增到 `control-center-lab.js`

放在 `friendlyMusicSourceLabel` 附近（同为 render 辅助函数）：

```js
function moodPhraseFromEmotion(name) {
  const n = String(name || "").toLowerCase().trim();
  const MAP = {
    "开心": "她好像挺开心的～",
    "高兴": "她好像挺高兴的～",
    "兴奋": "她好像很兴奋",
    "开朗": "她心情看起来不错",
    "温柔": "她好像很温柔",
    "平静": "她安静地在听",
    "默然": "她安静地在听",
    "思考": "她好像在想什么",
    "沉思": "她好像在想什么",
    "好奇": "她好像很好奇",
    "害羞": "她有点害羞",
    "难过": "她好像有点难过",
    "委屈": "她好像有点委屈",
    "无聊": "她好像有点无聊",
  };
  return MAP[n] || "";
}
```

**设计说明**：
- 返回空字符串时不渲染，不影响卡片布局
- 不 hardcode 所有可能情绪；未知情绪优雅退化（不显示）
- 用户的角色包可能有自定义情绪 ID，不匹配就静默跳过

### 6.2 `renderListeningTogetherCard()` 的 `nowBlock` 改动

在 `nowBlock` 组装时，在 `storyText` 之后追加心情行：

```js
// 现有：
const storyText = ...;
// 新增：
const emotionName = String(latestRuntimeSnapshot?.currentExpression?.name || "").trim();
const moodLine = moodPhraseFromEmotion(emotionName);

// 在 nowBlock 模板里，storyText 之后：
${storyText ? `<p>${escapeHtml(storyText)}</p>` : ""}
${moodLine ? `<p class="mood-line">${escapeHtml(moodLine)}</p>` : ""}
```

`mood-line` class 可以在 `control-center-lab.css` 追加极简样式，或者不追加（默认 `<p>` 样式已够用）。

### 6.3 `build_co_listen_memory_prompt()` 追加推荐正向提示

当前逻辑（T4 落地的）：只在有权限被关闭时才输出"【她能做什么】"block。

T6B 新增：**无论是否有权限被关**，只要 `recommend` 在 `enabled_controls` 中，就在 block 末尾追加一条正向提示。

逻辑位置在现有 `build_co_listen_memory_prompt` 里，`enabled_controls` 已作为参数传入：

```python
# 在函数末尾，组装 lines 之后，return 之前：
if MusicControl.RECOMMEND in enabled_controls:
    lines.append("如果你有想法，可以主动提议推荐或换歌。")
```

**不要**重新设计整个函数；只追加这一条。如果 `enabled_controls` 为 None 或 RECOMMEND 不在其中，不追加。

### 6.4 prompt 输出示例（T6B 后）

当 `recommend` 开启、`next` 和 `prev` 也开着时（默认状态）：

```
【我们的共听记忆】
你们正在一起听：晴天（周杰伦）
这是第一次一起听这首。
如果你有想法，可以主动提议推荐或换歌。
```

当 `recommend` 被关闭，`next` 开着：

```
【我们的共听记忆】
你们正在一起听：晴天（周杰伦）
这是第一次一起听这首。
【她能做什么】
她当前被限制了部分操作：
- recommend：不可主动提议新歌
```

## 7. 测试矩阵

### T6A（前端 —— 合约字符串断言）

| # | 断言 |
|---|------|
| T6A-01 | `moodPhraseFromEmotion` 函数名存在于 control-center-lab.js |
| T6A-02 | `mood-line` class 存在于 renderListeningTogetherCard 的输出模板 |
| T6A-03 | `currentExpression` 被 renderListeningTogetherCard 引用 |

在 `tests/test_desktop_pet_frontend_contract.py` 新增测试类 `ListeningTogetherT6FrontendContractTests`（同文件末尾追加）。

### T6B（后端 —— `test_music_context.py`）

| # | 测试 | 方法 |
|---|------|------|
| T6B-01 | recommend 开启时 prompt 包含"主动提议" | `CoListenPromptTests` 加 `test_recommend_enabled_nudge` |
| T6B-02 | recommend 关闭时 prompt 包含"不可主动提议新歌" | 已有 T4 测试覆盖；可额外断言"主动提议"不出现在限制 block 之外 |

```python
def test_recommend_enabled_nudge(self):
    # 所有权限都开着（默认）
    result = build_co_listen_memory_prompt(
        context=...,   # 有 identity 的 MusicContext
        summary=...,   # CoListenSummary，co_listen_count=0
        recent=[],
        enabled_controls=frozenset({MusicControl.PAUSE, MusicControl.NEXT,
                                    MusicControl.PREV, MusicControl.RECOMMEND}),
    )
    self.assertIn("主动提议", result)

def test_recommend_disabled_no_nudge(self):
    # recommend 被关掉
    result = build_co_listen_memory_prompt(
        context=...,
        summary=...,
        recent=[],
        enabled_controls=frozenset({MusicControl.PAUSE, MusicControl.NEXT, MusicControl.PREV}),
    )
    # 不应该鼓励她主动推荐
    self.assertNotIn("可以主动提议推荐", result)
```

## 8. 验证命令

```bash
# 后端测试
python -m unittest tests.test_music_context -v

# 前端合约测试
python -m unittest tests.test_desktop_pet_frontend_contract -v

# 语法检查
python -m py_compile companion_v01/music_context.py
python -m py_compile tests/test_music_context.py
```

## 9. 实施顺序

1. **T6B 后端**：在 `music_context.py:build_co_listen_memory_prompt()` 末尾加 recommend 正向提示（§6.3）
2. **写 T6B 测试**（`test_music_context.py` 的 2 个新测试）
3. **跑后端测试**，全绿
4. **T6A 前端**：在 `control-center-lab.js` 加 `moodPhraseFromEmotion()`（§6.1）
5. **T6A 前端**：修改 `renderListeningTogetherCard()` 的 `nowBlock`（§6.2）
6. **写 T6A 合约测试**（`test_desktop_pet_frontend_contract.py` 末尾加新类）
7. **跑合约测试**，全绿
8. **目测 diff**，确认只动了 3 个文件（`music_context.py`、`control-center-lab.js`、2 个测试文件）

## 10. Tripwire 自检

完成后回答这 5 个问题，全部通过才算落地：

- [ ] `moodPhraseFromEmotion` 接收未知情绪名时返回 `""` 而不是报错（已在 MAP 末尾用 `|| ""` 保底）
- [ ] `renderListeningTogetherCard` 的心情行只在 `hasNow === true` 且 `moodLine` 非空时才渲染（不影响空态）
- [ ] `build_co_listen_memory_prompt` 的新 `assertIn("主动提议", result)` 测试通过，不是 mock
- [ ] `recommend` 关闭时，`assertNotIn("可以主动提议推荐", result)` 测试通过
- [ ] 没有修改 `engine.py`、`routes/capabilities.py`、`co_listen_store.py`（T6 不需要这些）

---

## 附：给小模型的执行提示词（可直接 copy-paste）

```
你是 AkaneCompanionLab 项目的实施工程师。请阅读并完整执行以下 ticket，不做任何超出范围的改动。

ticket 路径：docs/listening_together_demo_v1_t6_ticket.md

背景：
- companion_v01/music_context.py —— 已有 build_co_listen_memory_prompt() 函数，T4 加了"【她能做什么】"block（当有权限被关时才输出）；MusicControl 枚举已有 RECOMMEND 成员
- desktop_pet_next/src/control-center-lab.js —— 已有 renderListeningTogetherCard()，nowBlock 显示曲目信息和共听故事文字；latestRuntimeSnapshot 已有 .currentExpression.name
- tests/test_music_context.py —— CoListenPromptTests 类中已有若干 prompt 测试
- tests/test_desktop_pet_frontend_contract.py —— 已有若干字符串断言测试类

实施顺序严格按 ticket §9：
1. music_context.py：build_co_listen_memory_prompt 末尾，在 return 前加 recommend 正向提示（§6.3）
2. test_music_context.py：CoListenPromptTests 加 2 个测试（§7 T6B）
3. 跑 python -m unittest tests.test_music_context -v，全绿
4. control-center-lab.js：在 friendlyMusicSourceLabel 附近新增 moodPhraseFromEmotion()（§6.1）
5. control-center-lab.js：renderListeningTogetherCard 的 nowBlock 末尾加心情行（§6.2）
6. test_desktop_pet_frontend_contract.py：加 ListeningTogetherT6FrontendContractTests 类（§7 T6A）
7. 跑 python -m unittest tests.test_desktop_pet_frontend_contract -v，全绿
8. 目测 git diff --stat，确认只动了 4 个文件

自检（§10 tripwire）全部通过后汇报：
- 改了什么（每个文件的行号范围）
- 验证结果
- 没做什么
```
