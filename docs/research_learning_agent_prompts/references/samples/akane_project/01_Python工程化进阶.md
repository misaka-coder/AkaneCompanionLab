---
tags:
  - akane/python-engineering
  - python/advanced
  - backend/architecture
  - software-engineering
created: 2026-05-20
---

# Python 工程化进阶

> Python 基础解决“语法怎么写”。  
> Python 工程化解决“一个真实项目怎么组织、怎么拆模块、怎么传数据、怎么长期维护”。

Akane 不是一个单文件脚本，而是一个真实工程：

```text
companion_v01/     Python 后端核心
services/          外部服务客户端
routes/            HTTP 路由层
tests/             测试
docs/              设计文档
web/               Web 前端
desktop_pet_next/  桌宠客户端
config.py          配置入口
```

这份笔记的目标：

```text
补齐从 Python 基础到 Akane 项目源码之间的工程知识
```

学习路线：

```text
脚本 vs 工程
→ 包和模块
→ import 和相对导入
→ pathlib.Path
→ 类型标注 typing
→ dataclass
→ field(default_factory)
→ contextmanager
→ yield 和生成器
→ 服务类 Service / Engine
→ 依赖注入
→ 配置管理
→ 回调 Callable
→ 锁、线程、后台任务
→ 日志、异常边界
→ Akane 源码阅读方法
```

---

## 一、脚本和工程的区别

刚学 Python 时，常写这样的脚本：

```python
print("hello")
```

或者：

```python
def add(a, b):
    return a + b

print(add(1, 2))
```

这种叫脚本式代码。

特点：

```text
文件少
逻辑短
从上往下执行
适合练习和小工具
```

真实项目不一样。

Akane 里有很多角色：

```text
app.py                 负责启动后端
routes/think.py        负责 /think 接口
engine.py              负责一轮对话主流程
store.py               负责数据库
prompt_builder.py      负责拼 prompt
llm_runtime.py         负责调用模型
retrieval_service.py   负责记忆检索
```

工程化就是：

```text
把复杂系统拆成很多文件
每个文件负责一个清楚的职责
再通过 import 和对象组合起来
```

---

## 二、包 package 和模块 module

一个 `.py` 文件就是模块。

```text
store.py       → store 模块
engine.py      → engine 模块
text_utils.py  → text_utils 模块
```

一个带 `__init__.py` 的目录就是包。

Akane：

```text
companion_v01/
├── __init__.py
├── app.py
├── engine.py
├── store.py
└── routes/
    ├── __init__.py
    └── think.py
```

`__init__.py` 的作用：

```text
告诉 Python：这个目录可以当作包导入
```

Akane 里的 `companion_v01/__init__.py` 很简单：

```python
"""Akane companion memory V0.1 package."""
```

它没有复杂逻辑，只是标记包和写一句说明。

---

## 三、import：导入别的模块

最常见导入：

```python
import json
import time
from pathlib import Path
```

含义：

```text
import json              导入整个 json 模块
from pathlib import Path 只导入 pathlib 里的 Path
```

例子：

```python
import json

data = {"name": "Akane", "level": 1}
text = json.dumps(data, ensure_ascii=False)
print(text)
```

---

## 四、绝对导入和相对导入

绝对导入：

```python
import config
from services.llm_client import build_llm_client
```

相对导入：

```python
from .store import MemoryStore
from .text_utils import normalize_text
from ..desktop_pet_contract import DESKTOP_PET_CONTRACT_VERSION
```

`.` 表示当前包。

```text
from .store import MemoryStore
```

意思是：

```text
从当前 companion_v01 包里的 store.py 导入 MemoryStore
```

`..` 表示上一级包。

Akane 的 `routes/think.py`：

```python
from ..desktop_pet_contract import DESKTOP_PET_CONTRACT_VERSION
```

因为 `think.py` 在：

```text
companion_v01/routes/think.py
```

而 `desktop_pet_contract.py` 在：

```text
companion_v01/desktop_pet_contract.py
```

所以要从 `routes` 往上走一级。

---

## 五、为什么工程里不要乱写 import

导入不是越多越好。

常见问题：

```text
1. 未使用的 import 让文件变乱
2. 循环导入会导致启动失败
3. 顶层 import 太重会让启动变慢
4. import 时执行副作用可能难排查
```

循环导入例子：

```python
# a.py
from b import func_b

def func_a():
    return "a"
```

```python
# b.py
from a import func_a

def func_b():
    return "b"
```

两个文件互相导入，就可能出问题。

工程习惯：

```text
工具函数放到独立模块
底层模块不要导入上层模块
确实需要时，可以把 import 放到函数内部
```

---

## 六、from __future__ import annotations

Akane 很多文件开头有：

```python
from __future__ import annotations
```

**为什么需要它？**

看一个会出问题的写法：

```python
class Node:
    def __init__(self, value: int, next_node: Node | None = None):
        #                                  ^^^^
        # Node 还没定义完，这里引用自己会报错
        self.value = value
        self.next_node = next_node
```

Python 执行 class 体时，`Node` 这个类名还没有绑定完成。所以在类型标注里引用 `Node` 本身（或者两个类互相引用），老版本 Python 会报 `NameError`。

`from __future__ import annotations` 的作用：

```text
把所有类型标注从"立即求值"变成"延迟求值"。
类型标注只存成字符串，等到真正需要时才解析。
```

所以加了这行之后，`Node | None` 就不会在定义时炸了。

在现代 Python（3.10+）工程里，这行极其常见。Akane 几乎所有文件都加它。

先记住：

```text
看到它不用紧张
它主要服务类型标注（尤其是类自己引用自己或互相引用时）
```

---

## 七、pathlib.Path：工程里的路径对象

基础写法可能是：

```python
path = "data/memory.txt"
```

工程里更推荐：

```python
from pathlib import Path

path = Path("data") / "memory.txt"
print(path)
```

`Path` 的好处：

```text
跨平台
拼路径清楚
读写文件方便
可以检查文件和目录
```

常用 API：

```python
from pathlib import Path

root = Path("demo_data")
root.mkdir(parents=True, exist_ok=True)

file_path = root / "note.txt"
file_path.write_text("hello", encoding="utf-8")

print(file_path.exists())
print(file_path.read_text(encoding="utf-8"))
print(file_path.name)
print(file_path.suffix)
print(file_path.stem)
```

输出含义：

```text
name    文件名 note.txt
suffix  后缀 .txt
stem    不带后缀的文件名 note
```

---

## 八、Akane 里的 Path

Akane 的 `config.py`：

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = str(BASE_DIR / "users_data")
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
```

解释：

```text
__file__                  当前文件路径
Path(__file__)            转成 Path 对象
.resolve()                转成绝对路径
.parent                   当前文件所在目录
BASE_DIR / "users_data"   拼出 users_data 目录
mkdir(...)                确保目录存在
```

Akane 的 `app.py`：

```python
APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent

WEB_DIR = PROJECT_DIR / "web"
ASSETS_DIR = WEB_DIR / "assets"
```

这是一种典型项目路径写法：

```text
先定位当前文件
再定位项目根目录
再拼出资源目录
```

---

## 九、Path 和字符串的转换

有些库接受 `Path`，有些库只接受字符串。

```python
from pathlib import Path

path = Path("demo.txt")

print(str(path))  # demo.txt
```

Akane 里有时会这样：

```python
DATA_DIR = str(BASE_DIR / "users_data")
```

原因：

```text
配置项通常希望是字符串
真正用路径时再 Path(DATA_DIR)
```

---

## 十、类型标注是什么

类型标注写在变量、参数和返回值旁边。

```python
def add(a: int, b: int) -> int:
    return a + b
```

含义：

```text
a 应该是 int
b 应该是 int
函数返回 int
```

类型标注不会自动阻止你传错。

```python
print(add("1", "2"))  # Python 仍然会运行，结果是 "12"
```

它主要用于：

```text
提升可读性
帮助 IDE 补全
帮助静态检查工具发现问题
让工程协作更清楚
```

---

## 十一、常见类型标注

基础类型：

```python
name: str = "Akane"
age: int = 18
score: float = 0.95
enabled: bool = True
```

列表：

```python
tags: list[str] = ["学习", "SQLite"]
```

字典：

```python
payload: dict[str, str] = {
    "role": "user",
    "content": "你好",
}
```

复杂一点：

```python
message: dict[str, object] = {
    "content": "你好",
    "seq_no": 1,
}
```

Akane 里常见：

```python
dict[str, Any]
list[dict[str, Any]]
str | None
```

---

## 十二、Any：先允许复杂，再慢慢收紧

`Any` 表示“任意类型”。

```python
from typing import Any

def read_field(payload: dict[str, Any], key: str) -> Any:
    return payload.get(key)
```

为什么 Akane 很多地方用 `Any`？

因为 LLM、HTTP 请求、前端 payload、数据库 JSON 都可能长这样：

```python
{
    "message": "你好",
    "timestamp": 1710000000,
    "current_visual": {
        "scene": "room",
        "emotion": "normal",
    },
}
```

结构可能变化，早期全部写成严格类型会很累。

所以工程里常见策略：

```text
边界层用 dict[str, Any]
核心稳定结构再用 dataclass 或明确类型
```

Akane 的 `engine.process_turn_stream`：

```python
def process_turn_stream(self, payload: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
    ...
```

意思：

```text
输入 payload 是一个字典
里面的值暂时允许任意类型
函数会不断 yield 事件字典
```

---

## 十三、Union：一个值可能有多种类型

现代写法：

```python
def find_user(user_id: str) -> dict[str, str] | None:
    if user_id == "master":
        return {"name": "master"}
    return None
```

`dict[str, str] | None` 表示：

```text
要么返回 dict[str, str]
要么返回 None
```

Akane 里常见：

```python
resource_manifest: ResourceManifest | None = None
llm: Any | None = None
early_tool_call: dict[str, Any] | None = None
```

读法：

```text
xxx | None 说明这个东西可以为空
使用前要考虑 None 的情况
```

---

## 十四、Callable：函数也可以作为参数

函数可以被传来传去。

```python
from typing import Callable


def run_twice(fn: Callable[[str], None]) -> None:
    fn("第一次")
    fn("第二次")


def print_message(text: str) -> None:
    print(text)


run_twice(print_message)
```

`Callable[[str], None]` 的意思：

```text
这是一个函数
接收一个 str
返回 None
```

Akane 的 `routes/think.py`：

```python
from typing import Callable

LogEvent = Callable[..., None]
```

意思：

```text
LogEvent 是一个函数类型
参数可以比较灵活
返回 None
```

路由函数里接收：

```python
def build_think_router(
    *,
    engine: Any,
    public_guard: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
) -> APIRouter:
    ...
```

这就是把“记录日志的能力”作为函数传进来。

---

## 十五、类型别名

类型别名就是给复杂类型起个名字。

```python
from typing import Callable

ResolveIdentity = Callable[[str], tuple[str, str]]
```

然后可以写：

```python
def handle(resolve_identity: ResolveIdentity) -> None:
    session_id, profile_user_id = resolve_identity("request")
```

Akane 的 `routes/core.py`：

```python
ResolveIdentity = Callable[[Request], tuple[str, str]]
```

这样比每次都写一长串类型更清楚。

---

## 十六、dataclass 是什么

普通 class 写数据对象：

```python
class Message:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content
```

使用：

```python
msg = Message("user", "你好")
print(msg.role, msg.content)
```

`dataclass` 可以少写很多样板代码：

```python
from dataclasses import dataclass


@dataclass
class Message:
    role: str
    content: str


msg = Message("user", "你好")
print(msg)
```

输出类似：

```text
Message(role='user', content='你好')
```

它自动帮你生成：

```text
__init__
__repr__
__eq__
```

---

## 十七、Akane 里的 dataclass

Akane 的 `retrieval_types.py`：

```python
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalPipelineResult:
    used_retrieval: bool
    confirmed_snippets: list[str]
    router_output: dict[str, Any]
    router_timing: dict[str, Any]
    retrieval_result: dict[str, Any]
    verifier_output: dict[str, Any]
    verifier_timing: dict[str, Any]
```

这是一个典型“结果对象”。

它把一次检索流水线的结果打包在一起。

相比返回普通 dict：

```python
{
    "used_retrieval": True,
    "confirmed_snippets": [],
    ...
}
```

dataclass 的好处：

```text
字段一眼能看到
IDE 可以补全
创建对象更稳定
比散乱 dict 更像一份明确协议
```

---

## 十八、frozen=True：不可变数据对象

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: int
    y: int


p = Point(1, 2)
print(p.x)

# p.x = 100  # 会报错
```

`frozen=True` 表示：

```text
对象创建后，不希望再修改字段
```

适合：

```text
配置对象
上下文对象
结果对象
协议对象
```

Akane 的 `ToolExecutionContext`：

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

它表示：

```text
工具执行时的上下文
```

上下文通常不希望在执行过程中被随手改乱，所以适合 frozen。

---

## 十九、field(default_factory=...)

先看一个坑：

```python
from dataclasses import dataclass


@dataclass
class BadBox:
    items: list[str] = []  # 不推荐
```

列表是可变对象。

多个对象可能共享同一个列表，容易出 bug。

推荐：

```python
from dataclasses import dataclass, field


@dataclass
class Box:
    items: list[str] = field(default_factory=list)


a = Box()
b = Box()

a.items.append("Akane")

print(a.items)  # ['Akane']
print(b.items)  # []
```

`default_factory=list` 的意思：

```text
每次创建对象时，都新建一个空 list
```

Akane 的 `ToolExecutionResult`：

```python
@dataclass
class ToolExecutionResult:
    tool_type: str
    raw_turns: list[dict[str, Any]] = field(default_factory=list)
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    followup_context: str = ""
    state_updates: dict[str, Any] = field(default_factory=dict)
```

这里的列表和字典都用 `default_factory`，这是正确工程写法。

---

## 二十、普通 dict、dataclass、class 怎么选

三种都能组织数据。

| 方式 | 适合场景 |
|---|---|
| `dict` | 外部 JSON、不稳定结构、快速拼 payload |
| `dataclass` | 稳定数据结构、结果对象、上下文对象 |
| 普通 `class` | 有大量行为、状态、依赖、方法 |

Akane 的选择：

```text
HTTP payload                 dict[str, Any]
RetrievalPipelineResult      dataclass
AkaneMemoryEngine            class
MemoryStore                  class
GiftSystemService            class
```

一句话：

```text
数据为主用 dataclass
行为和依赖很多用 class
边界层 JSON 用 dict
```

---

## 二十一、contextmanager：把资源借出去再收回来

你已经在 SQLite 笔记里见过 Akane 的 `_connect()`：

```python
from contextlib import contextmanager


@contextmanager
def open_resource():
    print("打开资源")
    try:
        yield "resource"
    finally:
        print("关闭资源")


with open_resource() as resource:
    print("使用", resource)
```

输出：

```text
打开资源
使用 resource
关闭资源
```

`contextmanager` 的核心：

```text
yield 前：准备资源
yield 中：交给 with 代码块使用
yield 后：清理资源
```

适合：

```text
数据库连接
文件打开
临时目录
锁
网络连接
```

---

## 二十二、Akane 的数据库上下文

Akane 的 `MemoryStore._connect()`：

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

这让其他方法可以写得很干净：

```python
with self._connect() as conn:
    rows = conn.execute("SELECT * FROM chat_messages").fetchall()
```

如果没有封装，很多地方都要重复：

```python
conn = sqlite3.connect(...)
try:
    ...
    conn.commit()
finally:
    conn.close()
```

工程化的重要思路：

```text
重复的资源管理逻辑，封装成上下文管理器
```

---

## 二十三、yield：生成器基础

普通函数一次返回一个结果：

```python
def get_numbers():
    return [1, 2, 3]


print(get_numbers())
```

生成器可以一个一个吐结果：

```python
def count_to_three():
    yield 1
    yield 2
    yield 3


for item in count_to_three():
    print(item)
```

输出：

```text
1
2
3
```

一句话理解：

```text
return 是一次性给结果
yield 是边运行边给结果
```

---

## 二十四、生成器适合流式输出

聊天模型经常不是等完整结果出来才显示。

更好的体验是：

```text
模型生成一点
后端发一点
前端显示一点
```

最小例子：

```python
import time


def stream_text(text: str):
    for char in text:
        time.sleep(0.1)
        yield char


for chunk in stream_text("Akane"):
    print(chunk)
```

这就是流式思路。

---

## 二十五、Generator 类型标注

```python
from typing import Generator


def numbers() -> Generator[int, None, None]:
    yield 1
    yield 2
```

`Generator[int, None, None]` 三个位置：

```text
第一个 int   yield 出来的类型
第二个 None  send 进来的类型，一般不用
第三个 None  return 的类型，一般不用
```

Akane：

```python
def process_turn_stream(self, payload: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
    ...
```

含义：

```text
这是一个生成器
每次 yield 一个 dict[str, Any] 事件
不接收 send
没有特殊 return 值
```

---

## 二十六、yield from：把另一个生成器接过来

普通写法：

```python
def sub():
    yield 1
    yield 2


def main():
    for item in sub():
        yield item
    yield 3
```

可以写成：

```python
def sub():
    yield 1
    yield 2


def main():
    yield from sub()
    yield 3
```

Akane 的 `engine.py`：

```python
final_output = yield from self._stream_final_response(...)
```

这行很关键。

它表示：

```text
把 _stream_final_response 生成的流式事件继续往外传
并且拿到它最终 return 的 final_output
```

这比普通 `for event in ...: yield event` 更强，因为它还能接住子生成器的返回值。

---

## 二十七、NDJSON：流式事件的常见格式

NDJSON 是：

```text
Newline Delimited JSON
一行一个 JSON
```

例子：

```text
{"type":"stream_start"}
{"type":"speech_chunk","text":"你好"}
{"type":"speech_chunk","text":"呀"}
{"type":"final","payload":{"speech":"你好呀"}}
{"type":"stream_end"}
```

Akane 的 `routes/think.py` 里：

```python
yield json.dumps(event, ensure_ascii=False) + "\n"
```

意思：

```text
把一个 Python dict 转成 JSON 字符串
末尾加换行
前端就能一行行读取
```

---

## 二十八、Akane 的 /think 流式结构

Akane 的 `build_think_router()` 里有一个内部生成器 `_stream()`：

```python
def _stream():
    yield json.dumps({"type": "stream_start"}, ensure_ascii=False) + "\n"
    try:
        for event in engine.process_turn_stream(payload):
            yield json.dumps(event, ensure_ascii=False) + "\n"
    finally:
        yield json.dumps({"type": "stream_end"}, ensure_ascii=False) + "\n"
```

真实代码更复杂，但骨架就是：

```text
发 stream_start
遍历 engine.process_turn_stream(payload)
每个事件转成一行 JSON
最后发 stream_end
```

一句话理解：

```text
routes/think.py 负责把 Python 事件流包装成 HTTP 流式响应
engine.py 负责真正生产这些事件
```

---

## 二十九、class 的工程角色

你已经学过 OOP。

在工程里，class 常常不是为了“继承炫技”，而是为了组织一组长期存在的状态和方法。

比如：

```python
class CounterService:
    def __init__(self):
        self.count = 0

    def incr(self) -> int:
        self.count += 1
        return self.count
```

这个类有：

```text
状态：count
行为：incr
```

Akane 的类也是这样。

---

## 三十、Service 和 Engine 命名习惯

常见命名：

```text
Store       数据存储
Service     一个业务能力
Engine      一个较大的流程编排器
Runtime     运行时封装
Registry    注册表
Builder     构建器
Router      路由器
Client      外部服务客户端
```

Akane 例子：

| 名字 | 角色 |
|---|---|
| `MemoryStore` | 数据库存储层 |
| `RetrievalService` | 记忆检索服务 |
| `GiftSystemService` | 礼物系统服务 |
| `AkaneMemoryEngine` | 对话主流程编排器 |
| `LLMRuntime` | 模型调用运行时 |
| `PromptBuilder` | prompt 构建器 |
| `CapabilityRegistry` | 能力注册表 |
| `NapCatQQGateway` | QQ 网关客户端 |

读代码时先看类名，通常能猜到职责。

---

## 三十一、AkaneMemoryEngine：总编排器

Akane 的 `engine.py` 里：

```python
class AkaneMemoryEngine:
    def __init__(
        self,
        base_dir: Path,
        resource_manifest: ResourceManifest | None = None,
        desktop_pet_character_resources: Any = None,
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.store = MemoryStore(self.base_dir)
        self.embedding_provider = self._build_embedding_provider()
        self.vector_store = VectorStore(
            self.base_dir / "chroma",
            embedding_provider=self.embedding_provider,
        )
        self.llm = LLMRuntime()
```

这段代码说明：

```text
Engine 不是只做一个小功能
它会创建和持有很多子系统
```

包括：

```text
store
vector_store
llm
gift_service
background_tasks
attachment_inbox_service
persona_card_service
task_workspace_service
generated_file_service
```

一句话理解：

```text
AkaneMemoryEngine 是后端大脑的总调度中心。
```

---

## 三十二、Service：把一个领域单独拆出去

Akane 的 `GiftSystemService`：

```python
class GiftSystemService:
    def __init__(self, base_dir: Path, *, store: MemoryStore, llm: Any | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.llm = llm
        self.repository = GiftRepository(store=store)
        self.processors: dict[str, BaseGiftProcessor] = {
            "audio": AudioGiftProcessor(base_dir=self.base_dir),
            "image": ImageGiftProcessor(base_dir=self.base_dir),
        }
```

这个 Service 负责礼物系统。

它不应该关心：

```text
/think HTTP 请求怎么来
LLM 最终怎么回复
前端按钮怎么显示
```

它只关心：

```text
礼物资源怎么保存
礼物状态怎么管理
音频礼物怎么处理
图片礼物怎么处理
```

工程化核心：

```text
一个类只负责一个领域
```

---

## 三十三、依赖注入：把需要的对象传进来

看这个构造函数：

```python
class GiftSystemService:
    def __init__(self, base_dir: Path, *, store: MemoryStore, llm: Any | None = None) -> None:
        self.store = store
        self.llm = llm
```

`store` 和 `llm` 不是在类里面偷偷创建，而是从外面传进来。

这叫依赖注入。

好处：

```text
更容易测试
更容易替换实现
类之间关系更清楚
```

最小例子：

```python
class FakeStore:
    def save(self, text: str) -> None:
        print("保存:", text)


class NoteService:
    def __init__(self, store: FakeStore):
        self.store = store

    def create_note(self, text: str) -> None:
        self.store.save(text)


service = NoteService(FakeStore())
service.create_note("Akane 学工程化")
```

---

## 三十四、组合优先于继承

继承：

```python
class Dog(Animal):
    ...
```

组合：

```python
class Engine:
    def __init__(self):
        self.store = Store()
        self.llm = LLMRuntime()
```

Akane 大量使用组合。

例如 `AkaneMemoryEngine` 持有：

```text
self.store
self.llm
self.vector_store
self.gift_service
self.task_workspace_service
```

为什么组合更适合复杂项目？

```text
关系更直接
替换组件更方便
不容易形成很深的继承树
```

继承也有用，但不要滥用。

---

## 三十五、抽象基类思想

Akane 的 `tool_runtime.py`：

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

这不是严格使用 `ABC`，但思想一样：

```text
定义一个工具处理器必须有哪些方法
子类必须自己实现
```

子类：

```python
class RetrieveMemoryToolHandler(BaseToolHandler):
    tool_type = "retrieve_memory"
    ...
```

一句话理解：

```text
BaseToolHandler 定义接口
具体工具类实现接口
```

---

## 三十六、配置管理

小脚本里经常直接写死：

```python
API_KEY = "xxx"
MODEL_NAME = "deepseek-chat"
```

工程里不推荐把配置散落在各个文件里。

Akane 用 `config.py` 集中管理：

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    RUN_MODE: str = "CLOUD"
    EMBEDDING_MODEL_NAME: str = "BAAI/bge-small-zh-v1.5"
    ENABLE_VECTOR_MEMORY: bool = False
    SUMMARY_TRIGGER_COUNT: int = 30
    TEXT_API_KEY: str = ""
```

好处：

```text
配置集中
默认值清楚
类型清楚
可以从环境变量覆盖
```

比如：

```text
ENABLE_VECTOR_MEMORY=True
SUMMARY_TRIGGER_COUNT=20
```

就可以改变运行行为，而不用改业务代码。

---

## 三十七、getattr：安全读取配置

Akane 经常这样写：

```python
bool(getattr(config, "PUBLIC_GUARD_ENABLED", False))
```

含义：

```text
从 config 里读取 PUBLIC_GUARD_ENABLED
如果没有这个字段，就用 False
```

最小例子：

```python
class Config:
    DEBUG = True


print(getattr(Config, "DEBUG", False))      # True
print(getattr(Config, "MISSING", "default")) # default
```

为什么要这样？

```text
配置项可能随着项目演进增减
用 getattr 可以提供默认值，减少启动崩溃
```

---

## 三十八、路由构建函数

Akane 的路由文件通常长这样：

```python
def build_core_router(
    *,
    engine: Any,
    config_module: Any,
    resolve_identity_from_query: ResolveIdentity,
    runtime_metrics: Any = None,
    public_guard: Any = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok"}

    return router
```

这种写法的好处：

```text
路由定义在单独文件
依赖从 app.py 传进来
测试时可以传 fake engine
app.py 负责总装配
```

一句话：

```text
build_xxx_router 是“路由工厂函数”。
```

---

## 三十九、闭包：内部函数使用外部变量

看这个结构：

```python
def build_router(engine):
    def endpoint():
        return engine.run()

    return endpoint
```

`endpoint()` 里可以使用外层的 `engine`。

这叫闭包。

Akane 的路由就是利用这个特性：

```python
def build_think_router(*, engine: Any, ...):
    router = APIRouter()

    @router.post("/think")
    async def think(request: Request):
        ...
        for event in engine.process_turn_stream(payload):
            ...
```

`think()` 是内部函数，但它能访问外面的 `engine`。

---

## 四十、async / await：异步函数基础

### 40.1 为什么 Web 后端需要异步

假设一个 Web 服务同时收到 3 个请求：

```text
请求 1：查数据库（等 0.5 秒）
请求 2：调 LLM API（等 3 秒）
请求 3：读写文件（等 0.1 秒）
```

**同步方式**：一个接一个处理。请求 2 等 3 秒时，请求 1 和 3 干等着。

**异步方式**：请求 2 等 LLM 回复时，CPU 可以先去处理请求 1 和请求 3。谁的 IO 先完成，谁继续。

体现在 Akane 里：

```text
用户 A 的 /think 请求正在等 LLM 返回
→ 后端 CPU 不卡住
→ 用户 B 的 /health 请求立刻响应
→ 用户 C 的 /memory 请求正常查询数据库
```

这就是 FastAPI 用 `async def` 的原因。

### 40.2 基础语法

Akane 的路由里有：

```python
async def think(request: Request):
    payload = await request.json()
```

`async def` 表示异步函数。

`await` 表示：

```text
等待一个异步操作完成
等待时可以把执行权让出去
让其他异步任务有机会运行
```

最小例子：

```python
import asyncio


async def say_after(delay: float, text: str) -> None:
    await asyncio.sleep(delay)
    print(text)


async def main() -> None:
    await say_after(1, "hello")


asyncio.run(main())
```

### 40.3 并发执行：gather

两个互不依赖的异步任务可以同时跑：

```python
import asyncio
import time


async def fetch(url: str) -> str:
    print(f"开始请求 {url}")
    await asyncio.sleep(1)  # 模拟网络 IO
    print(f"完成请求 {url}")
    return f"<{url}>"


async def main() -> None:
    start = time.time()

    # 顺序执行（慢）
    # r1 = await fetch("A")
    # r2 = await fetch("B")

    # 并发执行（快）
    r1, r2 = await asyncio.gather(
        fetch("A"),
        fetch("B"),
    )

    print(f"结果：{r1}, {r2}")
    print(f"耗时：{time.time() - start:.1f}s")  # 约 1s，不是 2s


asyncio.run(main())
```

输出：

```text
开始请求 A
开始请求 B
完成请求 A
完成请求 B
结果：<A>, <B>
耗时：1.0s
```

两个请求"同时"进行，总时间只花了最长的那个。

### 40.4 同步函数里不能直接 await

```python
# 错误：不在 async def 里
def main():
    await asyncio.sleep(1)  # SyntaxError
```

`await` 只能出现在 `async def` 函数里。

### 40.5 不是所有代码都需要 async

```text
async 适合：
  - 网络请求（调 API、读 HTTP body）
  - 数据库查询（异步驱动）
  - 文件 IO（aiofiles）
  - 多个独立 IO 操作并发

不需要 async：
  - CPU 密集计算
  - 纯内存操作
  - 调用同步库（sqlite3 标准库就是同步的）
```

Akane 的选择：FastAPI 路由用 `async def`，但 `MemoryStore`（SQLite）是同步的——因为 sqlite3 标准库没有真正的异步支持。

---

## 四十一、同步生成器也可以放进 StreamingResponse

Akane 的 `_stream()` 是普通函数：

```python
def _stream():
    yield ...
```

然后：

```python
return StreamingResponse(
    _stream(),
    media_type="application/x-ndjson",
)
```

这说明：

```text
FastAPI 的 StreamingResponse 可以接收一个可迭代对象
生成器正好就是可迭代对象
```

所以不一定所有流式逻辑都要写成 async generator。

---

## 四十二、异常边界：在哪里 try / except

小脚本里常常让错误直接爆出来。

服务端项目不行。

Akane 的 `/think` 里先处理 JSON 解析失败：

```python
try:
    payload = await request.json()
except Exception as exc:
    return JSONResponse(
        {"error": "invalid_json", "message": str(exc)},
        status_code=400,
    )
```

流式过程中也处理异常：

```python
try:
    for event in engine.process_turn_stream(payload):
        yield json.dumps(event, ensure_ascii=False) + "\n"
except Exception as exc:
    yield json.dumps({"type": "stream_error", "message": str(exc)}, ensure_ascii=False) + "\n"
finally:
    yield json.dumps({"type": "stream_end"}, ensure_ascii=False) + "\n"
```

工程原则：

```text
边界层要兜住错误
内部层可以抛出清晰异常
不要把错误吞掉还装作成功
```

---

## 四十三、finally：一定要执行的清理逻辑

`finally` 不管有没有异常都会执行。

```python
try:
    print("开始")
    raise ValueError("出错了")
except ValueError:
    print("捕获错误")
finally:
    print("清理资源")
```

输出：

```text
开始
捕获错误
清理资源
```

Akane 的 `_stream()` 里：

```python
finally:
    if guard_decision.acquired:
        public_guard.release()
    runtime_metrics.observe_request(...)
    yield json.dumps({"type": "stream_end"}, ensure_ascii=False) + "\n"
```

意思：

```text
不管流式回复成功还是失败
都要释放并发名额
都要记录指标
都要发 stream_end
```

---

## 四十四、线程和锁：先掌握工程直觉

### 44.1 为什么 Akane 需要后台线程

Akane 不是在"请求-响应"之间做完所有事。

有些事不适合放在请求处理里：

```text
HTTP 请求 /think 来了
→ 用户期望很快收到第一个字（< 1 秒）
→ 但"总结整个会话的记忆"可能需要 5-30 秒
→ 如果放在请求里同步做完，用户早就走了
```

所以 Akane 的架构是：

```text
主线程（HTTP 处理）：
  收到请求 → 读消息 → 调 LLM → 流式回复 → 响应完毕
  
后台线程：
  收到请求后触发 → 向量入库 → 摘要生成 → 附件处理
  → 礼物系统 → 这些不阻塞用户聊天
```

具体场景：

| 场景 | 谁做 | 为什么 |
|------|------|--------|
| 流式回复用户 | 主线程 | 用户需要立刻看到 |
| 新消息写入向量库 | 后台线程 | 不阻塞聊天 |
| 会话摘要触发 | 后台线程 | 可能调 LLM，耗时不定 |
| 附件下载和检查 | 后台线程 | 网络 IO 不确定 |
| 礼物资源生成 | 后台线程 | 可能很久 |

### 44.2 线程怎么创建

Akane 的后台任务里：

```python
thread = threading.Thread(
    target=self._worker_loop,
    name=f"akane-bg-{self.name}-{index + 1}",
    daemon=True,
)
thread.start()
```

直觉理解：

```text
Thread(target=某函数)    告诉线程：执行这个函数
daemon=True              主程序退出时，这个线程不阻止退出
.start()                 开始执行
```

锁用于保护共享状态：

```python
import threading

lock = threading.Lock()
count = 0

with lock:
    count += 1
```

Akane 有：

```python
self._attachment_inbox_write_lock = threading.Lock()
```

意思：

```text
附件写入相关流程需要排队，避免同时写出冲突数据
```

学习阶段先记住：

```text
多个线程同时改同一份数据时，要考虑锁
```

---

## 四十五、queue：后台任务队列

后台任务常见模型：

```text
主线程提交任务
任务进入队列
后台 worker 从队列取任务执行
```

最小例子：

```python
import queue
import threading
import time


tasks: queue.Queue[str | None] = queue.Queue()


def worker():
    while True:
        item = tasks.get()
        try:
            if item is None:
                return
            print("处理:", item)
            time.sleep(0.2)
        finally:
            tasks.task_done()


thread = threading.Thread(target=worker, daemon=True)
thread.start()

tasks.put("总结记忆")
tasks.put("处理附件")
tasks.join()

tasks.put(None)
thread.join()
```

Akane 的 `background_tasks.py` 就是这种思想的工程版本。

---

## 四十六、logging：工程里不要只靠 print

小脚本：

```python
print("出错了")
```

工程里更推荐：

```python
import logging

logger = logging.getLogger("akane.background_tasks")

try:
    1 / 0
except Exception:
    logger.exception("后台任务失败")
```

`logger.exception()` 会自动带上异常堆栈。

Akane 的后台任务使用：

```python
logger = logging.getLogger("akane.background_tasks")
```

日志的好处：

```text
可以分模块
可以分等级
可以写入文件
可以保留异常堆栈
```

等级：

```text
debug
info
warning
error
exception
```

---

## 四十七、常量：给固定值起名字

项目里经常有固定值：

```python
DESKTOP_PET_CONTRACT_VERSION = "v1"
```

常量通常用大写。

好处：

```text
避免魔法字符串散落各处
统一修改
表达含义
```

不推荐：

```python
if mode == "desktop_pet":
    ...
```

更推荐：

```python
DESKTOP_PET = "desktop_pet"

if mode == DESKTOP_PET:
    ...
```

如果常量很多，可以集中到一个 contract 或 config 文件。

---

## 四十八、函数名前面的下划线

Akane 里很多方法叫：

```python
_build_embedding_provider
_resolve_client_protocol_context
_stream_final_response
_normalize_tool_call
```

单下划线 `_` 的含义：

```text
这是内部 helper
外部尽量不要直接调用
```

它不是强制私有，只是约定。

工程阅读时：

```text
没有下划线的方法通常是对外能力
有下划线的方法通常是内部步骤
```

比如：

```python
process_turn_stream()
```

是主流程入口。

而：

```python
_stream_final_response()
```

是主流程内部的一个步骤。

---

## 四十九、关键字参数和 *

Akane 里很多函数这样写：

```python
def add_message(
    self,
    *,
    profile_user_id: str,
    session_id: str,
    role: str,
    content: str,
):
    ...
```

`*` 后面的参数必须用名字传。

正确：

```python
store.add_message(
    profile_user_id="user_a",
    session_id="session_a",
    role="user",
    content="你好",
)
```

错误：

```python
# store.add_message("user_a", "session_a", "user", "你好")
```

为什么工程里喜欢这样？

```text
参数多时不容易传错顺序
调用处更清楚
```

---

## 五十、返回 dict 还是对象

Akane 很多方法返回 `dict[str, Any]`。

例如 `store.add_message()` 返回：

```python
{
    "source_id": "...",
    "profile_user_id": "...",
    "session_id": "...",
    "seq_no": 1,
    "role": "user",
    "content": "你好",
}
```

为什么不是全部 dataclass？

因为这些数据经常要：

```text
转 JSON
写数据库
给前端
临时加字段
和 LLM payload 组合
```

dict 更灵活。

但工程里要注意：

```text
dict 灵活，也更容易写错 key
稳定结构可以逐渐迁移到 dataclass 或 Pydantic model
```

---

## 五十一、浅复制 dict

Akane 常见写法：

```python
ui_final_payload = dict(final_output)
```

这表示浅复制一个字典。

例子：

```python
data = {"speech": "你好"}
copy_data = dict(data)

copy_data["speech"] = "晚上好"

print(data["speech"])       # 你好
print(copy_data["speech"])  # 晚上好
```

为什么要复制？

```text
后面可能会给 final_output 加 debug 字段
但 UI 版本不一定要带完整 debug
```

注意：

```text
dict(data) 是浅复制
如果里面有嵌套 list/dict，嵌套对象仍然共享
```

---

## 五十二、列表推导式在工程里的用法

基础写法：

```python
numbers = [1, 2, 3]
squares = [x * x for x in numbers]
```

工程里经常用于提取字段：

```python
records = [
    {"source_id": "a"},
    {"source_id": "b"},
    {"source_id": ""},
]

source_ids = [
    str(item.get("source_id") or "").strip()
    for item in records
    if str(item.get("source_id") or "").strip()
]

print(source_ids)  # ['a', 'b']
```

Akane 的 `engine.py` 里类似：

```python
memory_exclude_source_ids = [
    str(hit.get("source_id") or "").strip()
    for hit in retrieval_result.get("fused_hits", [])
    if str(hit.get("source_id") or "").strip()
]
```

意思：

```text
从检索命中的结果里提取有效 source_id
空字符串不要
```

---

## 五十三、字符串规范化

工程里不要直接相信外部输入。

常见规范化：

```python
text = str(value or "").strip()
```

含义：

```text
None → ""
其他类型 → str
去掉首尾空白
```

Akane 里到处都能看到这种写法：

```python
user_message = str(payload.get("message") or "").strip()
session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
```

原因：

```text
HTTP payload 不可信
LLM 输出不稳定
前端可能漏字段
```

所以要在边界处做规范化。

---

## 五十四、正则 re

正则是文本模式匹配的工具。用特殊符号描述"什么样的文本算匹配"。

### 54.1 最小语法（5 分钟入门）

| 符号 | 含义 | 示例 |
|------|------|------|
| `.` | 任意一个字符 | `a.c` 匹配 `"abc"`、`"a.c"` |
| `*` | 前面字符重复 0 次或多次 | `ab*c` 匹配 `"ac"`、`"abbc"` |
| `+` | 前面字符重复 1 次或多次 | `ab+c` 匹配 `"abc"`、`"abbc"` |
| `[]` | 方括号内任意一个字符 | `[aeiou]` 匹配任一元音 |
| `[a-z]` | 字符范围 | `[0-9]` 匹配任意数字 |
| `()` | 分组 | `(ab)+` 匹配 `"ab"`、`"abab"` |
| `\d` | 数字（等价 `[0-9]`） | `\d+` 匹配 `"2026"` |
| `\w` | 字母/数字/下划线 | `\w+` 匹配 `"Akane_2026"` |
| `\s` | 空白字符（空格、tab、换行） | `a\s+b` 匹配 `"a b"` |
| `^` | 字符串开头 | `^Akane` 匹配以 Akane 开头的字符串 |
| `$` | 字符串结尾 | `end\$` 匹配以 end 结尾的字符串 |

最常用三个方法：

```python
import re

text = "Akane 2026 学 SQLite, Akane 2025 学 Python"

# findall: 找出所有匹配，返回列表
print(re.findall(r"\d+", text))         # ['2026', '2025']

# search: 找第一个匹配的位置
match = re.search(r"\d+", text)
print(match.group())                     # '2026'

# sub: 替换所有匹配
result = re.sub(r"\d+", "XXXX", text)
print(result)  # 'Akane XXXX 学 SQLite, Akane XXXX 学 Python'
```

### 54.2 compile：预编译模式

如果要多次使用同一个正则，`re.compile()` 比每次都写字符串效率更高：

```python
import re

digit_pattern = re.compile(r"\d+")

print(digit_pattern.findall("a1 b22 c333"))  # ['1', '22', '333']
print(digit_pattern.findall("x99 y00"))      # ['99', '00']
```

### 54.3 Akane 里的实际用法

Akane 的 `text_utils.py`：

```python
TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
```

它匹配：

```text
连续中文
英文 / 数字 / 下划线
```

最小例子：

```python
import re

pattern = re.compile(r"[A-Za-z0-9_]+")
text = "Akane 2026 学 SQLite"

print(pattern.findall(text))  # ['Akane', '2026', 'SQLite']
```

正则很有用，但别上来就写复杂表达式。

工程建议：

```text
简单文本处理先用 split、strip、replace
需要模式匹配时再用 re
复杂正则要加注释或拆成小函数
```

---

## 五十五、可运行综合 Demo：MiniCompanionEngine

这个小 Demo 把本章知识串起来：

```python
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Generator


@dataclass(frozen=True)
class Message:
    source_id: str
    role: str
    content: str
    tags: list[str] = field(default_factory=list)


class FileMessageStore:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / "messages.jsonl"

    def add_message(self, *, role: str, content: str, tags: list[str] | None = None) -> Message:
        message = Message(
            source_id=str(uuid.uuid4()),
            role=role,
            content=content,
            tags=tags or [],
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(message), ensure_ascii=False) + "\n")
        return message

    def list_messages(self) -> list[Message]:
        if not self.path.exists():
            return []
        messages: list[Message] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            messages.append(Message(**data))
        return messages


LogFn = Callable[[str], None]


class MiniCompanionEngine:
    def __init__(self, *, store: FileMessageStore, log: LogFn | None = None):
        self.store = store
        self.log = log or print

    def process_turn_stream(self, user_message: str) -> Generator[dict[str, Any], None, None]:
        user_record = self.store.add_message(
            role="user",
            content=user_message,
            tags=["user_input"],
        )
        self.log(f"saved user message: {user_record.source_id}")

        yield {"type": "stream_start", "timestamp": int(time.time())}

        reply = f"我收到啦：{user_message}"
        for char in reply:
            yield {"type": "speech_chunk", "text": char}

        assistant_record = self.store.add_message(
            role="assistant",
            content=reply,
            tags=["assistant_reply"],
        )
        self.log(f"saved assistant message: {assistant_record.source_id}")

        yield {
            "type": "final",
            "payload": {
                "speech": reply,
                "assistant_source_id": assistant_record.source_id,
            },
        }


if __name__ == "__main__":
    store = FileMessageStore("./tmp_mini_companion")
    engine = MiniCompanionEngine(store=store)

    for event in engine.process_turn_stream("今天学 Python 工程化"):
        print(event)

    print("历史消息：")
    for message in store.list_messages():
        print(message.role, message.content)
```

这个 Demo 对应 Akane：

| Demo | Akane |
|---|---|
| `Message` | 消息数据结构 |
| `FileMessageStore` | `MemoryStore` 的极简版 |
| `MiniCompanionEngine` | `AkaneMemoryEngine` 的极简版 |
| `process_turn_stream` | `engine.process_turn_stream` |
| `yield {"type": ...}` | Akane 的流式事件 |
| `LogFn` | `LogEvent = Callable[..., None]` |

---

## 五十六、Akane 源码阅读顺序

不要从工程入口一路跳到底。

先按“职责”读：

```text
1. app.py
   看项目怎么装配对象

2. routes/think.py
   看 /think 请求怎么进来，怎么变成流式响应

3. engine.py 的 process_turn_stream
   看一轮对话的主流程

4. store.py
   看消息和状态怎么落库

5. prompt_builder.py
   看 prompt 怎么拼

6. llm_runtime.py
   看模型怎么调用和流式解析
```

读每个文件时只问三个问题：

```text
这个文件负责什么？
它依赖谁？
它对外提供什么函数或类？
```

---

## 五十七、Akane 文件命名的阅读线索

看到文件名可以先猜职责：

```text
*_service.py      业务服务
*_engine.py       流程编排或核心逻辑
*_runtime.py      运行时封装
*_types.py        数据类型
*_builder.py      构建器
*_registry.py     注册表
routes/*.py       HTTP 接口
test_*.py         测试
```

比如：

```text
retrieval_service.py  记忆检索服务
retrieval_types.py    检索结果类型
prompt_builder.py     prompt 构建器
tool_runtime.py       工具运行时
```

这能帮助你快速定位。

---

## 五十八、知道即可：暂时不必深挖的 Python 高级特性

这些是 Python 高级知识，但 Akane 当前阶段优先级不高：

```text
元类 metaclass
描述符 descriptor
协程底层事件循环实现
装饰器源码级高级玩法
多进程 multiprocessing 深层调度
C 扩展
import 系统底层机制
```

不是没用，而是现在不用系统刷。

当前更重要的是：

```text
Path
typing
dataclass
contextmanager
yield
Service / Engine 拆分
配置管理
测试
```

---

## 五十九、常见坑

### 59.1 相对导入运行方式不对

如果直接运行包内部文件：

```powershell
python companion_v01/routes/think.py
```

相对导入可能失败。

工程里通常从项目入口运行：

```powershell
python launch_akane_memory_v01.py
```

或者：

```powershell
python -m unittest tests.test_store
```

### 59.2 可变默认参数

不推荐：

```python
def add_tag(tag, tags=[]):
    tags.append(tag)
    return tags
```

推荐：

```python
def add_tag(tag, tags=None):
    tags = list(tags or [])
    tags.append(tag)
    return tags
```

dataclass 里用：

```python
field(default_factory=list)
```

### 59.3 类型标注写了但运行时没检查

```python
def add(a: int, b: int) -> int:
    return a + b


print(add("1", "2"))  # 仍然能运行
```

类型标注主要给人、IDE、静态检查工具看。

### 59.4 生成器只能被消费一次

```python
def numbers():
    yield 1
    yield 2


gen = numbers()
print(list(gen))  # [1, 2]
print(list(gen))  # []
```

生成器被迭代完就空了。

### 59.5 Path 和 str 混用

```python
path = Path("data") / "a.txt"
```

需要字符串时：

```python
str(path)
```

不要手动拼：

```python
"data/" + filename
```

---

## 六十、这一章先记住

Akane 工程里最常见的 Python 进阶知识：

```text
Path              管理路径
typing            让函数和数据结构更清楚
dataclass         定义稳定数据对象
field             避免可变默认值坑
contextmanager    管理打开和关闭
yield             做流式输出
Callable          把函数作为依赖传入
Service           拆业务模块
Engine            编排主流程
config            集中配置
threading/queue   后台任务
logging           工程日志
```

看到 Akane 代码时，可以先把它翻译成：

```text
这个类持有哪些依赖？
这个函数输入什么？
这个函数输出什么？
它是在边界层、业务层、存储层，还是工具层？
```

---

## 六十一、我自己的复述

Python 工程化不是比基础语法更“玄”的东西，而是把代码从小脚本变成长期项目时必须出现的一套组织方法。

Akane 里那些看起来复杂的写法，本质上都能拆开：

```text
Path                 解决路径问题
typing               解决读代码时不知道类型的问题
dataclass            解决数据对象样板代码太多的问题
contextmanager       解决资源打开和关闭的问题
yield                解决流式输出的问题
Service / Engine     解决代码职责拆分的问题
config               解决配置散落的问题
```

学完这一章，再看 Akane 的代码，不要先问“这几千行我怎么看完”，而是先找：

```text
入口在哪里
依赖从哪里传入
数据对象长什么样
流式事件怎么 yield 出去
状态最终交给哪个 Service 或 Store
```

这样项目就会从一团代码，变成一张能导航的地图。

