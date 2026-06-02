---
tags:
  - akane/architecture
  - software-engineering/system-design
  - llm-app/full-stack
  - project-reading
created: 2026-05-23
---

# Akane 项目架构总览

> 前面 10 篇是在学零件。  
> 这一篇是把零件重新拼成一台机器。

Akane 项目文件很多，代码量也大。  
但不要被“几十个文件、几万行代码”吓住。

从架构上看，它并不是一团乱麻，而是很典型的一套 AI 应用系统：

```text
前端客户端
-> HTTP / NDJSON 协议
-> FastAPI 路由
-> AkaneMemoryEngine 业务大脑
-> SQLite / VectorStore / 文件工作区
-> RAG 检索
-> Prompt 构造
-> LLMRuntime 调模型
-> Tool Calling 执行工具
-> OutputAdapter 适配客户端
-> 流式事件返回前端
```

你学到第 10 篇之后，已经有能力读这个项目了。  
接下来不是“重新学一个陌生项目”，而是把你已经学过的知识点连接起来。

---

## 一、这一篇解决什么问题

这篇不是具体 API 笔记，而是项目地图。

学习目标：

```text
1. 知道 Akane 启动后有哪些核心对象。
2. 知道一个用户消息会经过哪些层。
3. 知道每个目录大概负责什么。
4. 知道以后想改功能该找哪个文件。
5. 知道哪些模块值得精读，哪些先知道位置即可。
```

一句话概括：

```text
Akane = 多客户端入口 + 统一后端大脑 + 记忆系统 + LLM/RAG/工具编排 + 流式前端体验。
```

---

## 二、先看项目根目录

根目录里最重要的东西：

```text
AkaneCompanionLab/
├─ companion_v01/                 后端核心
├─ web/                           网页预览前端
├─ desktop_pet/                   Electron 桌宠
├─ desktop_pet_next/              Tauri 桌宠 next
├─ tests/                         回归测试和合同测试
├─ docs/                          架构文档和设计说明
├─ services/                      外部服务客户端，比如 LLM client
├─ users_data/                    本地运行数据
├─ config.py                      配置入口
├─ launch_akane_memory_v01.py     后端启动入口
├─ start_akane_preview.ps1        预览启动脚本
├─ start_akane_desktop_pet.ps1    桌宠启动脚本
├─ requirements.txt               Python 依赖
└─ README.md                      项目说明
```

你读项目时先不要进入所有目录。

先记住三条主线：

```text
后端主线：
launch_akane_memory_v01.py
-> companion_v01/app.py
-> companion_v01/routes/think.py
-> companion_v01/engine.py

存储主线：
companion_v01/store.py
-> users_data/.../akane_memory_v01.db
-> companion_v01/vector_store.py

客户端主线：
web/app.js
desktop_pet/renderer/app.js
desktop_pet_next/src/main.js
```

---

## 三、启动入口：launch_akane_memory_v01.py

这个文件很短，但地位很高。

它做的事：

```text
读取 host / port
打印本地访问地址
用 uvicorn 启动 companion_v01.app:app
```

关键代码：

```python
if __name__ == "__main__":
    host = os.getenv("COMPANION_HOST", getattr(config, "HOST", "0.0.0.0"))
    port = int(os.getenv("COMPANION_PORT", str(getattr(config, "PORT", 9999))))

    uvicorn.run("companion_v01.app:app", host=host, port=port, reload=False)
```

你可以把它理解成：

```text
项目的电源开关。
```

真正的应用不是写在这个文件里，而是在：

```text
companion_v01/app.py
```

---

## 四、配置入口：config.py

`config.py` 负责把环境变量和默认值整理成项目配置。

重要配置大类：

```text
DATA_DIR
用户数据目录。

AUX_* / CHAT_*
辅助模型和聊天模型配置。

VISION_*
视觉模型配置。

TTS_*
语音合成配置。

ENABLE_VECTOR_MEMORY
是否启用向量记忆。

ENABLE_SEMANTIC_MEMORY
是否启用长期语义记忆。

MAX_TOOL_ROUNDS
一轮对话最多允许几轮工具调用。

WEB_IDENTITY_MODE
网页端身份模式。

QQ_*
QQ / NapCat 接入。

BACKGROUND_*
后台任务 worker 数量。
```

你以后调试时常看的配置：

```python
HOST = "0.0.0.0"
PORT = 9999
DATA_DIR = "users_data"
ENABLE_VECTOR_MEMORY = False
ENABLE_SEMANTIC_MEMORY = True
MAX_TOOL_ROUNDS = 3
```

配置层的学习重点：

```text
不要把业务逻辑写进 config。
config 只负责“开关、路径、模型名、限额、默认参数”。
```

---

## 五、app.py：后端装配中心

`companion_v01/app.py` 是 FastAPI 应用装配中心。

它做了几件事：

```text
1. 创建 FastAPI app
2. 配置 CORS
3. 创建 ResourceManifest
4. 创建 AkaneMemoryEngine
5. 创建 TTS client
6. 创建 PublicThinkGuard
7. 创建 QQ gateway
8. 挂载静态资源
9. include_router 注册所有路由
10. shutdown 时关闭 engine
```

核心对象装配：

```python
resources = ResourceManifest(ASSETS_DIR)

engine = AkaneMemoryEngine(
    Path(config.DATA_DIR) / "akane_memory_v01",
    resource_manifest=resources,
    desktop_pet_character_resources=desktop_pet_character_resources,
)
```

可以把 `app.py` 理解成：

```text
项目的总插线板。
```

它自己不应该写太多业务逻辑。

业务逻辑应该放在：

```text
engine.py
store.py
retrieval_service.py
tool_runtime.py
...
```

---

## 六、app.py 里的对象关系

可以画成这样：

```text
FastAPI app
├─ engine: AkaneMemoryEngine
├─ resources: ResourceManifest
├─ tts_client: EdgeTTSClient
├─ public_guard: PublicThinkGuard
├─ qq_gateway: NapCatQQGateway
├─ runtime_metrics: RuntimeMetrics
└─ routers
   ├─ core
   ├─ system
   ├─ think
   ├─ desktop_pet
   ├─ gifts
   ├─ qq
   ├─ sessions
   ├─ voice
   ├─ control_center
   ├─ web_static
   └─ reminders
```

`include_router` 的作用：

```python
app.include_router(build_think_router(...))
app.include_router(build_desktop_pet_router(...))
app.include_router(build_sessions_router(...))
```

这说明：

```text
路由模块不是全局乱拿变量。
app.py 把需要的依赖显式传进去。
```

这种写法叫依赖注入的简单形式。

好处：

```text
测试时可以传 FakeEngine。
路由逻辑和真实 engine 解耦。
```

`tests/test_backend_route_modules.py` 就大量用了这个思路。

---

## 七、routes：外部接口层

`companion_v01/routes/` 是 HTTP 接口层。

主要文件：

```text
routes/core.py
健康检查、资源清单、桌宠诊断。

routes/think.py
/think 和 /think_once，对话主入口。

routes/sessions.py
会话创建、切换、历史同步。

routes/voice.py
TTS / ASR。

routes/desktop_pet.py
桌宠工作区、附件、屏幕感知、音乐 timeline。

routes/gifts.py
礼物 / 用户资源上传。

routes/reminders.py
提醒查询和触发。

routes/qq.py
QQ / NapCat 接入。

routes/control_center.py
控制中心快照。

routes/web_static.py
网页静态文件入口。
```

路由层的原则：

```text
路由负责 HTTP。
Engine 负责业务。
Store 负责持久化。
```

比如 `/think` 的路由只做：

```text
读 request.json()
并发/限流 guard
调用 engine.process_turn_stream(payload)
把 engine 事件包装成 NDJSON StreamingResponse
记录 metrics
```

它不应该自己写 RAG、prompt、工具调用。

---

## 八、/think 是整个项目的主入口

`routes/think.py` 里有两个接口：

```text
POST /think
流式接口，返回 NDJSON。

POST /think_once
一次性 JSON 接口，主要用于测试或非流式客户端。
```

流式版本核心：

```python
@router.post("/think")
async def think(request: Request):
    payload = await request.json()

    def _stream():
        yield json.dumps({"type": "stream_start"}) + "\n"

        for event in engine.process_turn_stream(payload):
            yield json.dumps(event, ensure_ascii=False) + "\n"

        yield json.dumps({"type": "stream_end"}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")
```

真实代码更完整，会处理：

```text
invalid_json
invalid_payload
public_guard 限流
stream_error
partial
runtime_metrics
X-Accel-Buffering: no
```

但主线就是上面那几步。

---

## 九、AkaneMemoryEngine：业务大脑

`companion_v01/engine.py` 是项目最核心的文件。

不要把它看成“一个巨大文件”。  
它其实是一个编排器。

它连接了这些服务：

```text
MemoryStore
VectorStore
LLMRuntime
RetrievalService
MemoryCompactionService
GiftSystemService
AttachmentInboxService
AttachmentIngestService
GeneratedFileService
TaskWorkspaceService
TaskWorkerService
PersonaCardService
VisionObservationService
DesktopScreenVisionWorkspace
CapabilityRegistry
ModeProfileRegistry
PromptProfileRegistry
OutputAdapterRegistry
ToolHandlers
```

`engine.__init__()` 的意义：

```text
把整个后端核心的零件都造出来，并连接起来。
```

简化版：

```python
class AkaneMemoryEngine:
    def __init__(self, base_dir, resource_manifest=None):
        self.store = MemoryStore(base_dir)
        self.vector_store = VectorStore(base_dir / "chroma")
        self.llm = LLMRuntime()
        self.prompt_builder = PromptBuilder(PERSONA)
        self.retrieval_service = RetrievalService(...)
        self.compaction_service = MemoryCompactionService(...)
        self.tool_handlers = self._build_tool_handlers()
        self.capability_registry = CapabilityRegistry()
```

这就是“组合优于继承”的项目结构。

---

## 十、Engine 不是模型本身

很容易误解：

```text
engine.py = LLM 模型？
```

不是。

更准确地说：

```text
engine.py 是调度员。
LLMRuntime 才是模型调用层。
```

Engine 负责：

```text
接收 payload
解析 client mode
保存用户消息
运行记忆检索
构造 prompt
调用 LLM
归一化输出
执行工具
保存助手消息
生成前端事件
```

LLMRuntime 负责：

```text
把 system_prompt / user_prompt 发给模型
拿回 JSON
做 fallback
流式解析 JSON
输出 speech_chunk / ui / speech_segment
```

这两层不要混。

---

## 十一、一次完整对话的总流程

最重要的全链路：

```text
前端用户输入
-> POST /think
-> routes/think.py
-> engine.process_turn_stream(payload)
-> 解析 client_context
-> 保存用户消息到 SQLite
-> 调度记忆总结
-> 取 recent raw / episodic / semantic memory
-> 运行 RAG pre-retrieval
-> 构造最终 prompt
-> LLMRuntime 流式生成 JSON
-> 前端先收到 ui / speech_chunk
-> engine 检查 tool_call
-> 如果有工具，执行工具并生成 tool_events
-> 把工具结果写入 followup_context
-> 再次调用 LLM 生成最终回应
-> normalize_final_output
-> OutputAdapter 裁剪客户端字段
-> 保存助手消息
-> append eval_turn
-> yield final_ui
-> yield final
-> routes/think.py 包成 NDJSON
-> 前端逐行消费事件
```

这就是 Akane 的“主血管”。

你之后看任何功能，都要先问：

```text
它插在这条主链路的哪一段？
```

---

## 十二、process_turn 和 process_turn_stream

Engine 有两个主入口：

```python
def process_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
    ...

def process_turn_stream(self, payload: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
    ...
```

区别：

```text
process_turn
一次性执行，最后 return final_output。
对应 /think_once。

process_turn_stream
执行过程中不断 yield 事件。
对应 /think。
```

二者业务基本类似：

```text
保存用户消息
RAG
LLM
工具循环
保存助手消息
返回 final output
```

但流式版本多了：

```text
yield ui
yield speech_chunk
yield speech_segment
yield assistant_stage_decision
yield tool_events
yield final_ui
yield final
```

你读代码时优先看：

```text
process_turn_stream
```

因为 Akane 前端真正用的是流式接口。

---

## 十三、process_turn_stream 的关键阶段

可以把它拆成 9 段：

```text
1. 解析请求身份和客户端模式
2. 构造额外上下文
3. 保存用户消息
4. 获取可见记忆
5. 运行 RAG
6. 流式生成第一版 final_output
7. 工具循环
8. 后处理和持久化
9. yield final_ui / final
```

伪代码：

```python
def process_turn_stream(payload):
    client_context = resolve_client_protocol_context(payload)
    user_record = store.add_message(...)

    recent_raw = store.get_unsummarized_messages(session_id)
    summaries = store.get_visible_episodic_summaries(profile_user_id)
    semantic = store.get_recent_semantic_summaries(profile_user_id)

    retrieval_pipeline = run_pre_retrieval_pipeline(...)

    final_output = yield from stream_final_response(...)

    for _ in range(max_tool_rounds):
        tool_call = normalize_tool_call(final_output["tool_call"])
        if not tool_call:
            break

        tool_result = execute_tool_call(tool_call)
        yield from tool_result.stream_events

        final_output = yield from stream_final_response(
            extra_user_context=tool_result.followup_context
        )

    final_output = normalize_and_persist(final_output)
    yield {"type": "final_ui", "payload": ui_final_payload}
    yield {"type": "final", "payload": final_output}
```

这段伪代码就是你读 `engine.py` 的地图。

---

## 十四、身份系统：profile_user_id 和 session_id

Akane 里经常出现两个 ID：

```text
profile_user_id
用户身份。

session_id
会话身份。
```

可以这样理解：

```text
profile_user_id = 这个人是谁
session_id = 这次聊天窗口是哪一个
```

比如桌宠说明里：

```text
桌宠共享 profile_user_id=master，
但使用独立 session_id。
```

意义：

```text
同一个人可以有多个会话。
长期记忆归 profile。
当前对话和工作区归 session。
```

路由里经常有：

```python
session_id = payload.get("user_id") or payload.get("session_id")
profile_user_id = payload.get("real_user_id") or session_id
```

所以你看到：

```text
user_id
real_user_id
session_id
profile_user_id
```

不要乱。

核心记法：

```text
real_user_id / profile_user_id 更接近“主人是谁”。
user_id / session_id 更接近“这次对话是哪一条线”。
```

---

## 十五、MemoryStore：SQLite 持久化核心

`companion_v01/store.py` 是 SQLite 层。

它负责：

```text
建表
连接数据库
增删改查消息
保存总结
保存提醒
保存会话
保存礼物
保存视觉观察
保存 persona
保存附件
保存生成文件
保存任务工作区
```

核心数据库路径：

```python
self.db_path = self.base_dir / "akane_memory_v01.db"
```

主要表：

```text
chat_messages
原始聊天消息。

memory_summaries
阶段性 episodic summaries。

memory_semantic_summaries
长期语义记忆。

eval_turns
每轮调试记录。

chat_sessions
会话列表。

reminders
提醒。

user_media_assets
用户上传礼物/资源。

vision_observations
视觉观察。

persona_cards / persona_events / persona_session_states
人设卡系统。

attachment_inbox_items
临时附件收件箱。

generated_files
生成文件。

desktop_music_timelines
桌宠音乐 timeline。

task_workspaces / task_workspace_events
任务工作区和事件。
```

你读 `store.py` 不要从头背。

读法：

```text
先看 CREATE TABLE，知道数据模型。
再按功能 rg 函数名。
```

比如想找消息保存：

```powershell
rg -n "def add_message|def get_unsummarized|def get_session_messages" companion_v01\store.py
```

---

## 十六、SQLite 层的典型模式

`MemoryStore` 里有一个连接上下文：

```python
@contextmanager
def _connect(self):
    conn = sqlite3.connect(self.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
```

这说明每次数据库操作大概是：

```python
with self._connect() as conn:
    conn.execute(...)
```

这种模式你要掌握：

```text
打开连接
执行 SQL
commit
关闭连接
```

为什么 `row_factory = sqlite3.Row`？

因为这样查询结果既像 tuple，也能按字段名访问：

```python
row["content"]
row["timestamp"]
```

这比记列下标清楚很多。

---

## 十七、VectorStore：向量检索和关键词检索

`companion_v01/vector_store.py` 负责向量记忆。

底层使用：

```text
ChromaDB PersistentClient
```

主要能力：

```text
upsert_entries
把消息/总结写进向量库。

semantic_search
向量相似度检索。

keyword_search
BM25 关键词检索。

fuse_with_rrf
把向量结果和关键词结果融合。
```

向量库不是替代 SQLite。

关系是：

```text
SQLite
保存权威数据。

VectorStore
保存可检索索引。
```

如果向量库坏了，理论上可以从 SQLite 重建。

Engine 初始化时也有 reindex 逻辑：

```text
count_vectorizable_records
iter_messages_for_vector_reindex
iter_summaries_for_vector_reindex
iter_semantic_summaries_for_vector_reindex
vector_store.upsert_entries
```

这就是典型的：

```text
主存储 + 派生索引
```

---

## 十八、记忆三层结构

Akane 的记忆不是只有聊天记录。

它有三层：

```text
Layer 1: raw / working memory
chat_messages 里未总结的近期原始对话。

Layer 2: episodic memory
memory_summaries 里按时间片压缩的阶段总结。

Layer 3: semantic memory
memory_semantic_summaries 里长期稳定事实、反复话题、open loops。
```

流动方式：

```text
用户/助手消息
-> chat_messages
-> 到达阈值后，旧 raw 被压缩成 memory_summaries
-> episodic summaries 到达阈值后，被压缩成 semantic summaries
-> 三层都可以进入向量库
```

`MemoryCompactionService` 负责这个过程。

关键方法：

```python
schedule_summary_cycle(...)
run_summary_cycle(...)
_summarize_batch(...)
_semanticize_summary_batch(...)
```

你可以把它理解成：

```text
后台整理记忆的服务。
```

---

## 十九、RAG 检索主线

RAG 主要在：

```text
companion_v01/retrieval_service.py
companion_v01/retrieval_engine.py
companion_v01/retrieval_types.py
companion_v01/vector_store.py
```

主流程：

```text
当前用户消息
-> router 判断是否需要检索 / 构造 query / keywords / time_hint
-> semantic_search
-> keyword_search
-> RRF 融合
-> verifier 判断哪些候选真的有用
-> confirmed_snippets 注入 final prompt
```

Engine 里调用：

```python
retrieval_pipeline = self._run_pre_retrieval_pipeline(...)
confirmed_snippets = retrieval_pipeline.confirmed_snippets
```

最终 prompt 里会把它变成：

```text
memory_text = "\n\n".join(confirmed_snippets)
```

学习重点：

```text
RAG 不是“搜到就塞进 prompt”。
Akane 还有 verifier 过滤。
```

这能降低乱召回旧记忆的概率。

---

## 二十、LLMRuntime：模型调用层

`companion_v01/llm_runtime.py` 负责调用模型。

它有两个 bundle：

```python
self.aux = self._build_aux_bundle()
self.chat = self._build_chat_bundle()
```

可以理解为：

```text
aux
辅助模型，用于路由、总结、校验等。

chat
聊天模型，用于最终回复。
```

主要方法：

```text
call_aux_json
辅助模型输出 JSON。

call_chat_json
聊天模型输出 JSON。

stream_chat_json
聊天模型流式输出 JSON。
```

这里有一个关键组件：

```text
_TopLevelJSONStreamTap
```

它会在模型流式输出 JSON 时提前捕捉：

```text
emotion
speech
speech_segment
```

并生成前端事件：

```json
{"type":"ui","emotion":"开心"}
{"type":"speech_chunk","text":"主人，"}
{"type":"speech_segment","text":"主人，我在哦。"}
```

所以前端能边生成边显示，并不是前端猜出来的。  
是 `LLMRuntime` 在流式解析模型 JSON。

---

## 二十一、PromptBuilder 和 PromptProfile

Prompt 相关模块：

```text
companion_v01/prompt_builder.py
companion_v01/prompt_profiles.py
companion_v01/prompt_blocks.py
companion_v01/persona_config.py
```

职责分工：

```text
PromptBuilder
把 raw memory、summary、semantic memory、RAG snippets、资源、工具说明等拼成最终 prompt。

PromptProfileRegistry
根据 client_mode 决定本轮要挂哪些 prompt 模块。

prompt_blocks.py
存放不同客户端的规则块。

persona_config.py
Akane 人格设定和 fallback 文案。
```

`PromptModule` 包括：

```python
CLIENT_MODE
EXTRA_CONTEXT
CURRENT_VISUAL_STATE
SCENE_OBSERVATION
OUTFIT_OBSERVATION
RESOURCE_MANIFEST
PENDING_GIFTS
FOCUSED_GIFT_OBSERVATION
PERSONA
TOOLS
```

也就是说：

```text
不同客户端看到的 prompt 不是一样的。
```

比如：

```text
scene_static
需要场景、背景、BGM、礼物、小世界。

desktop_pet
需要桌宠视觉、activity、桌面上下文、工作区。

qq_text
需要文字聊天、附件、文件交付，不需要 Web 场景字段。
```

---

## 二十二、ClientMode：多客户端统一大脑

Akane 后端支持多客户端：

```python
class ClientMode(str, Enum):
    SCENE_STATIC = "scene_static"
    SCENE_LIVE2D = "scene_live2d"
    DESKTOP_PET = "desktop_pet"
    QQ_TEXT = "qq_text"
```

相关文件：

```text
client_protocol.py
mode_profiles.py
prompt_profiles.py
capability_registry.py
output_adapters.py
```

这几层依次解决：

```text
client_protocol.py
客户端模式和能力枚举。

mode_profiles.py
请求模式能不能真正运行，不行就降级。

prompt_profiles.py
这个模式该用哪些 prompt 模块。

capability_registry.py
这个模式能看到哪些工具。

output_adapters.py
最终输出要给客户端保留或移除哪些字段。
```

一句话：

```text
ClientMode 决定“同一个 Akane 大脑如何长出不同客户端身体”。
```

---

## 二十三、CapabilityRegistry：工具能力不是全量暴露

Akane 的工具很多。  
如果每轮都把所有工具说明塞给模型，会有两个问题：

```text
prompt 太长
模型更容易乱调用工具
```

所以有：

```text
CapabilityRegistry
```

它根据当前状态选择工具：

```text
当前客户端是什么？
有没有附件？
有没有文档附件？
有没有媒体附件？
有没有生成文件？
有没有可交付文件？
```

然后选择工具层：

```text
common
所有客户端共有。

web_scene
网页小世界专属。

shared_document
QQ / 桌宠共享文档能力。

shared_media
QQ / 桌宠共享媒体能力。

qq_delivery
QQ 交付专属。

desktop_workspace
桌宠工作区专属。
```

这就是为什么：

```text
桌宠可以用媒体处理工具，
但不会继承 QQ 贴图发送逻辑。
```

这部分是非常值得学的架构设计。

---

## 二十四、Tool Runtime：工具调用系统

工具协议在：

```text
companion_v01/tool_runtime.py
companion_v01/tool_orchestration_engine.py
```

核心类：

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

@dataclass
class ToolExecutionResult:
    tool_type: str
    raw_turns: list[dict[str, Any]] = field(default_factory=list)
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    followup_context: str = ""
    state_updates: dict[str, Any] = field(default_factory=dict)
```

每个工具 handler 都要实现：

```python
class BaseToolHandler:
    def build_prompt_instruction(self) -> str:
        ...

    def normalize_call(self, value) -> dict | None:
        ...

    def execute(self, *, call, context) -> ToolExecutionResult:
        ...
```

这是标准的插件式设计。

工具调用主线：

```text
模型输出 tool_call
-> normalize_tool_call 校验和裁剪
-> execute_tool_call 执行
-> 返回 stream_events 给前端
-> followup_context 回填给模型
-> 模型再生成自然语言回复
```

---

## 二十五、当前主要工具类别

不要背所有工具。

先按类别记：

```text
记忆工具
retrieve_memory

提醒工具
set_reminder / list_reminders / cancel_reminder

NPC 和小世界工具
call_npc / check_inventory / manage_gift / manage_artifact

附件工具
inspect_attachment / read_attachment_section / sync_attachment_workspace / retry_attachment

文件生成工具
compose_file / revise_generated_file / apply_style_to_existing_file

媒体工具
inspect_media_info / convert_media_file / separate_audio_stems / clean_voice_track / transcribe_media / prepare_voice_dataset

交付工具
send_file / send_generated_file

Persona 工具
manage_persona

任务工作区工具
manage_task_workspace / delegate_task

贴图工具
send_sticker
```

从架构角度看，最重要的是：

```text
工具不直接等于回复。
工具产生结果，结果再喂给模型，让 Akane 自然回应。
```

这就是 Agent 工程。

---

## 二十六、工具循环为什么有限制

Engine 里有：

```text
max_tool_rounds = self._max_tool_rounds()
```

配置来自：

```python
MAX_TOOL_ROUNDS = 3
```

这表示一轮对话最多允许几轮：

```text
模型 -> 工具 -> 模型 -> 工具 -> 模型
```

为什么要限制？

```text
防止模型重复调用同一个工具。
防止成本爆炸。
防止用户等太久。
防止无限循环。
```

代码里还会记录：

```python
seen_tool_calls: set[str] = set()
```

如果同一个工具调用重复出现，会拦截并提醒模型不要重复。

这是 Agent 系统很重要的安全阀。

---

## 二十七、OutputAdapter：最后给不同客户端裁剪

模型输出可能包含：

```json
{
  "emotion": "happy",
  "speech": "主人，我在。",
  "scene": {"background": "evening"},
  "character": {"outfit": "猫娘"},
  "live2d": {"motion": "idle"},
  "activity": {"action": "play", "target": "current"}
}
```

但不同客户端不能都拿一样的字段。

`output_adapters.py` 做裁剪：

```text
SceneStaticOutputAdapter
保留 Web scene 所需字段。

QQTextOutputAdapter
移除 scene / character / live2d / pet。

DesktopPetOutputAdapter
移除 scene / live2d / pet，
保留简化 character.outfit 和 activity。
```

为什么很重要？

```text
防止 QQ 看到 Web 场景字段。
防止桌宠继承 QQ 交付逻辑。
防止客户端之间互相污染。
```

这就是多客户端项目的边界治理。

---

## 二十八、前端客户端层

Akane 有多个客户端：

```text
web/
浏览器预览版。

desktop_pet/
Electron 桌宠。

desktop_pet_next/
Tauri 桌宠 next。

qq_gateway
QQ / NapCat 文本客户端。
```

它们共享后端大脑：

```text
都把输入转换成 /think payload。
都从后端拿最终 payload。
区别是 client_mode 和 capabilities。
```

网页端：

```text
web/app.js
-> sendMessage
-> consumeNdjsonStream
-> handleThinkStreamEvent
```

Electron 桌宠：

```text
desktop_pet/renderer/services/BackendClient.js
-> async generator 读取 /think

desktop_pet/renderer/app.js
-> processStream
-> PresentationController
-> ActivityRuntime
```

Tauri 桌宠 next：

```text
desktop_pet_next/src/main.js
-> sendThinkStream
-> readNdjsonEvents
-> processThinkStream
-> renderPayload
-> handleDesktopFileDeliveryEvent
```

---

## 二十九、资源系统和视觉上下文

相关模块：

```text
resource_manifest.py
visual_context_engine.py
vision_service.py
vision_observation_router.py
desktop_pet_character_resources.py
desktop_pet_contract.py
```

它们解决的问题：

```text
有哪些背景？
有哪些 BGM？
有哪些服装？
有哪些表情？
当前客户端能渲染哪些资源？
用户上传的图片/礼物是否能变成资源？
桌宠角色包如何给客户端？
```

网页 scene_static 需要完整资源：

```text
scene.major
scene.minor
background
bgm
character.outfit
emotion
```

桌宠更偏：

```text
character outfit
emotion
本地角色包
activity
workspace
```

所以资源系统也受 `client_mode` 影响。

---

## 三十、附件、生成文件、任务工作区

这一组是 Tool Calling 后期很重要的产品能力。

相关文件：

```text
attachment_inbox.py
attachment_ingest.py
generated_files.py
task_workspace.py
task_workspace_engine.py
task_worker.py
task_worker_tool.py
background_tasks.py
```

可以这样理解：

```text
Attachment Inbox
用户给 Akane 的临时材料。
比如 QQ 文件、桌宠拖入文件、图片、音频。

Generated Files
Akane 工具生成的结果文件。
比如 Markdown、docx、xlsx、音频转写、分离后的人声。

Task Workspace
复杂任务的工作台。
记录目标、步骤、状态、产物、事件。

Task Worker
后台 Agent 工坊。
把长任务交给受限 specialist 慢慢跑。
```

完整链路：

```text
用户：把这个音频转写成字幕
-> 附件进入 Attachment Inbox
-> 模型调用 transcribe_media
-> 工具生成字幕文件
-> GeneratedFileService 保存 gen_001
-> TaskWorkspace 记录产物
-> 前端收到 generated_file_ready
-> 桌宠/QQ 把文件交给用户
```

---

## 三十一、后台任务系统

Akane 有些任务不能卡住主对话：

```text
长音频转写
视频下载
人声分离
文件处理
后台工坊 Agent
```

所以有：

```text
BackgroundTaskRunner
TaskWorkerService
```

`TaskWorkerService` 的定位：

```text
受限后台专家。
不是另一个自由聊天助手。
```

它被限制：

```text
只能用指定工具集。
只围绕 task workspace 工作。
写进任务事件。
不会自己直接给用户发消息。
```

这点很关键：

```text
后台 Agent 要有边界，不然很容易失控。
```

Akane 的设计比“随便开一个 agent 循环”更工程化。

---

## 三十二、QQ 接入不是新大脑

QQ 相关：

```text
qq_gateway.py
routes/qq.py
config.py 里的 QQ_*
```

QQ 模式是：

```text
外部 QQ 消息
-> NapCat / OneBot
-> routes/qq.py
-> 转成 Akane /think 风格 payload
-> client_mode = qq_text
-> Engine 处理
-> 输出适配成 QQ 文本/文件/表情
-> qq_gateway 发回
```

重点：

```text
QQ 只是一个客户端。
不是另一套 Akane。
```

这和桌宠一样：

```text
桌宠也不是新大脑。
桌宠是 desktop_pet client_mode 的客户端身体。
```

这就是多端统一核心。

---

## 三十三、桌宠后端接口

桌宠相关后端文件：

```text
desktop_pet_contract.py
desktop_pet_engine.py
desktop_context_engine.py
desktop_screen_vision.py
desktop_music_timeline.py
routes/desktop_pet.py
```

桌宠接口包括：

```text
GET  /desktop-pet/health
GET  /desktop-pet/diagnostics
GET  /desktop-pet/workspace/summary
POST /desktop-pet/workspace/action
POST /desktop-pet/workspace/import-local
POST /desktop-pet/attachments/audio
GET  /desktop-pet/attachments/{handle}/content
GET  /desktop-pet/generated/{handle}/content
POST /desktop-pet/music-timeline/prepare
POST /desktop-pet/vision/clip
GET  /desktop-pet/vision/latest
POST /desktop-pet/vision/reaction
POST /desktop-pet/vision/clear
```

这些接口不是聊天主流程本身，而是桌宠本地体验的补充能力：

```text
工作区
音频上传
文件内容获取
音乐 timeline
屏幕感知
健康诊断
```

聊天主流程仍然是：

```text
POST /think
```

---

## 三十四、测试体系是项目地图的一部分

`tests/` 不是可有可无。

它告诉你项目哪些行为被认为重要。

重点测试：

```text
test_store.py
SQLite 存储行为。

test_vector_store.py
向量检索和融合。

test_retrieval_service.py
RAG 流程。

test_prompt_builder.py
Prompt 构造。

test_llm_runtime_stream.py
流式 JSON 解析。

test_client_protocol.py
client_mode / output adapter。

test_desktop_pet_backend_contract.py
桌宠后端合同。

test_desktop_pet_frontend_contract.py
桌宠前端关键行为。

test_desktop_pet_audio_activity_protocol.py
activity 从 prompt 到最终输出的链路。

test_backend_route_modules.py
路由模块边界。

test_task_worker.py
后台工坊。
```

读测试的方法：

```text
先看测试名。
测试名往往就是需求说明。
```

比如：

```text
test_desktop_pet_adapter_strips_web_fields_but_keeps_character_and_activity
```

翻译成人话：

```text
桌宠输出适配器应该移除 Web 字段，但保留 character 和 activity。
```

这比直接看实现更容易理解设计意图。

---

## 三十五、docs 目录的价值

`docs/` 里有一些很值得读的设计文档：

```text
client-mode-architecture.md
多客户端架构边界。

layered_memory_design.md
三层记忆设计。

memory_retrieval_v02.md
RAG 检索设计。

tool_interface.md
工具系统接口。

akane_workshop_multi_agent_v1.md
后台工坊 / 多 Agent 设计。

desktop_pet_v01.md
桌宠说明。

desktop_activity_runtime_v0.md
桌宠 activity runtime。

desktop-pet-visual-renderer.md
桌宠视觉渲染边界。
```

读项目时，文档和测试非常重要：

```text
源码告诉你“现在怎么做”。
测试告诉你“哪些行为不能破坏”。
文档告诉你“为什么这么设计”。
```

三者一起读，效率最高。

---

## 三十六、可运行练习：用迷你代码模拟 Akane 架构

下面这段不是 Akane 源码，而是迷你版架构模拟。

它帮你理解：

```text
Route -> Engine -> Store -> Retriever -> LLM -> Tool -> Output
```

可以保存成 `mini_akane_arch.py` 运行。

```python
from dataclasses import dataclass, field


class MemoryStore:
    def __init__(self):
        self.messages = []

    def add_message(self, role, content):
        record = {"role": role, "content": content}
        self.messages.append(record)
        return record

    def recent_messages(self, limit=6):
        return self.messages[-limit:]


class RetrievalService:
    def run(self, user_message, recent_messages):
        if "之前" not in user_message:
            return []
        return [f"可用记忆：最近聊过 {len(recent_messages)} 条消息。"]


class LLMRuntime:
    def call_chat_json(self, prompt):
        if "提醒" in prompt:
            return {
                "emotion": "normal",
                "speech": "我可以帮你记一下。",
                "tool_call": {"type": "set_reminder", "content": "喝水"},
            }
        return {
            "emotion": "happy",
            "speech": "主人，我在哦。",
            "tool_call": None,
        }


@dataclass
class ToolExecutionResult:
    stream_events: list[dict] = field(default_factory=list)
    followup_context: str = ""


class ReminderTool:
    def execute(self, call):
        return ToolExecutionResult(
            stream_events=[{"type": "reminder_set", "content": call["content"]}],
            followup_context=f"提醒已经设置：{call['content']}",
        )


class AkaneEngine:
    def __init__(self):
        self.store = MemoryStore()
        self.retrieval = RetrievalService()
        self.llm = LLMRuntime()
        self.tools = {"set_reminder": ReminderTool()}

    def process_turn_stream(self, message):
        yield {"type": "stream_start"}
        self.store.add_message("user", message)

        recent = self.store.recent_messages()
        memories = self.retrieval.run(message, recent)
        prompt = f"最近消息：{recent}\n记忆：{memories}\n用户：{message}"

        output = self.llm.call_chat_json(prompt)
        yield {"type": "speech_chunk", "text": output["speech"]}

        tool_call = output.get("tool_call")
        if tool_call:
            tool = self.tools.get(tool_call["type"])
            result = tool.execute(tool_call)
            for event in result.stream_events:
                yield event

            prompt += f"\n工具结果：{result.followup_context}"
            output = {
                "emotion": "happy",
                "speech": "好啦，我已经帮你记下了。",
                "tool_call": None,
            }

        self.store.add_message("assistant", output["speech"])
        yield {"type": "final_ui", "payload": output}
        yield {"type": "stream_end", "status": "ok"}


engine = AkaneEngine()

for event in engine.process_turn_stream("5 分钟后提醒我喝水"):
    print(event)
```

输出类似：

```text
{'type': 'stream_start'}
{'type': 'speech_chunk', 'text': '我可以帮你记一下。'}
{'type': 'reminder_set', 'content': '喝水'}
{'type': 'final_ui', 'payload': {'emotion': 'happy', 'speech': '好啦，我已经帮你记下了。', 'tool_call': None}}
{'type': 'stream_end', 'status': 'ok'}
```

这个迷你版就是 Akane 主链路的缩影。

---

## 三十七、如何从一个需求定位代码

以后你看到需求，可以这样定位。

需求：

```text
“网页发送消息后没反应”
```

先看：

```text
web/app.js -> sendMessage / consumeNdjsonStream
routes/think.py -> /think
engine.py -> process_turn_stream
```

需求：

```text
“模型输出格式乱了”
```

先看：

```text
prompt_profiles.py
prompt_builder.py
final_output_engine.py
llm_runtime.py
```

需求：

```text
“记忆没搜到”
```

先看：

```text
retrieval_service.py
retrieval_engine.py
vector_store.py
memory_rendering.py
tests/test_retrieval_service.py
```

需求：

```text
“提醒工具不工作”
```

先看：

```text
tool_runtime.py -> SetReminderToolHandler
reminder_engine.py
routes/reminders.py
store.py -> reminders 表
```

需求：

```text
“桌宠没有播放音乐”
```

先看：

```text
final_output_engine.py -> normalize_activity_action
client_protocol.py -> audio_playback capability
desktop_pet_next/src/main.js -> applyPayloadActivity
desktop_pet/renderer/services/ActivityRuntime.js
tests/test_desktop_pet_audio_activity_protocol.py
```

需求：

```text
“生成文件没有交付给用户”
```

先看：

```text
tool_runtime.py -> compose_file / send_file
generated_files.py
task_workspace_engine.py
desktop_pet_next/src/main.js -> handleDesktopFileDeliveryEvent
routes/desktop_pet.py -> workspace/generated content
```

需求：

```text
“QQ 和桌宠拿到了不该拿的字段”
```

先看：

```text
output_adapters.py
client_protocol.py
mode_profiles.py
prompt_profiles.py
tests/test_client_protocol.py
```

---

## 三十八、推荐阅读顺序

你已经看完前 10 篇笔记了，源码阅读可以按这个顺序：

```text
第一轮：只读主线
1. launch_akane_memory_v01.py
2. companion_v01/app.py
3. companion_v01/routes/think.py
4. companion_v01/engine.py 的 process_turn_stream
5. web/app.js 的 sendMessage
```

目标：

```text
知道请求怎么进来，怎么出去。
```

第二轮：读数据和记忆

```text
1. companion_v01/store.py 的表结构
2. companion_v01/vector_store.py
3. companion_v01/retrieval_service.py
4. companion_v01/memory_compaction_service.py
```

目标：

```text
知道消息怎么保存、怎么检索、怎么压缩。
```

第三轮：读 LLM 和输出

```text
1. companion_v01/llm_runtime.py
2. companion_v01/prompt_builder.py
3. companion_v01/prompt_profiles.py
4. companion_v01/final_output_engine.py
5. companion_v01/output_adapters.py
```

目标：

```text
知道 prompt 怎么来，JSON 怎么归一化，客户端字段怎么裁剪。
```

第四轮：读工具和 Agent

```text
1. companion_v01/tool_runtime.py
2. companion_v01/tool_orchestration_engine.py
3. companion_v01/capability_registry.py
4. companion_v01/task_worker.py
5. companion_v01/task_workspace.py
```

目标：

```text
知道 Akane 如何从聊天变成做事。
```

第五轮：读客户端

```text
1. web/app.js
2. web/modules/audio.js
3. desktop_pet/renderer/services/BackendClient.js
4. desktop_pet/renderer/app.js
5. desktop_pet_next/src/main.js
```

目标：

```text
知道前端如何消费事件并呈现体验。
```

---

## 三十九、哪些模块先不要深挖

为了不被项目吞掉，先不要深挖这些：

```text
桌宠设置页 UI 的全部细节
窗口拖动 / hit test / 透明窗口细节
角色包安装的每个边缘分支
音乐歌词 timeline 的全部实现
具体媒体处理命令的每个参数
视觉观察的所有 prompt 细节
QQ NapCat 的所有平台兼容处理
```

这些不是没用，而是：

```text
它们属于分支能力。
先读主链路，之后按需求进入。
```

你现在最该抓住：

```text
后端主链路
记忆系统
LLM/RAG/工具编排
客户端协议
流式前端
```

---

## 四十、Akane 架构里最值得学习的设计

高价值设计点：

```text
1. FastAPI 路由层和业务 Engine 分离。
2. SQLite 主存储 + VectorStore 派生索引。
3. 三层记忆：raw / episodic / semantic。
4. RAG 不是直接塞结果，而是 router + verifier。
5. LLM 输出固定 JSON，再 normalize。
6. Tool Calling 使用 handler registry。
7. 工具可见性由 CapabilityRegistry 控制。
8. 多客户端用 ClientMode / PromptProfile / OutputAdapter 解耦。
9. /think 使用 NDJSON 流式事件。
10. 前端按事件驱动 UI、TTS、activity 和文件交付。
11. 后台任务通过 TaskWorkspace 和 TaskWorkerService 有边界地执行。
12. 测试保护协议边界，而不是只测小函数。
```

这些都不是“只在 Akane 有用”的知识。

以后你做：

```text
AI 助手
RAG 应用
桌面 Agent
多端聊天产品
文件处理机器人
语音交互工具
```

都能用到。

---

## 四十一、用一句话记每个核心文件

```text
launch_akane_memory_v01.py
启动后端。

config.py
配置和开关。

companion_v01/app.py
创建 app、engine、routers。

routes/think.py
对话 HTTP 入口。

engine.py
一轮对话的总编排。

store.py
SQLite 权威存储。

vector_store.py
向量和关键词检索索引。

retrieval_service.py
RAG 检索流水线。

memory_compaction_service.py
记忆总结和语义压缩。

llm_runtime.py
模型调用和流式 JSON 解析。

prompt_builder.py
拼 prompt。

prompt_profiles.py
不同客户端用哪些 prompt 模块。

client_protocol.py
客户端模式和能力协议。

capability_registry.py
工具能力选择。

tool_runtime.py
工具 handler 定义和具体工具。

tool_orchestration_engine.py
工具调用规范化和执行编排。

final_output_engine.py
模型输出归一化。

output_adapters.py
不同客户端输出裁剪。

web/app.js
网页端发送消息和消费流。

desktop_pet_next/src/main.js
Tauri 桌宠主客户端。

tests/
行为边界和回归保护。

docs/
架构意图和演进说明。
```

---

## 四十二、项目结构用脑图记法

可以把 Akane 记成五层：

```text
第 1 层：入口层
launch / app / routes / frontend

第 2 层：协议层
HTTP / JSON / NDJSON / client_mode / capabilities

第 3 层：业务编排层
AkaneMemoryEngine / process_turn_stream

第 4 层：能力层
Memory / RAG / LLM / Tools / Persona / Vision / Files / Tasks

第 5 层：持久化和外部系统
SQLite / Chroma / filesystem / LLM API / TTS / QQ / Tauri / Electron
```

以后你看到任何文件，都可以先放进这五层里。

比如：

```text
routes/desktop_pet.py
入口层。

client_protocol.py
协议层。

engine.py
业务编排层。

retrieval_service.py
能力层。

store.py
持久化层。
```

这样项目就不会显得无边无际。

---

## 四十三、你现在该怎么学这个项目

不要试图一次性记住所有代码。

你的目标应该是：

```text
看到一个文件，知道它属于哪一层。
看到一个函数，知道它在一轮对话哪一步。
看到一个 bug，知道从哪条链路追。
```

学习方式：

```text
1. 先刷这 11 篇笔记。
2. 打开源码，只追一条主线。
3. 每追完一条线，回到这篇总览定位。
4. 不懂的函数先标记，不要在分支里迷路。
5. 最后用测试反向确认行为边界。
```

这和你之前学 Python / PyTorch 的方式其实一样：

```text
先建立知识模块
再拿项目代码对照
最后通过小练习把主线跑通
```

---

## 四十四、这 11 篇笔记的最终阅读顺序

推荐顺序：

```text
01_Python工程化进阶.md
02_SQLite数据库与持久化系统.md
03_FastAPI后端开发.md
04_HTTP_JSON与流式响应.md
05_unittest测试体系.md
06_状态机与业务建模.md
07_LLM应用工程.md
08_RAG与向量检索.md
09_ToolCalling与Agent工程.md
10_前端异步与桌宠客户端.md
11_Akane项目架构总览.md
```

如果你已经刷完前 10 篇，可以把第 11 篇当成：

```text
源码阅读导航页。
```

之后每次打开项目前，先看第 11 篇目录，知道今天要钻哪条线。

---

## 四十五、最后总结

Akane 项目值得学。

它不是一个只会调用大模型的 demo，而是一个包含这些要素的真实 AI 应用工程：

```text
多客户端
流式通信
长期记忆
RAG
结构化 LLM 输出
工具调用
后台任务
桌面客户端
文件工作区
测试合同
```

这套结构对你主攻软件方向非常有价值。

你不用一口气吃完整个项目。  
你只要记住这条主线：

```text
客户端发消息
-> FastAPI 接收
-> Engine 编排
-> Store / RAG / Prompt / LLM / Tools
-> OutputAdapter 裁剪
-> NDJSON 流回客户端
-> 前端事件驱动展示
```

只要这条线清楚，几万行代码就不再是墙，而是一张可以慢慢展开的地图。

