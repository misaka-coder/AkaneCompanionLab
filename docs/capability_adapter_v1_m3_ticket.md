# Capability Adapter v1 — M3 实施记录（ComfyUI 复用）

Updated: 2026-06-16
Parent design: `docs/capability_adapter_v1.md`
Prerequisite: M1/M2 已通过 focused tests 与 quick regression
Status: Implemented (uncommitted)

## 目标

M3 把现有 ComfyUI workflow 执行逻辑纳入 Capability Adapter v1，同时保持角色工坊现有 portrait cutout 路径不变。完成后：

- `ComfyUiCapabilityAdapter` 可以用同一套代码执行不同 ComfyUI workflow
- 现有 `ComfyUiWorkflowRunner` 通过 adapter 执行，不再在 runner 内部复制 ComfyUI 调用流程
- 第二条 workflow 可由 manifest + workflow JSON + slot mapping 构建 adapter，无需新增业务代码
- 后端仍只处理显式传入的 image bytes，不读取/写入角色包文件

## 边界

M3 不做：

- UI 改造
- 新 route
- 工作区文件产物落盘
- ComfyUI workflow 可视化编辑
- prompt 自主调用 ComfyUI
- 替换控制中心 workflow 配置页面

## 主要文件

- `companion_v01/capability_adapters/comfyui.py`
  - `ComfyUiCapabilityAdapter`
  - `ComfyUiWorkflowCapability`
  - `build_comfyui_adapter_from_manifest()`
- `companion_v01/local_workflow_runners/comfyui.py`
  - `ComfyUiWorkflowRunner.execute_workflow()` 改为通过 adapter 执行
  - 保留旧 upload filename / client id 形状，避免破坏现有 workflow runner 测试和外部行为
- `tests/test_capability_adapter_comfyui.py`
  - adapter descriptor
  - adapter invoke
  - unknown capability
  - manifest + second workflow JSON 构建 adapter

## 验收

- `tests.test_capability_adapter_comfyui` 全绿
- `tests.test_local_workflow_runners` 全绿，证明 portrait cutout 旧路径不退步
- M1/M2 focused tests 全绿
- quick regression 不退步
- `git diff --check` 通过

## M4 前提示

M3 只把 ComfyUI workflow 执行收进 adapter 壳。TTS/SoVITS 的 M4 不应复用 image workflow 的 IO 假设；应单独建 `openai_compat_tts` adapter，并让 voice route 继续保留 EdgeTTS fallback。
