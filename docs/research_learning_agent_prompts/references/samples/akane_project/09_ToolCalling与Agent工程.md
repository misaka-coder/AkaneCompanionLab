---
tags:
  - akane/tool-calling
  - llm/agent
  - software-engineering/orchestration
  - backend/tools
created: 2026-05-23
---

# Tool Calling 与 Agent 工程

> Tool Calling 的本质不是“模型变成了程序员”。  
> 它的本质是：模型只负责提出结构化动作请求，真正执行动作的是后端工具系统。

前几篇我们已经学到：

```text
07_LLM应用工程：
模型输出固定 JSON，后端解析、fallback、流式展示。

08_RAG与向量检索：
模型回答前，系统先检索记忆，把相关片段放进 prompt。
```

这一篇继续往前走：

```text
如果用户不是只想聊天，而是想让 Akane 做一件事呢？
```

比如：

```text
5 分钟后提醒我喝水
把刚才的内容整理成 Markdown 文件
帮我把这个音频转成 wav
把这个视频里的人声分离出来
再查一下我之前说过的那个项目
把这件复杂任务放后台处理
```

这时候只靠自然语言回复不够。

Akane 需要让模型输出：

```json
{
  "tool_call": {
    "type": "set_reminder",
    "content": "喝水",
    "offset_minutes": 5
  }
}
```

然后后端执行。

这就是 Tool Calling。

---

## 一、这一篇学什么

学习路线：

```text
Tool Calling 是什么
-> tool_call 和自然语言回复的区别
-> Akane 为什么不用模型直接执行动作
-> 工具协议怎么设计
-> ToolExecutionContext / ToolExecutionResult 是什么
-> BaseToolHandler 三件套
-> normalize_call 为什么是安全边界
-> Engine 如何执行多轮工具调用
-> followup_context 如何让模型继续回答
-> capability registry 如何按客户端过滤工具
-> retrieve_memory 如何衔接 RAG
-> delegate_task 和后台 worker 如何形成 Agent
-> Agent 工程里的循环、状态、限制和测试
```

先给结论：

```text
Agent 不是一个神秘的新物种。
Agent = LLM 决策 + 工具执行 + 状态记录 + 结果反馈 + 有上限循环。
```

---

## 二、Tool Calling 是什么

普通聊天是：

```text
用户输入
-> 模型生成自然语言
-> 前端展示
```

Tool Calling 是：

```text
用户输入
-> 模型判断需要工具
-> 模型输出结构化 tool_call
-> 后端校验参数
-> 后端执行工具
-> 工具结果回填给模型
-> 模型基于结果自然回复
```

用伪代码表示：

```python
result = call_llm(prompt)

if result["tool_call"] is not None:
    tool_result = execute_tool(result["tool_call"])
    final_result = call_llm(prompt + tool_result.followup_context)
else:
    final_result = result
```

这里最关键的是：

```text
模型不直接执行工具。
模型只是输出“我想调用哪个工具，以及参数是什么”。
```

真正执行的是后端。

---

## 三、Tool Calling 和普通 JSON 输出的关系

第 07 篇讲过，Akane 的最终回复是一个 JSON：

```json
{
  "emotion": "happy",
  "speech": "我在哦。",
  "speech_segments": [],
  "tool_call": null
}
```

如果不需要工具：

```json
"tool_call": null
```

如果需要工具：

```json
"tool_call": {
  "type": "retrieve_memory",
  "query": "MiniMind 项目",
  "keywords": ["MiniMind", "项目"]
}
```

也就是说：

```text
tool_call 是最终回复 JSON 里的一个字段。
```

Akane 这里用的是项目自定义工具协议。

它不是完全依赖某个模型平台的原生 function calling，而是让模型在自己的 JSON schema 里写：

```text
tool_call: {...} 或 null
```

这样做的好处是：

```text
1. OpenAI / Anthropic / Ollama 风格可以统一
2. 流式 JSON 可以提前识别 tool_call
3. 后端可以完全掌控校验和执行流程
4. 工具协议和 Akane 的业务状态绑定更紧
```

---

## 四、为什么不能让模型直接做动作

模型擅长：

```text
理解意图
生成结构化请求
根据结果组织语言
```

但模型不应该直接：

```text
写数据库
删文件
发文件
调用系统命令
修改任务状态
发送提醒
处理音视频
```

因为模型可能：

```text
参数写错
误解用户
重复调用
调用不存在的工具
把解释文本当工具调用
越权执行当前客户端不支持的能力
```

所以后端要做边界：

```text
模型说“我想做”
后端判断“你能不能做、参数是否合法、当前客户端能不能做”
后端真正执行
```

这就是 Tool Calling 工程化。

---

## 五、Akane 的工具系统核心文件

主要文件：

```text
companion_v01/tool_runtime.py
companion_v01/tool_orchestration_engine.py
companion_v01/task_worker_tool.py
companion_v01/task_worker.py
companion_v01/capability_registry.py
companion_v01/engine.py
```

它们分工：

| 文件 | 作用 |
|---|---|
| `tool_runtime.py` | 定义工具协议、上下文、结果，以及大量具体工具 handler |
| `tool_orchestration_engine.py` | 工具调用归一化、执行、重复检测、followup 拼接 |
| `capability_registry.py` | 根据客户端模式和当前资源选择可用工具 |
| `task_worker_tool.py` | `delegate_task` 工具，把复杂任务委派给后台 worker |
| `task_worker.py` | 后台 specialist worker 的 Agent 循环 |
| `engine.py` | 把工具循环接入整轮对话 |

主链路：

```text
PromptBuilder 把工具说明放进 system_prompt
-> LLM 输出 tool_call
-> Engine normalize tool_call
-> ToolHandler execute
-> ToolExecutionResult
-> followup_context 回填给 LLM
-> LLM 继续输出最终回复或下一步 tool_call
```

---

## 六、tool_call 的基本形状

Akane 的 tool_call 都至少有一个字段：

```python
{
    "type": "工具名"
}
```

不同工具有不同参数。

比如提醒：

```python
{
    "type": "set_reminder",
    "content": "喝水",
    "offset_minutes": 5
}
```

比如检索记忆：

```python
{
    "type": "retrieve_memory",
    "query": "MiniMind 项目",
    "keywords": ["MiniMind", "项目"],
    "time_hint": {}
}
```

比如生成文件：

```python
{
    "type": "compose_file",
    "output_title": "学习计划",
    "output_format": "md",
    "content_markdown": "# 学习计划\n\n..."
}
```

比如发送文件：

```python
{
    "type": "send_file",
    "targets": ["gen_001"]
}
```

注意：

```text
一次只调用一个工具。
```

Akane 的 prompt 里也强调：

```text
如果不需要工具，tool_call 输出 null。一次只调用一个工具。
```

---

## 七、ToolExecutionContext：工具执行时需要的上下文

Akane 的工具执行上下文：

```python
@dataclass(frozen=True)
class ToolExecutionContext:
    profile_user_id: str
    session_id: str
    now_ts: int
    visual_payload: dict[str, Any]
    current_user_source_id: str = ""
    client_mode: str = ""
    request_context: dict[str, Any] = field(default_factory=dict)
```

它告诉工具：

```text
当前是谁的资料
当前是哪次会话
当前时间是什么
当前视觉/最终输出上下文是什么
当前用户消息的 source_id 是什么
当前客户端模式是什么
当前请求还有哪些额外信息
```

比如 `set_reminder` 需要：

```text
profile_user_id
session_id
now_ts
```

比如 `send_file` 需要：

```text
client_mode
```

因为桌宠模式可能要：

```text
open
reveal
save_desktop
copy_path
```

而 QQ 文本模式更像直接发送文件。

---

## 八、ToolExecutionResult：工具执行后返回什么

Akane 的工具结果：

```python
@dataclass
class ToolExecutionResult:
    tool_type: str
    raw_turns: list[dict[str, Any]] = field(default_factory=list)
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    followup_context: str = ""
    state_updates: dict[str, Any] = field(default_factory=dict)
```

字段含义：

| 字段 | 作用 |
|---|---|
| `tool_type` | 哪个工具执行了 |
| `raw_turns` | 工具产生的对话轮次，比如 NPC 回复 |
| `stream_events` | 要立刻推给前端的事件，比如文件 ready |
| `followup_context` | 回填给模型看的工具结果 |
| `state_updates` | 给 debug 或业务层用的结构化状态 |

最重要的是：

```text
followup_context 给 LLM 看。
stream_events 给前端看。
state_updates 给程序看。
```

这三个对象不要混。

---

## 九、BaseToolHandler：每个工具都要实现三件事

基础类：

```python
class BaseToolHandler:
    tool_type: str = ""

    def build_prompt_instruction(self) -> str:
        raise NotImplementedError

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        raise NotImplementedError

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        raise NotImplementedError
```

每个工具 handler 都有三步：

```text
build_prompt_instruction：告诉模型这个工具怎么用
normalize_call：校验和清洗模型给的参数
execute：真正执行工具
```

这是一种非常清晰的工程分层。

```text
prompt 层
校验层
执行层
```

---

## 十、normalize_call 是安全边界

模型输出可能不靠谱。

比如它可能输出：

```python
{
    "type": "set_reminder",
    "text": "喝水",
    "delay_minutes": "五分钟"
}
```

也可能输出：

```python
{
    "type": "set_reminder",
    "content": "",
    "offset_minutes": -999
}
```

所以不能直接 execute。

要先 normalize。

Akane 的 `SetReminderToolHandler.normalize_call` 会做：

```text
检查 value 是不是 dict
检查 type 是否等于 set_reminder
抽取 content/task/reminder/text
清洗 time_text/date_label/time_of_day
把 hour/minute/offset_minutes 转成 int
没有 content 就返回 None
```

这意味着：

```text
normalize_call 返回 None，工具就不会执行。
```

这是非常重要的防线。

---

## 十一、写一个最小 ToolHandler

我们写一个最小版提醒工具。

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolExecutionContext:
    profile_user_id: str
    session_id: str
    now_ts: int
    visual_payload: dict[str, Any]


@dataclass
class ToolExecutionResult:
    tool_type: str
    followup_context: str = ""
    stream_events: list[dict[str, Any]] = field(default_factory=list)


class BaseToolHandler:
    tool_type = ""

    def build_prompt_instruction(self) -> str:
        raise NotImplementedError

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        raise NotImplementedError

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        raise NotImplementedError


class MiniReminderTool(BaseToolHandler):
    tool_type = "set_reminder"

    def build_prompt_instruction(self) -> str:
        return '- set_reminder：设置提醒。格式 {"type":"set_reminder","content":"喝水","offset_minutes":5}。'

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if value.get("type") != self.tool_type:
            return None

        content = str(value.get("content") or value.get("text") or "").strip()
        if not content:
            return None

        try:
            offset_minutes = int(value.get("offset_minutes") or 0)
        except ValueError:
            offset_minutes = 0

        if offset_minutes <= 0:
            return None

        return {
            "type": self.tool_type,
            "content": content[:120],
            "offset_minutes": offset_minutes,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        due_ts = context.now_ts + int(call["offset_minutes"]) * 60
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=f"已经设置提醒：{call['content']}，触发时间戳 {due_ts}。",
            stream_events=[{"type": "reminder_set", "content": call["content"], "due_ts": due_ts}],
        )


handler = MiniReminderTool()
raw_call = {"type": "set_reminder", "text": "喝水", "offset_minutes": "5"}
call = handler.normalize_call(raw_call)

if call:
    result = handler.execute(
        call=call,
        context=ToolExecutionContext(
            profile_user_id="master",
            session_id="s1",
            now_ts=1000,
            visual_payload={},
        ),
    )
    print(result.followup_context)
    print(result.stream_events)
```

这个小例子已经体现了工具 handler 的核心。

---

## 十二、Akane 有哪些工具

Akane 的工具很多，不需要一次全背。

可以按类别理解。

### 1. 记忆工具

```text
retrieve_memory
```

用于主动检索长期记忆。

### 2. 提醒工具

```text
set_reminder
list_reminders
cancel_reminder
```

用于管理提醒。

### 3. NPC / 场景工具

```text
call_npc
check_inventory
manage_gift
manage_artifact
```

偏视觉小说和场景互动。

### 4. 附件和工作台工具

```text
inspect_attachment
read_attachment_section
sync_attachment_workspace
clear_attachment_focus
retry_attachment
fetch_media_from_url
```

用于处理用户上传的图片、文档、音视频或远程链接。

### 5. 生成文件工具

```text
compose_file
revise_generated_file
apply_style_to_existing_file
inspect_generated_file
manage_generated_file
send_file
send_generated_file
```

用于生成、修改、查看、发送文件。

### 6. 媒体工具

```text
inspect_media_info
convert_media_file
separate_audio_stems
clean_voice_track
transcribe_media
prepare_voice_dataset
```

用于音视频处理。

### 7. 任务和后台工坊

```text
manage_task_workspace
delegate_task
```

用于多步任务、后台执行和任务交接。

你不用现在掌握每个工具细节。

先掌握统一结构：

```text
工具说明 -> 参数归一化 -> 执行 -> 结果回填
```

---

## 十三、工具说明如何进入 prompt

Akane 在 `engine.py` 里有：

```python
def _build_tool_prompt_context(...):
    ...
    lines.append("【当前可调用工具】")
    for handler in handlers.values():
        lines.append(handler.build_prompt_instruction())
    lines.append("如果不需要工具，tool_call 输出 null。一次只调用一个工具。")
```

这说明：

```text
模型之所以知道有哪些工具，是因为后端把工具说明写进 prompt。
```

如果本轮没有工具：

```text
本轮不要调用任何工具，tool_call 固定为 null。
```

如果本轮有工具：

```text
【当前可调用工具】
- retrieve_memory：...
- set_reminder：...
- compose_file：...
```

模型再根据这些说明决定：

```text
tool_call 写 null
或者写某个工具调用
```

---

## 十四、CapabilityRegistry：不是所有工具每轮都开放

Akane 不会在每一轮都把所有工具塞给模型。

它会根据：

```text
客户端模式
当前是否有附件
是否有文档附件
是否有媒体附件
是否有生成文件
是否有可交付文件
```

选择工具。

核心文件：

```text
companion_v01/capability_registry.py
```

比如：

```text
QQ 文本模式：
可以发文件、发贴纸、处理附件、媒体、后台任务

桌宠模式：
可以交付文件到桌面工作台，但不使用 QQ 贴纸

网页场景模式：
可以 call_npc、manage_gift，但不做 QQ 文件发送
```

这样做有两个好处：

```text
1. 减少 prompt 体积
2. 防止模型调用当前客户端不支持的工具
```

这就是权限和能力控制。

Agent 工程里非常重要。

---

## 十五、工具调用完整循环

Akane 的主循环在 `engine.py` 里。

简化后：

```python
final_output = self._build_final_response(...)

for tool_round_index in range(max_tool_rounds):
    tool_call = self._normalize_tool_call(final_output.get("tool_call"), ...)
    if not tool_call:
        break

    if tool_call_signature(tool_call) in seen_tool_calls:
        # 重复调用，拦截
        final_output = self._build_final_response(..., allow_tool_call=False)
        break

    tool_result = self._execute_tool_call(...)
    tool_followups.append(tool_result.followup_context)

    final_output = self._build_final_response(
        ...,
        extra_user_context=build_multi_tool_followup_context(tool_followups),
        allow_tool_call=allow_more_tools,
    )
```

这就是一个最小 Agent 循环：

```text
LLM 生成
-> 发现 tool_call
-> 执行工具
-> 把工具结果放回 prompt
-> LLM 再生成
```

注意它有上限：

```text
MAX_TOOL_ROUNDS 默认 3，限制在 1 到 5 之间
```

这很重要。

Agent 不能无限循环。

---

## 十六、写一个最小工具循环

下面写一个可运行的小工具循环。

```python
class MiniEchoTool(BaseToolHandler):
    tool_type = "echo"

    def build_prompt_instruction(self) -> str:
        return '- echo：复读一段文本。格式 {"type":"echo","text":"内容"}。'

    def normalize_call(self, value):
        if not isinstance(value, dict):
            return None
        if value.get("type") != self.tool_type:
            return None
        text = str(value.get("text") or "").strip()
        if not text:
            return None
        return {"type": "echo", "text": text[:100]}

    def execute(self, *, call, context):
        return ToolExecutionResult(
            tool_type="echo",
            followup_context=f"echo 工具返回：{call['text']}",
        )


def fake_llm(prompt: str) -> dict:
    if "工具返回" not in prompt:
        return {
            "speech": "我先查一下。",
            "tool_call": {"type": "echo", "text": "工具结果 123"},
        }
    return {
        "speech": "查到了，结果是工具结果 123。",
        "tool_call": None,
    }


handlers = {"echo": MiniEchoTool()}
tool_followups = []
prompt = "用户：帮我查一下"

for round_index in range(3):
    output = fake_llm(prompt)
    call = output.get("tool_call")

    if call is None:
        print("最终回复：", output["speech"])
        break

    handler = handlers.get(call.get("type"))
    normalized = handler.normalize_call(call) if handler else None
    if normalized is None:
        print("工具调用非法")
        break

    result = handler.execute(
        call=normalized,
        context=ToolExecutionContext(
            profile_user_id="master",
            session_id="s1",
            now_ts=1000,
            visual_payload={},
        ),
    )
    tool_followups.append(result.followup_context)
    prompt = prompt + "\n\n【工具结果】\n" + "\n".join(tool_followups)
```

输出：

```text
最终回复： 查到了，结果是工具结果 123。
```

这就是 Agent 循环的最小模型。

---

## 十七、重复工具调用拦截

模型有时会重复调用同一个工具。

比如连续输出：

```python
{"type": "retrieve_memory", "query": "MiniMind"}
{"type": "retrieve_memory", "query": "MiniMind"}
```

Akane 会计算签名：

```python
def tool_call_signature(tool_call):
    return json.dumps(tool_call, ensure_ascii=False, sort_keys=True, default=str)
```

如果重复：

```text
系统刚刚拦截了一次重复工具调用...
请基于已经拿到的工具结果自然回应，不要继续重复调用同一个工具。
```

然后下一轮会：

```text
allow_tool_call=False
```

这就是防循环。

Agent 工程里一定要有：

```text
最大轮数
重复动作检测
失败后停止或降级
```

否则模型可能一直调用工具。

---

## 十八、followup_context：工具结果如何回给模型

工具执行后，Akane 不会直接把 Python 对象丢给模型。

而是把结果组织成文字：

```text
第 1 次工具（compose_file）结果：
已生成 gen_001。
```

多轮工具结果会被拼成：

```text
【本轮工具执行记录】

第 1 次工具（retrieve_memory）结果：
...

第 2 次工具（compose_file）结果：
...

如果任务还没完成，可以继续在 tool_call 字段调用下一步必要工具；
如果结果已经足够，请将 tool_call 设为 null，并自然回复主人。
```

对应函数：

```python
build_multi_tool_followup_context(tool_followups, allow_more=True)
```

这说明：

```text
工具结果要变成模型能理解的上下文。
```

程序看的是结构。

模型看的是 prompt 文本。

---

## 十九、stream_events：工具结果如何推给前端

有些工具会产生前端事件。

比如 `compose_file` 成功：

```python
{
    "type": "generated_file_ready",
    "generated_file": generated,
    "send_to_user": True
}
```

比如 `send_file` 成功：

```python
{
    "type": "file_ready",
    "file": file_ref,
    "send_to_user": True
}
```

流式接口里，Akane 会：

```python
for stream_event in current_events:
    yield stream_event
```

这意味着：

```text
工具执行结果可以实时推给前端。
```

不要把所有东西都塞进最终 `speech`。

文件 ready、NPC turn、任务委派这些都是业务事件。

---

## 二十、raw_turns：工具产生的新对话

有些工具会产生对话轮次。

比如：

```text
call_npc
```

NPC 回复后，工具结果里会有：

```python
raw_turns=[npc_turn]
```

Akane 会把它写入历史：

```python
role=f"npc:{speaker}"
content=speech
```

这样 NPC 说过的话后续也能被记忆系统索引和总结。

这说明：

```text
工具结果不只是临时返回，它也可能改变长期状态。
```

---

## 二十一、retrieve_memory：RAG 也可以作为工具

第 08 篇讲过前置 RAG。

但 Akane 还有一个工具：

```text
retrieve_memory
```

它用于这种情况：

```text
模型看完当前可见上下文和前置检索结果后，仍然觉得需要主动回想更早内容。
```

工具说明里写得很清楚：

```text
这是内部记忆检索工具，不是对用户说出口的话。
query 要写具体实体、地点、人物、事件或偏好。
只有当前可见记忆不足以回答时才调用。
```

执行后返回：

```python
ToolExecutionResult(
    tool_type="retrieve_memory",
    followup_context="你刚刚主动检索了长期记忆。下面是可能回答主人问题的参考记忆：...",
    state_updates={
        "memory_retrieval": {
            "tool_call": {...},
            "retrieval_result": ...,
            "verifier_output": ...,
            "confirmed_snippets": ...,
        }
    },
)
```

这把 RAG 和 Tool Calling 接起来了。

```text
前置检索：系统主动查
retrieve_memory：模型主动请求再查
```

---

## 二十二、compose_file：工具不是替模型思考

`compose_file` 的工具说明里有一个关键点：

```text
这个工具只负责把你已经整理好的内容渲染成文件；
如果需要提取重点、改写或排版，请把最终内容写进 content_markdown 或 table_rows，
不要只写一句任务就指望工具替你思考。
```

这很重要。

工具和模型的分工是：

```text
模型：理解、整理、生成内容
工具：把内容写成文件、转换格式、执行确定性操作
```

如果用户说：

```text
把今天聊的内容整理成 md
```

模型应该输出：

```python
{
    "type": "compose_file",
    "output_title": "今日学习总结",
    "output_format": "md",
    "content_markdown": "# 今日学习总结\n\n..."
}
```

而不是：

```python
{
    "type": "compose_file",
    "task": "帮我总结一下今天聊的内容"
}
```

后者把思考推给了工具。

除非工具本身就是专门做思考的 LLM worker，否则普通工具应该尽量确定性。

---

## 二十三、manage_task_workspace：多步任务需要状态

有些任务不是一次工具能完成。

比如：

```text
把视频下载下来
提取音频
分离人声
降噪
切片
打包训练素材
最后发给我
```

这就需要任务工作区。

Akane 有：

```text
manage_task_workspace
```

它可以：

```text
create
update_steps
add_artifact
ask_user
complete
cleanup
inspect
```

任务工作区记录：

```text
目标
步骤
产物
等待用户的问题
最近事件
后台工坊状态
handoff 交接信息
```

这就是 Agent 的“外部状态”。

没有状态，Agent 只能靠上下文硬记，很容易乱。

---

## 二十四、delegate_task：把任务交给后台工坊

前台聊天不适合做很慢的任务。

比如：

```text
音视频转码
人声分离
降噪
转写
批量生成文件
训练素材打包
```

这时 Akane 可以调用：

```text
delegate_task
```

它会：

```text
创建或接管任务工作区
选择后台 agent
把任务 brief、输入、期望产物、约束写进去
提交后台任务
返回 followup_context 给前台
```

工具说明中特别强调：

```text
不要把一句话就能直接完成的小事委派出去。
委派成功后前台只需要简短告诉用户后台已经开始。
不要声称产物已经完成。
```

这很工程。

因为后台任务只是开始了，不是完成了。

---

## 二十五、TaskWorkerService：后台 Agent 循环

文件：

```text
companion_v01/task_worker.py
```

它的注释说得很准：

```text
worker 不是第二个前台助手。
它只接收限定任务，使用小工具集，把进度写回任务工作区，不直接给用户发消息或文件。
```

后台 worker 的循环：

```text
读取任务工作区
-> 构造 worker prompt
-> LLM 输出 JSON
-> 如果有 tool_call，执行工具
-> 把工具结果加入 tool_followups
-> 继续下一轮
-> done / blocked / waiting / round limit
```

它和前台工具循环很像，但更严格：

```text
工具集受 agent 类型限制
不能直接发送给用户
产物登记到任务工作区
完成后用 handoff 交给前台
```

---

## 二十六、后台 worker 的输出协议

worker 必须输出一个 JSON：

```json
{
  "status": "continue",
  "message": "先生成文件。",
  "tool_call": {
    "type": "compose_file",
    "output_title": "后台总结",
    "output_format": "md",
    "content_markdown": "..."
  },
  "steps": [
    {"title": "生成文件", "status": "running"}
  ],
  "artifacts": [],
  "question": "",
  "handoff": {
    "summary": "",
    "next_action": "continue_work",
    "user_question": ""
  }
}
```

`status` 有三类：

| status | 含义 |
|---|---|
| `continue` | 继续调用工具 |
| `done` | 任务完成 |
| `blocked` | 卡住，需要前台或用户补信息 |

如果完成，`handoff.next_action` 要说明：

```text
send_to_user
ask_confirmation
continue_work
ask_user
report_only
```

这就是后台 Agent 和前台助手之间的交接协议。

---

## 二十七、后台 Agent 的工具权限

不同后台 agent 可用工具不同。

```python
AGENT_ALLOWED_TOOLS = {
    "document_agent": {
        "sync_attachment_workspace",
        "inspect_attachment",
        "read_attachment_section",
        "compose_file",
        "revise_generated_file",
        "apply_style_to_existing_file",
        "inspect_generated_file",
    },
    "media_agent": {
        "fetch_media_from_url",
        "inspect_media_info",
        "convert_media_file",
        "separate_audio_stems",
        "clean_voice_track",
        "transcribe_media",
        "prepare_voice_dataset",
        "compose_file",
    },
}
```

这说明：

```text
Agent 不是工具越多越好。
Agent 应该拿到与任务相关的最小工具集。
```

工具越多：

```text
prompt 越长
误调用概率越高
安全边界越复杂
```

所以 Akane 用 specialist worker。

---

## 二十八、后台 worker 为什么不能直接发文件

Akane 的 worker prompt 里强调：

```text
你不能直接发送文件给用户，也不能替前台助手做最终汇报、最终发送或清理收尾。
产物生成后留在生成区和任务工作区，由前台助手决定如何确认、发送与清理。
```

原因是：

```text
后台 worker 不在用户对话现场。
它不知道前台是否需要先确认。
它不能越过用户交互边界直接交付。
```

所以后台只负责：

```text
做事
登记产物
写 handoff
```

前台负责：

```text
向用户确认
自然汇报
发送文件
收尾
```

这就是 Agent 分工。

---

## 二十九、写一个迷你后台 worker

我们写一个极简版。

```python
class MiniWorker:
    def __init__(self, llm_func, handlers: dict[str, BaseToolHandler]):
        self.llm_func = llm_func
        self.handlers = handlers

    def run(self, task: str, max_rounds: int = 3):
        tool_followups = []

        for round_index in range(max_rounds):
            output = self.llm_func(task, tool_followups)
            status = output.get("status", "blocked")
            tool_call = output.get("tool_call")

            if tool_call:
                handler = self.handlers.get(tool_call.get("type"))
                normalized = handler.normalize_call(tool_call) if handler else None
                if normalized is None:
                    return {"status": "blocked", "message": "工具参数不合法"}

                result = handler.execute(
                    call=normalized,
                    context=ToolExecutionContext(
                        profile_user_id="master",
                        session_id="s1",
                        now_ts=1000,
                        visual_payload={"_task_worker": True},
                    ),
                )
                tool_followups.append(result.followup_context)
                continue

            if status in {"done", "blocked"}:
                return output

        return {
            "status": "paused",
            "message": "达到最大执行轮数，等待前台接手。",
            "tool_followups": tool_followups,
        }
```

这就是后台 Agent 的骨架：

```text
循环
工具
结果
状态
上限
```

真实 Akane 只是把任务工作区、产物登记、handoff、权限控制都加完整了。

---

## 三十、Agent 工程的核心状态机

Agent 循环可以看成状态机：

```text
thinking
-> tool_call
-> executing
-> observing
-> thinking
-> done / blocked / round_limit
```

前台工具循环：

```text
final_output
-> tool_call?
-> execute
-> followup_context
-> final_output again
-> final response
```

后台 worker：

```text
queued
-> running
-> tool_call
-> worker_tool_executed
-> running
-> done / blocked / paused
```

这和第 06 篇状态机知识直接连接。

Agent 不是只靠 prompt。

Agent 必须有：

```text
状态
事件
转移条件
动作
退出条件
```

---

## 三十一、Agent 的退出条件

一个靠谱 Agent 必须知道什么时候停。

Akane 的退出条件包括：

```text
tool_call 为 null
达到 MAX_TOOL_ROUNDS
重复工具调用被拦截
工具 normalize 失败
worker status = done
worker status = blocked
worker 达到 MAX_TASK_WORKER_ROUNDS
```

这比“让模型自己决定”稳很多。

你以后做 Agent 时也要先设计：

```text
最多执行几轮？
什么算成功？
什么算失败？
什么情况需要问用户？
什么情况要停止调用工具？
```

---

## 三十二、Agent 的观察结果不要只存在模型上下文里

工具结果一般要存两份。

### 1. 给模型看的 followup_context

比如：

```text
已生成 gen_001。
```

### 2. 给系统看的结构化状态

比如：

```python
{
    "type": "generated_file_ready",
    "generated_file": {...},
    "send_to_user": True
}
```

还有任务工作区：

```text
steps
artifacts
events
handoff
```

只把结果塞进 prompt 是不够的。

因为 prompt 会丢，会被截断，会随着轮次变化。

真正重要的业务状态要写进数据库或工作区。

---

## 三十三、Tool Calling 与 RAG 的区别

这两个很容易混。

RAG：

```text
查资料，让模型回答更准。
```

Tool Calling：

```text
调用动作，让系统真的做事。
```

例子：

```text
retrieve_memory：虽然是工具，但它做的是检索资料，所以和 RAG 紧密相关。
compose_file：生成文件，是真动作。
set_reminder：写提醒，是真动作。
send_file：交付文件，是真动作。
delegate_task：创建后台任务，是真动作。
```

所以可以这样分：

```text
RAG 是给模型补上下文。
Tool Calling 是让后端改变世界状态。
Agent 是多次补上下文和改变状态的循环。
```

---

## 三十四、Tool Calling 与函数调用的关系

你可以把工具调用看成：

```python
result = set_reminder(content="喝水", offset_minutes=5)
```

但模型不能真的直接调用 Python 函数。

它只能输出：

```python
{"type": "set_reminder", "content": "喝水", "offset_minutes": 5}
```

后端再把这个 JSON 转成函数调用：

```python
handler = handlers["set_reminder"]
call = handler.normalize_call(raw_call)
result = handler.execute(call=call, context=context)
```

所以 Tool Calling 的本质是：

```text
LLM 输出 JSON
后端把 JSON 映射到函数
```

这就是你从 Python 基础能直接理解的地方。

---

## 三十五、一个完整迷你 Agent 示例

下面写一个稍完整的例子。

目标：

```text
用户让助手生成一份 Markdown。
模型先调用 compose_file。
工具返回 gen_001。
模型再自然回复。
```

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolExecutionContext:
    profile_user_id: str
    session_id: str
    now_ts: int
    visual_payload: dict[str, Any]


@dataclass
class ToolExecutionResult:
    tool_type: str
    followup_context: str = ""
    stream_events: list[dict[str, Any]] = field(default_factory=list)


class ComposeMarkdownTool:
    tool_type = "compose_file"

    def build_prompt_instruction(self):
        return '- compose_file：生成 Markdown 文件。格式 {"type":"compose_file","output_title":"标题","content_markdown":"正文"}。'

    def normalize_call(self, value):
        if not isinstance(value, dict) or value.get("type") != self.tool_type:
            return None
        title = str(value.get("output_title") or "").strip()
        content = str(value.get("content_markdown") or "").strip()
        if not title or not content:
            return None
        return {
            "type": self.tool_type,
            "output_title": title[:80],
            "content_markdown": content[:10000],
        }

    def execute(self, *, call, context):
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=f"已生成文件 gen_001：{call['output_title']}.md。",
            stream_events=[
                {
                    "type": "generated_file_ready",
                    "generated_file": {
                        "handle": "gen_001",
                        "title": call["output_title"],
                        "ext": "md",
                    },
                }
            ],
        )


def fake_llm(prompt: str) -> dict:
    if "已生成文件" not in prompt:
        return {
            "speech": "我来整理成文件。",
            "tool_call": {
                "type": "compose_file",
                "output_title": "学习总结",
                "content_markdown": "# 学习总结\n\n今天学习了 Tool Calling。",
            },
        }
    return {
        "speech": "整理好了，文件是 gen_001。",
        "tool_call": None,
    }


handlers = {"compose_file": ComposeMarkdownTool()}
prompt = "用户：帮我整理成 Markdown 文件"
tool_followups = []
stream_events = []

for round_index in range(3):
    output = fake_llm(prompt)
    raw_call = output.get("tool_call")
    if raw_call is None:
        print(output["speech"])
        break

    handler = handlers.get(raw_call.get("type"))
    call = handler.normalize_call(raw_call) if handler else None
    if call is None:
        print("工具调用失败")
        break

    result = handler.execute(
        call=call,
        context=ToolExecutionContext(
            profile_user_id="master",
            session_id="s1",
            now_ts=1000,
            visual_payload=output,
        ),
    )
    tool_followups.append(result.followup_context)
    stream_events.extend(result.stream_events)
    prompt += "\n\n【工具结果】\n" + result.followup_context

print(stream_events)
```

这个例子对应 Akane 的主流程：

```text
final_output -> tool_call -> execute -> stream_events/followup -> final_output
```

---

## 三十六、测试 Tool Calling 应该测什么

Tool Calling 很适合测试。

重点不是测模型聪不聪明，而是测工程边界。

应该测试：

```text
工具说明是否进入 prompt
不同客户端模式下工具是否被过滤
normalize_call 是否清洗参数
非法参数是否返回 None
execute 是否返回正确 ToolExecutionResult
stream_events 是否符合前端协议
followup_context 是否能指导下一轮模型
重复 tool_call 是否被拦截
MAX_TOOL_ROUNDS 是否生效
后台 worker 是否只使用允许工具
worker blocked/done/paused 是否写入任务工作区
delegate_task 是否生成 handoff
```

Akane 里相关测试：

```text
tests/test_vn_extensions.py
tests/test_task_worker.py
tests/test_attachment_inbox.py
tests/test_task_workspace.py
tests/test_llm_runtime_stream.py
```

比如测试工具 prompt：

```text
_build_tool_prompt_context 应包含当前可调用工具
QQ 模式不应该出现 manage_gift
桌宠模式不应该出现 send_sticker
有媒体附件时才展开媒体工具
```

比如测试 worker：

```text
worker 能执行允许工具并记录 artifact
worker 完成后写 handoff
worker blocked 时把 question 交给前台
worker 达到轮数上限时 paused
```

---

## 三十七、写一个 normalize_call 测试

```python
import unittest


class MiniReminderToolTests(unittest.TestCase):
    def test_normalize_valid_call(self):
        handler = MiniReminderTool()

        call = handler.normalize_call({
            "type": "set_reminder",
            "text": "喝水",
            "offset_minutes": "5",
        })

        self.assertEqual(call["content"], "喝水")
        self.assertEqual(call["offset_minutes"], 5)

    def test_reject_empty_content(self):
        handler = MiniReminderTool()

        call = handler.normalize_call({
            "type": "set_reminder",
            "content": "",
            "offset_minutes": 5,
        })

        self.assertIsNone(call)

    def test_reject_wrong_type(self):
        handler = MiniReminderTool()

        call = handler.normalize_call({
            "type": "send_file",
            "content": "喝水",
        })

        self.assertIsNone(call)


if __name__ == "__main__":
    unittest.main()
```

这种测试非常实用。

因为模型输出各种奇怪参数时，normalize_call 就是你的第一道防线。

---

## 三十八、Agent 常见坑

### 1. 没有最大轮数

模型可能一直调用工具。

必须有：

```text
MAX_TOOL_ROUNDS
MAX_TASK_WORKER_ROUNDS
```

### 2. 不校验参数

不能相信模型输出。

必须有：

```text
normalize_call
```

### 3. 工具太多

一次给模型几十个工具，误调用会变多。

要按：

```text
客户端
资源状态
任务类型
权限
```

过滤。

### 4. 工具结果只写自然语言

只写 `followup_context` 不够。

还要有：

```text
stream_events
state_updates
任务工作区
数据库记录
```

### 5. 后台 worker 直接对用户交付

后台 worker 应该做任务，不应该抢前台助手的交互职责。

复杂任务要：

```text
worker 产出
handoff
frontstage 确认或交付
```

### 6. 模型在 speech 里说“我调用工具了”

Akane 的 prompt 专门强调：

```text
真正调用工具只能写在 tool_call 字段。
```

如果只是说在 speech 里，系统不会执行。

### 7. 重复调用同一工具

要用签名去重。

否则模型可能反复执行同一动作。

---

## 三十九、Agent 和传统程序的关系

传统程序：

```python
if user_wants_reminder:
    set_reminder(...)
elif user_wants_file:
    compose_file(...)
```

Agent 程序：

```text
让 LLM 判断用户意图和下一步工具
但由后端校验和执行
```

它不是替代传统程序。

而是把传统程序变成工具，然后让模型选择。

所以你学 Agent 不能只学 prompt。

必须同时学：

```text
后端接口设计
参数校验
状态机
任务队列
权限控制
测试
可观测性
```

这就是为什么前面几篇笔记都很重要。

---

## 四十、Akane 源码阅读路线

建议按这个顺序读。

### 1. 先读 tool_runtime 的基础协议

文件：

```text
companion_v01/tool_runtime.py
```

重点：

```text
ToolExecutionContext
ToolExecutionResult
BaseToolHandler
RetrieveMemoryToolHandler
SetReminderToolHandler
ComposeFileToolHandler
SendFileToolHandler
ManageTaskWorkspaceToolHandler
```

先看每个工具怎么实现三件事：

```text
build_prompt_instruction
normalize_call
execute
```

### 2. 再读 tool_orchestration_engine

文件：

```text
companion_v01/tool_orchestration_engine.py
```

重点：

```text
max_tool_rounds
tool_call_signature
describe_tool_call_for_prompt
build_multi_tool_followup_context
normalize_tool_call
promote_narrated_tool_call
execute_tool_call
```

理解工具调用怎么被统一调度。

### 3. 再读 engine 的工具循环

文件：

```text
companion_v01/engine.py
```

重点：

```text
process_turn
process_turn_stream
_build_tool_handlers
_resolve_tool_handlers
_build_tool_prompt_context
_execute_tool_call
```

理解：

```text
模型输出 tool_call 后，Akane 怎么多轮执行。
```

### 4. 再读 capability_registry

文件：

```text
companion_v01/capability_registry.py
```

重点：

```text
CapabilitySnapshot
CapabilityModule
CapabilitySelection
CapabilityRegistry.select
```

理解不同客户端为什么看到不同工具。

### 5. 再读 task_worker_tool

文件：

```text
companion_v01/task_worker_tool.py
```

重点：

```text
DelegateTaskToolHandler
normalize_call
execute
```

理解前台如何把任务委派给后台。

### 6. 最后读 task_worker

文件：

```text
companion_v01/task_worker.py
```

重点：

```text
AGENT_ALLOWED_TOOLS
delegate_task
run_task_sync
_execute_worker_tool
_build_worker_system_prompt
_build_worker_user_prompt
_normalize_worker_output
_build_handoff_payload
```

理解真正的后台 Agent 循环。

---

## 四十一、这一篇的知识点清单

学完这一篇，你应该能看懂：

```text
tool_call 是模型输出的结构化动作请求
模型不直接执行工具，后端 handler 才执行
ToolExecutionContext 给工具提供用户、会话、时间、客户端上下文
ToolExecutionResult 同时服务前端、模型和程序
BaseToolHandler 有工具说明、参数归一化、执行三步
normalize_call 是工具安全边界
CapabilityRegistry 控制每轮可用工具
Engine 工具循环是一个有上限的 Agent 循环
followup_context 负责把工具结果回填给模型
stream_events 负责把工具结果推给前端
raw_turns 可以把 NPC 等工具结果写入长期记忆
retrieve_memory 把 RAG 变成可主动调用的工具
compose_file 说明工具不是替模型思考
manage_task_workspace 提供多步任务状态
delegate_task 把复杂任务交给后台工坊
TaskWorkerService 是受限 specialist Agent
Agent 必须有最大轮数、权限、状态、handoff 和测试
```

---

## 四十二、和下一篇的关系

下一篇是：

```text
10_前端异步与桌宠客户端.md
```

它会从后端转向前端：

```text
前端如何接收流式事件
speech_chunk 怎么显示
tool_events 怎么驱动 UI
file_ready 怎么交付
桌宠 activity / audio / workspace 怎么和后端协议对接
JavaScript 异步、fetch、ReadableStream 怎么读
```

本篇是后端动作系统。

下一篇是前端如何消费这些动作结果。

---

## 四十三、最终压缩版

Akane 的 Tool Calling 可以压缩成一条线：

```text
工具 handler 注册
-> 工具说明进入 prompt
-> LLM 输出 tool_call
-> normalize_call 校验参数
-> execute 真正执行
-> ToolExecutionResult 返回 stream_events / followup_context / state_updates
-> Engine 把结果回填给 LLM
-> LLM 再输出最终回复或下一步工具
-> 达到完成、失败、重复或最大轮数时停止
```

Agent 工程可以压缩成一句话：

```text
让模型在受控工具、明确状态、有限轮数和可测试边界里做多步决策。
```

你读这部分源码时，不要被工具数量吓到。

先盯住这四个问题：

```text
模型怎么知道有哪些工具？
工具参数在哪里被校验？
工具结果怎么回到模型和前端？
循环什么时候停止？
```

这四个问题答清楚，Tool Calling 和 Agent 的主干就通了。

