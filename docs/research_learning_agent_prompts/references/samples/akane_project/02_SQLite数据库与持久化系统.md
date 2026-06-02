---
tags:
  - akane/sqlite
  - backend/database
  - python/sqlite3
  - persistence
created: 2026-05-20
---

# SQLite 数据库与持久化系统

> SQLite 是一个直接保存在本地文件里的轻量数据库。  
> 对 Akane 来说，它就是“记忆仓库”：会话、消息、摘要、礼物、附件、任务状态，最后都要落到一个 `.db` 文件里。

这份笔记的目标：

```text
先掌握数据库的通用脑子
再看懂 Akane 的 MemoryStore
最后能自己写一个最小记忆数据库
```

学习路线：

```text
为什么需要持久化
→ SQLite 是什么
→ 表、行、列、主键
→ SQL 基础：建表、增删查改
→ Python sqlite3
→ 事务、索引、约束
→ JSON 字段、状态字段、迁移
→ Akane 的 MemoryStore
→ SQLite 学完后怎么过渡到 MySQL
```

---

## 一、为什么需要数据库

程序里的普通变量只活在内存里。

```python
messages = []

messages.append("主人：今天学 SQLite")
print(messages)
```

程序一关，`messages` 就没了。

如果 Akane 每次重启都忘记之前聊过什么，那就不是“记忆系统”，只是一次性聊天窗口。

所以需要持久化：

```text
内存里的数据  →  写入硬盘  →  下次启动还能读回来
```

最简单可以写文本文件：

```python
from pathlib import Path

path = Path("memory.txt")
path.write_text("主人今天开始学 SQLite", encoding="utf-8")

content = path.read_text(encoding="utf-8")
print(content)
```

但文本文件很快会遇到问题：

```text
怎么按 session_id 找消息？
怎么只取最近 120 条？
怎么按时间排序？
怎么保证写一半不坏？
怎么给几万条消息加速查询？
```

这就是数据库要解决的事。

---

## 二、SQLite 是什么

SQLite 是一种嵌入式关系型数据库。

```text
SQLite = 一个数据库文件 + 一套 SQL 查询能力
```

它不像 MySQL 那样需要先启动一个数据库服务器。SQLite 的数据库通常就是一个文件：

```text
akane_memory_v01.db
notes.db
demo.db
```

Akane 项目里就是这样：

```python
# companion_v01/store.py
self.db_path = self.base_dir / "akane_memory_v01.db"
```

这行的意思：

```text
在 base_dir 目录下创建 / 使用 akane_memory_v01.db
```

SQLite 适合：

```text
本地应用
桌面软件
移动端 App
原型项目
个人知识库
测试环境
中小规模数据存储
```

Akane 是本地陪伴型项目，所以 SQLite 很合适。

---

## 三、数据库的核心模型

关系型数据库的核心是表。

```text
数据库 database
└── 表 table
    ├── 行 row
    └── 列 column
```

可以把表想成 Excel：

| source_id | session_id | seq_no | role | content |
|---|---|---:|---|---|
| msg_001 | session_a | 1 | user | 今天学 SQLite |
| msg_002 | session_a | 2 | assistant | 好，我们慢慢来 |

含义：

```text
表 table      → chat_messages
行 row        → 一条聊天消息
列 column     → 消息的属性
主键 primary key → 每行唯一 ID
```

Akane 的消息表叫：

```sql
chat_messages
```

它负责保存每一条原始对话消息。

---

## 四、SQLite 数据类型

SQLite 常用类型：

| 类型 | 含义 | Akane 里的例子 |
|---|---|---|
| `INTEGER` | 整数 | `timestamp`, `seq_no`, `is_summarized` |
| `REAL` | 小数 | `importance` |
| `TEXT` | 字符串 | `content`, `session_id`, `role` |
| `BLOB` | 二进制 | 图片、音频原始字节，Akane 主要存路径 |
| `NULL` | 空值 | 一般不用，Akane 多用默认值 |

Akane 里大量使用：

```sql
TEXT NOT NULL DEFAULT ''
INTEGER NOT NULL DEFAULT 0
TEXT NOT NULL DEFAULT '[]'
TEXT NOT NULL DEFAULT '{}'
```

这是一种工程习惯：

```text
尽量不要让字段是 NULL
用空字符串、0、[]、{} 表示默认状态
```

这样 Python 读取时更稳定。

---

## 五、最小 SQLite 示例

Python 内置了 `sqlite3`，不用额外安装。

```python
import sqlite3

conn = sqlite3.connect("demo.db")

conn.execute("""
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL
)
""")

conn.execute(
    "INSERT INTO notes (title, content) VALUES (?, ?)",
    ("SQLite", "今天开始学数据库")
)

rows = conn.execute("SELECT * FROM notes").fetchall()
print(rows)

conn.commit()
conn.close()
```

运行后，当前目录会多一个：

```text
demo.db
```

这就是数据库文件。

> **顺便记一个技巧**：学习和测试时，可以把 `connect("demo.db")` 换成 `connect(":memory:")`，这样数据库只在内存里，程序结束自动消失，不会遗留测试文件。后面很多示例都会用 `:memory:`。

---

## 六、连接数据库

最基础连接方式：

```python
import sqlite3

conn = sqlite3.connect("demo.db")
```

`conn` 可以理解为“数据库连接对象”。

常见操作：

```python
conn.execute(...)   # 执行 SQL
conn.commit()       # 提交修改
conn.close()        # 关闭连接
```

### 6.1 使用 with

```python
import sqlite3

with sqlite3.connect("demo.db") as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS logs (text TEXT)")
    conn.execute("INSERT INTO logs (text) VALUES (?)", ("hello",))
```

`with sqlite3.connect(...) as conn` 会在正常结束时提交事务，异常时回滚。

注意：标准库里的 `sqlite3.Connection` 作为上下文管理器时会处理提交 / 回滚，但不会自动关闭连接。大型项目里通常会自己封装。

---

## 七、Akane 的连接封装

### 7.0 前置：`@contextmanager` 和 `yield` 是什么

`with` 语句你应该已经会了（打开文件时常用）：

```python
with open("demo.txt", "w") as f:
    f.write("hello")
# 出了 with 块，文件自动关闭
```

`@contextmanager` 让你**自己写一个能用在 `with` 里的东西**。

核心机制：

```python
from contextlib import contextmanager

@contextmanager
def demo():
    print("进入 with 块")       # ← with 块之前执行
    yield "hello"              # ← 把 "hello" 交出去，暂停在这里
    print("离开 with 块")       # ← with 块之后执行

with demo() as value:
    print(f"with 块里收到: {value}")

# 输出：
# 进入 with 块
# with 块里收到: hello
# 离开 with 块
```

**`yield` 把执行切成两半**：

```text
yield 之前的代码  →  with 块开始前执行
yield 的值       →  交给 as 后面的变量
yield 之后的代码  →  with 块结束后执行（无论是否异常）
```

Akane 就是利用这个机制来保证"连接一定被关闭"：

```text
yield 之前: 打开连接
yield:      把连接交给 with 块使用
yield 之后: commit + close（一定执行）
```

### 7.1 Akane 的 `_connect()` 实现

```python
from contextlib import contextmanager
import sqlite3

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

这段代码的意思：

```text
1. 打开数据库连接
2. 设置 row_factory，让查询结果可以按列名读取
3. yield conn，把连接交给外面的 with 代码块
4. with 代码块正常结束后 commit
5. 最后 close
```

使用方式：

```python
with self._connect() as conn:
    rows = conn.execute("SELECT * FROM chat_messages").fetchall()
```

这里的 `yield conn` 可以理解为：

```text
先把 conn 借出去
等外面的 with 用完了
再回来执行 commit 和 close
```

---

## 八、row_factory：让查询结果更像字典

默认情况下，SQLite 查询结果像元组：

```python
import sqlite3

conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
conn.execute("INSERT INTO users VALUES (?, ?)", (1, "Akane"))

row = conn.execute("SELECT * FROM users").fetchone()
print(row[0])  # 1
print(row[1])  # Akane
```

设置 `row_factory` 后，可以按列名访问：

```python
import sqlite3

conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row

conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
conn.execute("INSERT INTO users VALUES (?, ?)", (1, "Akane"))

row = conn.execute("SELECT * FROM users").fetchone()
print(row["id"])    # 1
print(row["name"])  # Akane
```

Akane 这样做后，就可以写：

```python
int(row["seq_no"])
row["content"]
row["session_id"]
```

比 `row[0]`、`row[1]` 清楚很多。

---

## 九、建表 CREATE TABLE

建表语法：

```sql
CREATE TABLE IF NOT EXISTS 表名 (
    字段名 类型 约束,
    字段名 类型 约束
);
```

最小例子：

```python
import sqlite3

conn = sqlite3.connect(":memory:")

conn.execute("""
CREATE TABLE IF NOT EXISTS messages (
    source_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
)
""")
```

解释：

| 语法 | 含义 |
|---|---|
| `CREATE TABLE` | 创建表 |
| `IF NOT EXISTS` | 表不存在才创建 |
| `TEXT` | 字符串 |
| `INTEGER` | 整数 |
| `PRIMARY KEY` | 主键，唯一标识一行 |
| `NOT NULL` | 不允许为空 |

---

## 十、Akane 的 chat_messages 表

Akane 的原始消息表核心字段可以这样理解：

```sql
CREATE TABLE IF NOT EXISTS chat_messages (
    source_id TEXT PRIMARY KEY,
    profile_user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    seq_no INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    date_label TEXT NOT NULL,
    time_of_day TEXT NOT NULL,
    semantic_tags_json TEXT NOT NULL,
    index_in_vector INTEGER NOT NULL DEFAULT 1,
    is_summarized INTEGER NOT NULL DEFAULT 0,
    summary_id TEXT NOT NULL DEFAULT ''
);
```

字段解释：

| 字段 | 含义 |
|---|---|
| `source_id` | 每条消息唯一 ID |
| `profile_user_id` | 用户身份，比如 `master` |
| `session_id` | 会话 ID |
| `seq_no` | 会话内第几条消息 |
| `role` | `user` 或 `assistant` |
| `content` | 消息正文 |
| `timestamp` | 时间戳 |
| `date_label` | 日期标签 |
| `time_of_day` | 上午 / 下午 / 夜晚等时间段 |
| `semantic_tags_json` | 语义标签，JSON 字符串 |
| `index_in_vector` | 是否加入向量索引 |
| `is_summarized` | 是否已经被摘要 |
| `summary_id` | 属于哪个摘要 |

一句话理解：

```text
chat_messages 保存的是最原始、最细粒度的聊天记录。
```

---

## 十一、主键 PRIMARY KEY

主键用于唯一标识一行。

```sql
source_id TEXT PRIMARY KEY
```

这表示：

```text
每条消息必须有 source_id
每个 source_id 不能重复
可以通过 source_id 精确找到一条消息
```

Python 里常用 `uuid` 生成唯一 ID：

```python
import uuid

source_id = str(uuid.uuid4())
print(source_id)
```

Akane 的 `add_message()` 也是这样：

```python
"source_id": str(uuid.uuid4())
```

---

## 十二、插入数据 INSERT

SQL：

```sql
INSERT INTO messages (source_id, session_id, role, content, created_at)
VALUES (?, ?, ?, ?, ?)
```

Python 示例：

```python
import sqlite3
import time
import uuid

conn = sqlite3.connect(":memory:")

conn.execute("""
CREATE TABLE messages (
    source_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
)
""")

record = {
    "source_id": str(uuid.uuid4()),
    "session_id": "session_a",
    "role": "user",
    "content": "今天学 SQLite",
    "created_at": int(time.time()),
}

conn.execute(
    """
    INSERT INTO messages (source_id, session_id, role, content, created_at)
    VALUES (?, ?, ?, ?, ?)
    """,
    (
        record["source_id"],
        record["session_id"],
        record["role"],
        record["content"],
        record["created_at"],
    ),
)

conn.commit()
```

注意 `?`：

```text
? 是参数占位符
真正的数据放在后面的 tuple 里
```

### 12.1 批量插入：`executemany()`

单条插入用 `execute()`。多条插入用 `executemany()`：

```python
messages = [
    ("s1", "user", "你好"),
    ("s1", "assistant", "你好呀"),
    ("s2", "user", "今天学习"),
]

conn.executemany(
    "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
    messages,
)
conn.commit()
```

对比：

```text
execute(sql, (a, b, c))        → 插入 1 条
executemany(sql, [(a1,b1,c1), (a2,b2,c2), ...])  → 插入 N 条
```

适合场景：

```text
初始化测试数据
批量导入
Akane 测试里一次插入多条消息
```

后面 GROUP BY 那一节的示例会用到它。

---

## 十三、不要用 f-string 拼用户输入

错误写法：

```python
name = "Akane"
sql = f"SELECT * FROM users WHERE name = '{name}'"
```

看起来方便，但如果 `name` 来自用户输入，就可能有 SQL 注入风险。

推荐写法：

```python
name = "Akane"
row = conn.execute(
    "SELECT * FROM users WHERE name = ?",
    (name,),
).fetchone()
```

重点记：

```text
值用 ?
表名 / 字段名一般不要来自用户输入
```

Akane 的业务查询基本都是这种写法：

```python
conn.execute(
    """
    SELECT * FROM chat_sessions
    WHERE session_id = ? AND profile_user_id = ?
    LIMIT 1
    """,
    (normalized_session_id, normalized_profile_user_id),
)
```

---

## 十四、查询数据 SELECT

查询全部：

```sql
SELECT * FROM messages;
```

带条件：

```sql
SELECT * FROM messages
WHERE session_id = ?;
```

排序：

```sql
SELECT * FROM messages
ORDER BY created_at DESC;
```

限制数量：

```sql
SELECT * FROM messages
LIMIT 10;
```

Python 示例：

```python
rows = conn.execute(
    """
    SELECT * FROM messages
    WHERE session_id = ?
    ORDER BY created_at ASC
    LIMIT ?
    """,
    ("session_a", 20),
).fetchall()

for row in rows:
    print(row)
```

---

## 十五、Akane：读取最近 N 条消息

Akane 的 `get_session_messages()` 做了一件很典型的事：

```python
def get_session_messages(self, *, profile_user_id: str, session_id: str, limit: int = 120):
    with self._connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM chat_messages
                WHERE profile_user_id = ? AND session_id = ?
                ORDER BY seq_no DESC
                LIMIT ?
            )
            ORDER BY seq_no ASC
            """,
            (str(profile_user_id), str(session_id), max(1, int(limit))),
        ).fetchall()
    return [self._row_to_message(dict(row)) for row in rows]
```

它为什么要嵌套查询？

```text
内层：
    ORDER BY seq_no DESC
    LIMIT 120
    先拿最近 120 条

外层：
    ORDER BY seq_no ASC
    再恢复成从旧到新的阅读顺序
```

举例：

```text
数据库里有 1, 2, 3, 4, 5
limit = 3

内层取：5, 4, 3
外层排：3, 4, 5
```

这正是聊天窗口需要的顺序。

---

## 十六、fetchone 和 fetchall

`fetchone()`：取一行。

```python
row = conn.execute(
    "SELECT * FROM users WHERE id = ?",
    (1,),
).fetchone()
```

适合：

```text
按主键查一条
查 COUNT
查 MAX
查配置项
```

`fetchall()`：取所有结果。

```python
rows = conn.execute(
    "SELECT * FROM messages WHERE session_id = ?",
    ("session_a",),
).fetchall()
```

适合：

```text
查列表
查最近消息
查所有待处理任务
```

---

## 十七、更新数据 UPDATE

SQL：

```sql
UPDATE messages
SET content = ?
WHERE source_id = ?;
```

Python 示例：

```python
conn.execute(
    """
    UPDATE messages
    SET content = ?
    WHERE source_id = ?
    """,
    ("修改后的内容", source_id),
)
conn.commit()
```

Akane 里更新语义标签：

```python
def update_message_semantic_tags(self, source_id: str, semantic_tags: list[str]) -> None:
    with self._connect() as conn:
        conn.execute(
            """
            UPDATE chat_messages
            SET semantic_tags_json = ?
            WHERE source_id = ?
            """,
            (
                json.dumps(semantic_tags or [], ensure_ascii=False),
                str(source_id),
            ),
        )
```

注意：

```text
UPDATE 一定要小心 WHERE
没有 WHERE 就可能更新整张表
```

---

## 十八、删除数据 DELETE

SQL：

```sql
DELETE FROM messages
WHERE source_id = ?;
```

Python 示例：

```python
conn.execute(
    "DELETE FROM messages WHERE source_id = ?",
    (source_id,),
)
conn.commit()
```

注意：

```text
DELETE 也一定要小心 WHERE
没有 WHERE 就会删整张表
```

很多工程里不会真的删除，而是使用状态字段：

```text
status = 'deleted'
status = 'cleared'
status = 'archived'
```

这样可以保留历史记录。

---

## 十九、索引 INDEX

索引是给查询加速用的。

没有索引时，数据库可能要从第一行扫到最后一行。

有索引后，数据库可以更快定位。

创建索引：

```sql
CREATE INDEX IF NOT EXISTS idx_chat_session_seq
ON chat_messages(session_id, seq_no);
```

Akane 为什么给 `(session_id, seq_no)` 建索引？

因为它经常这样查：

```sql
SELECT * FROM chat_messages
WHERE session_id = ?
ORDER BY seq_no ASC;
```

索引和查询模式要匹配。

再看一个 Akane 索引：

```sql
CREATE INDEX IF NOT EXISTS idx_chat_profile_time
ON chat_messages(profile_user_id, timestamp);
```

它适合：

```text
按用户查
按时间范围查
按时间排序
```

重点记：

```text
索引不是越多越好
索引能加速查询
但会让写入和更新稍微变慢
```

---

## 二十、聚合函数：COUNT、MAX、AVG

聚合函数用于统计。

```sql
SELECT COUNT(*) FROM messages;
SELECT MAX(seq_no) FROM messages;
SELECT AVG(score) FROM evals;
```

Akane 的 `next_seq_no()`：

```python
def next_seq_no(self, session_id: str) -> int:
    with self._connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq_no), 0) AS max_seq FROM chat_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["max_seq"]) + 1
```

解释：

```text
MAX(seq_no)              找到当前会话最大的消息序号
COALESCE(MAX(...), 0)    如果还没有消息，就当成 0
+ 1                     下一条消息序号
```

例子：

```text
已有 seq_no: 1, 2, 3
MAX(seq_no) = 3
下一条 = 4
```

---

## 二十一、GROUP BY：分组统计

`GROUP BY` 用于“按某个字段分组统计”。

例子：统计每个会话有多少条消息。

```python
import sqlite3

conn = sqlite3.connect(":memory:")

conn.execute("""
CREATE TABLE messages (
    session_id TEXT,
    role TEXT,
    content TEXT
)
""")

conn.executemany(
    "INSERT INTO messages VALUES (?, ?, ?)",
    [
        ("s1", "user", "你好"),
        ("s1", "assistant", "你好呀"),
        ("s2", "user", "今天学习"),
    ],
)

rows = conn.execute("""
SELECT session_id, COUNT(*) AS message_count
FROM messages
GROUP BY session_id
""").fetchall()

print(rows)  # [('s1', 2), ('s2', 1)]
```

常见用途：

```text
每个用户多少条消息
每个状态多少个任务
每天多少次对话
每种类型多少个附件
```

---

## 二十二、JOIN：多表关联

真实系统通常不只一张表。

比如：

```text
chat_sessions    会话表
chat_messages    消息表
```

一个会话有多条消息。

最小例子：

```python
import sqlite3

conn = sqlite3.connect(":memory:")

conn.execute("""
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL
)
""")

conn.execute("""
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    content TEXT NOT NULL
)
""")

conn.execute("INSERT INTO sessions VALUES (?, ?)", ("s1", "SQLite 学习"))
conn.execute("INSERT INTO messages VALUES (?, ?, ?)", ("m1", "s1", "今天学 JOIN"))

rows = conn.execute("""
SELECT
    sessions.title,
    messages.content
FROM messages
JOIN sessions
ON messages.session_id = sessions.session_id
""").fetchall()

print(rows)  # [('SQLite 学习', '今天学 JOIN')]
```

一句话理解：

```text
JOIN 用于把多张表按某个共同字段拼起来查。
```

Akane 当前很多地方选择“分开查 + Python 组装”，但 JOIN 是关系型数据库的经典能力，后面学 MySQL 也会高频遇到。

---

## 二十三、JSON 字段

SQLite 没有像 Python 那样直接存 list / dict。

所以常见做法是：

```text
Python list/dict
→ json.dumps()
→ 存成 TEXT
→ 读出来后 json.loads()
```

例子：

```python
import json
import sqlite3

conn = sqlite3.connect(":memory:")

conn.execute("""
CREATE TABLE messages (
    content TEXT NOT NULL,
    tags_json TEXT NOT NULL
)
""")

tags = ["学习", "SQLite", "Akane"]

conn.execute(
    "INSERT INTO messages VALUES (?, ?)",
    ("今天整理数据库笔记", json.dumps(tags, ensure_ascii=False)),
)

row = conn.execute("SELECT * FROM messages").fetchone()
loaded_tags = json.loads(row[1])

print(loaded_tags)  # ['学习', 'SQLite', 'Akane']
```

Akane 的 `add_message()`：

```python
"semantic_tags_json": json.dumps(semantic_tags or [], ensure_ascii=False)
```

Akane 的 `_row_to_message()`：

```python
"semantic_tags": json.loads(row.get("semantic_tags_json") or "[]")
```

为什么字段名后面带 `_json`？

```text
提醒自己：数据库里存的是 JSON 字符串
不是 Python 原生 list/dict
```

---

## 二十四、什么时候用 JSON 字段，什么时候拆表

适合 JSON 字段：

```text
小型列表
结构不稳定
不经常按内部字段查询
只是读出来给 Python 用
```

例如：

```text
semantic_tags_json
source_ids_json
artifact_flags_json
content_card_json
```

适合拆成新表：

```text
数据量大
需要频繁查询内部字段
需要建立索引
需要和其他表关联
需要单独更新其中一项
```

例子：

```text
如果以后要经常查“所有带 学习 标签的消息”
就可以考虑建 message_tags 表
```

工程判断：

```text
先用 JSON 字段简化系统
等查询需求变强，再拆表
```

---

## 二十五、把数据库行转换成业务对象

数据库读出来的是 row。

业务层更喜欢 dict。

Akane 用 `_row_to_message()` 统一转换：

```python
def _row_to_message(self, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "profile_user_id": row["profile_user_id"],
        "session_id": row["session_id"],
        "seq_no": int(row["seq_no"]),
        "role": row["role"],
        "content": row["content"],
        "timestamp": int(row["timestamp"]),
        "semantic_tags": json.loads(row.get("semantic_tags_json") or "[]"),
        "index_in_vector": bool(int(row.get("index_in_vector", 1) or 0)),
        "entry_type": "raw",
    }
```

这个函数做了几件事：

```text
1. 字段名统一
2. 类型转换：seq_no → int
3. JSON 反序列化：semantic_tags_json → semantic_tags
4. 整数转布尔：index_in_vector → True / False
5. 补充业务字段：entry_type = "raw"
```

这种转换函数非常重要。

它让系统其他地方不用关心数据库细节。

---

## 二十六、事务 transaction

事务就是：

```text
一组数据库操作，要么全部成功，要么全部失败。
```

例子：转账。

```text
A 扣 100
B 加 100
```

这两步必须一起成功。不能 A 扣了，B 没加。

SQLite 示例：

```python
import sqlite3

conn = sqlite3.connect(":memory:")

conn.execute("CREATE TABLE accounts (name TEXT PRIMARY KEY, balance INTEGER)")
conn.execute("INSERT INTO accounts VALUES (?, ?)", ("A", 500))
conn.execute("INSERT INTO accounts VALUES (?, ?)", ("B", 100))

try:
    conn.execute("UPDATE accounts SET balance = balance - ? WHERE name = ?", (100, "A"))
    conn.execute("UPDATE accounts SET balance = balance + ? WHERE name = ?", (100, "B"))
    conn.commit()
except Exception:
    conn.rollback()
    raise

print(conn.execute("SELECT * FROM accounts").fetchall())
```

Akane 的 `_connect()` 在 `with` 正常结束后统一 `commit()`：

```python
with self._connect() as conn:
    conn.execute(...)
```

也就是说：

```text
一个 with 块里的数据库写操作，最后统一提交。
```

---

## 二十七、数据库迁移：表结构变了怎么办

项目开发过程中，经常会新增字段。

比如一开始的表没有：

```text
index_in_vector
```

后面 RAG 需要区分“是否进向量库”，就要加字段。

SQL：

```sql
ALTER TABLE chat_messages
ADD COLUMN index_in_vector INTEGER NOT NULL DEFAULT 1;
```

Akane 封装了 `_ensure_column()`：

```python
def _ensure_column(self, *, conn, table_name, column_name, column_definition):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing_columns = {str(row["name"]) for row in rows}
    if column_name in existing_columns:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
```

解释：

```text
PRAGMA table_info(table_name)  查看当前表有哪些字段
如果字段已存在               什么都不做
如果字段不存在               ALTER TABLE 添加字段
```

这就是轻量级 migration。

注意：

```text
这里的 table_name / column_name 是项目内部固定传入的
不要直接拿用户输入拼进 f-string
```

---

## 二十八、约束 constraints

约束用于保护数据质量。

常见约束：

| 约束 | 含义 |
|---|---|
| `PRIMARY KEY` | 主键，唯一标识 |
| `NOT NULL` | 不允许为空 |
| `DEFAULT` | 默认值 |
| `UNIQUE` | 不允许重复 |
| `CHECK` | 检查条件 |
| `FOREIGN KEY` | 外键，关联另一张表 |

示例：

```sql
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    CHECK (status IN ('pending', 'running', 'done', 'failed'))
);
```

`CHECK` 的作用：

```text
防止 status 被写成奇怪的值
```

Akane 目前更多是在 Python 里做状态归一化，比如：

```python
def _normalize_generated_status(self, value):
    ...
```

这也是常见工程选择：

```text
数据库负责基础约束
Python 负责业务规则
```

---

## 二十九、状态字段：业务系统的骨架

Akane 里大量表都有 `status` 字段。

例如：

```text
reminders.status
attachment_inbox_items.status
generated_files.status
task_workspaces.status
desktop_music_timelines.status
persona_cards.status
```

状态字段的作用：

```text
告诉系统：这条记录现在处在哪个阶段。
```

比如附件：

```text
pending_observation → ready → cleared
                 ↘ failed
```

比如任务：

```text
queued → running → completed
              ↘ failed
              ↘ waiting_user
```

状态机比单纯的布尔值更强：

```text
is_done = True / False        太粗
status = queued/running/...   更清楚
```

---

## 三十、排序 ORDER BY

升序：

```sql
ORDER BY seq_no ASC
```

降序：

```sql
ORDER BY updated_at DESC
```

多字段排序：

```sql
ORDER BY updated_at DESC, created_at DESC, session_id DESC
```

Akane 的会话列表：

```sql
SELECT * FROM chat_sessions
WHERE profile_user_id = ?
ORDER BY updated_at DESC, created_at DESC, session_id DESC
LIMIT ?
```

含义：

```text
先看最近更新
更新时间一样再看创建时间
还一样就用 session_id 保持稳定顺序
```

---

## 三十一、分页 LIMIT / OFFSET

取前 10 条：

```sql
SELECT * FROM messages
LIMIT 10;
```

跳过 10 条，再取 10 条：

```sql
SELECT * FROM messages
LIMIT 10 OFFSET 10;
```

适合：

```text
后台管理列表
历史记录翻页
附件列表分页
```

但聊天场景经常不用 OFFSET，而是：

```text
按 seq_no 或 timestamp 找某个窗口
```

因为 OFFSET 很大时可能变慢。

---

## 三十二、LIKE：模糊搜索

```sql
SELECT * FROM messages
WHERE content LIKE ?;
```

Python：

```python
keyword = "SQLite"
rows = conn.execute(
    "SELECT * FROM messages WHERE content LIKE ?",
    (f"%{keyword}%",),
).fetchall()
```

含义：

```text
%SQLite%  → 内容中包含 SQLite
SQLite%   → 以 SQLite 开头
%SQLite   → 以 SQLite 结尾
```

Akane 的语义检索主要靠 embedding 和 vector store，但普通关键词搜索仍然是经典能力。

---

## 三十三、内存数据库 :memory:

SQLite 可以创建内存数据库：

```python
conn = sqlite3.connect(":memory:")
```

特点：

```text
不落盘
速度快
程序结束就消失
适合教学 Demo 和单元测试
```

但 Akane 的测试更常用临时目录：

```python
with tempfile.TemporaryDirectory() as temp_dir:
    store = MemoryStore(Path(temp_dir))
```

这样更接近真实情况：

```text
MemoryStore 仍然会创建 akane_memory_v01.db
但测试结束后临时目录自动删除
```

---

## 三十四、单元测试里的数据库

Akane 的 `tests/test_store.py` 里有一个很好的例子：

```python
def test_get_session_messages_returns_most_recent_window(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = MemoryStore(Path(temp_dir))
        for index in range(1, 6):
            store.add_message(
                profile_user_id="user_a",
                session_id="session_a",
                role="user" if index % 2 else "assistant",
                content=f"message-{index}",
                timestamp=100 + index,
            )

        recent_messages = store.get_session_messages(
            profile_user_id="user_a",
            session_id="session_a",
            limit=3,
        )

        self.assertEqual([item["seq_no"] for item in recent_messages], [3, 4, 5])
        self.assertEqual([item["content"] for item in recent_messages], ["message-3", "message-4", "message-5"])
```

这个测试验证：

```text
写入 5 条消息
只读取最近 3 条
返回顺序是 3, 4, 5
内容也对应正确
```

这比单看代码更容易理解 `get_session_messages()`。

---

## 三十五、并发与锁

SQLite 的并发特点：

```text
可以多个读
同一时间通常只有一个写
```

对于本地桌面应用，这通常够用。

但如果多个线程同时写数据库，就要注意冲突。

Akane 里有这样的锁：

```python
self._attachment_inbox_write_lock = threading.Lock()
```

它的意图是：

```text
某些写入流程需要排队，避免同时生成重复 handle 或写出冲突状态。
```

SQLite 可以开启 WAL 模式提升读写体验：

```python
conn.execute("PRAGMA journal_mode=WAL")
```

不过学习阶段先不用急着加。

先记住：

```text
SQLite 很适合本地应用
但不是高并发大型服务的首选
```

---

## 三十六、一个最小 MemoryStoreLite

下面这个 Demo 把 Akane 的核心思路缩小成一个可运行版本：

```python
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class MemoryStoreLite:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "memory_lite.db"
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                source_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                seq_no INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL
            )
            """)
            conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session_seq
            ON messages(session_id, seq_no)
            """)

    def next_seq_no(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq_no), 0) AS max_seq FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["max_seq"]) + 1

    def add_message(self, *, session_id: str, role: str, content: str, tags: list[str] | None = None) -> dict:
        record = {
            "source_id": str(uuid.uuid4()),
            "session_id": session_id,
            "seq_no": self.next_seq_no(session_id),
            "role": role,
            "content": content,
            "tags_json": json.dumps(tags or [], ensure_ascii=False),
            "created_at": int(time.time()),
        }

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (
                    source_id, session_id, seq_no, role, content, tags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["source_id"],
                    record["session_id"],
                    record["seq_no"],
                    record["role"],
                    record["content"],
                    record["tags_json"],
                    record["created_at"],
                ),
            )

        return self._row_to_message(record)

    def list_recent_messages(self, *, session_id: str, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE session_id = ?
                    ORDER BY seq_no DESC
                    LIMIT ?
                )
                ORDER BY seq_no ASC
                """,
                (session_id, limit),
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]

    def _row_to_message(self, row: dict) -> dict:
        return {
            "source_id": row["source_id"],
            "session_id": row["session_id"],
            "seq_no": int(row["seq_no"]),
            "role": row["role"],
            "content": row["content"],
            "tags": json.loads(row.get("tags_json") or "[]"),
            "created_at": int(row["created_at"]),
        }


if __name__ == "__main__":
    store = MemoryStoreLite("./tmp_memory_lite")

    store.add_message(
        session_id="study",
        role="user",
        content="今天开始学 SQLite",
        tags=["学习", "数据库"],
    )
    store.add_message(
        session_id="study",
        role="assistant",
        content="好，我们从持久化开始。",
        tags=["回复"],
    )

    messages = store.list_recent_messages(session_id="study", limit=5)
    for item in messages:
        print(item["seq_no"], item["role"], item["content"], item["tags"])
```

这个小 Demo 对应 Akane 的核心结构：

| Demo | Akane |
|---|---|
| `MemoryStoreLite` | `MemoryStore` |
| `messages` | `chat_messages` |
| `add_message()` | `add_message()` |
| `list_recent_messages()` | `get_session_messages()` |
| `tags_json` | `semantic_tags_json` |
| `_row_to_message()` | `_row_to_message()` |

---

## 三十七、SQLite 学完后，学 MySQL 好上手吗

很好上手。

因为最核心的数据库知识是通用的：

```text
表
行
列
主键
索引
SQL 查询
增删查改
事务
约束
数据建模
```

SQLite 和 MySQL 的区别主要在工程形态上：

| 对比 | SQLite | MySQL |
|---|---|---|
| 部署方式 | 一个本地 `.db` 文件 | 一个数据库服务器 |
| 连接方式 | 直接打开文件 | 通过网络连接服务 |
| 并发能力 | 适合本地和中小并发 | 适合多用户高并发 |
| 权限系统 | 很弱 | 用户、密码、权限完整 |
| 运维复杂度 | 低 | 较高 |
| 典型场景 | 桌面应用、本地工具、测试 | 网站后端、业务系统、生产服务 |

可以这样理解：

```text
SQLite 让你学会数据库思维
MySQL 让你学会数据库服务化和生产化
```

先学 SQLite 不是绕路。

对 Akane 来说，SQLite 甚至是当前更重要的数据库。

---

## 三十八、知道即可：视图、触发器、外键

这些是经典数据库知识，但 Akane 当前阶段不必优先深挖。

### 视图 VIEW

视图像“保存好的查询”：

```sql
CREATE VIEW recent_messages AS
SELECT * FROM messages
ORDER BY created_at DESC;
```

知道即可。

### 触发器 TRIGGER

触发器是在插入 / 更新 / 删除时自动执行的 SQL：

```sql
CREATE TRIGGER ...
```

强大，但容易让逻辑变隐蔽。应用项目里通常先用 Python 显式处理。

### 外键 FOREIGN KEY

外键用于保证表之间的引用关系。

```sql
FOREIGN KEY(session_id) REFERENCES sessions(session_id)
```

Akane 目前主要靠业务层维护关系。后面如果数据库模型更严格，可以再系统整理。

---

## 三十九、常见坑

### 39.1 忘记 commit

```python
conn.execute("INSERT INTO notes VALUES (?)", ("hello",))
# 忘记 conn.commit()
```

结果可能没有真正保存。

Akane 用 `_connect()` 统一提交，减少这个问题。

### 39.2 UPDATE / DELETE 忘记 WHERE

```sql
DELETE FROM messages;
```

这会删除整张表。

### 39.3 用字符串拼 SQL 参数

```python
sql = f"SELECT * FROM users WHERE name = '{name}'"
```

用 `?` 占位符。

### 39.4 JSON 字段忘记 loads

数据库里是字符串：

```text
"[\"学习\", \"SQLite\"]"
```

Python 里需要：

```python
json.loads(tags_json)
```

### 39.5 布尔值实际存成整数

SQLite 没有真正独立的 bool 类型。

常见写法：

```text
0 → False
1 → True
```

Akane：

```python
"index_in_vector": bool(int(row.get("index_in_vector", 1) or 0))
```

---

## 四十、回到 Akane：读 store.py 的顺序

不要从头到尾硬读 `store.py`。

先按这个顺序读：

```text
1. __init__
2. _connect
3. _init_db 里的 chat_messages 和 chat_sessions
4. ensure_session
5. next_seq_no
6. add_message
7. get_session_messages
8. _row_to_message
9. tests/test_store.py 里对应测试
```

读懂这 9 个点，就等于读懂了：

```text
Akane 如何创建数据库
Akane 如何创建会话
Akane 如何写入一条消息
Akane 如何读取最近消息
Akane 如何把数据库 row 变成业务 dict
```

这就是 `MemoryStore` 的第一根骨架。

---

## 四十一、这一章先记住

最核心模板：

```python
import sqlite3

conn = sqlite3.connect("demo.db")
conn.row_factory = sqlite3.Row

conn.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL
)
""")

conn.execute(
    "INSERT INTO messages (id, content) VALUES (?, ?)",
    ("m1", "hello"),
)

row = conn.execute(
    "SELECT * FROM messages WHERE id = ?",
    ("m1",),
).fetchone()

print(row["content"])

conn.commit()
conn.close()
```

Akane 里的本质也是这个：

```text
connect
→ create table
→ insert
→ select
→ row to dict
→ commit / close
```

---

## 四十二、我自己的复述

SQLite 不是一个很遥远的数据库服务器，而是一个能直接放在项目目录里的 `.db` 文件。  
Akane 的 `MemoryStore` 做的事情，就是把“会话、消息、摘要、附件、礼物、任务状态”这些 Python 对象，转换成一张张 SQLite 表里的行。

`store.py` 看起来很长，但底层动作并不神秘：

```text
建表
插入
查询
更新
状态归一化
JSON 转换
row 转 dict
```

学会 SQLite 之后，再看 `store.py`，就不是 4000 多行陌生代码，而是一套围绕 Akane 记忆系统展开的数据库访问层。

