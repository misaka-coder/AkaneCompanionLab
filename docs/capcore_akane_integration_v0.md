# capcore Akane integration v0

Status: M0回接试点
Date: 2026-06-30

## 当前接入形态

Akane 已把 capability adapter 的内核边界切到 `capcore`：

- `companion_v01/capability_adapters/types.py`
- `companion_v01/capability_adapters/protocol.py`
- `companion_v01/capability_adapters/manifest_loader.py`
- `companion_v01/capability_adapters/registry.py`

这些文件现在是兼容转发层，保留旧 import 路径，真实类型、协议、manifest loader、registry 来自 `capcore`。

## 仍留在 Akane 的范围

具体 adapter 仍属于 Akane 宿主层：

- `comfyui.py`
- `mcp_stdio.py`
- `openai_compat_tts.py`
- `openai_compat_asr.py`
- capability routes / approval / orchestration / control center UI

这些模块可以继续使用：

```python
from companion_v01.capability_adapters import CapabilityDescriptor
```

但该类型实际由 `capcore` 提供。

## 本地依赖

Akane 的 `requirements.txt` 增加：

```text
-e ../capcore
```

开发环境需要安装本地 editable 包：

```bash
python -m pip install -e F:\Akane\capcore
```

## 行为变化

`capcore` 的校验比 Akane 原实现更 fail-closed：

- profile 层同 provider 的坏 manifest 会遮蔽 builtin。
- `endpoint.loopback_only` 等布尔字段写错会 invalid。
- `capability.id` 必须安全且同 provider 内唯一。
- malformed `inputs` / `outputs` slot 会 invalid。

这意味着：用户 profile 明确覆盖某个 provider 但配置写坏时，Akane 不会继续暴露旧 builtin 能力。

## 验证命令

```bash
python -c "from companion_v01.capability_adapters import CapabilityManifest, CapabilityAdapter, load_manifest; import capcore; print('ok')"
python -m py_compile companion_v01\capability_adapters\types.py companion_v01\capability_adapters\protocol.py companion_v01\capability_adapters\manifest_loader.py companion_v01\capability_adapters\registry.py companion_v01\capability_adapters\comfyui.py companion_v01\capability_adapters\mcp_stdio.py companion_v01\capability_adapters\openai_compat_tts.py companion_v01\capability_adapters\openai_compat_asr.py
python -m unittest tests.test_capability_adapter_manifest_loader tests.test_capability_adapter_registry tests.test_capability_adapter_comfyui tests.test_capability_adapter_mcp_stdio tests.test_capability_adapter_mcp_orchestration tests.test_capability_adapter_openai_compat_asr tests.test_capability_adapter_openai_compat_tts -v
ruff check companion_v01/capability_adapters tests/test_capability_adapter_manifest_loader.py tests/test_capability_adapter_registry.py
ruff format --check companion_v01/capability_adapters tests/test_capability_adapter_manifest_loader.py tests/test_capability_adapter_registry.py
git diff --check
```
