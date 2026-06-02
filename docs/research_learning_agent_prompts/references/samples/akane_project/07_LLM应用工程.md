---
tags:
  - akane/llm-engineering
  - llm/application
  - prompt-engineering
  - json-output
created: 2026-05-23
---

# LLM 应用工程

> LLM 应用工程不是“会调 API”这么简单。  
> 真正的重点是：把一个不稳定、会自由发挥的模型，包成一个项目里可以依赖的工程模块。

这一篇开始，我们正式进入 Akane 项目里最核心的部分：

```text
用户输入
-> 组织上下文
-> 构造 prompt
-> 调用大模型
-> 要求模型输出固定 JSON
-> 解析 / 修复 / fallback
-> 流式吐给前端
-> 最终归一化为业务结果
```

你前面已经学过：

```text
Python 工程化
SQLite 持久化
FastAPI 后端
HTTP / JSON / 流式响应
unittest 测试
状态机与业务建模
```

现在这一篇的作用，就是把这些知识连接到 LLM 项目上。

---

## 一、这一篇到底学什么

先说清楚边界。

这一篇不是学 Transformer 原理，也不是学训练模型。

底层模型知识你已经在 MiniMind 那条线里学了：

```text
token
embedding
attention
transformer block
loss
训练
推理
```

Akane 项目这一条线更偏工程：

```text
我已经有一个模型服务了，怎么把它接进真实软件？
```

所以这一篇重点是：

```text
LLM API 怎么封装
prompt 怎么组织
模型输出怎么约束为 JSON
输出坏了怎么办
流式输出怎么边生成边展示
不同模型供应商怎么兼容
怎么测试 LLM 应用层逻辑
```

一句话：

```text
模型原理解决“模型为什么能生成”。
LLM 应用工程解决“模型生成的东西怎么被项目稳定使用”。
```

---

## 二、为什么不能直接把 LLM 当普通函数

普通函数一般是这样：

```python
def add(a, b):
    return a + b


print(add(1, 2))  # 3
```

只要输入一样，输出基本一样。

但 LLM 不一样。

你可以把它想成：

```text
输入：一大段 prompt
输出：一段可能符合要求、也可能跑偏的文本
```

它有几个特点：

```text
1. 输出可能不是合法 JSON
2. 输出可能夹带解释文本
3. 输出可能缺字段
4. 输出可能字段类型不对
5. 网络请求可能失败
6. 模型服务可能超时
7. 流式输出可能只输出一半
8. 不同平台 API 格式不完全一样
```

所以真实项目里不能这样写：

```python
reply = call_model(user_message)
print(reply["speech"])
```

因为模型可能返回：

```text
好的，以下是 JSON：

{
  "speech": "我在哦。"
}
```

也可能返回：

```text
{"speech": "我在哦。"
```

也可能直接网络异常。

因此工程里要多包几层：

```text
模型服务
-> LLM client
-> LLMRuntime
-> PromptBuilder
-> Engine 业务层
-> FastAPI route
```

Akane 就是这样做的。

---

## 三、Akane 的 LLM 调用链路总览

先看 Akane 的实际链路。

核心文件：

```text
services/llm_client.py
companion_v01/llm_runtime.py
companion_v01/prompt_builder.py
companion_v01/engine.py
```

大致流程：

```text
engine.py
  |
  | 1. 收集用户消息、记忆、视觉状态、附件、任务上下文
  v
PromptBuilder
  |
  | 2. 生成 system_prompt / user_prompt / fallback
  v
LLMRuntime
  |
  | 3. 选择 chat 模型或 aux 模型
  | 4. 构造 messages
  | 5. 调模型
  | 6. 解析 JSON / 修复部分 JSON / fallback
  v
services.llm_client
  |
  | 7. 兼容 OpenAI / Anthropic / Ollama
  v
真实模型服务
```

再换成“数据流”的视角：

```text
用户：今天有点累

-> user_message
-> recent_raw
-> memory_text
-> visual_defaults
-> resource_context
-> persona_context

-> system_prompt
-> user_prompt
-> fallback

-> LLM 输出 JSON

{
  "emotion": "soft",
  "speech": "辛苦啦，今天先慢一点也可以。",
  "speech_segments": [],
  "tool_call": null
}

-> normalize_final_output
-> 前端显示文字、表情、场景、工具调用等
```

这里最重要的一点是：

```text
Akane 不是让模型随便输出一段话。
Akane 要求模型输出一个固定结构的业务 JSON。
```

这就是 LLM 应用工程的核心思路。

---

## 四、LLMRuntime：把模型调用封装成稳定接口

Akane 的 `LLMRuntime` 在：

```text
companion_v01/llm_runtime.py
```

它大概提供四类能力：

```python
call_aux_json()
call_chat_json()
call_aux_ndjson()
stream_chat_json()
```

含义分别是：

| 方法 | 作用 |
|---|---|
| `call_aux_json` | 调辅助模型，返回普通 JSON |
| `call_chat_json` | 调聊天模型，返回普通 JSON |
| `call_aux_ndjson` | 调辅助模型，流式返回 NDJSON 事件 |
| `stream_chat_json` | 调聊天模型，边生成边返回语音片段，最后得到 JSON |

为什么要分 aux 和 chat？

```text
aux 模型：偏工具型、判断型、路由型，温度低，输出要稳定
chat 模型：偏最终回复，温度可以高一点，更自然
```

比如：

```text
router 判断要不要检索记忆：aux
verifier 判断检索结果是否匹配：aux
最终对用户说话：chat
```

这是一种很经典的 LLM 应用拆法：

```text
让便宜 / 快 / 稳定的模型做中间判断
让表达能力更好的模型做最终输出
```

---

## 五、ModelBundle：把 client 和 model 绑在一起

Akane 里有一个小 dataclass：

```python
from dataclasses import dataclass
from typing import Any


@dataclass
class ModelBundle:
    client: Any
    model: str
```

它表示：

```text
这个模型调用需要两样东西：
1. client：怎么请求模型服务
2. model：具体用哪个模型名
```

比如 Akane 初始化时会构造两个 bundle：

```python
def _build_aux_bundle(self) -> ModelBundle:
    client = build_llm_client(
        api_key=config.AUX_API_KEY,
        base_url=config.AUX_BASE_URL,
        protocol=getattr(config, "AUX_API_PROTOCOL", "auto"),
        timeout=90.0,
        max_retries=0,
    )
    return ModelBundle(client=client, model=config.AUX_MODEL_NAME)


def _build_chat_bundle(self) -> ModelBundle:
    client = build_llm_client(
        api_key=config.CHAT_API_KEY,
        base_url=config.CHAT_BASE_URL,
        protocol=getattr(config, "CHAT_API_PROTOCOL", "auto"),
        timeout=120.0,
        max_retries=0,
    )
    return ModelBundle(client=client, model=config.CHAT_MODEL_NAME)
```

这样业务代码就不需要到处关心：

```text
当前用哪个 key
当前 base_url 是什么
当前是 chat 模型还是 aux 模型
```

它只需要说：

```text
我要调聊天模型
我要调辅助模型
```

这就是封装的意义。

---

## 六、system_prompt 和 user_prompt

LLM 调用一般会把 prompt 分成两部分：

```text
system_prompt：告诉模型“你是谁、必须遵守什么规则、输出什么格式”
user_prompt：告诉模型“这一轮用户说了什么、当前上下文是什么”
```

Akane 的最终回复 prompt 是在：

```text
companion_v01/prompt_builder.py
```

它会返回一个结构：

```python
{
    "system_prompt": "...",
    "user_prompt": "...",
    "fallback": {...},
    "visual_defaults": {...},
    "debug_enabled": False,
}
```

你可以这样理解：

```text
system_prompt = 规则层
user_prompt   = 数据层
fallback      = 兜底层
```

### 1. system_prompt 放什么

通常放这些东西：

```text
角色设定
输出格式
禁止事项
工具调用规则
当前客户端模式规则
debug 模式规则
```

比如 Akane 里会把这些内容合并进 system_prompt：

```text
基础人格 prompt
当前模式 prompt
工具调用 prompt
persona 当前状态
```

### 2. user_prompt 放什么

通常放这些东西：

```text
当前用户消息
近期聊天记录
长期记忆摘要
语义记忆摘要
当前视觉状态
可用资源清单
附件 / 文件 / 任务上下文
当前时间
```

Akane 的 `engine.py` 会先收集大量上下文：

```text
raw_text
current_message_text
episodic_summary_text
semantic_summary_text
memory_text
current_visual_context
resource_context
extra_context
persona_context
```

然后交给 `PromptBuilder` 组装。

### 3. fallback 放什么

fallback 是模型失败时的兜底答案。

Akane 的最终回复 fallback 大概长这样：

```python
fallback = {
    "emotion": "normal",
    "speech": "我在哦，稍等一下。",
    "speech_segments": [],
    "tool_call": None,
    "code_snippet": "",
    "memory_tags": "",
    "status": "final",
    "score": 0.0,
    "choices": [],
    "character": {"outfit": "default"},
    "scene": {
        "major": "default",
        "minor": "default",
        "background": "evening_classroom",
        "bgm": "",
    },
    "persona": {"active": ""},
}
```

fallback 的意义是：

```text
哪怕模型挂了，业务层也能得到一个结构正确的结果。
```

这在真实项目里非常重要。

---

## 七、一个最小 PromptBuilder 示例

先不要看 Akane 那么复杂的 prompt。

我们自己写一个最小版。

```python
import json


def build_chat_prompt(user_message: str) -> dict:
    system_prompt = """
你是一个学习伙伴。
你必须只输出 JSON，不要输出 Markdown。
字段固定为 emotion, speech, tool_call。
emotion 是表情，speech 是要对用户说的话，tool_call 暂时填 null。
"""

    user_prompt = f"""
用户消息：
{user_message}

请根据用户消息回复。
"""

    fallback = {
        "emotion": "normal",
        "speech": "我在哦，我们慢慢来。",
        "tool_call": None,
    }

    return {
        "system_prompt": system_prompt.strip(),
        "user_prompt": user_prompt.strip(),
        "fallback": fallback,
    }


context = build_chat_prompt("我今天学不动了")

print(context["system_prompt"])
print("-" * 30)
print(context["user_prompt"])
print("-" * 30)
print(json.dumps(context["fallback"], ensure_ascii=False, indent=2))
```

运行后你会看到：

```text
system_prompt：规则
user_prompt：本轮输入
fallback：兜底 JSON
```

这就是 Akane 的 PromptBuilder 的简化版。

真实项目只是把上下文变多了：

```text
用户消息
聊天记录
记忆
视觉状态
资源清单
工具列表
客户端模式
persona
```

本质没变。

---

## 八、为什么要让模型输出 JSON

如果模型只输出自然语言：

```text
辛苦啦，今天先休息一下也没关系。
```

前端只能显示文字。

但 Akane 不只是显示文字，还要控制：

```text
表情 emotion
台词 speech
台词分段 speech_segments
工具调用 tool_call
服装 character.outfit
场景 scene
音乐 bgm
选项 choices
debug thought
```

所以模型输出要像这样：

```json
{
  "emotion": "soft",
  "speech": "辛苦啦，今天先慢一点也可以。",
  "speech_segments": [],
  "tool_call": null,
  "character": {
    "outfit": "default"
  },
  "scene": {
    "major": "daily",
    "minor": "study",
    "background": "evening_classroom",
    "bgm": ""
  }
}
```

这样业务层才能写：

```python
emotion = result["emotion"]
speech = result["speech"]
tool_call = result["tool_call"]
```

这就是结构化输出。

一句话：

```text
自然语言适合给人看。
JSON 适合给程序用。
```

LLM 应用工程里，经常要把这两者结合起来：

```text
让模型用自然语言思考和表达
但最终必须落进程序能消费的结构
```

---

## 九、JSON 输出的关键：字段固定

让模型输出 JSON，不只是说一句“请输出 JSON”。

你要明确告诉它：

```text
有哪些字段
字段顺序是什么
字段类型是什么
缺省值是什么
哪些字段可以为 null
不要额外输出解释
不要包 Markdown 代码块
```

Akane 的测试里专门检查了最终输出 schema 的提示：

```text
字段固定为 emotion, speech, speech_segments, tool_call
tool_call 必须放在 speech_segments 字段之后
```

为什么字段顺序也重要？

因为 Akane 有流式输出。

如果模型先输出：

```json
{
  "emotion": "happy",
  "speech": "我在哦。"
}
```

那么后端可以在完整 JSON 结束之前，就从流里抓到：

```text
emotion
speech 的增量文本
```

然后前端可以更早显示。

如果模型把 `speech` 放得很后面，用户就会等很久。

所以对于流式体验来说，字段顺序不是小事。

---

## 十、解析模型 JSON：最小版

模型返回的内容可能很干净：

```json
{"emotion":"happy","speech":"我在哦。","tool_call":null}
```

也可能包了 Markdown：

````text
```json
{"emotion":"happy","speech":"我在哦。","tool_call":null}
```
````

也可能前后带废话：

```text
好的，下面是结果：
{"emotion":"happy","speech":"我在哦。","tool_call":null}
```

所以解析时要做容错。

写一个简单版：

```python
import json
import re


def extract_json(text: str) -> dict | None:
    raw = text.strip()
    if not raw:
        return None

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


examples = [
    '{"emotion":"happy","speech":"我在哦。","tool_call":null}',
    '好的：{"emotion":"happy","speech":"我在哦。","tool_call":null}',
    '不是 JSON',
]

for item in examples:
    print(extract_json(item))
```

输出大概是：

```text
{'emotion': 'happy', 'speech': '我在哦。', 'tool_call': None}
{'emotion': 'happy', 'speech': '我在哦。', 'tool_call': None}
None
```

Akane 的 `_extract_json` 也是这个思路，不过更稳一点：

```text
1. 先尝试整段 json.loads
2. 再尝试从文本中找第一个完整 JSON object
3. 再用正则兜底
4. 失败就返回 None
```

---

## 十一、fallback：模型失败时不能让业务崩掉

假设你写：

```python
result = extract_json(model_text)
print(result["speech"])
```

如果 `extract_json` 返回 `None`，就会报错：

```text
TypeError: 'NoneType' object is not subscriptable
```

工程里通常要这样写：

```python
def parse_or_fallback(text: str, fallback: dict) -> dict:
    parsed = extract_json(text)
    if isinstance(parsed, dict):
        return parsed
    return dict(fallback)


fallback = {
    "emotion": "normal",
    "speech": "我在哦，不过刚才模型输出有点乱，我们继续。",
    "tool_call": None,
}

print(parse_or_fallback("不是 JSON", fallback))
```

输出：

```text
{'emotion': 'normal', 'speech': '我在哦，不过刚才模型输出有点乱，我们继续。', 'tool_call': None}
```

Akane 的 `_call_json` 核心也是这个逻辑：

```python
try:
    response = self._create_completion(...)
    content = self._extract_text(response)
    parsed = self._extract_json(content)
    if isinstance(parsed, dict):
        return parsed
    recovered = self._recover_partial_chat_json(content, fallback=fallback)
    if isinstance(recovered, dict):
        return recovered
except Exception:
    self._record_metric("errors")
    pass
return dict(fallback)
```

这段代码说明了一个很重要的工程原则：

```text
外部服务可以失败，但项目内部的数据结构不要崩。
```

---

## 十二、部分 JSON 修复：救回模型已经说出的内容

有时候模型不是完全失败，而是输出了一半。

比如：

```text
{"emotion":"happy","speech":"我在哦，今天我们继续
```

这不是合法 JSON。

但里面其实已经有有用信息：

```text
emotion = happy
speech = 我在哦，今天我们继续
```

所以 Akane 有一个修复逻辑：

```text
_recover_partial_chat_json
```

它会尽量从不完整 JSON 里捞出：

```text
emotion
speech
speech_segments
```

我们写一个极简版感受一下。

```python
import re


def recover_partial(text: str, fallback: dict) -> dict:
    result = dict(fallback)

    emotion_match = re.search(r'"emotion"\s*:\s*"([^"]*)', text)
    speech_match = re.search(r'"speech"\s*:\s*"([^"]*)', text)

    if emotion_match:
        result["emotion"] = emotion_match.group(1)

    if speech_match:
        result["speech"] = speech_match.group(1)

    return result


fallback = {
    "emotion": "normal",
    "speech": "我在哦。",
    "tool_call": None,
}

broken = '{"emotion":"happy","speech":"今天已经学了很多，先缓一缓也没关系'

print(recover_partial(broken, fallback))
```

输出：

```text
{'emotion': 'happy', 'speech': '今天已经学了很多，先缓一缓也没关系', 'tool_call': None}
```

Akane 的版本比这个复杂，因为它要处理：

```text
转义字符
Unicode
字符串边界
speech_segments 数组
最多取前几个分段
```

但核心思想就是：

```text
完整 JSON 解析失败时，不要立刻放弃。
先尽量从已生成文本里恢复用户能看到的内容。
```

这对流式聊天尤其重要。

---

## 十三、普通 JSON 调用：call_chat_json

普通调用是：

```text
请求模型
等模型完整返回
解析 JSON
得到最终结果
```

在 Akane 里，对应：

```python
result = self.llm.call_chat_json(
    system_prompt=str(generation_context["system_prompt"]),
    user_prompt=str(generation_context["user_prompt"]),
    fallback=dict(generation_context["fallback"]),
    temperature=0.7,
    prompt_cache_key="chat:final",
    user_images=user_images,
)
```

这里每个参数含义：

| 参数 | 含义 |
|---|---|
| `system_prompt` | 规则、角色、输出格式 |
| `user_prompt` | 当前输入和上下文 |
| `fallback` | 失败时的兜底 JSON |
| `temperature` | 随机性，越高越发散 |
| `prompt_cache_key` | prompt 缓存提示 |
| `user_images` | 用户上传的图片输入 |

普通调用适合：

```text
不需要边生成边展示
流程比较短
对实时体验要求不高
```

但聊天产品通常更喜欢流式。

---

## 十四、流式 JSON 调用：stream_chat_json

流式调用不是等完整结果，而是模型生成一点，后端就收到一点。

大概像这样：

```text
chunk1: {"emotion":"happy",
chunk2: "speech":"我在
chunk3: 哦，今天
chunk4: 我们继续。"
chunk5: ,"tool_call":null}
```

前端如果等完整 JSON 才显示，就会很慢。

Akane 的做法是：

```text
一边收 chunk
一边从顶层 JSON 里抓 speech 字段的增量文本
一边 yield speech_chunk 给前端
最后再解析完整 JSON
```

这就是 `_TopLevelJSONStreamTap` 的作用。

它像一个“流式 JSON 观察器”：

```text
不一定完整解析整个 JSON
只盯住顶层字段 emotion 和 speech
发现 speech 新增文本就吐事件
```

Akane 的 stream 流程大概是：

```python
tap = _TopLevelJSONStreamTap()
raw_parts = []

for chunk in response:
    text = self._extract_stream_text(chunk)
    raw_parts.append(text)

    for event in tap.feed(text):
        yield event

raw_text = "".join(raw_parts)
parsed = self._extract_json(raw_text)
```

用户感受到的是：

```text
不是等模型全部说完才出现文字，
而是像打字机一样逐步出现。
```

---

## 十五、写一个最小流式 tap

完整 JSON 流式解析比较复杂。

我们先写一个非常小的版本，只处理这种理想情况：

```json
{"speech":"你好呀，今天继续学习。"}
```

代码：

```python
class MiniSpeechTap:
    def __init__(self):
        self.in_speech = False
        self.key_seen = False
        self.buffer = ""
        self.speech = ""

    def feed(self, chunk: str) -> list[dict]:
        events = []
        self.buffer += chunk

        if not self.key_seen:
            marker = '"speech":"'
            index = self.buffer.find(marker)
            if index >= 0:
                self.key_seen = True
                self.in_speech = True
                self.buffer = self.buffer[index + len(marker):]

        if self.in_speech:
            end = self.buffer.find('"')
            if end >= 0:
                delta = self.buffer[:end]
                self.in_speech = False
            else:
                delta = self.buffer
                self.buffer = ""

            if delta:
                self.speech += delta
                events.append({"type": "speech_chunk", "text": delta})

        return events


tap = MiniSpeechTap()

chunks = [
    '{"emotion":"happy",',
    '"speech":"你好呀，',
    '今天继续学习。',
    '","tool_call":null}',
]

for chunk in chunks:
    for event in tap.feed(chunk):
        print(event)

print("完整 speech:", tap.speech)
```

输出：

```text
{'type': 'speech_chunk', 'text': '你好呀，'}
{'type': 'speech_chunk', 'text': '今天继续学习。'}
完整 speech: 你好呀，今天继续学习。
```

这个小例子很粗糙，但能帮你理解 Akane 的核心思想：

```text
模型输出的是 JSON 文本流。
后端在文本流里提前提取可展示内容。
```

Akane 的 `_TopLevelJSONStreamTap` 做得更完整，它会处理：

```text
JSON 层级 depth
字符串状态 in_string
转义 escape
Unicode 转义
当前 key
当前 value
speech 增量
emotion 捕获
speech_segment 分句
```

这就需要前面学过的“状态机”知识了。

---

## 十六、流式事件和最终结果不是一回事

这个点很容易混。

`stream_chat_json` 里会产生两类东西：

```text
1. 中途 yield 出来的事件
2. 生成器最后 return 的 ChatJSONStreamResult
```

中途事件给前端实时显示：

```python
{"type": "speech_chunk", "text": "我在哦"}
{"type": "speech_segment", "text": "我在哦。"}
```

最后结果给业务层做归一化：

```python
ChatJSONStreamResult(
    parsed={...},
    raw_text="...",
    elapsed_ms=1234.5,
    error="",
    latest_emotion="happy",
    latest_speech="我在哦。",
    stopped_early=False,
    early_tool_call=None,
)
```

Akane 的 `engine.py` 里是这样使用的：

```python
yield {
    "type": "turn_start",
    "speaker": PERSONA.assistant_name,
}

stream_result = yield from self.llm.stream_chat_json(...)

if str(stream_result.error or "").strip():
    yield {
        "type": "stream_error",
        "message": str(stream_result.error),
        "partial": {
            "emotion": str(stream_result.latest_emotion or ""),
            "speech": str(stream_result.latest_speech or ""),
        },
    }

return self._normalize_final_output(...)
```

这里的关键是：

```text
yield from 不只是把子生成器的事件转发出去。
它还能拿到子生成器 return 的最终值。
```

这正好把前面 Python 工程化里学过的 generator 串起来了。

---

## 十七、early_tool_call：提前发现工具调用

Akane 的流式最终回复里还有一个很工程化的优化：

```text
如果模型一开始就输出 tool_call，并且这个 tool_call 合法，
那可以提前停止继续生成。
```

大概场景：

```json
{
  "tool_call": {
    "name": "create_file",
    "arguments": {
      "filename": "notes.md"
    }
  }
}
```

如果模型已经明确要调工具，就没必要继续等它把整段回复都生成完。

Akane 里有一个方法：

```text
_try_extract_leading_tool_call
```

它会检查流式输出开头是不是：

```text
{
  "tool_call": ...
}
```

如果拿到对象，并且通过 `early_tool_call_validator` 校验：

```python
if probe_state == "object" and isinstance(probe_call, dict):
    if early_tool_call_validator is None or early_tool_call_validator(probe_call):
        early_tool_call = dict(probe_call)
        stopped_early = True
        break
```

这属于 LLM 应用工程里很常见的思路：

```text
不要把模型输出当一整坨文本。
它其实是业务事件流。
能提前确定的动作，就提前处理。
```

不过 Tool Calling 本身会在第 09 篇专门讲。

这一篇你先记住：

```text
LLM 输出可以被设计成“可解析、可中断、可验证”的业务协议。
```

---

## 十八、_build_completion_kwargs：把 prompt 变成 API 请求

Akane 最后真正发给模型的 payload 是在：

```text
_build_completion_kwargs
```

简化后大概是：

```python
payload = {
    "model": bundle.model,
    "temperature": temperature,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ],
}

if stream:
    payload["stream"] = True

if json_mode:
    payload["response_format"] = {"type": "json_object"}
```

这就是 OpenAI 风格 Chat Completions API 的常见结构：

```text
model：模型名
temperature：随机性
max_tokens：最大输出 token 数（防止模型无限生成 / 控制成本）
messages：对话消息
stream：是否流式
response_format：是否要求 JSON 模式
```

> `max_tokens` 是工程里容易被忽略但很重要的参数——它直接决定一次调用的成本和最大耗时。Akane 的 aux 和 chat 调用通常都有对应的限制。

你目前不需要死记 API 参数。

先理解这个结构就够了：

```text
PromptBuilder 产出 prompt。
LLMRuntime 把 prompt 包装成模型服务能理解的 payload。
```

---

## 十九、user_images：多模态输入的工程形状

Akane 的 `call_chat_json` 和 `stream_chat_json` 都支持：

```python
user_images: list[dict[str, Any]] | None = None
```

内部会把图片转成：

```python
{"type": "image_url", "image_url": {"url": data_url}}
```

然后 user content 就不再是一个字符串，而是：

```python
[
    {"type": "text", "text": user_prompt},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
]
```

这说明多模态输入在工程上通常长这样：

```text
文本 prompt
+ 图片 block
+ 统一交给模型
```

注意 Akane 做了限制：

```text
最多取前 5 张图片
必须是 data:image/ 开头
```

这也是工程习惯：

```text
外部输入必须过滤。
不要什么都直接塞给模型服务。
```

---

## 二十、services/llm_client.py：兼容不同模型服务

Akane 的底层 LLM client 在：

```text
services/llm_client.py
```

它支持三类协议：

```text
openai
anthropic
ollama
```

入口函数是：

```python
def build_llm_client(
    *,
    api_key: str,
    base_url: str,
    timeout: float = 60.0,
    max_retries: int = 0,
    protocol: str = "auto",
):
    ...
```

### 1. 协议识别

Akane 会先判断协议：

```python
def normalize_api_protocol(protocol: str = "", base_url: str = "") -> str:
    explicit = str(protocol or "").strip().lower()
    if explicit in {"openai", "anthropic", "ollama"}:
        return explicit

    lowered = str(base_url or "").strip().lower()
    if "/claude" in lowered or "anthropic" in lowered:
        return "anthropic"
    if "11434" in lowered or "ollama" in lowered:
        return "ollama"
    return "openai"
```

意思是：

```text
如果 config 明确写了协议，就用明确的。
否则根据 base_url 猜。
```

### 2. Ollama base_url 归一化

Ollama 本地服务常见地址：

```text
http://127.0.0.1:11434
```

但 OpenAI 风格接口经常需要：

```text
http://127.0.0.1:11434/v1
```

所以 Akane 会补 `/v1`：

```python
def normalize_base_url(*, protocol: str, base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if protocol == "ollama":
        normalized = normalized or "http://127.0.0.1:11434"
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
    return normalized
```

### 3. Anthropic 转 OpenAI 风格

Akane 内部希望都用：

```python
client.chat.completions.create(...)
```

但 Anthropic 原生接口不是这个形状。

所以项目写了一个兼容层：

```text
AnthropicCompatClient
```

它把 Anthropic 的 messages API 包装成类似 OpenAI 的返回结构。

这就是适配器模式：

```text
外部 API 格式不同
内部统一成项目自己想用的格式
```

这是非常经典的软件工程知识。

---

## 二十一、为什么要做 client 兼容层

如果没有兼容层，业务代码可能会变成：

```python
if provider == "openai":
    response = openai_client.chat.completions.create(...)
elif provider == "anthropic":
    response = requests.post(...)
elif provider == "ollama":
    response = ollama_client.chat(...)
```

然后项目里到处都是分支。

有了兼容层之后，业务代码只需要：

```python
response = bundle.client.chat.completions.create(**payload)
```

这样上层不关心：

```text
模型来自 OpenAI
模型来自 Anthropic
模型来自本地 Ollama
模型来自兼容 OpenAI API 的第三方服务
```

这一层的价值是：

```text
把供应商差异限制在 services/llm_client.py 里。
不要污染 engine.py 和 prompt_builder.py。
```

---

## 二十二、prompt cache hints：工程优化，不是核心知识

Akane 里还有 prompt cache 相关逻辑：

```text
prompt_cache_key
prompt_cache_retention
```

你现在只需要知道：

```text
这是为了让模型服务复用某些 prompt 前缀，从而降低延迟或成本。
```

Akane 会非常谨慎地发送它：

```text
只有 openai 协议
只有看起来是官方 OpenAI base_url
或者配置强制开启
才发送 prompt cache hints
```

如果模型服务不支持这些参数，Akane 会重试去掉它们：

```python
def _create_completion(self, *, bundle: ModelBundle, payload: dict[str, Any]) -> Any:
    try:
        return bundle.client.chat.completions.create(**payload)
    except TypeError:
        stripped = self._without_prompt_cache_hints(payload)
        if stripped != payload:
            return bundle.client.chat.completions.create(**stripped)
        raise
```

这一段体现了工程经验：

```text
高级优化要能优雅降级。
不能因为一个优化参数导致整个模型调用失败。
```

你现在不用深学 prompt cache。

优先级更高的是：

```text
prompt 组织
JSON 输出
fallback
流式解析
client 兼容
测试
```

---

## 二十三、metrics：知道模型调用发生了什么

Akane 的 `LLMRuntime` 里有一组计数：

```python
self._metrics = {
    "aux_json_calls": 0,
    "chat_json_calls": 0,
    "aux_ndjson_calls": 0,
    "chat_stream_calls": 0,
    "errors": 0,
}
```

每次调用会记录：

```python
self._record_metric("chat_stream_calls")
```

出错会记录：

```python
self._record_metric("errors")
```

为什么需要？

因为 LLM 应用出问题时，你要知道：

```text
是调用次数太多？
是错误率上升？
是 aux 模型挂了？
是 chat 流式出错？
```

这就是可观测性。

现在 Akane 只是简单计数。

更大的生产项目可能会记录：

```text
请求耗时
token 数
模型名
错误类型
重试次数
用户 id
trace id
成本估算
```

但基础思想一样：

```text
LLM 调用不是黑盒，要留下可排查的信息。
```

---

## 二十四、NDJSON：让辅助模型输出事件流

第 04 篇已经讲过 NDJSON。

这里把它和 LLM 应用连起来。

Akane 的辅助模型有时会输出一行一行的事件：

```text
{"type":"analysis","text":"当前问题可能需要检索"}
{"type":"decision","need_retrieval":true}
```

这时候不适合等完整 JSON。

更适合：

```text
每来一行就解析一行
每个事件都可以被业务层处理
```

Akane 的 `_call_ndjson` 大概做：

```python
buffer += text
buffer, parsed_events = self._drain_ndjson_buffer(buffer)

for event in parsed_events:
    events.append(event)
    if on_event and on_event(event):
        stopped_early = True
        break
```

这和最终聊天 JSON 不同：

```text
chat_json：最终要一个完整业务对象
aux_ndjson：中途就可以产生多个判断事件
```

你可以这样区分：

```text
JSON：一次性结果
NDJSON：事件序列
```

---

## 二十五、把模型输出当作协议

这是这一篇最重要的观念。

很多人学 LLM 应用时会这样想：

```text
prompt 写好一点，模型就会听话。
```

但真实工程里更稳的想法是：

```text
模型输出是一个协议。
协议要定义、解析、验证、兜底、测试。
```

比如 Akane 最终回复协议：

```json
{
  "emotion": "happy",
  "speech": "我在哦。",
  "speech_segments": [],
  "tool_call": null,
  "character": {
    "outfit": "default"
  },
  "scene": {
    "major": "daily",
    "minor": "room",
    "background": "evening_classroom",
    "bgm": ""
  }
}
```

这就像后端接口协议一样。

你要关心：

```text
字段是否存在
类型是否正确
坏输出怎么处理
是否兼容老版本
前端依赖哪些字段
测试是否覆盖边界
```

所以 LLM 应用工程其实不是玄学。

它非常软件工程。

---

## 二十六、一个完整的迷你 LLMRuntime

下面写一个可运行的小版本。

它不真的请求模型，而是用 fake model 模拟返回。

```python
import json
import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class MiniResult:
    parsed: dict
    raw_text: str
    error: str = ""


def extract_json(text: str) -> dict | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


class MiniLLMRuntime:
    def __init__(self, model_func: Callable[[str, str], str]):
        self.model_func = model_func
        self.metrics = {
            "chat_json_calls": 0,
            "errors": 0,
        }

    def call_chat_json(self, *, system_prompt: str, user_prompt: str, fallback: dict) -> dict:
        self.metrics["chat_json_calls"] += 1
        try:
            raw_text = self.model_func(system_prompt, user_prompt)
            parsed = extract_json(raw_text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            self.metrics["errors"] += 1
        return dict(fallback)


def fake_model(system_prompt: str, user_prompt: str) -> str:
    if "学不动" in user_prompt:
        return '{"emotion":"soft","speech":"那今天就慢一点，我们先保住节奏。","tool_call":null}'
    return "坏输出"


runtime = MiniLLMRuntime(fake_model)

context = {
    "system_prompt": "你必须输出 JSON。",
    "user_prompt": "我今天学不动了",
    "fallback": {
        "emotion": "normal",
        "speech": "我在哦，我们慢慢来。",
        "tool_call": None,
    },
}

result = runtime.call_chat_json(**context)
print(result)
print(runtime.metrics)
```

输出：

```text
{'emotion': 'soft', 'speech': '那今天就慢一点，我们先保住节奏。', 'tool_call': None}
{'chat_json_calls': 1, 'errors': 0}
```

这个小例子已经包含 LLMRuntime 的核心：

```text
接收 prompt
调用模型
解析 JSON
失败 fallback
记录 metrics
```

真实项目就是把每一步做得更完整。

---

## 二十七、LLM 应用测试应该测什么

很多人觉得：

```text
LLM 输出不稳定，所以没法测。
```

这是误解。

你不一定要测试“模型会不会回答得好”。

但你可以测试“你自己的工程包装是否可靠”。

Akane 里有相关测试：

```text
tests/test_llm_runtime_stream.py
tests/test_prompt_builder.py
```

它们重点测的是：

```text
流式 JSON 能不能吐 speech_chunk
tool_call 能不能提前识别
Markdown 代码块里的 JSON 能不能提取
content blocks 能不能 flatten
坏 JSON 能不能部分恢复
PromptBuilder 是否包含关键规则
fallback 字段顺序是否正确
不同客户端模式 prompt 是否正确
```

这就是 LLM 应用测试的正确方向。

不是测：

```text
今天模型心情好不好
```

而是测：

```text
模型不管怎么输出，我的解析器、fallback、prompt 构造、协议约束能不能工作。
```

---

## 二十八、写一个 JSON 解析测试

用 `unittest` 写一个小测试。

```python
import unittest


class ExtractJsonTests(unittest.TestCase):
    def test_extract_plain_json(self):
        text = '{"emotion":"happy","speech":"我在哦。","tool_call":null}'

        result = extract_json(text)

        self.assertEqual(result["emotion"], "happy")
        self.assertEqual(result["speech"], "我在哦。")
        self.assertIsNone(result["tool_call"])

    def test_extract_json_with_prefix(self):
        text = '好的：{"emotion":"happy","speech":"我在哦。","tool_call":null}'

        result = extract_json(text)

        self.assertEqual(result["speech"], "我在哦。")

    def test_bad_json_returns_none(self):
        result = extract_json("不是 JSON")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
```

这种测试非常有价值。

因为它保护的是：

```text
你的工程层是否能处理模型的常见坏习惯。
```

---

## 二十九、写一个 fallback 测试

继续测试 mini runtime。

```python
import unittest


class MiniLLMRuntimeTests(unittest.TestCase):
    def test_bad_model_output_uses_fallback(self):
        def bad_model(system_prompt: str, user_prompt: str) -> str:
            return "我不想输出 JSON"

        runtime = MiniLLMRuntime(bad_model)

        fallback = {
            "emotion": "normal",
            "speech": "我在哦。",
            "tool_call": None,
        }

        result = runtime.call_chat_json(
            system_prompt="必须输出 JSON",
            user_prompt="你好",
            fallback=fallback,
        )

        self.assertEqual(result, fallback)


if __name__ == "__main__":
    unittest.main()
```

如果以后你改解析逻辑改坏了，这个测试会立刻提醒你。

这就是为什么 LLM 项目依然需要测试。

AI 能帮你写代码，但不能替你保证系统边界永远可靠。

---

## 三十、PromptBuilder 也要测试

prompt 看起来像文本，为什么也要测试？

因为 prompt 在 LLM 应用里就是协议的一部分。

比如 Akane 的测试会检查：

```python
self.assertIn("字段固定为 emotion, speech, speech_segments, tool_call", persona.final_fast_mode_prompt)
self.assertIn("tool_call 必须放在 speech_segments 字段之后", persona.final_system_prompt)
```

这不是为了测试中文句子写得漂不漂亮。

而是在保护关键约束：

```text
模型必须输出哪些字段
字段顺序不能随便改
tool_call 位置会影响流式提前识别
```

如果以后有人改 prompt，把这些规则删了，测试就会失败。

所以 prompt 测试的意义是：

```text
保护 LLM 协议。
```

---

## 三十一、LLM 应用里的 temperature

`temperature` 可以理解为随机性。

大致规律：

```text
temperature 低：更稳定、更保守、更适合分类/判断/JSON
temperature 高：更自由、更有表达力、更适合聊天/创作
```

Akane 里：

```text
aux JSON 默认 0.2
chat JSON 默认 0.7
```

这很合理：

```text
路由、验证、判断：不要太飘
最终回复：可以自然一点
```

你可以这样记：

```text
机器要读的输出，temperature 低一点。
人要感受的表达，temperature 可以高一点。
```

---

## 三十二、LLM 应用常见坑

### 1. 只写“请输出 JSON”

不够。

更稳的写法是：

```text
只输出 JSON。
不要输出 Markdown。
不要输出解释。
字段固定为 ...
字段类型为 ...
无法确定时使用 ...
```

### 2. 没有 fallback

模型一旦出错，业务就崩。

正确做法：

```text
每个模型调用都应该有 fallback。
```

### 3. 把 prompt 写死在业务函数里

短期方便，长期难维护。

更好的做法：

```text
PromptBuilder 单独负责 prompt。
Engine 负责业务流程。
LLMRuntime 负责模型调用。
```

### 4. 不测试解析器

模型输出最容易坏的地方就是格式。

应该测试：

```text
纯 JSON
Markdown JSON
前后有废话
坏 JSON
部分 JSON
流式 chunk
```

### 5. 过早追求 Agent

很多项目一上来就想：

```text
我要让模型自主规划、自主调用工具、自主执行任务。
```

但基础没打好时，Agent 会变成更大的混乱。

正确顺序是：

```text
先把单次 LLM 调用做好
再把结构化输出做好
再把 RAG 做好
最后再做 Tool Calling 和 Agent
```

这也是我们笔记顺序的原因。

---

## 三十三、和 FastAPI / HTTP 那几篇有什么重叠

会有一点概念重叠，但重点不同。

第 03 篇 FastAPI 关注：

```text
请求怎么进来
路由怎么写
响应怎么返回
```

第 04 篇 HTTP / JSON / 流式响应关注：

```text
HTTP 协议
JSON 数据交换
NDJSON 流怎么传给前端
```

这一篇关注：

```text
模型怎么调用
prompt 怎么构造
模型输出怎么变成稳定 JSON
流式模型输出怎么边解析边展示
模型失败怎么 fallback
```

可以这样分：

```text
FastAPI：外部用户怎么访问后端
HTTP/JSON：后端和前端怎么通信
LLM 应用工程：后端怎么把模型变成可靠能力
```

所以它们有连接，但不是重复。

---

## 三十四、你阅读 Akane 源码的路线

建议按这个顺序读。

### 1. 先读 PromptBuilder

文件：

```text
companion_v01/prompt_builder.py
```

重点看：

```text
build_final_generation_context
fallback 怎么构造
system_prompt 怎么拼
user_prompt 怎么拼
debug_enabled 怎么影响输出
allow_tool_call 怎么影响 prompt
```

你读的时候问自己：

```text
哪些内容属于规则？
哪些内容属于上下文？
哪些内容属于兜底？
```

### 2. 再读 LLMRuntime 的普通 JSON

文件：

```text
companion_v01/llm_runtime.py
```

重点看：

```text
call_chat_json
_call_json
_build_completion_kwargs
_extract_text
_extract_json
_recover_partial_chat_json
```

你读的时候问自己：

```text
模型原始输出在哪里？
什么时候解析 JSON？
解析失败怎么处理？
异常怎么处理？
```

### 3. 再读 LLMRuntime 的流式 JSON

重点看：

```text
stream_chat_json
_stream_chat_json
_TopLevelJSONStreamTap
_extract_stream_text
_try_extract_leading_tool_call
```

你读的时候问自己：

```text
chunk 从哪里来？
speech_chunk 什么时候 yield？
最终 parsed 什么时候得到？
tool_call 为什么能提前停止？
```

### 4. 再读 llm_client

文件：

```text
services/llm_client.py
```

重点看：

```text
build_llm_client
normalize_api_protocol
normalize_base_url
AnthropicCompatClient
_AnthropicStream
```

你读的时候问自己：

```text
不同模型服务如何统一成同一个接口？
OpenAI 风格接口在项目里起了什么作用？
```

### 5. 最后回到 engine

文件：

```text
companion_v01/engine.py
```

重点看：

```text
_prepare_final_response_context
_generate_final_response
_stream_final_response
_normalize_final_output
```

你读的时候问自己：

```text
业务上下文在哪里收集？
PromptBuilder 在哪里被调用？
LLMRuntime 在哪里被调用？
模型结果如何变成最终业务输出？
```

---

## 三十五、这一篇的知识点清单

你学完这一篇，应该能看懂这些东西：

```text
LLM 不是普通函数，而是不稳定外部服务
LLM 应用需要 prompt / schema / parser / fallback
system_prompt 和 user_prompt 的分工
fallback 是工程兜底，不是可有可无
JSON 输出是让模型结果可编程
字段顺序会影响流式解析体验
普通 JSON 调用和流式 JSON 调用的区别
_TopLevelJSONStreamTap 本质上是一个状态机
NDJSON 适合事件序列
aux 模型和 chat 模型可以分工
client 兼容层是在隔离模型供应商差异
prompt cache 是优化，不是核心
metrics 是可观测性
LLM 应用依然需要 unittest
```

---

## 三十六、和后面几篇的关系

下一篇是：

```text
08_RAG与向量检索.md
```

它会讲：

```text
为什么模型需要外部记忆
embedding 是什么
向量相似度是什么
Akane 怎么检索历史记忆
router / verifier 为什么存在
```

再下一篇是：

```text
09_ToolCalling与Agent工程.md
```

它会讲：

```text
模型如何决定调用工具
tool_call 的结构
工具参数如何校验
Agent 为什么不是简单循环
如何避免工具乱调
```

所以本篇的位置是：

```text
07：先把单次模型调用做稳
08：再让模型能查资料和记忆
09：再让模型能调用工具和执行动作
```

这个顺序很重要。

因为：

```text
如果连一次 LLM JSON 调用都不稳定，
RAG 和 Agent 只会把问题放大。
```

---

## 三十七、最终压缩版

LLM 应用工程可以压缩成一句话：

```text
把模型的自然语言能力，封装成项目里稳定、可解析、可兜底、可测试的业务接口。
```

Akane 的做法：

```text
PromptBuilder 负责组织 prompt 和 fallback
LLMRuntime 负责调用模型、解析 JSON、流式 tap、fallback、metrics
llm_client 负责兼容不同模型服务
engine 负责把模型结果接回业务状态和前端事件
tests 负责保护 prompt 协议和解析边界
```

你读源码时不用一口气记住全部代码。

先抓住这条主线：

```text
上下文 -> prompt -> 模型 -> JSON -> 解析 -> fallback -> 业务输出
```

这条线抓住了，Akane 的 LLM 部分就不会再像一团雾。
