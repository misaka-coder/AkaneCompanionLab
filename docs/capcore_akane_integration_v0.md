# capcore Akane integration v0

Status: M1/M1.2 回接试点
Date: 2026-06-30

## 当前接入形态

Akane 已把 capability adapter 的内核边界切到 `capcore`。

类型 / manifest / registry 转发层：

- `companion_v01/capability_adapters/types.py`
- `companion_v01/capability_adapters/protocol.py`
- `companion_v01/capability_adapters/manifest_loader.py`
- `companion_v01/capability_adapters/registry.py`

这些文件现在是兼容转发层，保留旧 import 路径，真实类型、协议、manifest loader、registry 来自 `capcore`。

调用 / 权限闸门：

- `companion_v01/local_capability_config.py` 用 `capcore.project_mapping_fields()` /
  `capcore.permission_request_from_mapping()` / `capcore.resolve_permission()` 推导公开 catalog 的
  `risk / confirm / requiresConfirmation / approvalMode`。
- `companion_v01/local_capability_catalog.py` 用 `capcore.project_mapping_fields()` 投影后端工具 catalog 字段。
- `companion_v01/tool_runtime.py` 的 `AdapterCapabilityToolHandler` 现在按
  `validate_invocation_args -> build_permission_request -> resolve_permission -> adapter.invoke` 执行。
- `BrowserPageToolHandler` 的高风险浏览器控制动作也通过 `capcore.PermissionRequest` 决策。
- `companion_v01/capcore_runtime.py` 用 `capcore.sanitize_permission_preview()` 生成 approval stream event 的安全预览。
- `companion_v01/capability_approval.py` 的审批请求路由复用 `capcore.sanitize_permission_preview()`，
  Akane 只额外做公开 route 的敏感 key 名过滤和 approval request/grant 生命周期。

宿主队列、UI 事件、profile 配置和真实 adapter 执行仍留在 Akane。

## 仍留在 Akane 的范围

具体 adapter 仍属于 Akane 宿主层：

- `comfyui.py`
- `mcp_stdio.py`
- `openai_compat_tts.py`
- `openai_compat_asr.py`
- capability routes / approval / orchestration / control center UI

其中 approval store / request TTL / grant lifecycle / route status code 仍是 Akane 产品层职责；
`capcore` 只提供 permission request/decision 和 preview sanitizing helper。

这些模块可以继续使用：

```python
from companion_v01.capability_adapters import CapabilityDescriptor
```

但该类型实际由 `capcore` 提供。

## 版本化依赖形态

Akane 从 wheelhouse 或包索引安装 `requirements-packages.txt` 中精确锁定的
`capcore` 发行版。Windows bootstrap 校验已安装 distribution 的版本与来源，
拒绝 editable/source-directory 安装；不检查或导入 sibling checkout。

`memcore` 采用相同的“可复用 core”方向，但当前 Akane 后端还未把 `memcore`
作为运行时依赖强制安装；接入时应优先复用 `memcore.MemorySystem` 公共 API，而不是复制内部实现。

## 行为变化

`capcore` 的校验比 Akane 原实现更 fail-closed：

- profile 层同 provider 的坏 manifest 会遮蔽 builtin。
- `endpoint.loopback_only` 等布尔字段写错会 invalid。
- `capability.id` 必须安全且同 provider 内唯一。
- malformed `inputs` / `outputs` slot 会 invalid。

这意味着：用户 profile 明确覆盖某个 provider 但配置写坏时，Akane 不会继续暴露旧 builtin 能力。

M1.2 后，Akane 公开能力 catalog 和 approval preview 的通用清洗逻辑也回流到 `capcore`：

- legacy catalog entry 通过 `project_mapping_fields()` 统一得到 canonical `risk / confirm / effects`。
- approval preview 会统一脱敏本地绝对路径、Bearer、secret-like literal 和 URL query secret。
- Akane route 层仍可在 `capcore` 输出之上做产品侧过滤，例如不把 `api_key` 这类敏感 key 名暴露给控制中心。

## 验证命令

```bash
python -c "from companion_v01.capability_adapters import CapabilityManifest, CapabilityAdapter, load_manifest; import capcore; print('ok')"
python -m py_compile companion_v01\capability_adapters\types.py companion_v01\capability_adapters\protocol.py companion_v01\capability_adapters\manifest_loader.py companion_v01\capability_adapters\registry.py companion_v01\capability_adapters\comfyui.py companion_v01\capability_adapters\mcp_stdio.py companion_v01\capability_adapters\openai_compat_tts.py companion_v01\capability_adapters\openai_compat_asr.py
python -m unittest tests.test_capability_adapter_manifest_loader tests.test_capability_adapter_registry tests.test_capability_adapter_comfyui tests.test_capability_adapter_mcp_stdio tests.test_capability_adapter_mcp_orchestration tests.test_capability_adapter_openai_compat_asr tests.test_capability_adapter_openai_compat_tts -v
ruff check companion_v01/capability_adapters tests/test_capability_adapter_manifest_loader.py tests/test_capability_adapter_registry.py
ruff format --check companion_v01/capability_adapters tests/test_capability_adapter_manifest_loader.py tests/test_capability_adapter_registry.py
git diff --check
```
