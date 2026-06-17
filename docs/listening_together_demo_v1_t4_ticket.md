# Listening Together Demo v1 — T4 实施 Ticket（"让她也能"权限开关）

Updated: 2026-06-17
Parent design: `docs/listening_together_demo_v1.md`
Status: ready for small-model implementation
Preceding work: T1 + T2（MusicContext 装配 / 共听记忆 prompt）+ T3（control center "我们的共听"卡片）均已落地，详见 git log。

## 0. 这份 ticket 的位置

设计稿 §5.3 把"我们的共听"卡片定为 4 节：现在听的 / 她的心情 / 我们的共听 / **让她也能**。前 3 节已在 T1–T3 落地（"她的心情"暂留空，本 ticket 也不做）。

本 ticket 负责 **"让她也能"权限开关**这一节，含完整链路：

- 持久化：profile-scoped 开关状态
- 后端：GET / POST endpoint + 注入到 `MusicContext.enabled_music_controls` + 注入到 prompt
- 前端：卡片末尾加 4 个开关 + 点击调 endpoint + 即时反馈
- 测试：覆盖持久化默认值、endpoint 行为、prompt 输出、profile 隔离

## 1. 目标（v1 边界内）

让用户能在控制中心**显式撤回 / 授予**她对系统媒体播放器的 4 类主动控制权限：

| control | 含义 | 默认 |
|---|---|---|
| `pause` | 她能否主动 pause 当前播放 | 开 |
| `next` | 她能否主动 next（切下一首） | 开 |
| `prev` | 她能否主动 prev（切上一首） | 开 |
| `recommend` | 她能否主动**提议**新歌（不真切，只发气泡推荐） | 开 |

撤权后**两个表现层都要兑现**：

1. **UI 即时反馈**：开关点击 → 持久化 + 当前卡片状态更新（不刷新整页）
2. **后端 prompt 兑现**：下次 prompt 装配时，关掉的控制对应**自然语言提示**塞进 prompt 末尾。LLM 看到后必须只读，不能主动执行（这是设计稿 §6.3 "她必须只读的窗口"的接口）。

## 2. 不做（避免范围蔓延）

| 不做 | 留给谁 |
|---|---|
| 桌宠立绘伸手动画 / SMTC 控制时序 | T5（独立 ticket，更复杂） |
| "她的心情"叙述 | T6 |
| 自动撤权 / 自动恢复（如检测到她滥用→自动关切歌权） | v2+ |
| 角色包级 / 会话级权限（v1 仅 profile 级） | v2 |
| "她想要某个权限"反向请求（她说"我能帮你切吗"） | T6 后续 |
| 撤权审计日志 | v2 |

## 3. 现有可复用模块（必须复用，不要造轮子）

| 模块 | 路径 | 用途 |
|---|---|---|
| sqlite 模式 + connection | `companion_v01/co_listen_store.py` | 参考它的 schema / record / get 三件套写新表 |
| MusicContext 数据结构 | `companion_v01/music_context.py` | `MusicControl` 枚举已有；`MusicContext.enabled_music_controls` 字段已留位 |
| Assembler | `companion_v01/music_context.py:MusicContextAssembler` | 它的 `assemble(enabled_controls=...)` 参数已留位，**只需在调用处构造 frozenset 传入** |
| Co-listen summary route | `companion_v01/routes/capabilities.py:resolve_co_listen_summary` | 新 endpoint 同 pattern；同时把 `enabled_music_controls` 加进它的 response |
| Route helpers | `_resolve_identity_with_payload` / `_resolve_identity` / `_observe_request` / `_log_best_effort` | 直接用 |
| 控制中心卡片 | `desktop_pet_next/src/control-center-lab.js:renderListeningTogetherCard` | 在末尾加开关区块 |
| Card refresh hook | `desktop_pet_next/src/control-center-lab.js:refreshListeningTogetherCard` | endpoint 已经返回 controls，refresh 时一并更新 |

## 4. 新建文件

```
companion_v01/music_control_store.py
tests/test_music_control_store.py
tests/test_music_control_permissions_route.py
```

## 5. 改动现有文件

| 文件 | 改什么 |
|---|---|
| `companion_v01/music_context.py` | `MusicContextAssembler.__init__` 增加 `controls_provider: Callable[[str], frozenset[MusicControl]] \| None`；`assemble()` 内若 `enabled_controls` 入参为 None 且 provider 在，则调 provider 取；`build_co_listen_memory_prompt` 新增"【她能做什么】"段（仅当有权限被关时输出） |
| `companion_v01/engine.py` | `_get_music_context_assembler()` 在构造 Assembler 时传入 `controls_provider`，从 `music_control_store` 读 |
| `companion_v01/routes/capabilities.py` | 新增 `GET /capabilities/music/control_permissions` 和 `POST /capabilities/music/control_permissions`；同时 `resolve_co_listen_summary` 的 response 增加 `enabled_music_controls` 字段 |
| `desktop_pet_next/src/control-center-lab.js` | `renderListeningTogetherCard` 末尾加 "让她也能" 区块；新增 `toggleListeningTogetherControl(controlId, nextValue)` 调 POST endpoint；`listeningTogetherState` 新增 `controls` 字段；refresh fetch 解析 `enabled_music_controls` |

## 6. 关键契约

### 6.1 `music_control_store.py`

参照 `co_listen_store.py` 的 pattern（self-contained module，接受 sqlite3 connection）：

```python
ALLOWED_CONTROL_NAMES = frozenset({"pause", "next", "prev", "recommend"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS music_control_permissions (
    profile_user_id TEXT NOT NULL,
    control_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_user_id, control_name)
);
"""

def ensure_schema(connection) -> None: ...

def get_enabled_controls(
    connection, *, profile_user_id: str
) -> set[str]:
    """Return the set of currently-enabled control names.

    Default semantics: if a profile has NO row for a control, that control
    is treated as enabled. Only explicit `enabled=0` rows revoke. This
    avoids needing to pre-populate rows on first run.
    """

def set_control_enabled(
    connection, *, profile_user_id: str, control_name: str,
    enabled: bool, now_ts: int,
) -> None: ...

def bulk_set_controls(
    connection, *, profile_user_id: str, controls: dict[str, bool],
    now_ts: int,
) -> None:
    """Atomically apply a partial update; keys not in `controls` are untouched."""
```

**严格规则**：

- 拒绝写入不在 `ALLOWED_CONTROL_NAMES` 的 control_name（raise `ValueError`）
- 默认全开：未写入 = enabled
- `get_enabled_controls` 返回的是**已启用**集合（方便直接 `frozenset[MusicControl]` 转换）

### 6.2 `MusicContextAssembler` 改动

```python
class MusicContextAssembler:
    def __init__(
        self,
        *,
        store: Any,
        controls_provider: Callable[[str], frozenset[MusicControl]] | None = None,
        # ... existing kwargs unchanged
    ) -> None: ...
```

- 在 `assemble()` 中：`enabled_controls = enabled_controls if enabled_controls is not None else (controls_provider(profile_user_id) if controls_provider else <existing default>)`
- **默认值保持不变**（pause/next/prev/recommend 全开），保证旧调用方不破

### 6.3 `engine.py:_get_music_context_assembler()`

```python
def _get_music_context_assembler(self):
    # ... existing lazy-load logic
    from .music_context import MusicContextAssembler, MusicControl
    from . import music_control_store

    def _controls_provider(profile_user_id: str) -> frozenset:
        if not profile_user_id:
            return frozenset({MusicControl.PAUSE, MusicControl.NEXT,
                              MusicControl.PREV, MusicControl.RECOMMEND})
        try:
            with self.store._connect() as conn:
                music_control_store.ensure_schema(conn)
                names = music_control_store.get_enabled_controls(
                    conn, profile_user_id=profile_user_id
                )
        except Exception:
            names = {"pause", "next", "prev", "recommend"}
        return frozenset(MusicControl(name) for name in names if name in {c.value for c in MusicControl})

    assembler = MusicContextAssembler(store=store, controls_provider=_controls_provider)
    # ...
```

### 6.4 Prompt 注入（在 `music_context.py`）

`build_co_listen_memory_prompt` 末尾追加一段 **仅当至少一个 control 被关** 时输出：

```
【她现在能做什么】
- 主人这会儿没让你主动切下一首；想换的话先用气泡问一句，等他点了你再切。
- 主人这会儿没让你主动暂停；可以说"要不停一下？"，等他点了再动。
- ...
```

权限正向（全开）= **不输出**任何提示（避免噪音）。文案对应表：

| control 关闭 | 提示文案 |
|---|---|
| `pause` | "主人这会儿没让你主动暂停；想停的话先问一句，等他点了再动。" |
| `next` | "主人这会儿没让你主动切下一首；想换的话先用气泡提议，等他同意再切。" |
| `prev` | "主人这会儿没让你回到上一首；想回去的话先问一句。" |
| `recommend` | "主人这会儿没让你主动推歌；除非他先问，否则别甩推荐。" |

### 6.5 `summarize_listening_together()` 改动

response 新增 `enabled_music_controls` 字段（list[str]，按字典序），方便前端直接渲染：

```python
{
  "ok": True,
  "status": "ready",
  "now": {...},
  "recent": [...],
  "enabled_music_controls": ["next", "pause", "prev", "recommend"]  # 已启用的子集
}
```

### 6.6 新 endpoints

#### `GET /capabilities/music/control_permissions`

```http
GET /capabilities/music/control_permissions?user_id=...&real_user_id=...
→ {
  "ok": true,
  "status": "ready",
  "controls": {
    "pause": true,
    "next": true,
    "prev": true,
    "recommend": true
  }
}
```

response 总是返回 **全 4 个 control**（未写入 = true），方便前端不用关心"未知 control"。

#### `POST /capabilities/music/control_permissions`

```http
POST /capabilities/music/control_permissions?user_id=...&real_user_id=...
{
  "controls": { "next": false }
}
→ {
  "ok": true,
  "status": "ready",
  "controls": {
    "pause": true,
    "next": false,
    "prev": true,
    "recommend": true
  }
}
```

- 只更新 body 里给出的 keys，其余不动
- 未知 key 静默忽略（不要 400 — 否则前端 schema 漂移会炸）
- 非 bool 值（如 `null` / `"true"`）按 `bool(value)` 解释
- 通过 `asyncio.to_thread` 调 store（同 lyrics route）

### 6.7 前端 UI

`renderListeningTogetherCard` 末尾追加 "让她也能" 区块。**最小 UI，CSS 不雕琢**，跟现有 `.glass-card` 风格保持一致即可（用户 UI 会重构）。

```html
<div class="listening-permissions">
  <small>让她也能</small>
  <div class="permission-row">
    <button type="button"
            data-co-listen-control="pause"
            data-co-listen-enabled="true"
            aria-pressed="true">
      让她暂停 · 开
    </button>
    <button type="button"
            data-co-listen-control="next"
            data-co-listen-enabled="false"
            aria-pressed="false">
      让她切歌 · 关
    </button>
    ...
  </div>
</div>
```

- 按钮文案：`让她暂停` / `让她切歌` / `让她回上一首` / `让她推荐新歌`
- 状态后缀：`· 开` / `· 关`
- 默认全开

**事件绑定**：参考 control-center-lab.js 现有的 `data-action-id` event delegation 模式。或者用 root.addEventListener("click", ...) 监听 `data-co-listen-control` dataset。

```js
async function toggleListeningTogetherControl(controlName, nextEnabled) {
  // 立刻更新本地 state（乐观更新），renderActivePage 重绘
  // POST 到 /capabilities/music/control_permissions
  // 失败 → 回滚 + 错误状态文案（同 listeningTogetherState.message 字段）
}
```

`listeningTogetherState` 增加 `controls` 字段：

```js
let listeningTogetherState = {
  status: "idle",
  now: null,
  recent: [],
  controls: { pause: true, next: true, prev: true, recommend: true },
  message: "",
  lastFetchTrackKey: "",
  lastFetchAt: 0
};
```

`refreshListeningTogetherCard` 解析 response 中 `enabled_music_controls`（list），转回 `controls` dict：

```js
const enabledList = Array.isArray(payload.enabled_music_controls) ? payload.enabled_music_controls : null;
const controls = enabledList
  ? {
      pause: enabledList.includes("pause"),
      next: enabledList.includes("next"),
      prev: enabledList.includes("prev"),
      recommend: enabledList.includes("recommend")
    }
  : listeningTogetherState.controls;
```

## 7. 测试要点

### 7.1 `tests/test_music_control_store.py`（参照 `tests/test_music_context.py:CoListenStoreTests`）

| 测试 | 验收 |
|---|---|
| 默认全开（空表查询） | `get_enabled_controls(...)` 返回 `{"pause","next","prev","recommend"}` |
| 单项关闭 | `set_control_enabled(..., "next", False)` 后 next 不在返回集 |
| 单项再开 | 关→再开后 next 回到返回集 |
| Bulk 部分更新 | `bulk_set_controls(..., {"next": False})` 只关 next，其余不动 |
| 非法 control 名 | `set_control_enabled(..., "wipe_disk", False)` 抛 ValueError |
| Profile 隔离 | alice 关 next 不影响 bob |

### 7.2 `tests/test_music_control_permissions_route.py`（参照 `tests/test_co_listen_route.py`，用 `_InMemoryStore` 同方案 + threading.Lock + check_same_thread=False）

| 测试 | 验收 |
|---|---|
| GET 默认全开 | 空表 → response.controls 全 4 个 true |
| POST 单项关 + GET 反映 | POST next: false → GET next == false |
| POST 部分更新不影响其它 | POST 只关 next → pause/prev/recommend 仍 true |
| POST 未知 key 静默忽略 | POST `{"controls": {"wipe": true}}` 不报错，不写入 |
| POST 非 bool 容错 | POST `{"controls": {"next": "yes"}}` 按 truthy 处理 |
| Profile 隔离 | alice POST 不影响 bob GET |

### 7.3 `tests/test_co_listen_route.py` 扩展

| 测试 | 验收 |
|---|---|
| co_listen_summary response 含 `enabled_music_controls` | 字段存在 + 为 list |
| 撤权后 co_listen_summary 反映 | POST control_permissions 关 next 后 GET co_listen_summary 的 `enabled_music_controls` 不含 "next" |

### 7.4 `tests/test_music_context.py` 扩展

| 测试 | 验收 |
|---|---|
| `controls_provider` 被传入时优先于默认值 | provider 返回 `{pause}` → assemble().enabled_music_controls 只含 PAUSE |
| 撤权后 prompt 含提示句 | `enabled_music_controls = {pause}` → `build_co_listen_memory_prompt` 输出含 "没让你主动切下一首" / "没让你回到上一首" / "没让你主动推歌" |
| 全开时 prompt 不输出权限段 | `enabled_music_controls = {pause,next,prev,recommend}` → 不出现 "她现在能做什么" 标签 |

## 8. 验证命令

```powershell
python -m unittest tests.test_music_control_store
python -m unittest tests.test_music_control_permissions_route
python -m unittest tests.test_co_listen_route
python -m unittest tests.test_music_context
python -m unittest tests.test_backend_route_modules
python -m unittest tests.test_desktop_activity_runtime_contract
python -m unittest tests.test_desktop_pet_frontend_contract

cd desktop_pet_next
npm run build
npm run verify:control-center

git diff --check
git diff --stat
```

**所有测试必须全绿才能算完成**。

## 9. 出戏 tripwire（设计稿 §9 对齐，本 ticket 直接相关）

施工完成前自查：

| Tripwire | 严重 | 自查方式 |
|---|---|---|
| UI 切换开关无视觉反馈 | 🟠 | 关一个开关 → 按钮文案立刻从 `开` 变 `关`，不等 fetch 回来 |
| 切换无持久化（重启后丢） | 🔴 | 关一个 → 重启控制中心 → GET 仍是关 |
| 撤权后 prompt 没体现 | 🔴 | unit test 验证（§7.4） |
| 未知 control 名让 endpoint 500 | 🟠 | unit test 验证（§7.2） |
| 默认值 hardcode 在多处导致漂移 | 🟠 | 默认值**只在 `ALLOWED_CONTROL_NAMES` 一处定义**；前端 / route / store 都引用它（前端 mirror 一份 list） |

## 10. 实施顺序建议

按依赖顺序施工，每一小步跑测试：

1. **建 store** — `music_control_store.py` + `test_music_control_store.py` → 跑测试
2. **接 assembler** — `MusicContextAssembler.controls_provider` + 测试扩展 → 跑测试
3. **接 prompt** — `build_co_listen_memory_prompt` 加权限段 + 测试扩展 → 跑测试
4. **加 endpoints** — GET/POST + 路由测试 → 跑测试
5. **co_listen_summary 加字段** + 测试扩展 → 跑测试
6. **engine 注入 provider** → 跑全套 backend 测试
7. **前端卡片加开关** → npm build / verify
8. **联调** → 跑所有验证命令

**每一步都不要破坏前一步**。如果某一步发现需要改前一步的契约，停下来想清楚再改，不要硬修。

## 11. 给小模型的提醒

- **不要顺手重构其它模块**。本 ticket 范围严格限定在上述清单。
- **不要把默认值"硬编码到 if 分支里"**。所有默认值集中在 `ALLOWED_CONTROL_NAMES` + `_DEFAULT_ENABLED_CONTROLS`（如果需要）。
- **不要为 endpoint 加 auth/限流**。沿用现有路由的 identity resolver 即可。
- **不要新增依赖**。所有改动用 stdlib + 现有第三方包。
- **CSS 不雕琢**。用户 UI 会重构，本 ticket 的卡片样式跟现有 `.glass-card` 一致即可。
- **commit 由用户决定**。完成所有改动后停下，等待用户审查 + 提交。
- **遇到设计稿没说清的边界**：默认走"撤权 = 完全只读"，不要自作主张允许"她可以重试一次"之类。

## 12. 完成后输出

完成本 ticket 后向用户报告以下信息：

1. 改了哪些文件（含行数变化）
2. 验收命令的实际输出（全绿截图 / 摘要）
3. 任何**绕过 ticket 约束**的地方（如必须新加依赖、必须改约束外文件）—— 必须明说，不要默默改
4. 用户在桌宠里能感觉到的变化（开关位置、点击效果、撤权后她下一轮的语气变化）
5. 还**没做**的：T5（桌宠伸手动作 + SMTC 时序）、T6（她的心情叙述 + 反向请求）、本地音乐路径接入 MusicContext —— 这些不属于 T4，**别顺手做**

---

**核心一句**：本 ticket 是把设计稿 §6.3 "她必须只读的窗口" 从产品语言落到代码——用户撤权 = 后端 prompt 兑现 + 前端开关持久化。**完成后用户应该能在控制中心点一下"让她切歌：关"，下次 Akane 在主动想切歌时会改用提议口吻而非动手切**。
