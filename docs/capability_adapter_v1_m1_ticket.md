# Capability Adapter v1 — M1 实施 Ticket（骨架）

Updated: 2026-06-16
Parent design: `docs/capability_adapter_v1.md`
Scope: Milestone 1（仅骨架，不解锁任何用户功能）

## 目标

让 manifest 文件能被加载、校验、聚合成内存中的 registry，但**没有任何 adapter 真的被调用**。M1 完成后：

- `companion_v01/builtin_capability_manifests/` 和 profile manifest 目录都能扫描
- manifest 校验严格按设计 §3.3 落地，invalid manifest 单点降级
- 风险自动提升（设计 §6.3.1）按 effects 字段执行
- `CapabilityAdapter` Protocol 定义清楚，但暂无具体实现
- 单元测试覆盖加载、校验、降级、自动提升

**M1 不做**：HTTP/IPC 调用、adapter 实例化、prompt 注入、UI 显示、热重载、capability_registry 接入。这些在 M2 起做。

## 现有可复用模块

避免重复造轮子，M1 必须复用：

| 现有 | 路径 | 用途 |
|---|---|---|
| 数据根目录解析 | `akane_paths.get_akane_data_paths()` | profile manifest 目录定位 |
| 审批模式映射 | `local_capability_config.capability_approval_mode()` | risk → approval mode |
| Loopback 白名单 | `local_capability_config.LOOPBACK_HOSTS` | endpoint.loopback_only 校验 |
| 密钥关键字 | `local_capability_config.MCP_SECRET_MARKERS` | secrets 明文检测 |
| 安全 type 正则 | `local_capability_config.MCP_SAFE_TYPE_RE` | provider.type 字符校验 |
| 审批模式常量 | `local_capability_config.APPROVAL_MODE_*` | 不重定义 |

**预检**：动手前确认 `requirements.txt` 是否含 PyYAML（grep `^yaml` / `PyYAML`）。若否，在 ticket 实施时同步加上；不要私自换 ruamel 等其它库。

## 新建文件

```
companion_v01/capability_adapters/
├── __init__.py                # 重新导出 public 接口
├── types.py                   # dataclasses + 异常类
├── protocol.py                # CapabilityAdapter Protocol
├── manifest_loader.py         # YAML → CapabilityManifest（含校验、风险提升）
└── registry.py                # CapabilityAdapterRegistry

companion_v01/builtin_capability_manifests/
└── .gitkeep                   # 占位，M1 不放真 manifest

tests/
├── test_capability_adapter_manifest_loader.py
└── test_capability_adapter_registry.py

docs/fixtures/capability_adapter_m1/
├── valid_comfyui.yaml         # 完整有效 manifest 样例
├── invalid_missing_id.yaml    # provider.id 缺失
├── invalid_bad_type.yaml      # provider.type 不在 allowlist
├── invalid_non_loopback.yaml  # loopback_only=true 但 url 非 loopback
├── invalid_plaintext_secret.yaml  # secrets 含明文 token
├── effects_high.yaml          # effects 含 command_exec，验证自动提升 high
└── effects_medium.yaml        # effects 含 file_write，验证 medium 兜底
```

## 现有文件改动

**只改一个文件**：

- `companion_v01/engine.py`
  - 在 `AkaneMemoryEngine.__init__` 末尾添加：
    ```python
    self.capability_adapter_registry = CapabilityAdapterRegistry(
        builtin_dir=Path(__file__).parent / "builtin_capability_manifests",
        profile_dir_provider=self._resolve_profile_capability_manifests_dir,
    )
    self.capability_adapter_registry.scan()
    ```
  - 添加 `_resolve_profile_capability_manifests_dir(self) -> Path` 方法：从 `akane_paths.get_akane_data_paths()` + `profile_user_id` 拼出 `users_data/<profile>/capability_manifests/`
  - **不要**注册到任何路由、不要注入到任何 prompt、不要给 tool_orchestration 调用——只是让它在引擎里活着

`app.py` / `routes/*.py` / `tool_runtime.py` / `capability_registry.py` 在 M1 **不动**。

## 关键契约

### `types.py` 数据结构

```python
@dataclass(frozen=True)
class CapabilityManifest:
    schema: str                          # "capability_adapter/v1"
    provider_id: str
    provider_type: str
    display_name: str
    endpoint: EndpointConfig | None
    health: HealthConfig | None
    tiers: tuple[TierConfig, ...]
    capabilities: tuple[CapabilityDescriptor, ...]
    secrets: tuple[str, ...]             # 只存 key 名，不存值
    source_path: Path                    # 调试/UI 用
    source_layer: Literal["builtin", "profile"]
    raw: Mapping[str, Any]               # type-specific 字段透传给 adapter

@dataclass(frozen=True)
class CapabilityDescriptor:
    id: str
    display_name: str
    short_hint: str
    visible_in: tuple[str, ...]          # {base, web, desktop, qq}
    prompt_exposed: bool                 # 默认 False
    risk: Literal["low", "medium", "high"]
    confirm: Literal["never", "first_time", "always"]
    effects: tuple[str, ...]
    trigger: TriggerConfig | None
    inputs: tuple[CapabilityIOSlot, ...]
    outputs: tuple[CapabilityIOSlot, ...]
    raw: Mapping[str, Any]

class CapabilityManifestError(ValueError):
    """单 manifest 校验失败，标 invalid_config 但不阻塞其它。"""

class CapabilityProtocolError(RuntimeError):
    """M2 起 adapter.invoke 协议级错误用。M1 仅定义不抛。"""
```

### `manifest_loader.py` 行为

`load_manifest(path: Path, *, source_layer: str) -> CapabilityManifest | InvalidManifest`：

1. 读 yaml；任何 yaml 解析错 → 返回 `InvalidManifest(path, reason="yaml_parse_error", detail=...)`，**不抛**
2. 按设计 §3.3 应用 9 条校验；任一失败 → 返回 `InvalidManifest(path, reason="<rule>", detail=...)`
3. 应用风险自动提升（§6.3.1）：
   - 含 `command_exec` 或 `browser_action` → 强制 `risk: high`，`confirm: always`
   - 否则含 `file_write` / `network_outbound` → 提升至少 `medium`
   - 全空 / 仅 `file_read|media_generation` → 保留作者声明
4. 对每个 capability，若 `prompt_exposed` 缺失，默认 `False`
5. 校验 secrets：每个 secrets 项必须是字符串 key 名（不是 `{key: value}` mapping，不允许像 token / api_key / password / secret 这类名字直接出现值）
6. 校验 endpoint.loopback_only：若为 true 且 url host ∉ `LOOPBACK_HOSTS` → invalid

**重要**：所有校验失败都返回 `InvalidManifest`，**不抛异常**。registry 用它构建 invalid_config 卡片。

### `registry.py` 行为

`CapabilityAdapterRegistry`：

- `__init__(builtin_dir: Path, profile_dir_provider: Callable[[], Path])`
- `scan() -> None`：扫描两个目录所有 `*.yaml`，调 `load_manifest`，按 source_layer 优先级（profile > builtin）合并；记录 invalid 列表
- `list_manifests() -> tuple[CapabilityManifest, ...]`：返回所有有效 manifest
- `list_invalid() -> tuple[InvalidManifest, ...]`：返回所有 invalid 报告
- `get(provider_id: str) -> CapabilityManifest | None`
- `reload(provider_id: str) -> None`：单 provider 重扫（M5 起用，M1 占位实现即可）

**关键**：scan 必须能在内置目录为空时正常返回空列表，不抛。

### `protocol.py`

```python
from typing import Protocol, ClassVar, Mapping, Any

class CapabilityAdapter(Protocol):
    type: ClassVar[str]
    provider_id: str

    async def health(self) -> "HealthStatus": ...
    async def list_capabilities(self) -> "tuple[CapabilityDescriptor, ...]": ...
    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: "InvocationContext",
    ) -> "CapabilityResult": ...
    async def aclose(self) -> None: ...
```

M1 不实现任何具体 adapter；registry 也不实例化 adapter。protocol.py 只是给 M2 用的契约。

## Adapter type allowlist（M1 锁定为 v1 全集）

```python
ALLOWED_ADAPTER_TYPES = frozenset({
    "mcp_stdio",
    "comfyui",
    "openai_compat_tts",
    "openai_compat_asr",
    "python_plugin",
})
```

manifest 声明 `provider.type` 不在 allowlist → invalid。

## 测试要点

`test_capability_adapter_manifest_loader.py`（每条对应一个 fixture）：

1. `valid_comfyui.yaml` 解析成功，所有字段就位
2. `invalid_missing_id.yaml` → InvalidManifest, reason="missing_provider_id"
3. `invalid_bad_type.yaml` → reason="provider_type_not_allowed"
4. `invalid_non_loopback.yaml` → reason="endpoint_not_loopback"
5. `invalid_plaintext_secret.yaml` → reason="secrets_must_be_key_names"
6. `effects_high.yaml` 含 `command_exec` → 加载后 risk 被强制提升为 high，confirm=always
7. `effects_medium.yaml` 含 `file_write` 但作者声明 low → 加载后 risk 提升为 medium
8. capability 缺 `prompt_exposed` → 解析后默认 false
9. capability 缺 `risk` → 默认 medium + confirm: first_time
10. yaml 解析错（故意写一个语法坏掉的 fixture）→ InvalidManifest 不抛

`test_capability_adapter_registry.py`：

1. 空目录扫描返回空，不抛
2. 仅 builtin 有 manifest，profile 目录不存在 → 仍能加载 builtin
3. 同 provider_id 同时存在 builtin + profile → list_manifests 只出现 profile 版本
4. 一份 manifest 坏掉不影响其它正常加载
5. `get(provider_id)` 找不到时返回 None，不抛

## 验证命令

```powershell
python -m unittest tests.test_capability_adapter_manifest_loader
python -m unittest tests.test_capability_adapter_registry
python -m unittest tests.quick_regression_suite   # 确认没破坏既有 30 个路由测试
git diff --check
```

期望：新增测试全过，quick_regression 不退步。

## M1 期间的本地决议（实施时如有冲突按此判定）

1. **PyYAML 已经存在还是要新增**：若 `requirements.txt` 已有，直接 `import yaml`；若无，加入并写明"M1 引入 PyYAML 作为运行时依赖"。不引入 ruamel/strictyaml。
2. **InvalidManifest 是 dataclass 还是异常**：用 dataclass，不让它继承 Exception。理由：单点降级要"返回"不要"抛"，避免 try/except 满天飞。
3. **registry 是 engine 持有还是模块级单例**：engine 持有。沿用现有"AkaneMemoryEngine 拥有所有 service"风格。
4. **profile_dir_provider 接收 profile_user_id 还是无参**：无参，由 provider 闭包从 engine 拿当前 profile。这样 reload 不用关心多 profile 切换。
5. **manifest 字段访问大小写**：YAML 用 snake_case（`provider_id`、`prompt_exposed`、`visible_in`），不用 camelCase。和现有 `capabilities.yaml` 的 camelCase 不同，但 adapter manifest 是新的 schema，可以重新选；snake_case 跟 Python 端更顺。
6. **测试 fixtures 位置**：放 `docs/fixtures/capability_adapter_m1/`，不放 `tests/fixtures/`，因为这些既是测试数据也是设计样例（M2 起还会被参考）。

## M1 不做（明确推迟到后续 milestone）

- ❌ 任何 adapter 实例化或网络/IPC 调用（M2 起）
- ❌ capability_registry 集成、prompt 注入（M2 起）
- ❌ tool_orchestration_engine 接入（M2 起）
- ❌ approval 闸接入（M2 起，risk_mode 字段先准备好，approval 不消费）
- ❌ 路由（M2/M5）
- ❌ 控制中心 UI（M5）
- ❌ 文件监听 / 热重载（M5；M1 的 `reload()` 仅占位）
- ❌ 把 `mcp_stdio_discoverer.py` / `local_workflow_runners/comfyui.py` 改造成 adapter（M2/M3）

## M2 前置议题（M1 审查结论）

- M2 ticket 必须把设计 §6.3.1 规则 #4 加入验收：动态发现型 capability（尤其 MCP discovered tools）若未在 `low_risk_allowlist` 显式列出 tool name，不允许保留 `risk: low`，最低提升到 `medium`。
- M2 设计阶段需要先决定 profile manifest 多 profile 切换模型。当前 M1 engine 没有 active `profile_user_id`，profile dir provider 使用 `.no_active_profile` 哨兵目录 honest 降级；M2 建议优先评估“builtin 由 engine 持有 + profile manifest 按请求懒加载”的方案，以贴合现有 per-request profile 模型。

## 完成定义（DoD）

1. 新增的两个 test 文件全绿
2. `tests.quick_regression_suite` 不退步
3. `git diff --check` 通过
4. engine 启动日志能看到 `CapabilityAdapterRegistry scanned: 0 valid, 0 invalid`（builtin 暂空时）
5. 文档 `docs/capability_adapter_v1.md` 在 §8 把 M1 标记为已完成（追加日期和提交 hash）

## 实施估时

- 新建 5 个 py 文件 + 7 个 fixture：1-2 小时
- 两个测试文件 ~15 个用例：1-2 小时
- engine.py 接入 + 联调：30 分钟
- 文档收尾：15 分钟

总计：3-5 小时一个工作切片。
