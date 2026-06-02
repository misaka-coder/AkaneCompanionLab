---
tags:
  - akane/testing
  - python/unittest
  - backend/quality
  - regression
created: 2026-05-23
---

# unittest 测试体系

> 写代码解决“功能能不能跑”。  
> 写测试解决“以后改代码时，功能会不会悄悄坏掉”。

Akane 已经不是单文件脚本，而是一个真实工程：

```text
后端接口
数据库存储
LLM 流式解析
RAG 检索
工具调用
桌宠客户端契约
QQ 网关
文件 / 附件 / 礼物 / 任务工作区
```

这种项目不能只靠手动点点看。

因为：

```text
你改了一个函数
可能影响接口返回格式
可能影响数据库字段
可能影响前端渲染
可能影响 QQ 和桌宠两种客户端
可能影响 LLM 流式事件
```

测试的作用就是：

```text
把“我担心会坏的行为”写成可重复执行的检查。
```

学习路线：

```text
为什么要测试
→ unittest 最小结构
→ TestCase
→ assert 断言
→ Arrange / Act / Assert
→ 临时目录 tempfile
→ Fake 对象
→ mock / patch
→ FastAPI TestClient
→ 流式响应测试
→ 数据库测试
→ 契约测试
→ 回归测试套件
→ Akane 测试阅读路线
```

---

## 一、测试到底是什么

测试不是“为了证明我很专业”。

测试是把一次人工检查变成代码。

比如你手动检查：

```text
创建一条会话
再列出会话
看标题是不是“新的对话”
```

测试会写成：

```python
def test_session_titles_can_be_listed_and_renamed(self):
    ...
    self.assertEqual(first["display_title"], "新的对话")
```

以后每次改 `MemoryStore`，都可以自动跑一遍。

如果行为坏了，测试会立刻红。

所以测试本质上是：

```text
用代码记录你对系统行为的期待。
```

---

## 二、Akane 为什么需要测试

Akane 这种项目有几个特点：

```text
文件多
模块多
状态多
接口多
前后端有契约
LLM 输出有不确定性
外部服务可能失败
```

如果没有测试，改代码会很虚。

比如你改 `/think`：

```text
它还会不会返回 stream_start？
最后还会不会返回 stream_end？
headers 里还有没有 X-Akane-Contract？
stream_end.partial.speech 还能不能兜底显示？
```

Akane 里已经有对应测试：

```text
tests/test_backend_route_modules.py
test_think_router_handles_once_and_stream_contract_with_fake_engine
```

测试不是只看“有没有报错”。

更重要的是：

```text
确认系统对外承诺的行为没有变。
```

---

## 三、unittest 是什么

`unittest` 是 Python 标准库自带的测试框架。

不用额外安装。

最小例子：

```python
import unittest


def add(a, b):
    return a + b


class AddTests(unittest.TestCase):
    def test_add_two_numbers(self):
        result = add(1, 2)
        self.assertEqual(result, 3)


if __name__ == "__main__":
    unittest.main()
```

保存为 `test_demo.py`，运行：

```powershell
python test_demo.py
```

或者：

```powershell
python -m unittest test_demo
```

> **附：unittest vs pytest** — Akane 用的是 Python 标准库 `unittest`（不用额外安装）。社区里更流行的是 `pytest`（语法更简洁，但需要 `pip install pytest`）。两者的核心思想完全一样：TestCase、断言、Fake、patch。学懂 `unittest` 后转 `pytest` 只需要 10 分钟。

输出大概：

```text
.
----------------------------------------------------------------------
Ran 1 test in 0.000s

OK
```

`.` 表示一个测试通过。

---

## 四、TestCase 是什么

`unittest.TestCase` 是测试类的基类。

```python
class AddTests(unittest.TestCase):
    ...
```

一个测试类里可以写很多测试方法。

测试方法必须以 `test_` 开头：

```python
def test_add_positive_numbers(self):
    ...

def test_add_negative_numbers(self):
    ...
```

如果不以 `test_` 开头，`unittest` 默认不会执行它。

错误示例：

```python
def check_add(self):
    ...
```

这不是测试方法。

正确示例：

```python
def test_add(self):
    ...
```

---

## 五、断言 assert

断言就是：

```text
我认为结果应该是这样，如果不是，就让测试失败。
```

常用断言：

| 断言 | 含义 |
|---|---|
| `assertEqual(a, b)` | 判断 a == b |
| `assertNotEqual(a, b)` | 判断 a != b |
| `assertTrue(x)` | 判断 x 为真 |
| `assertFalse(x)` | 判断 x 为假 |
| `assertIsNone(x)` | 判断 x is None |
| `assertIsNotNone(x)` | 判断 x is not None |
| `assertIn(a, b)` | 判断 a in b |
| `assertNotIn(a, b)` | 判断 a not in b |
| `assertGreater(a, b)` | 判断 a > b |
| `assertRaises(Error)` | 判断会抛出异常 |

例子：

```python
import unittest


class AssertTests(unittest.TestCase):
    def test_assert_examples(self):
        user = {
            "name": "Akane",
            "active": True,
            "tags": ["chat", "memory"],
        }

        self.assertEqual(user["name"], "Akane")
        self.assertTrue(user["active"])
        self.assertIn("memory", user["tags"])
        self.assertIsNotNone(user)
```

测试失败时，断言会告诉你：

```text
预期是什么
实际是什么
哪一行失败
```

---

## 六、Arrange / Act / Assert

写测试时常用三段式：

```text
Arrange：准备环境
Act：执行动作
Assert：检查结果
```

例子：

```python
import unittest


def normalize_message(text: str) -> str:
    return text.strip()


class MessageTests(unittest.TestCase):
    def test_normalize_message_strips_spaces(self):
        # Arrange
        raw = "  hello  "

        # Act
        result = normalize_message(raw)

        # Assert
        self.assertEqual(result, "hello")
```

中文理解：

```text
先准备一段带空格的文本
再调用函数
最后断言结果应该去掉空格
```

真实项目里不一定每段都写注释，但脑子里要有这个结构。

---

## 七、Akane 测试目录画像

Akane 的测试都在：

```text
tests/
```

代表性文件：

| 文件 | 测什么 |
|---|---|
| `test_store.py` | SQLite 存储、会话、消息、提醒、eval turn |
| `test_backend_route_modules.py` | FastAPI 路由、接口返回、流式契约 |
| `test_llm_runtime_stream.py` | LLM 流式 JSON 解析、部分 JSON 恢复 |
| `test_public_guard.py` | 并发限制、每日限制 |
| `test_vector_store.py` | 向量检索、RRF 融合、embedding provider |
| `test_attachment_ingest.py` | 附件下载、QQ 缓存、失败重试 |
| `test_desktop_pet_frontend_contract.py` | 桌宠前端契约 |
| `test_resource_visibility_contract.py` | 资源可见性契约 |
| `quick_regression_suite.py` | 快速回归测试集合 |

可以把 Akane 测试分成几类：

```text
纯函数测试
数据库 / 存储测试
HTTP 接口测试
流式协议测试
外部依赖隔离测试
前后端契约测试
回归测试套件
```

---

## 八、怎么运行测试

运行单个测试文件：

```powershell
python -m unittest tests.test_store
```

运行单个测试类：

```powershell
python -m unittest tests.test_public_guard.PublicThinkGuardTests
```

运行单个测试方法：

```powershell
python -m unittest tests.test_public_guard.PublicThinkGuardTests.test_concurrent_limit_blocks_second_request
```

运行快速回归套件：

```powershell
python -m tests.quick_regression_suite
```

自动发现所有测试：

```powershell
python -m unittest discover -s tests
```

实际开发建议：

```text
刚改某个小模块：先跑对应测试文件
改了接口契约：跑 route / frontend contract 相关测试
改了核心流程：跑 quick_regression_suite
准备提交：再考虑跑全部 discover
```

---

## 九、临时目录 tempfile

测试数据库或文件系统时，不要直接写真实项目目录。

应该用临时目录：

```python
import tempfile
import unittest
from pathlib import Path


class TempDirTests(unittest.TestCase):
    def test_write_file_in_temp_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "messages.jsonl"

            path.write_text("hello", encoding="utf-8")

            self.assertEqual(path.read_text(encoding="utf-8"), "hello")
```

好处：

```text
测试之间互不污染
测试结束自动清理
不会写坏真实数据
```

Akane `test_store.py` 大量使用：

```python
with tempfile.TemporaryDirectory() as temp_dir:
    store = MemoryStore(Path(temp_dir))
    ...
```

意思：

```text
每个测试都拿一个全新的临时数据库目录。
测试结束后目录自动删除。
```

---

## 十、测试 MemoryStore

Akane 的 `MemoryStore` 是持久化核心。

测试它时重点不是“SQLite 语法对不对”，而是：

```text
业务行为对不对
数据能不能写入
数据能不能查回
排序是否正确
状态是否正确变化
profile/session 是否隔离
```

真实测试例子：

```python
def test_session_titles_can_be_listed_and_renamed(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = MemoryStore(Path(temp_dir))
        first = store.ensure_session(profile_user_id="user_a", session_id="session_a", timestamp=100)
        second = store.ensure_session(profile_user_id="user_a", session_id="session_b", timestamp=200)

        sessions = store.list_sessions("user_a")

        self.assertEqual(first["display_title"], "新的对话")
        self.assertEqual(second["display_title"], "新的对话 2")
        self.assertEqual([item["session_id"] for item in sessions], ["session_b", "session_a"])
```

这段测了什么？

```text
创建第一个会话，标题是“新的对话”
创建第二个会话，标题自动变成“新的对话 2”
列出会话时，新会话排在前面
```

这就是好测试：

```text
不是测内部 SQL 怎么写
而是测外部行为是否符合预期
```

---

## 十一、测试状态变化

很多业务不是简单返回值，而是状态会变化。

比如提醒：

```text
pending
→ fired
→ 再 claim 时不应该重复返回
```

Akane 测试：

```python
def test_claim_due_reminders_marks_rows_as_fired(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = MemoryStore(Path(temp_dir))
        reminder = store.add_reminder(
            profile_user_id="user_a",
            session_id="session_a",
            content="复习微积分",
            due_ts=100,
            raw_time_text="明天晚上八点",
        )

        claimed = store.claim_due_reminders(
            profile_user_id="user_a",
            session_id="session_a",
            now_ts=120,
        )

        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["reminder_id"], reminder["reminder_id"])
        self.assertEqual(claimed[0]["status"], "fired")

        claimed_again = store.claim_due_reminders(
            profile_user_id="user_a",
            session_id="session_a",
            now_ts=130,
        )
        self.assertEqual(claimed_again, [])
```

它保护的是：

```text
到期提醒只触发一次。
```

这种测试非常重要。

因为状态机类 bug 往往不是语法错，而是：

```text
重复触发
漏触发
状态没更新
跨用户串数据
```

---

## 十二、测试隔离 profile / session

Akane 是多用户、多会话系统。

所以测试经常要检查：

```text
同一个 session_id 在不同 profile 下不能串数据
同一个用户不同会话不能串数据
```

真实例子：

```python
def test_get_latest_eval_turn_for_session_is_profile_scoped(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = MemoryStore(Path(temp_dir))
        store.append_eval_turn(
            trace_id="trace_user_a",
            session_id="session_shared",
            profile_user_id="user_a",
            user_message="hello",
            router_json={"route": "direct_answer"},
            verifier_json={"match_result": "skip"},
            final_json={"speech": "给 user_a 的回复"},
        )
        store.append_eval_turn(
            trace_id="trace_user_b",
            session_id="session_shared",
            profile_user_id="user_b",
            user_message="hello",
            router_json={"route": "direct_answer"},
            verifier_json={"match_result": "skip"},
            final_json={"speech": "给 user_b 的回复"},
        )

        latest = store.get_latest_eval_turn_for_session(
            profile_user_id="user_a",
            session_id="session_shared",
        )

        self.assertEqual(latest["trace_id"], "trace_user_a")
```

这保护的是：

```text
profile_user_id 是数据边界。
```

这类测试很有工程价值，因为真实项目里最怕：

```text
用户 A 看到用户 B 的记忆
会话 A 混进会话 B 的回复
```

---

## 十三、Fake 对象

真实系统有很多依赖：

```text
LLM
数据库
向量库
网络请求
文件系统
外部 QQ 网关
```

测试时不一定要真的调用它们。

可以用 Fake 对象替代。

Fake 对象就是：

```text
行为可控的假实现。
```

Akane 接口测试里有：

```python
class FakeRuntimeMetrics:
    def __init__(self) -> None:
        self.observed = []
        self.counters = {}

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        self.observed.append((name, ok))

    def incr(self, key: str, amount: float = 1.0) -> None:
        self.counters[key] = self.counters.get(key, 0.0) + amount
```

它不是真的监控系统。

它只是记录：

```text
代码有没有调用 observe_request
有没有 incr 某个计数器
```

Fake 的好处：

```text
测试更快
结果稳定
不用真实外部服务
可以精确制造场景
```

---

## 十四、Fake Engine 测接口契约

测试 `/think` 时，如果真的调用完整 `AkaneMemoryEngine`，会很慢。

而且可能牵涉：

```text
LLM
数据库
RAG
工具
配置
文件
```

所以测试可以只造一个 FakeEngine：

```python
class FakeEngine:
    def process_turn(self, payload: dict) -> dict:
        return {
            "status": "ok",
            "emotion": "normal",
            "speech": f"echo: {payload.get('message')}",
            "_debug": {},
        }

    def process_turn_stream(self, payload: dict):
        yield {"type": "ui", "emotion": "normal"}
        yield {"type": "speech_chunk", "text": "hello"}
        yield {"type": "final", "payload": self.process_turn(payload)}
```

这段不是在测 LLM 能不能回复。

它在测：

```text
路由层是否正确消费 engine 的流式事件。
```

也就是：

```text
测试目标越清楚，依赖就越少。
```

---

## 十五、FastAPI TestClient

FastAPI 提供 `TestClient`，可以在测试里直接请求接口。

不需要真的启动 uvicorn。

最小例子：

```python
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient


app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


class HealthTests(unittest.TestCase):
    def test_health(self):
        response = TestClient(app).get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
```

重点：

```text
TestClient(app)
client.get(...)
client.post(..., json={...})
response.status_code
response.json()
response.headers
```

Akane 的路由测试都是这个思路。

---

## 十六、测试 /think_once 和 /think

Akane 真实测试：

```python
def test_think_router_handles_once_and_stream_contract_with_fake_engine(self) -> None:
    runtime = FakeRuntimeMetrics()
    guard = FakeGuard()

    app = FastAPI()
    app.include_router(
        build_think_router(
            engine=FakeEngine(),
            public_guard=guard,
            runtime_metrics=runtime,
            log_event=lambda *_args, **_kwargs: None,
        )
    )
    client = TestClient(app)

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

它保护了两个接口：

```text
/think_once：普通 JSON 响应
/think：NDJSON 流式响应
```

它保护的契约：

```text
状态码是 200
/think_once 能返回 speech
/think 响应头有 x-akane-contract
流式响应第一行是 stream_start
最后一行是 stream_end
stream_end.partial.speech 能兜底显示最终文本
```

这就是接口测试的重点：

```text
不是测内部怎么实现
而是测外部能不能按约定使用。
```

---

## 十七、测试非法 payload

接口不能只测正常情况，也要测错误输入。

真实测试：

```python
response = TestClient(app).post("/think_once", json=["not", "object"])

self.assertEqual(response.status_code, 400)
self.assertEqual(response.json()["error"], "invalid_payload")
```

这测的是：

```text
如果前端传的不是 JSON object，后端应该返回 400。
```

为什么重要？

因为真实世界里请求可能是：

```text
空 body
数组
字符串
字段缺失
字段类型错
旧版本客户端
```

如果不测错误输入，接口可能一遇到异常请求就 500。

好的接口应该：

```text
能明确告诉客户端哪里错了。
```

---

## 十八、mock 和 patch

`mock` 用来替换外部依赖。

`patch` 是最常见用法。

例子：

```python
import unittest
from unittest.mock import patch


def get_remote_text():
    import requests
    response = requests.get("https://example.com")
    return response.text


class RemoteTests(unittest.TestCase):
    def test_get_remote_text(self):
        class FakeResponse:
            text = "hello"

        with patch("requests.get", return_value=FakeResponse()) as get_mock:
            result = get_remote_text()

        self.assertEqual(result, "hello")
        get_mock.assert_called()
```

作用：

```text
测试不会真的访问 example.com。
requests.get 被替换成我们指定的假函数。
```

---

## 十九、Akane 里 patch 外部请求

Akane 附件下载会调用外部 HTTP。

测试不能真的依赖 QQ / NapCat / 网络。

真实例子简化：

```python
class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "status": "ok",
            "retcode": 0,
            "data": {
                "file": str(cached),
                "url": "https://gchat.qpic.cn/download?bad=true",
            },
        }


with patch("companion_v01.attachment_ingest.requests.post", return_value=FakeResponse()) as post_mock:
    with patch("companion_v01.attachment_ingest.requests.get") as get_mock:
        created = service.ingest_qq_attachments(...)

post_mock.assert_called()
get_mock.assert_not_called()
```

这测的不是网络。

它测的是：

```text
如果 NapCat 已经提供本地缓存文件
服务应该优先用本地缓存
不应该再 requests.get 下载远程 URL
```

也就是说：

```text
patch 不是为了偷懒。
patch 是为了把测试目标从“外部世界”收回来。
```

---

## 二十、测试流式 JSON 解析

LLM 输出经常不是一次性完整 JSON。

它可能分块到达：

```text
第 1 块：{"emotion":"happy","speech":"喵呜，
第 2 块：主人欢迎回来……
第 3 块：课上辛苦啦！","status":"final"}
```

Akane 测试：

```python
def test_emits_ui_event_and_speech_chunks_from_split_json(self) -> None:
    tap = _TopLevelJSONStreamTap()
    events = []
    chunks = [
        '{"emotion":"happy","speech":"喵呜，',
        '主人欢迎回来……',
        '课上辛苦啦！","status":"final"}',
    ]

    for chunk in chunks:
        events.extend(tap.feed(chunk))

    self.assertEqual(events[0], {"type": "ui", "emotion": "happy"})
    speech_events = [event for event in events if event.get("type") == "speech_chunk"]
    self.assertGreaterEqual(len(speech_events), 1)
    self.assertEqual(tap.latest_emotion, "happy")
    self.assertEqual(tap.latest_speech, "喵呜，主人欢迎回来……课上辛苦啦！")
```

它保护的是：

```text
LLM JSON 被拆成多个 chunk 时，系统仍能逐步提取 emotion 和 speech。
```

这类测试特别适合 LLM 应用。

因为 LLM 相关 bug 常常不是：

```text
完全没有输出
```

而是：

```text
输出了一半
JSON 不完整
工具调用在最前面
speech_segments 需要恢复
模型多说了 markdown 代码块
```

---

## 二十一、测试异常恢复

LLM 输出可能不完整。

Akane 有部分 JSON 恢复测试：

```python
def test_partial_chat_json_recovery_keeps_generated_speech(self) -> None:
    runtime = object.__new__(LLMRuntime)

    recovered = runtime._recover_partial_chat_json(
        '{"emotion":"happy","speech":"我听到啦，主人。","speech_segments":[]',
        fallback={"emotion": "normal", "speech": "fallback", "speech_segments": []},
    )

    self.assertEqual(recovered["emotion"], "happy")
    self.assertEqual(recovered["speech"], "我听到啦，主人。")
    self.assertEqual(recovered["speech_segments"], [])
```

这里有一个技巧：

```python
runtime = object.__new__(LLMRuntime)
```

它绕过了 `LLMRuntime.__init__`。

为什么？

```text
这个测试只想测一个内部解析函数。
不想初始化真实 LLM client、配置或外部依赖。
```

这在测试复杂类时很常见。

---

## 二十二、测试并发保护

Akane 有 `PublicThinkGuard`，用于限制公共入口的并发和每日次数。

测试正常关闭：

```python
def test_disabled_guard_allows_without_acquire(self):
    guard = PublicThinkGuard(
        enabled=False,
        max_concurrent_thinks=2,
        daily_think_limit=10,
        busy_message="busy",
        daily_limit_message="limit",
    )

    decision = guard.try_acquire()

    self.assertTrue(decision.allowed)
    self.assertFalse(decision.acquired)
    self.assertEqual("disabled", decision.reason)
```

测试并发限制：

```python
def test_concurrent_limit_blocks_second_request(self):
    guard = PublicThinkGuard(
        enabled=True,
        max_concurrent_thinks=1,
        daily_think_limit=10,
        busy_message="busy",
        daily_limit_message="limit",
    )

    first = guard.try_acquire()
    second = guard.try_acquire()

    self.assertTrue(first.allowed)
    self.assertFalse(second.allowed)
    self.assertEqual("busy", second.reason)
```

这类测试保护的是：

```text
高并发时系统不要被打爆。
```

---

## 二十三、测试向量检索逻辑

向量检索涉及 embedding provider 和 collection。

真实向量库可能很重，所以测试里可以造假：

```python
class DummyEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "dummy provider"
    version = "v9"

    def __init__(self) -> None:
        super().__init__(dimension=3)

    def embed_text(self, text: str) -> list[float]:
        text_length = float(len(str(text or "")))
        return [text_length, text_length + 1.0, text_length + 2.0]
```

再造一个假 collection：

```python
class FakeCollection:
    def __init__(self) -> None:
        self.last_upsert = None
        self.last_query = None

    def upsert(self, **kwargs) -> None:
        self.last_upsert = kwargs

    def query(self, **kwargs) -> dict:
        self.last_query = kwargs
        return {
            "ids": [["memory-1"]],
            "documents": [["欢迎回来"]],
            "metadatas": [[{"entry_type": "raw"}]],
            "distances": [[0.2]],
        }
```

测试：

```python
store = VectorStore.__new__(VectorStore)
store._lock = threading.RLock()
store.embedding_provider = DummyEmbeddingProvider()
store.collection = FakeCollection()

store.upsert_entry(
    source_id="memory-1",
    text="欢迎回来",
    metadata={"profile_user_id": "user-1"},
)

self.assertEqual(
    store.collection.last_upsert["embeddings"],
    [[4.0, 5.0, 6.0]],
)
```

它保护的是：

```text
VectorStore 会使用注入的 embedding provider。
upsert 时传给 collection 的向量是预期的。
```

这种测试不会真的跑大模型 embedding。

所以：

```text
快
稳定
可重复
```

---

## 二十四、契约测试

契约测试关注：

```text
模块之间约定好的字段、格式、行为不能随便变。
```

Akane 里很多文件名都带 `contract`：

```text
test_desktop_pet_frontend_contract.py
test_desktop_pet_backend_contract.py
test_desktop_activity_runtime_contract.py
test_resource_visibility_contract.py
test_scene_frontend_contract.py
```

契约测试保护的不是某一个内部函数，而是：

```text
前端依赖的字段还在吗
后端返回的资源结构还对吗
不同 client_mode 下工具是否隔离
桌宠、QQ、Web scene 有没有串线
```

比如 `/think` 的流式契约：

```text
第一行必须是 stream_start
最后一行必须是 stream_end
响应头必须有 X-Akane-Contract
```

这些字段看起来小，但前端可能依赖它们。

所以契约测试的价值是：

```text
防止你重构后“看似能跑”，但另一端坏了。
```

---

## 二十五、回归测试

回归测试就是：

```text
以前修过的 bug，以后不要再出现。
```

Akane 有快速回归套件：

```text
tests/quick_regression_suite.py
```

里面不是自动发现所有测试，而是手动列出关键路径：

```python
QUICK_TESTS = [
    "tests.test_repository_hygiene",
    "tests.test_resource_visibility_contract",
    "tests.test_desktop_pet_frontend_contract",
    "tests.test_desktop_pet_backend_contract",
    ...
    "tests.test_qq_gateway.QQGatewayTests.test_duplicate_message_id_is_ignored",
    ...
]
```

运行：

```powershell
python -m tests.quick_regression_suite
```

这类套件的作用：

```text
不是覆盖全部细节
而是快速确认最关键的跨端行为没坏。
```

适合在：

```text
大改前端契约
大改工具调用
大改文件交付
大改 QQ / 桌宠分流
提交前快速体检
```

---

## 二十六、setUp 和 tearDown

如果每个测试都要重复准备对象，可以用 `setUp`。

```python
import unittest


class Counter:
    def __init__(self):
        self.value = 0

    def inc(self):
        self.value += 1


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.counter = Counter()

    def test_inc_once(self):
        self.counter.inc()
        self.assertEqual(self.counter.value, 1)

    def test_initial_value(self):
        self.assertEqual(self.counter.value, 0)
```

注意：

```text
每个 test 方法执行前，都会重新执行 setUp。
```

所以这两个测试不会互相影响。

如果需要清理，可以用 `tearDown`：

```python
def tearDown(self):
    ...
```

但在 Akane 里，很多测试用 `with tempfile.TemporaryDirectory()`，自动清理就够了。

---

## 二十七、测试命名

好的测试名应该像一句话。

Akane 里很多测试名很好：

```text
test_session_titles_can_be_listed_and_renamed
test_claim_due_reminders_marks_rows_as_fired
test_get_latest_eval_turn_for_session_is_profile_scoped
test_concurrent_limit_blocks_second_request
test_emits_ui_event_and_speech_chunks_from_split_json
test_think_router_handles_once_and_stream_contract_with_fake_engine
```

你一看名字就知道在测什么。

坏名字：

```text
test_store
test_func
test_1
test_ok
```

好名字模板：

```text
test_场景_预期行为
```

或者：

```text
test_when_x_then_y
```

例子：

```text
test_invalid_payload_returns_400
test_duplicate_message_id_is_ignored
test_stream_error_keeps_partial_speech
```

---

## 二十八、一个完整的小型测试文件

下面这个例子模拟 Akane 的消息存储，但非常简化。

```python
import tempfile
import unittest
from pathlib import Path


class MiniMessageStore:
    def __init__(self, root: Path):
        self.path = root / "messages.txt"

    def add_message(self, role: str, content: str) -> dict:
        line = f"{role}: {content}\n"
        with self.path.open("a", encoding="utf-8") as file:
            file.write(line)
        return {"role": role, "content": content}

    def list_messages(self) -> list[str]:
        if not self.path.exists():
            return []
        return self.path.read_text(encoding="utf-8").splitlines()


class MiniMessageStoreTests(unittest.TestCase):
    def test_add_message_can_be_listed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MiniMessageStore(Path(temp_dir))

            created = store.add_message("user", "你好")
            messages = store.list_messages()

            self.assertEqual(created, {"role": "user", "content": "你好"})
            self.assertEqual(messages, ["user: 你好"])

    def test_empty_store_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MiniMessageStore(Path(temp_dir))

            self.assertEqual(store.list_messages(), [])


if __name__ == "__main__":
    unittest.main()
```

运行：

```powershell
python mini_store_test.py
```

这个例子覆盖了：

```text
TestCase
test_ 方法
tempfile
Arrange / Act / Assert
assertEqual
```

---

## 二十九、一个完整的 FastAPI 测试文件

下面模拟 Akane 的 `/think_once`：

```python
import unittest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


app = FastAPI()


@app.post("/think_once")
async def think_once(request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse(
            {"error": "invalid_payload"},
            status_code=400,
        )
    return {
        "emotion": "normal",
        "speech": f"echo: {payload.get('message', '')}",
    }


class ThinkOnceTests(unittest.TestCase):
    def test_think_once_returns_speech(self):
        response = TestClient(app).post(
            "/think_once",
            json={"user_id": "desktop", "message": "hi"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["speech"], "echo: hi")

    def test_think_once_rejects_non_object_payload(self):
        response = TestClient(app).post(
            "/think_once",
            json=["not", "object"],
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_payload")


if __name__ == "__main__":
    unittest.main()
```

这就是 Akane 接口测试的最小模型。

---

## 三十、一个完整的流式响应测试

这个例子模拟 Akane `/think` 的 NDJSON。

```python
import json
import unittest

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient


app = FastAPI()


@app.post("/think")
def think():
    def stream():
        yield json.dumps({"type": "stream_start"}) + "\n"
        yield json.dumps({"type": "speech_chunk", "text": "hello"}) + "\n"
        yield json.dumps({"type": "final", "payload": {"speech": "hello"}}) + "\n"
        yield json.dumps({"type": "stream_end", "partial": {"speech": "hello"}}) + "\n"

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"X-Akane-Contract": "test-v1"},
    )


class ThinkStreamTests(unittest.TestCase):
    def test_think_stream_contract(self):
        response = TestClient(app).post("/think")
        lines = [json.loads(line) for line in response.text.splitlines()]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-akane-contract"], "test-v1")
        self.assertEqual(lines[0]["type"], "stream_start")
        self.assertEqual(lines[-1]["type"], "stream_end")
        self.assertEqual(lines[-1]["partial"]["speech"], "hello")


if __name__ == "__main__":
    unittest.main()
```

这个测试保护的是：

```text
流式响应第一行是什么
最后一行是什么
响应头有没有协议版本
partial 是否能兜底
```

和 Akane 的真实测试非常像。

---

## 三十一、测试时不要做什么

### 31.1 不要测太多内部实现

不推荐：

```text
断言某个内部临时变量必须是什么。
```

更推荐：

```text
断言对外行为是否正确。
```

比如测试 `MemoryStore`，重点是：

```text
add 后能不能 get
list 顺序对不对
状态是否更新
profile 是否隔离
```

而不是：

```text
内部 SQL 字符串必须长什么样
```

### 31.2 不要让测试依赖真实网络

真实网络会导致：

```text
慢
不稳定
离线失败
外部服务变更导致测试失败
```

应该用：

```text
FakeResponse
patch
临时文件
```

### 31.3 不要让测试依赖执行顺序

每个测试应该能单独运行。

不应该：

```text
test_b 依赖 test_a 先创建了某个数据
```

正确做法：

```text
每个测试自己准备需要的数据。
```

### 31.4 不要只测 happy path

happy path 是正常情况。

还要测：

```text
非法输入
空数据
重复请求
权限边界
状态边界
外部服务失败
```

---

## 三十二、什么时候该写测试

不是所有小改动都要写很重的测试。

但这些情况很值得写：

```text
修 bug：写一个能复现 bug 的测试
改接口字段：写契约测试
改数据库逻辑：写存储测试
改状态流转：写状态测试
改 LLM JSON 解析：写各种坏格式输入测试
改工具调用：写 Fake tool / stream_events 测试
改多端分流：写 client_mode 契约测试
```

一句话：

```text
越容易回归、越难手动检查、越影响多个模块，越应该写测试。
```

---

## 三十三、Akane 源码阅读路线

学测试时不要一口气读所有测试。

建议按这个顺序：

```text
1. tests/test_public_guard.py
   最短，适合理解 TestCase 和 assert。

2. tests/test_store.py
   学 tempfile、存储行为、状态变化。

3. tests/test_backend_route_modules.py
   学 TestClient、FakeEngine、接口契约、流式响应。

4. tests/test_llm_runtime_stream.py
   学 LLM 流式 JSON、部分输出恢复。

5. tests/test_vector_store.py
   学 fake embedding provider、fake collection、依赖注入。

6. tests/quick_regression_suite.py
   学如何组织关键回归路径。
```

不要从 `test_generated_files.py` 这种超大文件开始。

先读小而清楚的。

---

## 三十四、这篇先记住

### 34.1 速查：测试类型 → 技术 → Akane 示例

| 你要保护什么 | 用哪种测试 | 核心技术 | Akane 示例文件 |
|------------|----------|---------|--------------|
| 存储行为不对 | 数据层测试 | tempfile + MemoryStore | `test_store.py` |
| 接口返回格式变了 | 接口测试 | TestClient + FakeEngine | `test_backend_route_modules.py` |
| 状态流转出错 | 状态测试 | AAA + assertEqual | `test_store.py` (reminder) |
| 用户数据串了 | 隔离测试 | 不同 profile/session | `test_store.py` (eval turn) |
| LLM 输出解析坏了 | 流式解析测试 | chunk 拆分 + assertEqual | `test_llm_runtime_stream.py` |
| 前端依赖的字段没了 | 契约测试 | TestClient + assertEqual | `test_desktop_pet_frontend_contract.py` |
| 旧 bug 复发 | 回归测试 | quick_regression_suite | `quick_regression_suite.py` |
| 外部服务影响测试 | 隔离测试 | Fake + patch | `test_attachment_ingest.py` |

### 34.2 核心概念

```text
unittest：Python 标准测试框架
TestCase：测试类基类
test_：测试方法命名前缀
assert：断言结果
Arrange / Act / Assert：准备、执行、检查
tempfile：隔离文件和数据库
Fake：可控的假对象
patch：替换外部依赖
TestClient：不启动服务器也能测 FastAPI
契约测试：保护模块之间的字段和格式
回归测试：防止旧 bug 复发
```

Akane 测试主线：

```text
MemoryStore 用 tempfile 测真实持久化行为
路由层用 TestClient 测 HTTP 契约
复杂依赖用 Fake / patch 隔离
LLM 流式解析用 chunk 测边界输入
前后端交互用 contract 测字段和事件
quick_regression_suite 固定关键回归路径
```

---

## 三十五、我自己的复述

测试不是为了背 `unittest` 的 API。

测试是在回答：

```text
这个功能对外承诺了什么？
我怎么用代码证明它没有坏？
```

Akane 的测试体系其实很清晰：

```text
存储测试保护数据库行为
接口测试保护 HTTP 返回格式
流式测试保护 NDJSON 事件协议
Fake / patch 保护测试不被外部服务影响
契约测试保护前后端和多端客户端不串线
快速回归套件保护关键路径
```

所以你读测试时，不要只看语法。

要问三件事：

```text
这个测试在保护什么行为？
它为什么要造这个 Fake / 临时目录？
如果这个测试失败，用户会遇到什么问题？
```

能这样读测试，就已经开始像工程师一样理解项目了。

