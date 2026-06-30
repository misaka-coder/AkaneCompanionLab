# Capability Adapter v1 — M4 实施记录（TTS / SoVITS）

Updated: 2026-06-16
Parent design: `docs/capability_adapter_v1.md`
Prerequisite: M1/M2/M3 focused tests and route tests passing
Status: Implemented (uncommitted)

## 目标

M4 把现有 `/tts` 主流量纳入 Capability Adapter v1，并让桌宠当前 emotion 可以选择 GPT-SoVITS 参考音频。完成后：

- `OpenAICompatTTSAdapter` 统一包装 EdgeTTS / GPT-SoVITS 风格的 `synthesize()` client
- `/tts` 不再直接调用 GPT-SoVITS client，改走 adapter.invoke
- EdgeTTS 默认路径和 GPT-SoVITS 失败回退仍保持原行为
- `emotionVoiceMap` 可以在 voice profile 私有配置中保存，运行时按 `emotion` 覆盖 `refAudioPath` / `promptText`
- 桌宠 next-gen 请求 `/tts` 时带上当前 emotion

## 边界

M4 不做：

- 新 UI
- 角色包语音编辑器
- ASR adapter
- 非 loopback TTS provider
- 改外部 GPT-SoVITS HTTP 协议
- 把本地参考音频路径暴露到公开 catalog / snapshot

## 主要文件

- `companion_v01/capability_adapters/openai_compat_tts.py`
  - `OpenAICompatTTSAdapter`
  - `tts.synthesize` capability
  - emotion voice map overlay
- `companion_v01/routes/voice.py`
  - `/tts` GPT-SoVITS 和 EdgeTTS 路径都通过 adapter helper
  - GPT-SoVITS 失败继续降级到 EdgeTTS
- `companion_v01/local_capability_config.py`
  - voice profile 私有字段新增 `emotionVoiceMap`
  - 运行时 profile 返回 emotion map
  - 公开 entry 不回显 emotion map 或派生摘要，避免保留未消费字段
  - MCP 动态工具 low risk allowlist 在 catalog 侧与 adapter 侧对齐
- `desktop_pet_next/src/main.js`
  - `buildBackendCharacterContext()` 带上当前 emotion
- `tests/test_capability_adapter_openai_compat_tts.py`
  - adapter invoke
  - emotion overlay
  - route 主流量覆盖
  - 私有配置不泄露路径

## 验收

- `tests.test_capability_adapter_openai_compat_tts` 全绿
- `tests.test_tts_client` 全绿
- `tests.test_backend_route_modules` 全绿
- M1-M4 focused adapter tests 全绿
- quick regression 不退步
- `git diff --check` 通过

## M5 前提示

M4 只处理 TTS。M5 做 ASR / hot reload UX 时，建议复用本次 route helper 的形状：路由负责 provider 选择与 fallback，adapter 负责协议与输入覆盖，公开 catalog 只暴露安全摘要。
