---
tags:
  - akane/fastapi
  - backend/http
  - python/web
  - api-design
created: 2026-05-20
---

# FastAPI 后端开发

> FastAPI 是一个 Python Web 后端框架。  
> 它负责把“外部 HTTP 请求”接进 Python 程序，再把 Python 结果变成 HTTP 响应返回给前端。

对 Akane 来说，FastAPI 就是后端入口：

```text
浏览器 / 桌宠客户端 / QQ 网关
→ HTTP 请求
→ FastAPI 路由
→ AkaneMemoryEngine
→ MemoryStore / LLM / RAG / Tool
→ HTTP 响应或流式响应
```

这份笔记的目标：

```text
学会 FastAPI 的经典后端知识
再回到 Akane 看懂 app.py 和 routes/*.py
```

学习路线：

```text
Web 后端是什么
→ HTTP 基础
→ FastAPI 最小应用
→ uvicorn 启动
→ GET / POST
→ 路径参数、查询参数、请求体
→ Pydantic 数据模型
→ Request 手动读 JSON
→ JSONResponse / Response
→ 状态码和错误处理
→ APIRouter 模块化路由
→ 中间件 CORS
→ 静态文件
→ 文件上传
→ StreamingResponse 流式响应
→ 生命周期 shutdown
→ Akane 的后端结构
```

---

## 一、Web 后端是什么

前端负责展示界面。

后端负责处理请求。

```text
用户点击按钮
→ 前端发请求
→ 后端处理逻辑
→ 后端返回数据
→ 前端更新界面
```

比如 Akane 主界面发一句话：

```text
前端发送 POST /think
后端调用 engine.process_turn_stream(payload)
后端流式返回 Akane 的回复事件
前端一边收到一边显示
```

后端不是“算法本身”，而是把算法、数据库、模型、文件系统包装成可访问的服务。

---

## 二、HTTP 是什么

HTTP 是浏览器和后端之间最常见的通信协议。

一个 HTTP 请求通常包括：

```text
方法 method
路径 path
请求头 headers
请求体 body
```

例子：

```text
POST /think
Content-Type: application/json

{
  "user_id": "default_session",
  "message": "你好 Akane"
}
```

后端返回 HTTP 响应：

```text
状态码 status code
响应头 headers
响应体 body
```

例子：

```text
200 OK
Content-Type: application/json

{
  "speech": "你好呀"
}
```

---

## 三、常见 HTTP 方法

| 方法 | 含义 | 常见用途 |
|---|---|---|
| `GET` | 获取数据 | 健康检查、资源列表、配置 |
| `POST` | 提交数据 | 发送消息、上传文件、创建任务 |
| `PUT` | 整体更新 | 更新完整资源 |
| `PATCH` | 局部更新 | 修改某几个字段 |
| `DELETE` | 删除资源 | 删除记录 |
| `OPTIONS` | 预检请求 | CORS 浏览器预检 |

Akane 里常见：

```text
GET  /health
GET  /resource-manifest
POST /think
POST /tts
POST /asr
```

---

## 四、状态码

常见状态码：

| 状态码 | 含义 |
|---:|---|
| `200` | 成功 |
| `204` | 成功，但没有响应体 |
| `400` | 请求格式错了 |
| `401` | 未登录 |
| `403` | 没权限 |
| `404` | 路径不存在 |
| `413` | 上传内容太大 |
| `429` | 请求太频繁 |
| `500` | 后端内部错误 |
| `502` | 上游服务失败 |
| `503` | 服务暂时不可用 |

Akane 的 `/think` 并发限制触发时：

```python
raise HTTPException(status_code=429, detail=guard_decision.message)
```

意思：

```text
请求太多了，返回 429
```

Akane 的 ASR 文件太大时：

```python
return JSONResponse(
    {"ok": False, "error": "audio_too_large", "message": "录音太长啦，先说短一点试试。"},
    status_code=413,
)
```

---

## 五、安装 FastAPI

Akane 的 `requirements.txt` 里有：

```text
fastapi
python-multipart
uvicorn
```

含义：

```text
fastapi           Web 框架本体
uvicorn           ASGI 服务启动器
python-multipart 处理文件上传 / 表单上传
```

最小安装：

```powershell
python -m pip install fastapi uvicorn
```

如果要文件上传：

```powershell
python -m pip install python-multipart
```

---

## 六、最小 FastAPI 应用

创建 `main.py`：

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def index():
    return {"message": "hello FastAPI"}
```

启动：

```powershell
uvicorn main:app --reload
```

打开：

```text
http://127.0.0.1:8000/
```

返回：

```json
{"message":"hello FastAPI"}
```

---

## 七、uvicorn main:app 是什么意思

```powershell
uvicorn main:app --reload
```

拆开看：

```text
main    main.py 这个模块
app     main.py 里的 app 对象
--reload 代码变化后自动重启，开发时常用
```

Akane 里不是直接命令行启动，而是在 `launch_akane_memory_v01.py` 中：

```python
uvicorn.run("companion_v01.app:app", host=host, port=port, reload=False)
```

含义：

```text
模块路径：companion_v01.app
对象名：app
host：监听地址
port：端口
reload=False：不自动热重载
```

所以 Akane 的后端应用对象在：

```text
companion_v01/app.py 里的 app
```

---

## 八、FastAPI 应用对象

Akane 的 `app.py`：

```python
from fastapi import FastAPI

app = FastAPI(title="Aihong Companion V0.1")
```

`app` 是整个后端应用。

它负责：

```text
注册路由
注册中间件
挂载静态资源
注册生命周期事件
接收 HTTP 请求
返回 HTTP 响应
```

可以把 `app` 理解为：

```text
后端服务总入口
```

---

## 九、GET 路由

最小例子：

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}
```

访问：

```text
GET http://127.0.0.1:8000/health
```

返回：

```json
{"status":"ok"}
```

Akane 的 `routes/core.py`：

```python
@router.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "pid": os.getpid(),
        "python": sys.executable,
        "yt_dlp": yt_dlp_available,
    }
```

这就是健康检查接口。

---

## 十、POST 路由

`POST` 用于提交数据。

最小例子：

```python
from fastapi import FastAPI

app = FastAPI()


@app.post("/echo")
def echo(payload: dict):
    return {"received": payload}
```

请求：

```json
{
  "message": "hello"
}
```

返回：

```json
{
  "received": {
    "message": "hello"
  }
}
```

Akane 最重要的 POST：

```python
@router.post("/think")
async def think(request: Request):
    payload = await request.json()
    ...
```

---

## 十一、路径参数

路径参数写在 URL 中。

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/users/{user_id}")
def get_user(user_id: str):
    return {"user_id": user_id}
```

访问：

```text
GET /users/master
```

返回：

```json
{"user_id":"master"}
```

路径参数适合：

```text
/users/{user_id}
/sessions/{session_id}
/files/{file_id}
```

Akane 里有些资源接口会使用路径参数，比如桌宠工作区和生成文件内容相关接口。

---

## 十二、查询参数

查询参数在 `?` 后面：

```text
/resource-manifest?user_id=master&client=desktop_pet
```

FastAPI 自动解析：

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/search")
def search(q: str = "", limit: int = 10):
    return {"q": q, "limit": limit}
```

访问：

```text
GET /search?q=akane&limit=5
```

返回：

```json
{"q":"akane","limit":5}
```

Akane 里手动从 `Request` 读取：

```python
def _resolve_identity_from_query(request: Request) -> tuple[str, str]:
    session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "default_session")
    profile_user_id = str(request.query_params.get("real_user_id") or session_id)
    return session_id, profile_user_id
```

这段代码负责：

```text
从查询参数里解析 session_id 和 profile_user_id
```

---

## 十三、请求体 body

POST 请求常常带 JSON body。

例子：

```json
{
  "user_id": "default_session",
  "message": "你好 Akane"
}
```

FastAPI 有两种常见读法：

```text
1. 用 Pydantic 模型自动解析
2. 用 Request 手动读取 JSON
```

Akane 当前 `/think` 用的是第二种。

---

## 十四、Pydantic 请求模型

FastAPI 最经典写法是定义请求模型。

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class ThinkRequest(BaseModel):
    user_id: str = "default_session"
    message: str
    real_user_id: str | None = None


@app.post("/think-demo")
def think_demo(payload: ThinkRequest):
    return {
        "user_id": payload.user_id,
        "message": payload.message,
        "real_user_id": payload.real_user_id,
    }
```

好处：

```text
字段清楚
类型清楚
缺字段会自动报错
文档自动生成
IDE 更好补全
```

如果请求缺少 `message`，FastAPI 会自动返回 422。

---

## 十五、为什么 Akane 有时不用 Pydantic 模型

Akane 的 `/think` 请求体比较灵活。

里面可能有：

```text
message
user_id
real_user_id
current_visual
desktop_context
client_mode
screen frames
debug flags
```

而且前端、桌宠、QQ 等客户端可能传不同字段。

所以 Akane 选择：

```python
payload = await request.json()
if not isinstance(payload, dict):
    return JSONResponse(...)
```

也就是：

```text
先手动读成 dict
再在 engine 里逐步规范化字段
```

工程判断：

```text
稳定接口适合 Pydantic
变化很快的实验接口可以先用 dict[str, Any]
```

---

## 十六、Request 对象

`Request` 表示原始 HTTP 请求。

```python
from fastapi import Request


@app.post("/raw")
async def raw(request: Request):
    payload = await request.json()
    return {"payload": payload}
```

常用能力：

```python
request.query_params   # 查询参数
await request.json()   # JSON body
await request.body()   # 原始 bytes
await request.form()   # 表单 / 文件上传
request.headers        # 请求头
```

Akane 的 `routes/voice.py`：

```python
form = await request.form()
upload = form.get("file") or form.get("audio")
audio_bytes = await upload.read()
```

这就是从上传表单里读音频文件。

---

## 十七、async def 和 await

FastAPI 路由经常写：

```python
async def endpoint(request: Request):
    payload = await request.json()
```

`async def` 是异步函数。

`await` 表示等待一个异步操作。

常见要 await 的东西：

```text
await request.json()
await request.body()
await request.form()
await upload.read()
await asyncio.sleep(...)
```

最小例子：

```python
import asyncio
from fastapi import FastAPI

app = FastAPI()


@app.get("/wait")
async def wait():
    await asyncio.sleep(1)
    return {"done": True}
```

先记住：

```text
读请求体、处理上传、等待异步任务，经常需要 await。
```

---

## 十八、同步耗时任务放到线程里

如果某个函数是同步的、耗时的，就不要直接卡在 async 路由里太久。

FastAPI / asyncio 常见写法：

```python
import asyncio

result = await asyncio.to_thread(sync_function, arg1, arg2)
```

Akane 的 ASR：

```python
result = await asyncio.to_thread(
    run_asr_transcription,
    engine=engine,
    config_module=config_module,
    audio_bytes=audio_bytes,
    filename=filename,
    language=language,
    content_type=str(getattr(upload, "content_type", "") or ""),
)
```

意思：

```text
把同步的语音识别准备流程丢到线程里跑
避免阻塞异步路由
```

### 18.1 BackgroundTasks：请求后触发的轻量后台任务

FastAPI 内置了 `BackgroundTasks`，适合"请求处理完后再做一件小事"：

```python
from fastapi import BackgroundTasks


def send_notification(user_id: str, message: str) -> None:
    # 发通知、写日志、更新统计等
    print(f"通知 {user_id}: {message}")


@app.post("/register")
def register(user_id: str, bg: BackgroundTasks):
    bg.add_task(send_notification, user_id, "注册成功")
    # 请求立刻返回，send_notification 在后台执行
    return {"status": "ok"}
```

与 `asyncio.to_thread` 的区别：

| 方式 | 特点 |
|------|------|
| `await asyncio.to_thread(fn)` | 请求会等待 fn 执行完才返回 |
| `BackgroundTasks` | 请求立刻返回，fn 在后台慢慢跑 |

Akane 的选择：它没有用 `BackgroundTasks`，而是自己管理后台线程（见 Python 工程化笔记的线程章节）。因为 Akane 的后台任务更复杂——需要队列、worker 循环、状态追踪——已经超出了 `BackgroundTasks` 的能力范围。但对于简单的"发邮件、写日志、更新统计"，`BackgroundTasks` 是标准选择。

---

## 十九、返回普通 dict

FastAPI 可以直接返回字典。

```python
@app.get("/health")
def health():
    return {"status": "ok"}
```

FastAPI 会自动转成 JSON。

Akane 的健康检查就是：

```python
return {
    "status": "ok",
    "pid": os.getpid(),
    "python": sys.executable,
}
```

适合：

```text
简单 JSON 响应
不需要自定义状态码 / headers
```

---

## 二十、JSONResponse

如果要控制状态码、headers，就用 `JSONResponse`。

```python
from fastapi.responses import JSONResponse


@app.get("/error-demo")
def error_demo():
    return JSONResponse(
        {"ok": False, "error": "bad_request"},
        status_code=400,
        headers={"Cache-Control": "no-store"},
    )
```

Akane 的 `/think` JSON 解析失败：

```python
return JSONResponse(
    build_desktop_pet_error_payload(
        error="invalid_json",
        message=f"无法读取 /think 请求：{str(exc)[:160]}",
        retryable=False,
    ),
    status_code=400,
    headers={"Cache-Control": "no-store"},
)
```

---

## 二十一、Response：返回非 JSON 内容

如果返回音频、图片、二进制，就用 `Response`。

```python
from fastapi.responses import Response


@app.get("/plain")
def plain():
    return Response(content="hello", media_type="text/plain")
```

Akane 的 `/tts` 返回音频：

```python
return Response(
    content=audio,
    media_type="audio/mpeg",
    headers={
        "Cache-Control": "no-store",
        "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION,
    },
)
```

含义：

```text
响应体是 audio bytes
Content-Type 是 audio/mpeg
```

### 21.1 FileResponse：高效发送文件

如果要返回磁盘上的文件，`FileResponse` 比手动 `Response(content=file.read_bytes(), ...)` 更高效——它会利用操作系统级的文件发送机制，不会把整个文件加载到内存：

```python
from fastapi.responses import FileResponse


@app.get("/download/{filename}")
def download(filename: str):
    file_path = Path("exports") / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )
```

| 响应类型 | 适用场景 |
|---------|---------|
| `return dict` | 简单 JSON |
| `JSONResponse(...)` | JSON + 自定义状态码/headers |
| `Response(content=bytes, ...)` | 已经读进内存的二进制 |
| `FileResponse(path=...)` | 磁盘上的大文件，不占内存 |
| `StreamingResponse(gen, ...)` | 流式生成内容 |
| `HTMLResponse(...)` | 返回 HTML 页面 |

---

## 二十二、HTTPException

`HTTPException` 用于直接抛出 HTTP 错误。

```python
from fastapi import HTTPException


@app.get("/items/{item_id}")
def get_item(item_id: str):
    if item_id != "akane":
        raise HTTPException(status_code=404, detail="item not found")
    return {"item_id": item_id}
```

Akane 的 `/think` 被限流时：

```python
raise HTTPException(status_code=429, detail=guard_decision.message)
```

适合：

```text
权限不足
资源不存在
请求太频繁
直接中断当前请求
```

---

## 二十三、headers 响应头

响应头用于告诉客户端额外信息。

常见：

```text
Cache-Control       缓存策略
Content-Type        内容类型
X-Accel-Buffering   Nginx 是否缓冲
```

Akane 流式响应里：

```python
headers={
    "Cache-Control": "no-store",
    "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION,
    "X-Accel-Buffering": "no",
}
```

含义：

```text
no-store              不要缓存
X-Akane-Contract      告诉客户端当前协议版本
X-Accel-Buffering=no  告诉 nginx 不要缓冲流式响应
```

---

## 二十四、APIRouter：模块化路由

小项目可以直接在 `app.py` 写：

```python
@app.get("/health")
def health():
    return {"status": "ok"}
```

大项目不适合所有路由堆在一个文件。

FastAPI 提供 `APIRouter`：

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}
```

然后在主 app 里：

```python
app.include_router(router)
```

---

## 二十五、Akane 的路由工厂

Akane 不直接暴露一个全局 router，而是写成工厂函数：

```python
def build_think_router(
    *,
    engine: Any,
    public_guard: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
) -> APIRouter:
    router = APIRouter()

    @router.post("/think")
    async def think(request: Request):
        ...

    return router
```

主入口 `app.py`：

```python
app.include_router(
    build_think_router(
        engine=engine,
        public_guard=public_guard,
        runtime_metrics=runtime_metrics,
        log_event=_log_event,
    )
)
```

这种写法的好处：

```text
routes/think.py 只负责 think 相关接口
app.py 负责创建 engine、guard、metrics，再传进去
测试时可以传 fake engine
```

---

## 二十六、路由分层

Akane 的路由文件：

```text
routes/core.py           健康检查、资源清单
routes/think.py          对话接口
routes/voice.py          TTS / ASR
routes/desktop_pet.py    桌宠接口
routes/gifts.py          礼物接口
routes/sessions.py       会话接口
routes/reminders.py      提醒接口
routes/qq.py             QQ 网关
routes/system.py         系统指标
routes/control_center.py 控制中心
routes/web_static.py     静态页面
```

一句话：

```text
路由层负责“HTTP 怎么进来和出去”
业务逻辑尽量交给 engine / service
```

这就是后端项目的层次感。

---

## 二十七、CORS 是什么

CORS 是浏览器的跨域安全机制。

当前端页面和后端 API 不是同一个源时，浏览器会检查后端是否允许访问。

源包括：

```text
协议 + 域名 + 端口
```

例子：

```text
http://127.0.0.1:5173
http://127.0.0.1:9999
```

端口不同，也算不同源。

---

## 二十八、Akane 的 CORS 配置

Akane 的 `app.py`：

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
        "null",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Accel-Buffering"],
    max_age=600,
)
```

这段配置让这些客户端能访问 Akane 后端：

```text
本地浏览器
Tauri 桌宠
localhost / 127.0.0.1 不同端口
```

学习阶段先记住：

```text
前端请求后端被浏览器拦住，常常是 CORS 没配好
```

---

## 二十九、OPTIONS 预检请求

浏览器跨域发复杂请求前，可能先发：

```text
OPTIONS /some-path
```

询问后端：

```text
我能不能发真正的请求？
```

Akane 的 `routes/core.py` 有：

```python
@router.options("/{rest_of_path:path}", include_in_schema=False)
async def options_preflight(rest_of_path: str) -> Response:
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
```

意思：

```text
接住 OPTIONS 请求
返回 204
不放进文档 schema
```

---

## 三十、静态文件 StaticFiles

后端不只能返回 JSON，也可以托管静态资源。

比如：

```text
HTML
CSS
JS
图片
音频
```

FastAPI 示例：

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
```

访问：

```text
/static/logo.png
```

会读取：

```text
static/logo.png
```

---

## 三十一、Akane 的静态资源挂载

Akane 的 `app.py`：

```python
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

if USER_ASSETS_DIR.exists():
    app.mount("/user-assets", StaticFiles(directory=str(USER_ASSETS_DIR)), name="user-assets")
```

含义：

```text
/assets/...      对应 web/assets/...
/user-assets/... 对应用户上传或内部化资源
```

这让前端可以访问图片、角色资源、礼物资源。

---

## 三十二、文件上传：UploadFile 经典写法

FastAPI 经典文件上传：

```python
from fastapi import FastAPI, File, UploadFile

app = FastAPI()


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(content),
    }
```

启动前要安装：

```powershell
python -m pip install python-multipart
```

因为上传通常是：

```text
multipart/form-data
```

---

## 三十三、Akane 的文件上传读法

Akane 的 `/asr` 更手动：

```python
form = await request.form()
upload = form.get("file") or form.get("audio")

if upload is None or not hasattr(upload, "read"):
    return JSONResponse(
        {"ok": False, "error": "missing_file", "message": "没有收到录音文件。"},
        status_code=400,
    )

audio_bytes = await upload.read()
```

它这样写的原因：

```text
兼容字段名 file 或 audio
可以手动做错误响应
可以细致控制上传大小、错误消息、日志
```

然后检查大小：

```python
if len(audio_bytes) > max_bytes:
    return JSONResponse(
        {"ok": False, "error": "audio_too_large", "message": "录音太长啦，先说短一点试试。"},
        status_code=413,
    )
```

---

## 三十四、StreamingResponse：流式响应

普通响应是：

```text
后端算完全部结果
一次性返回
```

流式响应是：

```text
后端生成一点
返回一点
前端收到一点
显示一点
```

FastAPI 示例：

```python
import time
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


def generate_text():
    for word in ["hello", " ", "FastAPI"]:
        time.sleep(0.3)
        yield word


@app.get("/stream")
def stream():
    return StreamingResponse(generate_text(), media_type="text/plain")
```

访问 `/stream` 时，客户端可以逐块接收。

---

## 三十五、NDJSON 流式响应

Akane 用的是 NDJSON：

```text
一行一个 JSON
```

例子：

```text
{"type":"stream_start"}
{"type":"speech_chunk","text":"你"}
{"type":"speech_chunk","text":"好"}
{"type":"final","payload":{"speech":"你好"}}
{"type":"stream_end"}
```

后端生成：

```python
import json


def event_stream():
    yield json.dumps({"type": "stream_start"}, ensure_ascii=False) + "\n"
    yield json.dumps({"type": "speech_chunk", "text": "你好"}, ensure_ascii=False) + "\n"
    yield json.dumps({"type": "final", "payload": {"speech": "你好"}}, ensure_ascii=False) + "\n"
```

返回：

```python
return StreamingResponse(
    event_stream(),
    media_type="application/x-ndjson",
)
```

---

## 三十六、Akane 的 /think 流式响应

Akane 的核心：

```python
def _stream():
    yield json.dumps(
        {
            "type": "stream_start",
            "contract_version": DESKTOP_PET_CONTRACT_VERSION,
        },
        ensure_ascii=False,
    ) + "\n"

    try:
        for event in engine.process_turn_stream(payload):
            yield json.dumps(event, ensure_ascii=False) + "\n"
    finally:
        yield json.dumps(
            {
                "type": "stream_end",
                "contract_version": DESKTOP_PET_CONTRACT_VERSION,
            },
            ensure_ascii=False,
        ) + "\n"
```

然后：

```python
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

一句话理解：

```text
/think 不是一次性返回完整 JSON
而是一行一行返回事件
```

---

## 三十七、为什么 LLM 应用需要流式响应

如果不流式：

```text
用户发消息
等模型生成完整回复
等 TTS / 工具 / 检索处理
前端最后才显示
```

体验会很慢。

流式之后：

```text
先发 stream_start
模型出一点 speech_chunk
前端立刻显示
中间可能发 ui / tool_event
最后发 final 和 stream_end
```

对陪伴型应用尤其重要。

Akane 的桌宠气泡、情绪、语音分段，都依赖事件流。

---

## 三十八、media_type

`media_type` 告诉客户端响应是什么类型。

常见：

| media_type | 含义 |
|---|---|
| `application/json` | JSON |
| `text/plain` | 普通文本 |
| `text/html` | HTML |
| `application/x-ndjson` | 一行一个 JSON |
| `audio/mpeg` | mp3 音频 |
| `image/png` | PNG 图片 |

Akane：

```python
StreamingResponse(..., media_type="application/x-ndjson")
Response(content=audio, media_type="audio/mpeg")
```

---

## 三十九、生命周期事件

后端启动和关闭时，可以执行一些逻辑。

Akane 里有 shutdown：

```python
@app.on_event("shutdown")
async def shutdown_event() -> None:
    engine.close()
```

意思：

```text
后端关闭时，调用 engine.close()
```

适合清理：

```text
后台线程
数据库连接池
临时资源
外部客户端
```

注意：新版本 FastAPI 更推荐 lifespan 写法，但 `on_event` 在很多项目里仍能看到。读 Akane 时知道它是生命周期钩子即可。

---

## 四十、可运行 Demo：最小 Akane 风格后端

创建 `mini_akane_api.py`：

```python
from __future__ import annotations

import json
import time
from typing import Any, Generator

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse


class MiniEngine:
    def process_turn_stream(self, payload: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
        message = str(payload.get("message") or "").strip()
        reply = f"我收到啦：{message}"

        yield {"type": "ui", "emotion": "normal"}
        for char in reply:
            time.sleep(0.03)
            yield {"type": "speech_chunk", "text": char}
        yield {"type": "final", "payload": {"speech": reply}}


def build_think_router(*, engine: MiniEngine) -> APIRouter:
    router = APIRouter()

    @router.post("/think")
    async def think(request: Request):
        try:
            payload = await request.json()
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": "invalid_json", "message": str(exc)},
                status_code=400,
            )

        if not isinstance(payload, dict):
            return JSONResponse(
                {"ok": False, "error": "invalid_payload"},
                status_code=400,
            )

        def stream():
            yield json.dumps({"type": "stream_start"}, ensure_ascii=False) + "\n"
            try:
                for event in engine.process_turn_stream(payload):
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            finally:
                yield json.dumps({"type": "stream_end"}, ensure_ascii=False) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return router


app = FastAPI(title="Mini Akane API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = MiniEngine()


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(build_think_router(engine=engine))
```

启动：

```powershell
uvicorn mini_akane_api:app --reload
```

测试健康检查：

```text
http://127.0.0.1:8000/health
```

用 Python 请求 `/think`：

```python
import requests

response = requests.post(
    "http://127.0.0.1:8000/think",
    json={"message": "今天学 FastAPI"},
    stream=True,
)

for line in response.iter_lines(decode_unicode=True):
    if line:
        print(line)
```

这个 Demo 对应 Akane：

| Demo | Akane |
|---|---|
| `FastAPI(title=...)` | `app = FastAPI(title="Aihong Companion V0.1")` |
| `MiniEngine` | `AkaneMemoryEngine` |
| `build_think_router` | `routes/think.py` |
| `/health` | `routes/core.py` |
| `/think` | Akane 对话接口 |
| `StreamingResponse` | Akane 流式回复 |
| `application/x-ndjson` | Akane 事件流格式 |

---

## 四十一、自动文档 Swagger UI

FastAPI 会自动生成 API 文档。

启动后访问：

```text
http://127.0.0.1:8000/docs
```

这是 Swagger UI。

也可以访问：

```text
http://127.0.0.1:8000/redoc
```

如果使用 Pydantic 模型，文档会非常清楚。

如果像 Akane `/think` 一样手动读 `Request`，自动文档里的请求体信息会少一些。

工程取舍：

```text
稳定公开 API       更适合 Pydantic 模型
快速变化内部 API   可以先手动 Request
```

---

## 四十二、依赖注入 Depends

FastAPI 有自己的依赖注入系统。

最小例子：

```python
from fastapi import Depends, FastAPI

app = FastAPI()


def get_current_user():
    return "master"


@app.get("/me")
def me(user_id: str = Depends(get_current_user)):
    return {"user_id": user_id}
```

访问 `/me` 时，FastAPI 会先调用 `get_current_user()`。

Akane 当前更常用“路由工厂函数”的方式注入依赖：

```python
build_think_router(engine=engine, public_guard=public_guard, ...)
```

两种方式都常见。

先记住：

```text
Depends 是 FastAPI 内置依赖注入
build_xxx_router 是 Akane 当前采用的显式依赖注入
```

---

## 四十三、请求校验和手动校验

Pydantic 自动校验：

```python
class CreateTaskRequest(BaseModel):
    title: str
    priority: int = 0
```

手动校验：

```python
payload = await request.json()
title = str(payload.get("title") or "").strip()
if not title:
    return JSONResponse({"ok": False, "error": "missing_title"}, status_code=400)
```

Akane 大量使用手动校验，因为：

```text
LLM 应用 payload 灵活
客户端模式多
错误消息需要更贴近用户
有些字段需要兼容旧名字
```

比如：

```python
upload = form.get("file") or form.get("audio")
```

这就是兼容多个字段名。

---

## 四十四、后端边界层要做什么

路由层通常负责：

```text
读取请求
校验基本格式
解析身份
调用 engine / service
转换响应
处理 HTTP 状态码
记录指标和日志
```

路由层不适合塞太多业务逻辑。

比如 `/think` 路由不应该自己做完整记忆检索。

它应该：

```text
读 payload
做限流
调用 engine.process_turn_stream(payload)
把事件转成 NDJSON
```

这就是 Akane 当前的结构。

---

## 四十五、Akane 的 FastAPI 总装配

Akane 的 `app.py` 做了这些事：

```text
1. 创建 FastAPI app
2. 添加 CORS 中间件
3. 计算项目路径
4. 创建 ResourceManifest
5. 创建 AkaneMemoryEngine
6. 创建 TTS client
7. 创建 runtime_metrics / public_guard / qq_gateway
8. 挂载静态资源
9. 注册 shutdown 事件
10. include_router 注册所有路由
```

关键代码：

```python
app = FastAPI(title="Aihong Companion V0.1")

engine = AkaneMemoryEngine(
    Path(config.DATA_DIR) / "akane_memory_v01",
    resource_manifest=resources,
    desktop_pet_character_resources=desktop_pet_character_resources,
)

app.include_router(build_core_router(...))
app.include_router(build_think_router(...))
app.include_router(build_voice_router(...))
```

一句话理解：

```text
app.py 是 Akane 后端的装配车间。
```

---

## 四十六、Akane 的一次 /think 请求链路

```text
前端 fetch("/think")
→ FastAPI 匹配 routes/think.py 的 @router.post("/think")
→ await request.json() 读取 payload
→ public_guard.try_acquire() 做限流
→ StreamingResponse(_stream())
→ _stream 里调用 engine.process_turn_stream(payload)
→ engine yield ui / speech_chunk / final 等事件
→ routes/think.py 转成 NDJSON 行
→ 前端逐行读取并显示
```

对应文件：

```text
web/app.js
companion_v01/routes/think.py
companion_v01/engine.py
companion_v01/store.py
companion_v01/llm_runtime.py
```

读项目时，先把这条链拿住。

---

## 四十七、测试 FastAPI 接口：TestClient

FastAPI 可以用 `TestClient` 测接口。

最小例子：

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

### 47.1 测试流式接口

流式接口的测试比普通 JSON 稍微复杂一点——需要逐块读取：

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

app = FastAPI()


@app.get("/stream")
def stream():
    def gen():
        for word in ["hello", " ", "world"]:
            yield word
    return StreamingResponse(gen(), media_type="text/plain")


client = TestClient(app)


def test_stream():
    response = client.get("/stream")
    assert response.status_code == 200

    # 流式响应：逐块读取
    chunks = []
    for chunk in response.iter_content(chunk_size=1):
        chunks.append(chunk.decode("utf-8"))

    assert "".join(chunks) == "hello world"
```

如果是 NDJSON（Akane 格式）：

```python
def test_think_stream():
    response = client.post(
        "/think",
        json={"message": "测试"},
        stream=True,
    )
    assert response.status_code == 200

    events = []
    for line in response.iter_lines(decode_unicode=True):
        if line:
            events.append(json.loads(line))

    # 应该有 stream_start ... stream_end
    assert events[0]["type"] == "stream_start"
    assert events[-1]["type"] == "stream_end"
    # 中间至少有一个 speech_chunk 或 final
    event_types = [e["type"] for e in events]
    assert any(t in event_types for t in ("speech_chunk", "final"))
```

后面写测试体系笔记时会详细整理。

这里先知道：

```text
FastAPI 接口可以不用真的启动服务器也能测试
流式接口用 response.iter_lines() 逐行读取 NDJSON
```

---

## 四十八、常见坑

### 48.1 忘记启动 uvicorn

写了 `app = FastAPI()` 不等于服务已经运行。

需要：

```powershell
uvicorn main:app --reload
```

Akane 用：

```powershell
python launch_akane_memory_v01.py
```

### 48.2 main:app 路径写错

如果文件叫 `mini_akane_api.py`，启动应该是：

```powershell
uvicorn mini_akane_api:app --reload
```

不是：

```powershell
uvicorn main:app --reload
```

### 48.3 async 函数里忘记 await

错误：

```python
payload = request.json()
```

正确：

```python
payload = await request.json()
```

### 48.4 返回音频却用了 JSONResponse

音频、图片、二进制要用：

```python
Response(content=audio_bytes, media_type="audio/mpeg")
```

### 48.5 流式响应被 nginx 缓冲

如果中间有 nginx，流式内容可能被攒到最后一次性发。

Akane 加了：

```python
"X-Accel-Buffering": "no"
```

### 48.6 文件上传缺 python-multipart

如果使用 `request.form()` 或 `UploadFile`，需要：

```powershell
python -m pip install python-multipart
```

---

## 四十九、知道即可：ASGI / WSGI

FastAPI 是 ASGI 框架。

```text
ASGI 支持异步、WebSocket、流式响应
WSGI 是更早的同步 Python Web 标准
```

Uvicorn 是 ASGI server。

学习阶段先记住：

```text
FastAPI app 需要 uvicorn 这样的 ASGI server 来运行
```

不需要现在深挖 ASGI 底层协议。

---

## 五十、这一章先记住

FastAPI 最核心模板：

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/echo")
async def echo(request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid_payload"}, status_code=400)
    return {"received": payload}
```

Akane 后端的核心理解：

```text
app.py              创建并装配 FastAPI app
routes/*.py         定义不同领域的 HTTP 路由
engine.py           执行业务主流程
store.py            数据持久化
StreamingResponse   支撑 /think 的流式回复
```

---

## 五十一、回到 Akane：读代码顺序

建议这样读：

```text
1. launch_akane_memory_v01.py
   看 uvicorn 怎么启动 companion_v01.app:app

2. companion_v01/app.py
   看 FastAPI app 怎么创建、CORS 怎么配、router 怎么注册

3. companion_v01/routes/core.py
   看简单 GET 接口，如 /health

4. companion_v01/routes/think.py
   看 POST /think、Request、JSONResponse、StreamingResponse

5. companion_v01/routes/voice.py
   看文件上传、Response 返回音频、asyncio.to_thread

6. companion_v01/engine.py
   看路由调用的业务主流程
```

先看简单 GET，再看复杂 POST，再看流式响应。

---

## 五十二、我自己的复述

FastAPI 是 Akane 后端的门口。

前端、桌宠、QQ 网关都不是直接调用 Python 函数，而是通过 HTTP 访问 FastAPI 暴露的接口。

Akane 的 FastAPI 结构可以压缩成一句话：

```text
app.py 负责装配，routes/*.py 负责接请求，engine.py 负责干活，store.py 负责保存。
```

`/think` 是最重要的接口。

它不是普通 JSON 返回，而是：

```text
读取请求 JSON
调用 engine.process_turn_stream
把 engine yield 出来的事件转成 NDJSON
用 StreamingResponse 一行一行发给前端
```

学会 FastAPI 后，再看 Akane 的后端，就不是“很多文件乱跳”，而是清楚的分层：

```text
HTTP 边界层
业务编排层
数据存储层
模型与工具层
```

