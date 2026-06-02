---
tags:
  - akane/http-json
  - backend/protocol
  - streaming-response
  - ndjson
created: 2026-05-23
---

# HTTP、JSON 与流式响应

> FastAPI 笔记解决“接口怎么写”。  
> 这份笔记解决“接口里的数据怎么流动，以及为什么 Akane 要用流式响应”。

这篇和 `03_FastAPI后端开发.md` 会有少量重叠，但重点不同。

```text
03 FastAPI：
重点是框架用法。
比如路由、Request、JSONResponse、APIRouter、StreamingResponse。

04 HTTP / JSON / 流式响应：
重点是通信协议和数据契约。
比如请求体长什么样、响应体长什么样、前端怎么解析、为什么要一行一行返回。
```

对 Akane 来说，这一篇很关键，因为桌宠客户端和后端不是“直接函数调用”，而是通过 HTTP 传 JSON：

```text
桌宠前端
→ fetch("/think", JSON 请求体)
→ FastAPI 路由
→ engine.process_turn_stream(payload)
→ 一行一行 yield JSON 事件
→ StreamingResponse 返回 NDJSON
→ 前端 ReadableStream 逐块读取
→ 根据事件类型更新表情、气泡、文件、最终回复
```

学习路线：

```text
HTTP 请求 / 响应
→ JSON 数据格式
→ 请求体 payload
→ 响应体 frame
→ 数据契约 contract
→ 普通 JSON 响应
→ 流式响应 StreamingResponse
→ NDJSON
→ 前端 ReadableStream 解析
→ Akane /think 完整链路
→ 常见坑和调试方法
```

---

## 一、HTTP 解决什么问题

HTTP 本质是一个“请求-响应”协议。

```text
客户端发请求
服务端处理
服务端返回响应
```

在 Akane 里：

```text
桌宠客户端是客户端
FastAPI 后端是服务端
AkaneMemoryEngine 是后端内部业务核心
```

典型链路：

```text
用户输入：“伙伴，今天继续学 Akane”

前端：
POST /think
Content-Type: application/json

后端：
读取 JSON
调用 engine
返回一串事件

前端：
一边收到事件，一边更新界面
```

所以 HTTP 不是业务逻辑本身，而是：

```text
让两个程序能稳定说话的通信层。
```

---

## 二、HTTP 请求由什么组成

一个 HTTP 请求可以拆成四块：

```text
method   方法
path     路径
headers  请求头
body     请求体
```

比如 Akane 桌宠发送消息：

```http
POST /think?t=1710000000000
Content-Type: application/json

{
  "user_id": "default_session",
  "real_user_id": "master",
  "message": "你好 Akane",
  "client_mode": "desktop_pet"
}
```

拆开看：

| 部分 | 作用 |
|---|---|
| `POST` | 表示提交一段数据给后端 |
| `/think` | 表示请求聊天思考接口 |
| `Content-Type: application/json` | 告诉后端 body 是 JSON |
| `{...}` | 真正的业务数据 |

Akane 前端里的真实发送逻辑在：

```text
desktop_pet_next/src/main.js
sendThinkStream(...)
```

核心形状可以简化成：

```javascript
const requestInit = {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  cache: "no-store",
  body: JSON.stringify({
    user_id: state.sessionId,
    real_user_id: getProfileUserId(),
    message,
    client_mode: CLIENT_MODE,
    character_pack_id: getCurrentCharacterPackId(),
    current_visual: buildCurrentVisual()
  })
};
```

重点：

```text
HTTP 只能传文本或字节。
JavaScript 对象必须先 JSON.stringify 成字符串。
Python 后端再把字符串解析回 dict。
```

---

## 三、HTTP 响应由什么组成

一个 HTTP 响应也可以拆成三块：

```text
status code  状态码
headers      响应头
body         响应体
```

普通 JSON 响应：

```http
200 OK
Content-Type: application/json

{
  "emotion": "normal",
  "speech": "你好呀，伙伴。"
}
```

Akane 的 `/think_once` 就是普通 JSON 响应：

```python
frame = engine.process_turn(payload)
return JSONResponse(frame)
```

流式响应则不同：

```http
200 OK
Content-Type: application/x-ndjson

{"type":"stream_start","contract_version":"..."}
{"type":"turn_start","speaker":"Akane"}
{"type":"speech_chunk","text":"你好"}
{"type":"speech_chunk","text":"呀，伙伴。"}
{"type":"final","payload":{"emotion":"normal","speech":"你好呀，伙伴。"}}
{"type":"stream_end","status":"ok","partial":{"speech":"你好呀，伙伴。"}}
```

这不是一个完整的大 JSON，而是很多行小 JSON。

---

## 四、JSON 是什么

JSON 是一种轻量数据格式。

它的目标是：

```text
让不同语言之间传结构化数据。
```

Python 和 JSON 的对应关系：

| Python | JSON |
|---|---|
| `dict` | object |
| `list` | array |
| `str` | string |
| `int` / `float` | number |
| `True` / `False` | true / false |
| `None` | null |

例子：

```python
import json

payload = {
    "user_id": "desktop",
    "message": "你好 Akane",
    "timestamp": 1710000000,
    "debug": False,
    "images": [],
}

text = json.dumps(payload, ensure_ascii=False)
print(text)

data = json.loads(text)
print(data["message"])
```

运行结果大概是：

```text
{"user_id": "desktop", "message": "你好 Akane", "timestamp": 1710000000, "debug": false, "images": []}
你好 Akane
```

注意：

```text
json.dumps 负责 Python -> JSON 字符串
json.loads 负责 JSON 字符串 -> Python
ensure_ascii=False 可以保留中文，不转成 \u4f60\u597d
```

---

## 五、JSON 不是 Python 字典

它们长得像，但不是同一个东西。

Python 字典：

```python
{"ok": True, "value": None}
```

JSON 字符串：

```json
{"ok": true, "value": null}
```

区别：

| 概念 | Python | JSON |
|---|---|---|
| 真 | `True` | `true` |
| 假 | `False` | `false` |
| 空 | `None` | `null` |
| 字符串 | 单引号双引号都可 | 必须双引号 |
| 本质 | 内存对象 | 文本格式 |

错误示例：

```python
import json

raw = "{'ok': True}"  # 这不是合法 JSON
json.loads(raw)
```

会报错。

正确写法：

```python
raw = '{"ok": true}'
data = json.loads(raw)
print(data)
```

---

## 六、payload 和 frame

读 Akane 源码时，可以把 HTTP 里的 JSON 分成两类：

```text
payload：前端发给后端的请求数据
frame：后端返回给前端的结果数据
```

### 6.1 payload

payload 是用户这一轮请求的上下文。

Akane `/think` 里常见字段：

| 字段 | 含义 |
|---|---|
| `user_id` | 会话 ID |
| `real_user_id` | 真实用户 ID / profile ID |
| `message` | 用户输入文本 |
| `client_mode` | 客户端模式，比如桌宠 |
| `character_pack_id` | 当前角色资源包 |
| `current_visual` | 当前桌宠视觉状态 |
| `desktop_context` | 桌面上下文 |
| `desktop_screen_frames` | 屏幕帧信息 |
| `desktop_activity` | 音乐等桌面活动信息 |

简化版：

```json
{
  "user_id": "desktop",
  "real_user_id": "master",
  "message": "伙伴，继续学 HTTP",
  "client_mode": "desktop_pet"
}
```

后端读取：

```python
payload = await request.json()
```

然后检查：

```python
if not isinstance(payload, dict):
    return JSONResponse(
        build_desktop_pet_error_payload(
            error="invalid_payload",
            message="/think payload must be a JSON object",
            retryable=False,
        ),
        status_code=400,
    )
```

意思是：

```text
后端只接受 JSON object，也就是 Python dict。
如果前端传了 list、字符串、数字，直接返回 400。
```

### 6.2 frame

frame 是后端处理完一轮后给前端看的最终状态。

常见字段：

| 字段 | 含义 |
|---|---|
| `status` | 处理状态 |
| `emotion` | 桌宠表情 |
| `speech` | 最终说话文本 |
| `speech_segments` | 分段语音 / 文本 |
| `choices` | 可选回复或交互选项 |
| `npc_turns` | 工具或 NPC 产生的对话 |
| `client_mode` | 返回时带上的客户端模式 |
| `trace_id` | 调试追踪 ID |
| `_debug` | 调试信息 |

简化版：

```json
{
  "status": "ok",
  "emotion": "normal",
  "speech": "可以的伙伴，我们继续。",
  "trace_id": "akane_abc123"
}
```

前端拿到 frame 后：

```text
更新表情
显示气泡
播放 TTS
渲染按钮 / 文件 / 场景状态
```

---

## 七、数据契约 contract

“数据契约”就是双方约定好：

```text
请求必须有哪些字段
响应会有哪些字段
字段类型是什么
错误时长什么样
流式事件有哪些 type
```

没有契约会发生什么？

```text
前端以为 emotion 是字符串，后端返回了对象
前端以为 speech 一定存在，后端没返回
后端以为 message 是字符串，前端传了数组
前端以为响应是 JSON，后端返回音频
```

这类 bug 很难受，因为两边都“没有语法错”，但对不上。

Akane 在 `/think` 流式响应里带了契约版本：

```python
yield json.dumps(
    {
        "type": "stream_start",
        "contract_version": DESKTOP_PET_CONTRACT_VERSION,
    },
    ensure_ascii=False,
) + "\n"
```

响应头也带：

```python
headers={
    "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION,
}
```

这表示：

```text
前端和后端正在使用同一套桌宠通信协议。
以后如果事件格式大改，可以通过 contract_version 区分。
```

---

## 八、普通 JSON 响应

普通 JSON 响应适合：

```text
请求很快完成
结果一次性返回
前端不需要中途更新
```

例子：

```python
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/echo")
def echo(payload: dict):
    return JSONResponse({
        "ok": True,
        "received": payload,
    })
```

启动：

```powershell
uvicorn demo_json:app --reload
```

请求：

```powershell
curl -X POST http://127.0.0.1:8000/echo `
  -H "Content-Type: application/json" `
  -d "{\"message\":\"hello\"}"
```

返回：

```json
{
  "ok": true,
  "received": {
    "message": "hello"
  }
}
```

Akane 的 `/think_once` 就接近这种模式：

```text
前端发一条消息
后端等 LLM、RAG、工具全部跑完
最后一次性返回完整 frame
```

优点：

```text
简单
容易调试
适合普通接口
```

缺点：

```text
LLM 慢时，用户要一直等
中途无法显示“正在思考 / 已生成一部分 / 工具已完成”
```

---

## 九、为什么需要流式响应

LLM 应用和传统接口不一样。

传统接口：

```text
查数据库 50ms
返回 JSON
```

LLM 接口：

```text
构造 prompt
请求模型
模型一个 token 一个 token 生成
可能调用工具
可能生成文件
最后才有完整答案
```

如果非要等全部结束才返回，用户体验会变成：

```text
点击发送
界面卡住
等 5-30 秒
突然出现一大段回复
```

流式响应可以变成：

```text
点击发送
立刻收到 stream_start
显示“思考中”
收到 turn_start
收到 speech_chunk
收到工具事件
收到 final_ui / final
收到 stream_end
```

也就是：

```text
后端一边计算，一边把进度和部分结果送给前端。
```

这就是 Akane `/think` 使用 `StreamingResponse` 的原因。

---

## 十、生成器 yield 是流式响应的基础

> `yield` 的完整讲解见 [[01_Python工程化进阶]] 第 23-26 节。这里只讲它在流式响应中的角色。

`yield` 让函数变成"边运行边产出"的生成器，而不是等全部算完再一次性 `return`。这正是流式响应的基础。

Akane 的核心就是这一行：

```python
for event in engine.process_turn_stream(payload):
    yield json.dumps(event, ensure_ascii=False) + "\n"
```

也就是：

```text
engine 每产出一个事件
路由层就把它编码成一行 JSON
StreamingResponse 立刻发给前端
```

---

## 十一、NDJSON 是什么

NDJSON = Newline Delimited JSON。

中文可以理解成：

```text
用换行分隔的一行一个 JSON。
```

普通 JSON 数组：

```json
[
  {"type": "speech_chunk", "text": "你好"},
  {"type": "speech_chunk", "text": "伙伴"}
]
```

问题：

```text
必须等整个数组结束，前端才能 JSON.parse。
```

NDJSON：

```text
{"type":"speech_chunk","text":"你好"}
{"type":"speech_chunk","text":"伙伴"}
```

好处：

```text
每一行都是完整 JSON。
前端收到一行，就能 parse 一行。
非常适合流式事件。
```

Akane `/think` 的响应类型：

```python
media_type="application/x-ndjson"
```

这行告诉客户端：

```text
这个响应不是普通 application/json。
它是一行一行的 NDJSON。
```

---

## 十二、最小 NDJSON 服务端例子

新建 `demo_stream.py`：

```python
import json
import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


def make_events():
    yield {"type": "stream_start"}

    for word in ["你好", "，", "伙伴", "。"]:
        time.sleep(0.4)
        yield {"type": "speech_chunk", "text": word}

    yield {
        "type": "final",
        "payload": {
            "emotion": "normal",
            "speech": "你好，伙伴。",
        },
    }
    yield {"type": "stream_end", "status": "ok"}


@app.post("/think")
def think():
    def stream_lines():
        for event in make_events():
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        stream_lines(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )
```

启动：

```powershell
uvicorn demo_stream:app --reload
```

请求：

```powershell
curl -N -X POST http://127.0.0.1:8000/think
```

你会看到一行一行出现：

```text
{"type": "stream_start"}
{"type": "speech_chunk", "text": "你好"}
{"type": "speech_chunk", "text": "，"}
{"type": "speech_chunk", "text": "伙伴"}
{"type": "speech_chunk", "text": "。"}
{"type": "final", "payload": {"emotion": "normal", "speech": "你好，伙伴。"}}
{"type": "stream_end", "status": "ok"}
```

`curl -N` 的意思：

```text
不缓冲输出，收到就显示。
```

---

## 十三、Akane 的 /think 流式路由

Akane 真实代码在：

```text
companion_v01/routes/think.py
```

核心结构：

```python
@router.post("/think")
async def think(request: Request):
    payload = await request.json()

    def _stream():
        yield json.dumps({
            "type": "stream_start",
            "contract_version": DESKTOP_PET_CONTRACT_VERSION,
        }, ensure_ascii=False) + "\n"

        for event in engine.process_turn_stream(payload):
            yield json.dumps(event, ensure_ascii=False) + "\n"

        yield json.dumps({
            "type": "stream_end",
            "status": "ok",
            "partial": partial,
        }, ensure_ascii=False) + "\n"

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION,
            "X-Accel-Buffering": "no",
        },
    )
```

真实代码还做了更多事：

```text
捕获非法 JSON
检查 payload 必须是 dict
并发保护 public_guard
统计 runtime_metrics
记录 log_event
遇到异常时返回 stream_error
finally 中释放 guard
最后无论成功失败都发 stream_end
```

这说明路由层的职责不是“生成回复”，而是：

```text
1. 读请求
2. 校验请求
3. 调用 engine
4. 把 engine 的事件包装成 HTTP 流
5. 处理异常和收尾
```

---

## 十四、Akane 的 engine 流式事件

真实代码在：

```text
companion_v01/engine.py
process_turn_stream(...)
```

简化流程：

```text
解析 payload
确定 session_id / profile_user_id / user_message
写入用户消息
执行检索 pipeline
调用 _stream_final_response
如果有 tool_call，执行工具
把工具产生的 stream_events yield 出去
再次调用 _stream_final_response
保存助手消息
保存评估记录
yield final_ui
yield final
```

它不是一次性返回一个 dict，而是：

```python
def process_turn_stream(self, payload):
    ...
    final_output = yield from self._stream_final_response(...)
    ...
    yield {"type": "assistant_stage_decision", "has_tool_call": bool(tool_call)}
    ...
    for stream_event in current_events:
        yield stream_event
    ...
    yield {"type": "final_ui", "payload": ui_final_payload}
    yield {"type": "final", "payload": final_output}
```

这就是为什么叫 stream：

```text
不是“算完再 return”
而是“边推进业务流程，边 yield 事件”
```

---

## 十五、常见流式事件类型

Akane 中常见事件：

| 事件 type | 含义 | 前端通常做什么 |
|---|---|---|
| `stream_start` | HTTP 流开始 | 初始化状态 |
| `turn_start` | 一轮回复开始 | 显示思考中 |
| `ui` | 表情 / UI 状态更新 | 更新表情 |
| `speech_chunk` | 小段文本增量 | 累积 partialSpeech |
| `speech_segment` | 可直接展示的一段话 | 提前显示气泡 |
| `assistant_stage_decision` | 是否准备调用工具 | 可用于调试 |
| `file_ready` | 文件生成或准备完成 | 前端处理文件交付 |
| `generated_file_ready` | 生成文件完成 | 前端处理文件交付 |
| `final_ui` | 给 UI 用的最终 payload | 渲染最终状态 |
| `final` | 完整最终结果 | 保存 / 调试 / 日志 |
| `stream_error` | 流中出错 | 展示 partial 或报错 |
| `stream_end` | 流结束 | 收尾，必要时渲染 partial |

一个典型事件流：

```text
stream_start
turn_start
ui
speech_chunk
speech_chunk
assistant_stage_decision
generated_file_ready
final_ui
final
stream_end
```

重点：

```text
事件 type 是前后端约定好的“动作名”。
前端看到不同 type，就执行不同渲染逻辑。
```

---

## 十六、前端如何读取 NDJSON

Akane 桌宠前端在：

```text
desktop_pet_next/src/main.js
readNdjsonEvents(response)
```

简化版：

```javascript
async function* readNdjsonEvents(response) {
  const reader = response.body?.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const segments = buffer.split(/\r?\n/);
    buffer = segments.pop() || "";

    for (const segment of segments) {
      const trimmed = segment.trim();
      if (!trimmed) continue;
      yield JSON.parse(trimmed);
    }
  }
}
```

关键点：

```text
response.body.getReader() 读取字节流
TextDecoder 把字节转成字符串
buffer 暂存半截 JSON
split 换行得到完整行
JSON.parse 解析每一行
yield event 把事件交给上层处理
```

为什么需要 `buffer`？

因为网络 chunk 不等于 JSON 行。

后端发：

```text
{"type":"speech_chunk","text":"你好"}\n
```

前端可能分两次收到：

```text
第 1 块：{"type":"speech_
第 2 块：chunk","text":"你好"}\n
```

如果每个 chunk 直接 `JSON.parse`，会报错。

所以正确做法是：

```text
按换行判断“一条 JSON 事件是否完整”
不是按网络 chunk 判断。
```

这是流式解析最重要的细节之一。

---

## 十七、前端如何处理事件

Akane 前端在：

```text
desktop_pet_next/src/main.js
processThinkStream(...)
```

简化版：

```javascript
async function processThinkStream(stream) {
  let partialSpeech = "";
  let rendered = false;

  for await (const event of stream) {
    const type = String(event?.type || "").trim().toLowerCase();

    if (type === "turn_start") {
      showThinking();
    } else if (type === "ui") {
      applyPayloadEmotion(event);
    } else if (type === "speech_chunk") {
      partialSpeech += String(event?.text || "");
    } else if (type === "speech_segment") {
      showBubbleText(String(event?.text || ""));
    } else if (type === "final" || type === "final_ui") {
      rendered = renderPayload(event?.payload || event);
    } else if (type === "stream_error") {
      if (event?.partial && !rendered) {
        rendered = renderPayload(event.partial);
      }
    } else if (type === "stream_end") {
      if (event?.partial && !rendered) {
        rendered = renderPayload(event.partial);
      }
    }
  }

  if (!rendered && partialSpeech.trim()) {
    rendered = renderPayload({ speech: partialSpeech.trim() });
  }
}
```

这段逻辑非常重要。

它说明前端不是只等最终答案，而是：

```text
收到 turn_start：显示思考
收到 ui：改表情
收到 speech_chunk：累积文本
收到 speech_segment：提前展示一句
收到 final_ui / final：渲染完整结果
收到 stream_error：尽量展示 partial
收到 stream_end：兜底收尾
```

这就是“桌宠活起来”的关键。

---

## 十八、完整链路复盘

从用户点击发送开始：

```text
1. 前端拿到输入 message
2. 前端构造 payload
3. JSON.stringify(payload)
4. fetch POST /think
5. FastAPI request.json()
6. 校验 payload 是 dict
7. 创建 _stream 生成器
8. 返回 StreamingResponse
9. _stream 先 yield stream_start
10. engine.process_turn_stream(payload)
11. engine yield turn_start / speech_chunk / tool events / final
12. 路由层把每个 event json.dumps + "\n"
13. 前端 ReadableStream 读取 chunk
14. TextDecoder 解码
15. 按换行切出 NDJSON 行
16. JSON.parse 成 event
17. processThinkStream 根据 type 更新 UI
18. stream_end 收尾
```

一句话：

```text
HTTP 负责传输
JSON 负责结构
NDJSON 负责一行一行流式传输
event.type 负责驱动前端行为
```

---

## 十九、错误响应和异常事件

错误分两类：

```text
请求还没进入流：返回普通 HTTP 错误
流已经开始：返回 stream_error 事件
```

### 19.1 请求阶段错误

比如 JSON 读不了：

```python
try:
    payload = await request.json()
except Exception as exc:
    return JSONResponse(
        build_desktop_pet_error_payload(
            error="invalid_json",
            message=f"无法读取 /think 请求：{str(exc)[:160]}",
            retryable=False,
        ),
        status_code=400,
    )
```

这时还没有开始流式响应，所以可以直接返回：

```http
400 Bad Request
Content-Type: application/json
```

### 19.2 流中错误

一旦 `StreamingResponse` 已经开始发数据，就不能再改成普通 400 / 500 了。

所以 Akane 在 `_stream()` 里捕获异常：

```python
except Exception as exc:
    yield json.dumps(
        {
            "type": "stream_error",
            "error": "think_stream_failed",
            "message": f"stream failed: {exc}",
            "retryable": True,
            "partial": partial,
        },
        ensure_ascii=False,
    ) + "\n"
```

意思：

```text
HTTP 连接还在。
后端发一个 stream_error 事件告诉前端：流中出错了。
如果已有 partial，就尽量展示 partial。
```

最后仍然会发：

```python
yield json.dumps(
    {
        "type": "stream_end",
        "status": "ok" if ok else "error",
        "partial": partial,
    },
    ensure_ascii=False,
) + "\n"
```

这是一种很稳的设计：

```text
无论成功失败，前端都能收到收尾事件。
```

---

## 二十、为什么要 Cache-Control 和 X-Accel-Buffering

Akane `/think` 响应头里有：

```python
headers={
    "Cache-Control": "no-store",
    "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION,
    "X-Accel-Buffering": "no",
}
```

含义：

| 响应头 | 作用 |
|---|---|
| `Cache-Control: no-store` | 不要缓存这次聊天响应 |
| `X-Akane-Contract` | 标记通信协议版本 |
| `X-Accel-Buffering: no` | 告诉 nginx 不要缓冲流式响应 |

为什么不能缓冲？

```text
流式响应的价值在于“收到一点显示一点”。
如果 nginx 或代理把内容攒到最后才发，前端看到的还是一次性响应。
```

所以部署流式接口时，代理层也要配合。

项目里也有 nginx 配置提示：

```text
Akane relies on NDJSON streaming for live speech / scene / choices updates.
```

意思：

```text
Akane 的实时说话、场景、选项更新都依赖 NDJSON 流。
```

---

## 二十一、普通 JSON、NDJSON、SSE、WebSocket 对比

| 技术 | 适合场景 | 特点 |
|---|---|---|
| 普通 JSON | 一次请求一次结果 | 最简单 |
| NDJSON | 服务端连续发事件 | Akane 当前使用 |
| SSE | 服务端向浏览器推事件 | 浏览器原生 EventSource |
| WebSocket | 双向实时通信 | 更复杂，适合高频双向 |

### 21.1 普通 JSON

```text
客户端请求一次
服务端返回一个完整 JSON
```

适合：

```text
配置
列表
健康检查
一次性计算结果
```

### 21.2 NDJSON

```text
客户端请求一次
服务端持续返回多行 JSON
```

适合：

```text
LLM 回复
任务进度
日志流
生成文件事件
```

### 21.3 SSE

SSE 格式长这样：

```text
event: message
data: {"text":"hello"}

event: done
data: {}
```

优点：

```text
浏览器 EventSource 支持好
天然是事件流
```

限制：

```text
主要是服务端到客户端
自定义 POST 场景没有 fetch + NDJSON 灵活
```

### 21.4 WebSocket

WebSocket 是长连接双向通信：

```text
前端可以随时发
后端也可以随时推
```

适合：

```text
实时游戏
多人协作
高频双向状态同步
```

但复杂度更高：

```text
连接管理
断线重连
心跳
消息序号
权限
并发
```

Akane 当前用 NDJSON 是合理的：

```text
一次用户输入
一条后端响应流
事件类型足够表达中间状态
实现复杂度比 WebSocket 低
```

---

## 二十二、用测试理解通信契约

Akane 测试里有一个很好的例子：

```text
tests/test_backend_route_modules.py
test_think_router_handles_once_and_stream_contract_with_fake_engine
```

测试里的假 engine：

```python
class FakeEngine:
    def process_turn(self, payload):
        return {
            "status": "ok",
            "emotion": "normal",
            "speech": f"echo: {payload.get('message')}",
            "_debug": {},
        }

    def process_turn_stream(self, payload):
        yield {"type": "ui", "emotion": "normal"}
        yield {"type": "speech_chunk", "text": "hello"}
        yield {"type": "final", "payload": self.process_turn(payload)}
```

测试断言：

```python
once_response = client.post("/think_once", json={"user_id": "desktop", "message": "hi"})
stream_response = client.post("/think", json={"user_id": "desktop", "message": "hi"})

stream_lines = [json.loads(line) for line in stream_response.text.splitlines()]

self.assertEqual(once_response.status_code, 200)
self.assertEqual(once_response.json()["speech"], "echo: hi")
self.assertEqual(stream_response.status_code, 200)
self.assertEqual(stream_response.headers["x-akane-contract"], DESKTOP_PET_CONTRACT_VERSION)
self.assertEqual(stream_lines[0]["type"], "stream_start")
self.assertEqual(stream_lines[-1]["type"], "stream_end")
self.assertEqual(stream_lines[-1]["partial"]["speech"], "echo: hi")
```

这说明测试不只是在测函数，而是在测契约：

```text
/think_once 必须返回普通 JSON
/think 必须返回 NDJSON 行
第一行必须是 stream_start
最后一行必须是 stream_end
响应头必须包含 X-Akane-Contract
partial.speech 必须能兜底拿到最终文本
```

以后你写类似功能，也可以这样想：

```text
测试不是只测“有没有报错”。
测试要确认前后端约定没有被破坏。
```

---

## 二十三、requests 客户端怎么发 JSON

Python 里常用 `requests` 调 HTTP。

普通写法：

```python
import requests

payload = {
    "user_id": "desktop",
    "message": "你好",
}

response = requests.post(
    "http://127.0.0.1:8000/think_once",
    json=payload,
    timeout=30,
)

print(response.status_code)
print(response.json())
```

注意：

```text
json=payload 会自动：
1. 把 dict 转成 JSON 字符串
2. 设置 Content-Type: application/json
```

不要和 `data=` 混淆：

```python
requests.post(url, data=payload)  # 通常是表单，不是 JSON
requests.post(url, json=payload)  # 发送 JSON
```

---

## 二十四、requests 怎么读流式响应

如果用 Python 读 NDJSON：

```python
import json
import requests

payload = {
    "user_id": "desktop",
    "message": "你好 Akane",
}

with requests.post(
    "http://127.0.0.1:8000/think",
    json=payload,
    stream=True,
    timeout=60,
) as response:
    response.raise_for_status()

    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        event = json.loads(line)
        print(event["type"], event)
```

关键：

```text
stream=True 表示不要一次性下载完整响应
iter_lines 适合读 NDJSON
json.loads 解析每一行
```

### 24.1 异步客户端：httpx

`requests` 是同步库。如果需要在 `async def` 路由里调另一个 HTTP 服务，可以用 `httpx`（支持 async + 流式）：

```python
import json
import httpx


async def call_think_stream(payload: dict):
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST",
            "http://127.0.0.1:8000/think",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                event = json.loads(line)
                print(event["type"], event)
```

| 场景 | 推荐 |
|------|------|
| 脚本、测试、同步代码 | `requests` |
| FastAPI 路由里调别的服务 | `httpx`（async） |
| 需要 HTTP/2 | `httpx` |

---

## 二十五、常见坑

### 25.1 忘记 Content-Type

前端发送 JSON 时应该写：

```javascript
headers: { "Content-Type": "application/json" }
```

否则后端或中间件可能不知道 body 是 JSON。

### 25.2 JSON 写成 Python 字典格式

错误：

```text
{'message': 'hello', 'ok': True}
```

正确：

```json
{"message": "hello", "ok": true}
```

### 25.3 把普通 JSON 当流解析

普通 JSON：

```json
{"speech": "hello"}
```

NDJSON：

```text
{"type":"speech_chunk","text":"hello"}
{"type":"stream_end","status":"ok"}
```

前端必须知道自己拿到的是哪一种。

### 25.4 以为网络 chunk 就是一条 JSON

错误思路：

```text
收到一个 chunk，直接 JSON.parse(chunk)
```

正确思路：

```text
把 chunk 加进 buffer
按换行切分
只 parse 完整行
```

### 25.5 流中出错后还想改状态码

一旦响应已经开始发送：

```text
HTTP 状态码基本已经定了。
```

所以流中错误要用：

```text
stream_error 事件
```

而不是试图再返回 `JSONResponse(status_code=500)`。

### 25.6 代理层缓冲

如果部署在 nginx 后面，可能出现：

```text
后端确实 yield 了很多次
但前端最后才一次性收到
```

要检查：

```text
X-Accel-Buffering: no
nginx proxy_buffering
客户端是否真的按流读取
```

---

## 二十六、调试 HTTP / JSON / 流式响应

### 26.1 看请求 payload

后端可以临时打印：

```python
print(json.dumps(payload, ensure_ascii=False, indent=2))
```

Akane 的 `print_debug` 就会打印：

```text
session_id
user_text
router_output
retrieval_result
final JSON
```

### 26.2 看响应头

用浏览器 DevTools 或 curl 看：

```powershell
curl -i -N -X POST http://127.0.0.1:8000/think `
  -H "Content-Type: application/json" `
  -d "{\"user_id\":\"desktop\",\"message\":\"hi\"}"
```

重点看：

```text
HTTP 状态码
Content-Type
X-Akane-Contract
Cache-Control
是否一行一行输出
```

### 26.3 看前端事件

可以在前端临时加：

```javascript
for await (const event of readNdjsonEvents(response)) {
  console.log("stream event", event);
}
```

重点看：

```text
事件有没有收到
type 是否符合预期
final_ui / final 是否出现
stream_end 是否出现
```

### 26.4 看测试

如果接口契约有测试，先看测试比盲读源码快：

```text
tests/test_backend_route_modules.py
```

它告诉你：

```text
接口应该返回什么
异常时应该怎么表现
前端依赖哪些字段
```

---

## 二十七、Akane 源码阅读路线

读 HTTP / JSON / 流式响应时，按这个顺序：

```text
1. desktop_pet_next/src/main.js
   看 sendThinkStream 如何构造 payload

2. companion_v01/routes/think.py
   看 /think 如何读取 payload、返回 StreamingResponse

3. companion_v01/engine.py
   看 process_turn_stream 如何 yield 事件

4. desktop_pet_next/src/main.js
   回来看 readNdjsonEvents 和 processThinkStream 如何消费事件

5. tests/test_backend_route_modules.py
   看测试如何固定通信契约
```

不要一上来读整个 `engine.py`。

更好的方式：

```text
先抓住 /think 这一条主线。
能画出“payload -> event -> render”的链路，再去读 RAG / Tool / Agent。
```

---

## 二十八、自己手搓一个迷你 Akane 流

这个例子不依赖 Akane 项目，可以单独理解流式通信。

新建 `mini_akane_stream.py`：

```python
import json
import time
from typing import Any, Generator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()


def process_turn_stream(payload: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
    message = str(payload.get("message") or "")

    yield {"type": "turn_start", "speaker": "Akane"}
    yield {"type": "ui", "emotion": "thinking"}

    reply = f"我收到了：{message}"
    for ch in reply:
        time.sleep(0.05)
        yield {"type": "speech_chunk", "text": ch}

    yield {
        "type": "final",
        "payload": {
            "emotion": "normal",
            "speech": reply,
        },
    }


@app.post("/think_once")
async def think_once(request: Request):
    payload = await request.json()
    message = str(payload.get("message") or "")
    return JSONResponse({
        "emotion": "normal",
        "speech": f"我收到了：{message}",
    })


@app.post("/think")
async def think(request: Request):
    payload = await request.json()

    def lines():
        partial = {"speech": ""}
        yield json.dumps({"type": "stream_start"}, ensure_ascii=False) + "\n"

        try:
            for event in process_turn_stream(payload):
                if event.get("type") == "speech_chunk":
                    partial["speech"] += str(event.get("text") or "")
                if event.get("type") == "final":
                    final_payload = event.get("payload") or {}
                    partial["speech"] = str(final_payload.get("speech") or partial["speech"])
                yield json.dumps(event, ensure_ascii=False) + "\n"
            status = "ok"
        except Exception as exc:
            status = "error"
            yield json.dumps({
                "type": "stream_error",
                "message": str(exc),
                "partial": partial,
            }, ensure_ascii=False) + "\n"

        yield json.dumps({
            "type": "stream_end",
            "status": status,
            "partial": partial,
        }, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")
```

启动：

```powershell
uvicorn mini_akane_stream:app --reload
```

普通请求：

```powershell
curl -X POST http://127.0.0.1:8000/think_once `
  -H "Content-Type: application/json" `
  -d "{\"message\":\"你好\"}"
```

流式请求：

```powershell
curl -N -X POST http://127.0.0.1:8000/think `
  -H "Content-Type: application/json" `
  -d "{\"message\":\"你好\"}"
```

你会看到：

```text
/think_once：等完整结果
/think：一行一行返回事件
```

这就是 Akane `/think_once` 和 `/think` 的最小模型。

---

## 二十九、这篇先记住

最重要的概念：

```text
HTTP：客户端和后端通信的协议
JSON：前后端传结构化数据的格式
payload：前端发给后端的请求数据
frame：后端返回给前端的最终状态
contract：前后端约定好的字段和事件格式
StreamingResponse：FastAPI 流式返回
NDJSON：一行一个 JSON，适合事件流
ReadableStream：前端读取流式响应
event.type：驱动前端行为的关键字段
```

Akane 主线：

```text
sendThinkStream
→ POST /think
→ request.json()
→ engine.process_turn_stream
→ yield event
→ json.dumps(event) + "\n"
→ StreamingResponse
→ readNdjsonEvents
→ processThinkStream
→ renderPayload
```

---

## 三十、我自己的复述

HTTP / JSON / 流式响应这部分，本质上是在学 Akane 的“通信血管”。

FastAPI 只是把路由搭起来，真正让桌宠有实时感的是：

```text
后端不等全部算完才返回
而是把中间状态包装成一个个 JSON 事件
通过 NDJSON 一行一行发给前端
前端根据 event.type 一步步更新 UI
```

所以读 Akane 的时候，不要只盯着某个函数。

要顺着数据走：

```text
用户消息怎么变成 payload
payload 怎么进入 /think
/think 怎么调用 engine
engine 怎么 yield 事件
事件怎么变成 NDJSON
前端怎么 parse
前端怎么渲染
```

这条链路看懂后，再学后面的 LLM、RAG、Tool Calling、Agent 工程就不会散。

