# Local Ollama Model

Akane 可以通过 Ollama 的 OpenAI-compatible API 接入本地模型。

## 前置条件

确认 Ollama 正在运行：

```powershell
ollama list
```

默认接口：

```text
http://127.0.0.1:11434
```

## 聊天主模型切到 Qwen2.5 7B

在 `.env` 里配置：

```env
CHAT_API_PROTOCOL=ollama
CHAT_BASE_URL=http://127.0.0.1:11434
CHAT_MODEL_NAME=qwen2.5:7b
CHAT_API_KEY=
```

说明：

- `CHAT_API_PROTOCOL=ollama` 会自动把 base URL 规范成 `/v1` OpenAI-compatible endpoint。
- Ollama 不需要真实 API Key，留空即可，程序内部会使用占位 key。
- 目前只建议先切 `CHAT_*`，不要急着把 `AUX_*` 也切到本地。辅助任务对 JSON 稳定性要求很高，云模型更稳。

## 备选 OpenAI-compatible 写法

也可以显式写：

```env
CHAT_API_PROTOCOL=openai
CHAT_BASE_URL=http://127.0.0.1:11434/v1
CHAT_MODEL_NAME=qwen2.5:7b
CHAT_API_KEY=ollama
```

## 当前工程适配

- `services.llm_client` 支持 `protocol=ollama`。
- `LLMRuntime` 的聊天通道现在会读取 `CHAT_*`，辅助通道继续读取 `AUX_*`。
- Ollama 的 JSON 调用会自动加 `response_format={"type":"json_object"}`，提高本地模型按 JSON 输出的稳定性。

## 注意事项

Qwen2.5 7B 可以接入，但能力和稳定性弱于云端大模型：

- 最终对话可能更慢。
- 复杂工具调用和长上下文可能更容易跑偏。
- 推荐先用于测试、备用、本地隐私模式，不建议马上替代主力模型。
