# Capability Adapter v1 — M5 实施记录（ASR + Reload）

Updated: 2026-06-16
Parent design: `docs/capability_adapter_v1.md`
Prerequisite: M1-M4 focused tests and quick regression passing
Status: Implemented (uncommitted)

## 目标

M5 收口 Capability Adapter v1 的输入侧能力和后端热重载契约。完成后：

- `OpenAICompatASRAdapter` 支持本地 OpenAI Whisper API 兼容 ASR endpoint
- `provider.asr.openai_compat.local` 成为可配置 provider，仍强制 loopback endpoint
- `/asr` 在该 provider 配置后优先走 adapter；协议失败时回落到既有 faster-whisper 路径
- 能力目录的 `voice.input.asr` resolution 会优先显示配置的 OpenAI 兼容 ASR provider
- 新增 `/capabilities/adapter-registry/reload`，返回 manifest valid/invalid 摘要，供后续控制中心触发刷新

## 边界

M5 不做：

- 新前端 UI
- 文件监听器
- 远程 ASR endpoint / API key
- 彻底替换 faster-whisper 内置路径
- ASR 流式识别
- ASR prompt 自主调用

## 主要文件

- `companion_v01/capability_adapters/openai_compat_asr.py`
  - `OpenAICompatASRAdapter`
  - `asr.transcribe` capability
  - OpenAI-compatible `/v1/audio/transcriptions` HTTP 调用
- `companion_v01/routes/voice.py`
  - `/asr` 配置 provider 优先路径
  - adapter 失败 fallback 到既有 `run_asr_transcription`
- `companion_v01/local_capability_config.py`
  - 新增 `provider.asr.openai_compat.local`
- `companion_v01/local_capability_catalog.py`
  - `voice.input.asr` resolution 优先显示 configured/ready OpenAI 兼容 ASR
- `companion_v01/routes/capabilities.py`
  - 新增 `/capabilities/adapter-registry/reload`
  - reload 返回 redacted valid/invalid 摘要
- `tests/test_capability_adapter_openai_compat_asr.py`
  - adapter client invoke
  - OpenAI-compatible HTTP path
  - `/asr` route provider path
  - catalog resolution
  - reload endpoint redaction

## 验收

- `tests.test_capability_adapter_openai_compat_asr` 全绿
- `tests.test_backend_route_modules` 全绿
- M1-M5 focused adapter tests 全绿
- quick regression 不退步
- `git diff --check` 通过

## v1 收尾提示

Capability Adapter v1 的 M1-M5 后端骨架已收口。后续如果继续推进，建议进入 v1 hardening/review，而不是继续新增里程碑：重点审查多 profile manifest cache、ASR/TTS UI 配置入口、reload endpoint 的前端接线、以及 Windows 下 Chroma sqlite 临时目录释放 warning。
