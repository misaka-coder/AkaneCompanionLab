---
tags:
  - akane/rag
  - llm/retrieval
  - vector-search
  - embedding
  - memory-system
created: 2026-05-23
---

# RAG 与向量检索

> RAG 的核心不是“让模型变聪明”。  
> 它的核心是：在模型回答前，把它需要的外部资料找出来，塞进上下文里。

Akane 的记忆系统就是一个典型 RAG 系统。

用户问：

```text
你还记得我之前说过那个项目叫什么吗？
```

模型本身不一定知道，因为模型没有自动读取本地数据库。

所以 Akane 要先做一件事：

```text
从历史消息、阶段摘要、长期语义记忆里找相关片段。
```

找到之后，再把这些片段交给最终回复模型：

```text
可用回忆片段：
【原始对话回忆】
...

用户原始消息：
你还记得我之前说过那个项目叫什么吗？
```

这就是 RAG。

---

## 一、这一篇学什么

这一篇接在第 07 篇后面。

第 07 篇讲的是：

```text
怎么把一次 LLM 调用封装成稳定的工程接口
```

这一篇讲的是：

```text
怎么在调用 LLM 之前，找出它需要的外部记忆
```

学习路线：

```text
RAG 是什么
-> 为什么 LLM 需要检索
-> embedding 是什么
-> 向量相似度是什么
-> Akane 如何把记忆写入向量库
-> ChromaDB 在项目里扮演什么角色
-> 语义检索和关键词检索有什么区别
-> BM25 是什么
-> RRF 融合是什么
-> router 为什么要判断是否检索
-> verifier 为什么要二次校验
-> 检索片段如何进入最终 prompt
-> 摘要记忆和长期语义记忆如何形成
-> 这一套系统怎么测试
```

先给结论：

```text
RAG 是 AI 应用工程里的高优先级知识。
```

它不是冷门细节。

现在大量 LLM 产品都会用到：

```text
知识库问答
长期记忆
文档问答
代码库问答
客服系统
企业搜索
个人知识库助手
Agent 工具前置检索
```

所以这一篇值得认真学。

---

## 二、RAG 是什么

RAG 全称是：

```text
Retrieval-Augmented Generation
```

中文通常叫：

```text
检索增强生成
```

拆开看：

```text
Retrieval：检索，把相关资料找出来
Augmented：增强，把资料放进上下文
Generation：生成，让模型基于资料回答
```

最朴素的流程：

```text
用户问题
-> 检索知识库
-> 拿到相关片段
-> 把片段和问题一起交给 LLM
-> LLM 基于片段回答
```

用伪代码表示：

```python
def rag_answer(question: str) -> str:
    docs = search_knowledge_base(question)
    prompt = build_prompt(question, docs)
    answer = call_llm(prompt)
    return answer
```

RAG 的本质是：

```text
不要要求模型凭空记住所有东西。
需要什么，就先查什么。
```

---

## 三、为什么 LLM 需要 RAG

LLM 很强，但它有几个天然限制。

### 1. 模型不知道你的本地数据

比如你的 Akane 数据库里有：

```text
2026-04-12 晚上，用户说自己在学 MiniMind。
```

模型参数里不会自动有这条记录。

你不把它放进 prompt，模型就不知道。

### 2. 上下文窗口有限

哪怕模型支持很长上下文，也不可能每次把所有历史消息都塞进去。

假设有：

```text
100000 条聊天记录
```

每次都塞给模型会导致：

```text
慢
贵
容易干扰
超过上下文限制
```

所以要先检索。

### 3. 模型会编

如果用户问历史事实，而模型没拿到记忆，它可能会“感觉像是这样”。

这就是幻觉。

RAG 的作用是：

```text
让模型回答前先看到证据。
```

### 4. 资料会更新

模型训练完以后，参数不会自动更新。

但数据库、笔记、项目文件每天都在变。

RAG 可以直接查最新外部数据。

一句话：

```text
模型负责语言理解和生成。
检索系统负责把正确材料找出来。
```

---

## 四、Akane 的 RAG 主链路

Akane 里和 RAG 相关的核心文件：

```text
companion_v01/embedding_provider.py
companion_v01/huggingface_provider.py
companion_v01/vector_store.py
companion_v01/vector_entry_builder.py
companion_v01/retrieval_service.py
companion_v01/retrieval_engine.py
companion_v01/memory_compaction_service.py
companion_v01/memory_rendering.py
companion_v01/store.py
companion_v01/engine.py
```

简化主线：

```text
用户消息进入 engine.py
-> 写入 SQLite
-> 提取 semantic_tags
-> 需要时写入向量库
-> 后台把原始消息压缩成阶段摘要
-> 再把阶段摘要压缩成长期语义记忆

新一轮用户提问
-> router 判断是否需要检索
-> 改写搜索 query
-> 生成 keywords 和 time_hint
-> 向量检索 + 关键词检索
-> RRF 融合
-> rerank
-> 构造 memory_snippets
-> verifier 判断片段是否真的能回答
-> confirmed_snippets 放入最终回复 prompt
-> LLM 生成回答
```

画成结构：

```text
写入侧：
raw message -> vector entry
raw messages -> episodic summary -> vector entry
episodic summaries -> semantic summary -> vector entry

读取侧：
user query -> router -> vector/keyword search -> verifier -> final prompt
```

RAG 系统一定要分清这两侧：

```text
Indexing：资料怎么入库
Retrieval：问题来了怎么查
```

---

## 五、RAG 的三层记忆

Akane 不是只存一种记忆。

它大致有三层：

```text
1. 原始消息 raw message
2. 阶段摘要 episodic summary
3. 长期语义记忆 semantic summary
```

### 1. 原始消息

就是用户和 Akane 的每条聊天：

```text
user: 我最近在学 MiniMind
assistant: 好呀，我们可以慢慢拆。
```

优点：

```text
细节最完整
```

缺点：

```text
数量最多，噪声也最多
```

### 2. 阶段摘要

把一批原始消息压缩成一段阶段记录。

比如：

```text
用户这段时间在学习 MiniMind，并且已经掌握了 PyTorch 基础。
```

优点：

```text
更短，更像事件记录
```

缺点：

```text
可能丢掉细节
```

### 3. 长期语义记忆

再把多个阶段摘要压缩成稳定事实、反复话题、待续线索。

比如：

```text
用户主攻软件方向，正在沿着 MiniMind 和 Akane 项目两条线学习 AI 工程。
```

优点：

```text
更适合长期记住用户画像、偏好、持续目标
```

缺点：

```text
不适合还原某一句原话
```

所以它们不是互相替代，而是互补。

```text
问“我当时原话怎么说”：更依赖 raw
问“我最近一直在学什么”：summary / semantic summary 更好
```

---

## 六、embedding 是什么

embedding 就是：

```text
把文本变成一串数字向量。
```

比如：

```text
“我喜欢 Python”
-> [0.12, -0.31, 0.08, ...]

“我在学编程”
-> [0.10, -0.28, 0.11, ...]

“今晚吃火锅”
-> [-0.44, 0.02, 0.67, ...]
```

如果两个句子语义相近，它们的向量距离应该更近。

这就是向量检索的基础。

可以这么理解：

```text
文本是给人看的。
向量是给机器算相似度的。
```

---

## 七、一个最小 embedding 玩具例子

真实 embedding 模型很复杂。

我们先用一个非常粗糙的玩具版：

```python
def toy_embedding(text: str) -> list[int]:
    keywords = ["python", "学习", "火锅", "项目"]
    return [1 if word in text.lower() else 0 for word in keywords]


texts = [
    "我今天学习 Python",
    "这个 Python 项目有点复杂",
    "今晚想吃火锅",
]

for text in texts:
    print(text, toy_embedding(text))
```

输出大概是：

```text
我今天学习 Python [1, 1, 0, 0]
这个 Python 项目有点复杂 [1, 0, 0, 1]
今晚想吃火锅 [0, 0, 1, 0]
```

这个例子很简单，但它已经有 embedding 的味道：

```text
每一维表示某种特征。
文本被转换成数字数组。
数组之间可以计算相似度。
```

真实 embedding 的区别是：

```text
维度更多
特征不是人工写死
语义能力更强
```

---

## 八、余弦相似度

有了向量，怎么判断两个文本像不像？

常见方法是：

```text
cosine similarity
余弦相似度
```

简单理解：

```text
看两个向量方向像不像。
```

代码：

```python
import math


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


print(cosine_similarity([1, 1, 0], [1, 0.8, 0]))
print(cosine_similarity([1, 1, 0], [0, 0, 1]))
```

输出大概是：

```text
0.9938837346736189
0.0
```

第一组方向很像，所以相似度高。

第二组完全不相干，所以相似度低。

Akane 的 `text_utils.py` 里也有一个简单版：

```python
def cosine_similarity(vec_a, vec_b) -> float:
    a = list(vec_a)
    b = list(vec_b)
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))
```

这里能直接点乘，是因为 Akane 的 embedding provider 通常已经做了归一化。

---

## 九、Akane 的 embedding provider

Akane 把 embedding 抽象成 provider。

核心接口：

```python
class BaseEmbeddingProvider:
    def embed_text(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_texts(self, texts) -> list[list[float]]:
        return [self.embed_text(text) for text in texts]
```

它的意思是：

```text
不管底层用什么模型，只要能把文本转成向量就行。
```

Akane 目前主要有三种：

| Provider | 作用 |
|---|---|
| `HuggingFaceEmbeddingProvider` | 用 sentence-transformers 模型生成真实语义向量 |
| `HashedEmbeddingProvider` | 不依赖模型的哈希向量兜底 |
| `CachedEmbeddingProvider` | 给已有 provider 加缓存 |

这是一种很好的工程设计：

```text
上层 VectorStore 不关心 embedding 怎么来的。
它只关心 embed_texts 能不能返回向量。
```

---

## 十、HuggingFaceEmbeddingProvider

文件：

```text
companion_v01/huggingface_provider.py
```

它默认使用：

```text
BAAI/bge-small-zh-v1.5
```

这是一个中文/多语言语义检索里常见的小型 embedding 模型。

核心逻辑：

```python
vectors = self._model.encode(
    raw_texts,
    normalize_embeddings=self.normalize_embeddings,
    convert_to_numpy=True,
    show_progress_bar=False,
)
```

然后返回：

```python
return [list(map(float, vector)) for vector in vectors]
```

注意：

```text
normalize_embeddings=True
```

表示输出向量会被归一化。

归一化后：

```text
点乘 ≈ 余弦相似度
```

这就是为什么向量数据库可以用 cosine 距离。

---

## 十一、HashedEmbeddingProvider：兜底向量

文件：

```text
companion_v01/embedding_provider.py
```

`HashedEmbeddingProvider` 不用神经网络模型。

它会：

```text
1. 对文本 tokenize
2. 每个 token 做 sha256 哈希
3. 根据哈希落到某个向量维度
4. 加上正负权重
5. 最后归一化
```

简化版：

```python
import hashlib
import math


def hashed_embedding(text: str, dim: int = 8) -> list[float]:
    vector = [0.0] * dim
    tokens = text.lower().split()

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(x * x for x in vector))
    if norm > 0:
        vector = [x / norm for x in vector]
    return vector


print(hashed_embedding("python project", dim=8))
print(hashed_embedding("python study", dim=8))
```

哈希 embedding 的优点：

```text
不需要下载模型
速度快
测试和兜底方便
```

缺点：

```text
语义理解能力弱
更像关键词特征，不是真正理解“意思相近”
```

所以它适合：

```text
默认兜底
开发环境
测试环境
模型加载失败时保证系统还能跑
```

真实效果更好的还是 HuggingFace embedding。

---

## 十二、CachedEmbeddingProvider：缓存向量

embedding 计算可能比较贵。

如果同一段文本反复查，就没必要每次重新算。

Akane 的 `CachedEmbeddingProvider` 做了一个简单 LRU 缓存：

```text
缓存 key：原始文本
缓存 value：向量 tuple
超过 max_entries 后，删掉最久没用的
```

简化理解：

```python
provider = CachedEmbeddingProvider(
    inner=HuggingFaceEmbeddingProvider(...),
    max_entries=2048,
)
```

上层调用还是：

```python
provider.embed_text("我在学 Python")
```

缓存层会先查：

```text
这句话之前算过吗？
算过：直接返回
没算过：调用 inner provider 计算，然后存起来
```

这是非常常见的工程优化：

```text
昂贵计算前面加缓存。
```

---

## 十三、tokenize 和 semantic_tags

Akane 里不只用 embedding，还会提取关键词。

文件：

```text
companion_v01/text_utils.py
```

核心函数：

```python
def tokenize(text: str) -> list[str]:
    ...


def extract_semantic_tags(text: str, limit: int = 8) -> list[str]:
    counts = Counter(tokenize(text))
    ranked = sorted(
        counts.items(),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )
    return [token for token, _ in ranked[:limit]]
```

中文处理里有一个小技巧：

```text
如果中文词长度 > 3，就保留整段，同时切出 2 字和 3 字片段。
```

比如：

```text
Personal_knowledge_base
MiniMind
扬州城地点
```

会被拆出一些可匹配的小片段。

这不是专业分词器，但对个人项目很实用。

示例：

```python
from collections import Counter
import re


TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
STOPWORDS = {"的", "了", "我", "你", "这个", "那个"}


def tokenize(text: str) -> list[str]:
    tokens = []
    for match in TOKEN_RE.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            if len(match) <= 3:
                tokens.append(match)
            else:
                tokens.append(match)
                for size in (2, 3):
                    for i in range(len(match) - size + 1):
                        tokens.append(match[i:i + size])
        else:
            tokens.append(match)
    return [token for token in tokens if token not in STOPWORDS]


def extract_semantic_tags(text: str, limit: int = 8) -> list[str]:
    counts = Counter(tokenize(text))
    ranked = sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [token for token, _ in ranked[:limit]]


print(tokenize("我最近在学习MiniMind和Akane项目"))
print(extract_semantic_tags("我最近在学习MiniMind和Akane项目，项目有点复杂"))
```

关键词的作用：

```text
1. 存入 metadata，辅助关键词检索
2. 给 router / verifier 做 query 改写参考
3. 给 summary / semantic summary 做标签
```

---

## 十四、VectorStore：向量库封装

文件：

```text
companion_v01/vector_store.py
```

Akane 使用：

```text
ChromaDB
```

初始化：

```python
self.client = chromadb.PersistentClient(path=str(self.base_dir))
self.collection = self.client.get_or_create_collection(
    name=self.collection_name,
    metadata=self._build_collection_metadata(),
)
```

几个关键词：

```text
PersistentClient：持久化到本地磁盘
collection：类似向量数据库中的一张表
documents：原始文本
embeddings：文本向量
metadatas：过滤和排序用的元数据
ids：每条向量记录的唯一 id
```

Akane 的 collection metadata：

```python
{
    "hnsw:space": "cosine",
    "embedding_provider": self.embedding_provider.name,
    "embedding_dimension": int(self.embedding_provider.dimension),
    "embedding_version": str(self.embedding_provider.version),
}
```

意思是：

```text
这个 collection 用 cosine 距离。
并且记录了 embedding provider、维度、版本。
```

为什么要记录 provider 和版本？

因为不同 embedding 模型生成的向量不能混用。

比如：

```text
hashed 128 维
bge-small-zh 512 维
另一个模型 768 维
```

它们不是同一个向量空间。

所以 collection name 也会跟 provider 绑定：

```python
return f"akane_memory_{embedding_provider.collection_key()}"
```

这是向量系统很重要的工程细节。

---

## 十五、向量入库：upsert_entries

Akane 把记忆写入向量库时，走：

```python
VectorStore.upsert_entries()
```

核心步骤：

```text
1. 收集 source_id
2. 收集 text
3. 清洗 metadata，只保留 str/int/float/bool
4. 调 embedding_provider.embed_texts(texts)
5. collection.upsert(ids, documents, embeddings, metadatas)
```

简化版：

```python
def upsert_entries(entries):
    ids = []
    texts = []
    metadatas = []

    for entry in entries:
        ids.append(entry["source_id"])
        texts.append(entry["text"])
        metadatas.append(entry["metadata"])

    embeddings = embedding_provider.embed_texts(texts)

    collection.upsert(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )
```

这里的 `upsert` 是：

```text
如果 id 不存在，就插入。
如果 id 已存在，就更新。
```

这很适合记忆系统。

因为摘要或语义记忆可能会被强化更新。

---

## 十六、vector_entry_builder：把业务记录变成向量记录

文件：

```text
companion_v01/vector_entry_builder.py
```

它的作用是：

```text
把 SQLite 里的业务 record 转成 VectorStore 能 upsert 的 entry。
```

### 1. raw message

```python
def build_raw_vector_entry(record):
    return {
        "source_id": record["source_id"],
        "text": record.get("content", "") or "",
        "metadata": {
            "profile_user_id": record["profile_user_id"],
            "session_id": record["session_id"],
            "seq_no": int(record["seq_no"]),
            "timestamp": int(record["timestamp"]),
            "date_label": record["date_label"],
            "time_of_day": record["time_of_day"],
            "speaker": record["role"],
            "entry_type": "raw",
            "semantic_tags_text": join_tags(record.get("semantic_tags") or []),
        },
    }
```

重点：

```text
text：真正参与 embedding 的内容
metadata：后续过滤、展示、回查 SQLite 用
```

### 2. summary

阶段摘要的检索文本不是只用一句 summary。

它会拼：

```text
diary_summary
key_events
core_facts
```

这样更容易命中。

### 3. semantic summary

长期语义记忆的检索文本会拼：

```text
semantic_summary
stable_facts
recurring_topics
important_people
open_loops
```

这说明一个很重要的技巧：

```text
向量库里存的 text，不一定等于用户最后看到的文本。
它可以是专门为检索优化过的 search_text。
```

---

## 十七、写一个迷你向量库

我们不用 ChromaDB，先写一个纯 Python 版。

```python
import math


def toy_embedding(text: str) -> list[float]:
    words = ["python", "学习", "项目", "火锅"]
    return [1.0 if word in text.lower() else 0.0 for word in words]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MiniVectorStore:
    def __init__(self):
        self.rows = []

    def upsert(self, source_id: str, text: str, metadata: dict):
        vector = toy_embedding(text)
        self.rows = [row for row in self.rows if row["source_id"] != source_id]
        self.rows.append({
            "source_id": source_id,
            "text": text,
            "metadata": metadata,
            "vector": vector,
        })

    def search(self, query: str, limit: int = 3) -> list[dict]:
        query_vector = toy_embedding(query)
        scored = []
        for row in self.rows:
            scored.append({
                **row,
                "score": cosine(query_vector, row["vector"]),
            })
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:limit]


store = MiniVectorStore()
store.upsert("m1", "我今天学习 Python", {"entry_type": "raw"})
store.upsert("m2", "这个 Akane 项目有很多文件", {"entry_type": "raw"})
store.upsert("m3", "今晚想吃火锅", {"entry_type": "raw"})

for hit in store.search("Python 项目"):
    print(hit["source_id"], hit["score"], hit["text"])
```

输出大概是：

```text
m1 0.7071067811865475 我今天学习 Python
m2 0.7071067811865475 这个 Akane 项目有很多文件
m3 0.0 今晚想吃火锅
```

真实向量库做的事情更复杂：

```text
高维向量
近似最近邻索引
持久化
metadata 过滤
批量 upsert
并发锁
```

但核心思想就是这个。

---

## 十八、semantic_search：语义检索

Akane 的语义检索：

```python
result = self.collection.query(
    query_embeddings=self.embedding_provider.embed_texts([str(query_text or "")]),
    n_results=max(1, int(n_results)),
    where=where,
    include=["documents", "metadatas", "distances"],
)
```

这里做了三件事：

```text
1. 把 query_text 转成 query embedding
2. 用 ChromaDB 找最接近的向量
3. 用 where 做用户和时间过滤
```

结果里有：

```text
ids
documents
metadatas
distances
```

Akane 再把 distance 转成 score：

```python
"semantic_score": max(0.0, 1.0 - distance)
```

语义检索擅长：

```text
同义表达
近义问题
句子意思相近但字面不同
```

比如：

```text
query：我最近主攻什么方向？
memory：用户目前主要往软件方向发展，硬件以后可能涉及。
```

即使没有完全相同的关键词，embedding 也可能找到。

---

## 十九、metadata 过滤：不要全库乱搜

Akane 的向量记录有 metadata：

```text
profile_user_id
session_id
seq_no
timestamp
date_label
time_of_day
entry_type
semantic_tags_text
```

检索时会构造 where：

```python
clauses = [{"profile_user_id": str(profile_user_id)}]

if hint.get("date_label"):
    clauses.append({"date_label": str(hint["date_label"])})
if hint.get("time_of_day"):
    clauses.append({"time_of_day": str(hint["time_of_day"])})
if hint.get("start_ts") is not None:
    clauses.append({"timestamp": {"$gte": int(hint["start_ts"])}})
if hint.get("end_ts") is not None:
    clauses.append({"timestamp": {"$lte": int(hint["end_ts"])}})
```

这非常重要。

比如用户问：

```text
4月12日晚上我们聊了什么？
```

如果不加时间过滤，可能搜到任何一天的“晚上”。

所以检索不是只有向量相似度。

真实 RAG 通常会同时用：

```text
语义相似度
关键词匹配
时间过滤
用户隔离
类型过滤
权限过滤
```

Akane 至少做了：

```text
profile_user_id 过滤
date_label 过滤
time_of_day 过滤
timestamp 范围过滤
```

---

## 二十、keyword_search：关键词检索

向量检索很强，但不是万能。

它可能对这些内容不稳定：

```text
人名
项目名
文件名
日期
数字
专有名词
非常短的 query
```

比如：

```text
MiniMind
AkaneCompanionLab
4月12日
F:\Personal_knowledge_base
```

这种东西有时关键词匹配更可靠。

所以 Akane 还有：

```python
keyword_search()
```

它会：

```text
1. 从 Chroma collection 里取出符合 where 的 documents 和 metadatas
2. 对 document + semantic_tags_text 做 tokenize
3. 用 BM25 算关键词相关性
4. 返回 tag_score
```

关键词检索擅长：

```text
精确词
专有名词
日期
用户明确给出的关键词
```

---

## 二十一、BM25 是什么

BM25 是传统搜索引擎里的经典相关性算法。

它大致考虑：

```text
query 里的词有没有出现在文档里
词出现多少次
这个词是不是很稀有
文档长度是否过长
```

Akane 的 `_bm25_score` 简化结构：

```python
tf = Counter(doc_terms)
k1 = 1.5
b = 0.75

for term in query_terms:
    freq = tf.get(term, 0)
    if freq <= 0:
        continue
    df = term_doc_freq.get(term, 0)
    idf = math.log(1 + ((doc_count - df + 0.5) / (df + 0.5)))
    score += idf * ...
```

你现在不需要背公式。

先记住：

```text
BM25 是关键词搜索里非常经典的打分方法。
```

用一个简单版感受：

```python
from collections import Counter


def simple_keyword_score(query_terms: list[str], doc_terms: list[str]) -> int:
    tf = Counter(doc_terms)
    return sum(tf.get(term, 0) for term in query_terms)


print(simple_keyword_score(["python", "项目"], ["python", "项目", "项目"]))
print(simple_keyword_score(["python", "项目"], ["火锅", "晚饭"]))
```

输出：

```text
3
0
```

BM25 就是这个思想的成熟版。

---

## 二十二、为什么要混合检索

Akane 同时用：

```text
semantic_search：向量语义检索
keyword_search：BM25 关键词检索
```

原因是：

```text
语义检索能理解相近意思。
关键词检索能抓住精确实体。
```

例子：

```text
query：我最近主攻什么方向
memory：用户目前主攻软件方向，硬件以后可能涉及
```

语义检索更有优势。

再看：

```text
query：MiniMind 那个项目
memory：用户正在手搓 MiniMind 架构
```

关键词检索也很重要，因为 `MiniMind` 是专有名词。

真实项目里，单一路线经常不稳。

所以混合检索是很经典的 RAG 工程做法：

```text
Dense retrieval + Sparse retrieval
向量检索 + 关键词检索
```

---

## 二十三、RRF 融合

Akane 用：

```python
fuse_with_rrf()
```

RRF 全称：

```text
Reciprocal Rank Fusion
```

它不直接比较原始分数，而是比较排名。

核心思想：

```text
如果一个结果在多个检索器里排名都靠前，它应该更可靠。
```

公式大概是：

```text
score = 1 / (k + semantic_rank) + 1 / (k + keyword_rank)
```

Akane 代码里：

```python
if source_id in semantic_rank:
    rrf_score += 1.0 / (k + semantic_rank[source_id])
if source_id in keyword_rank:
    rrf_score += 1.0 / (k + keyword_rank[source_id])
```

写一个可运行小例子：

```python
def fuse_with_rrf(semantic_ids: list[str], keyword_ids: list[str], k: int = 60):
    semantic_rank = {source_id: index + 1 for index, source_id in enumerate(semantic_ids)}
    keyword_rank = {source_id: index + 1 for index, source_id in enumerate(keyword_ids)}
    all_ids = []

    for source_id in semantic_ids + keyword_ids:
        if source_id not in all_ids:
            all_ids.append(source_id)

    fused = []
    for source_id in all_ids:
        score = 0.0
        if source_id in semantic_rank:
            score += 1 / (k + semantic_rank[source_id])
        if source_id in keyword_rank:
            score += 1 / (k + keyword_rank[source_id])
        fused.append((source_id, score))

    return sorted(fused, key=lambda item: item[1], reverse=True)


print(fuse_with_rrf(
    semantic_ids=["a", "b", "c"],
    keyword_ids=["b", "d", "a"],
))
```

输出大概：

```text
[('b', 0.0325...), ('a', 0.0322...), ('d', 0.0161...), ('c', 0.0158...)]
```

`b` 排第一，因为它在两个检索结果里都靠前。

这就是 RRF 的直觉。

---

## 二十四、rerank：融合后再微调

Akane 在 RRF 后还会：

```python
fused_hits = self._rerank_fused_hits(...)
```

它会根据问题意图加一点 bonus。

比如：

```text
identity：名字、我叫什么
preference：喜欢什么、偏好
plan：计划、安排
generic_past：之前、上次、记得
```

如果用户问名字：

```text
我叫什么名字来着？
```

命中的文档里出现：

```text
我叫...
名字是...
叫做...
```

就加分。

如果用户问偏好：

```text
我之前说我喜欢什么？
```

命中的文档里出现：

```text
喜欢
更喜欢
偏好
讨厌
```

就加分。

这属于工程经验：

```text
纯向量分数不一定符合业务目标。
可以在召回后做轻量 rerank。
```

---

## 二十五、RetrievalService 的主流程

文件：

```text
companion_v01/retrieval_service.py
```

核心入口：

```python
def run(...):
    router_output, router_timing = self._build_router_output(...)

    if router_output.get("need_retrieval"):
        retrieval_result, verifier_output, confirmed_snippets, verifier_timing = \
            self._run_retrieval_chain(...)

    return RetrievalPipelineResult(...)
```

主流程：

```text
router 判断是否要检索
如果不需要：直接返回空 confirmed_snippets
如果需要：进入检索链
```

返回结构：

```python
RetrievalPipelineResult(
    used_retrieval=True,
    confirmed_snippets=[...],
    router_output={...},
    router_timing={...},
    retrieval_result={...},
    verifier_output={...},
    verifier_timing={...},
)
```

这说明 Akane 的 RAG 不是偷偷发生的。

它会把调试信息也留下：

```text
router 怎么判断的
检索到了什么
verifier 怎么判断的
最终用了哪些 snippet
```

这对调试 RAG 非常关键。

---

## 二十六、Router：先判断要不要检索

不是每句话都要检索。

比如：

```text
你好呀
今天天气不错
我们继续学吧
```

这些通常不需要查历史。

但这些需要：

```text
你还记得我之前说过什么吗？
上次那个项目叫什么来着？
4月12日晚上我们聊了什么？
我之前说我更喜欢哪种学习方式？
```

Akane 的 router 输出：

```python
{
    "need_retrieval": True,
    "route": "memory_search",
    "rewritten_query": "2026-04-12 晚上 发生了什么",
    "keywords": ["2026-04-12", "4月12日", "晚上", "发生了什么"],
    "time_hint": {
        "date_label": "2026-04-12",
        "time_of_day": "night",
        "relative_time": "past",
    },
    "index_current_message": False,
}
```

各字段含义：

| 字段 | 含义 |
|---|---|
| `need_retrieval` | 是否需要查记忆 |
| `route` | 检索路线，比如 `memory_search` |
| `rewritten_query` | 改写后的搜索短句 |
| `keywords` | 关键词检索用 |
| `time_hint` | 时间过滤线索 |
| `index_current_message` | 当前这句是否写入向量库 |

`index_current_message` 很有意思。

如果用户说：

```text
你还记得我之前说过什么吗？
```

这句话本身是测试检索能力，不是值得未来召回的记忆事实。

所以可以不把它写进向量库，避免以后搜到一堆“你还记得吗”。

---

## 二十七、Hard Route：明显回忆问题直接强制检索

Akane 不完全依赖 LLM router。

它有外层规则：

```python
HARD_ROUTE_WORDS = [
    "记得",
    "之前",
    "上次",
    "回忆",
    "说过什么",
    ...
]
```

如果用户明显在问过去记忆：

```text
我们当时定的计划是什么来着？
昨天我们都买了什么呀？
我有没有提过那个项目叫什么？
```

就直接倾向：

```text
need_retrieval = true
```

为什么要这样？

因为 LLM router 也可能误判。

所以 Akane 用：

```text
规则兜底 + LLM 判断
```

这比纯靠模型稳。

---

## 二十八、Query Rewrite：不要直接拿原话搜

用户原话经常很口语：

```text
那个呢？你再想想，我对这个挺有执念的
```

如果直接拿这句话去搜，效果可能很差。

Router 要做 query rewrite：

```text
根据最近上下文，把“那个/这个/它”改成具体对象。
```

比如最近上下文里提到：

```text
扬州城地点、二十四桥、瘦西湖
```

那 query 应该改成：

```text
扬州城地点 二十四桥 瘦西湖 执念
```

而不是：

```text
主人对什么有执念
```

Akane 的 prompt 里专门强调：

```text
不要输出“主人对什么有执念”“具体的事情或话题”“请回忆一下”这类空泛追问式检索词。
```

这就是 RAG 中很重要的概念：

```text
用户问题不一定是好的搜索 query。
```

---

## 二十九、time_hint：时间线索

Akane 会从用户消息里抽取时间：

```python
def _extract_time_hint(user_message, now_ts):
    if "今天" in text:
        date_label = today
    elif "昨天" in text:
        date_label = yesterday
    elif "前天" in text:
        date_label = two_days_ago
    elif "之前" in text:
        relative_time = "past"
```

还会识别：

```text
上午 morning
下午 afternoon
晚上 night
凌晨 midnight
```

比如：

```text
Akane，回忆一下4月12日晚上的事情
```

在 2026 年语境下可能得到：

```python
{
    "date_label": "2026-04-12",
    "time_of_day": "night",
    "relative_time": "past",
}
```

然后搜索 query 也会变成：

```text
2026-04-12 晚上 发生了什么
```

时间线索是记忆检索里非常重要的过滤条件。

因为很多聊天内容语义相似，但发生时间不同。

---

## 三十、_retrieve_memories：真正召回候选记忆

Akane 的 `_retrieve_memories` 做：

```text
1. 标准化 time_hint
2. 排除当前可见上下文里的 source_id
3. semantic_search 取 10 条
4. keyword_search 取 10 条
5. RRF 融合
6. 业务 rerank
7. 截取 top 4
8. 构造 memory_snippets
```

核心代码形状：

```python
semantic_hits = self.vector_store.semantic_search(..., n_results=10)
keyword_hits = self.vector_store.keyword_search(..., n_results=10)
fused_hits = fuse_with_rrf(semantic_hits, keyword_hits)
fused_hits = self._rerank_fused_hits(..., fused_hits=fused_hits)[:4]
memory_snippets = self._build_memory_snippets(fused_hits)
```

这里有两个细节很重要。

### 1. 排除可见上下文

如果最近对话已经在 prompt 里了，就不应该再从向量库里召回一次。

否则模型会看到重复内容。

Akane 会收集：

```text
recent_raw
recent_episodic_summaries
recent_semantic_summaries
current_user_source_id
```

然后排除这些 source_id。

### 2. 只取 top 4

检索不是越多越好。

太多片段会：

```text
挤占上下文
带来噪声
让模型分心
增加成本
```

所以 Akane 先召回更多候选，再融合排序，最后只给 verifier 前几条。

---

## 三十一、memory_snippets：给模型看的记忆片段

向量库命中的只是 source_id 和 document。

但最终给 verifier / final model 看的不是裸 document。

Akane 会根据 `entry_type` 重新渲染：

```text
raw -> 原始对话回忆
summary -> 摘要回忆
semantic_summary -> 长期语义记忆
```

### 1. raw snippet

如果命中原始消息，Akane 会取上下文窗口：

```python
context_rows = self.store.get_context_slice(
    record["session_id"],
    record["seq_no"],
    window=window,
)
```

这样不是只给一句孤零零的话，而是给前后文。

渲染成：

```text
【原始对话回忆】
[日期 2026-04-12]
[20:10] 主人: 我最近在学 MiniMind
[20:11] Akane: 好呀，我们可以拆架构。
```

### 2. summary snippet

```text
【摘要回忆】[2026-04-12 20:00 ~ 20:30 | 阶段:学习 | 类型:日常]
用户这段时间在学习 MiniMind。
关键事件：...
核心事实：...
```

### 3. semantic summary snippet

```text
【长期语义记忆】[2026-04-01 20:00 ~ 2026-04-18 23:00 | 重要度:0.82]
用户长期主攻软件方向，正在学习 AI 工程。
稳定事实：...
反复话题：...
待续线索：...
```

这一步很关键：

```text
检索系统负责找 id。
MemoryStore 负责回查完整业务记录。
Renderer 负责把记录变成模型易读的文本。
```

---

## 三十二、Verifier：检索结果还要二次判断

RAG 里一个常见错误是：

```text
检索到的 top1/top3 就直接塞给模型。
```

但向量检索可能召回“看起来相关但不能回答”的片段。

所以 Akane 加了 verifier。

它会看：

```text
用户原始问题
检索改写问题
关键词
时间线索
检索到的 snippets
```

然后输出 NDJSON：

```text
第一行 decision
第二行 selection 或 retry
```

可能结果：

```python
{
    "match_result": "match",
    "need_retry": False,
    "selected_indexes": [2, 3],
}
```

表示：

```text
片段 2 和 3 可以回答用户问题。
```

也可能：

```python
{
    "match_result": "mismatch",
    "need_retry": True,
    "retry_query": "MiniMind 项目 名字",
    "retry_keywords": ["MiniMind", "项目", "名字"],
}
```

表示：

```text
这轮没找准，换个 query 再搜一次。
```

这就是 Akane 的检索自修正机制。

---

## 三十三、为什么 verifier 很重要

因为检索和回答之间需要一道闸门。

没有 verifier，模型可能会拿错材料回答。

比如用户问：

```text
我昨天晚上说想学什么？
```

检索结果可能命中：

```text
前天晚上用户说想学 FastAPI。
昨天上午用户说想吃饭。
```

这些语义上有些像，但时间不对。

verifier 的任务就是判断：

```text
这条记忆是否真的能回答当前问题？
```

如果不能：

```text
不要硬塞给 final model。
```

这能减少幻觉。

因为最终模型如果看到错误片段，很可能会顺着错误片段说下去。

---

## 三十四、Retry：最多再搜一次

Akane 的 `_run_retrieval_chain` 里最多尝试 2 次：

```python
for attempt in range(2):
    retrieval_result = self._retrieve_memories(...)
    verifier_output = self._verify_memories(...)

    if match:
        return confirmed_snippets

    if need_retry:
        current_router = {
            "rewritten_query": verifier_output["retry_query"],
            "keywords": verifier_output["retry_keywords"],
            ...
        }
```

第二次检索时，还会排除第一次已经取过的 source_id：

```text
current_user_msg
memory_a
memory_b
memory_c
...
```

这样可以避免原地打转。

这也是很好的 Agent/RAG 工程意识：

```text
允许有限重试，但必须有上限。
```

否则系统可能陷入无限检索循环。

---

## 三十五、confirmed_snippets 如何进入最终回答

当 verifier 判断匹配后，Akane 得到：

```python
confirmed_snippets = [...]
```

然后在 `engine.py` 里传给最终回复：

```python
final_output = self._build_final_response(
    ...
    confirmed_snippets=confirmed_snippets,
    ...
)
```

最终在 `PromptBuilder.build_final_generation_context` 里变成：

```python
memory_text = "\n\n".join(confirmed_snippets) if confirmed_snippets else ""
```

然后进入 user_prompt：

```text
可用回忆片段：
{memory_text}
```

这就是完整的 RAG：

```text
检索出来的资料不是直接返回给用户。
它是作为上下文交给最终生成模型。
```

最终模型要基于：

```text
当前消息
最近上下文
长期语义记忆
阶段摘要
可用回忆片段
视觉状态
工具结果
```

综合生成自然回复。

---

## 三十六、前置检索和工具检索

Akane 有两种检索时机。

### 1. 前置检索

用户消息刚进来，还没生成最终回复前，就先检索。

对应：

```text
run_pre_retrieval_pipeline
```

这适合：

```text
用户明显在问过去记忆
```

比如：

```text
你还记得我之前说过什么吗？
```

### 2. 工具检索

最终模型生成过程中，也可以主动发起：

```text
retrieve_memory
```

这属于 Tool Calling，会在第 09 篇专门讲。

这里只先记住区别：

```text
前置检索：系统在回答前自动查
工具检索：模型在回答过程中主动要求查
```

它们底层都可以复用 RetrievalService。

---

## 三十七、摘要压缩：长期记忆不能只靠 raw

如果一直只存原始聊天，记忆系统会越来越乱。

所以 Akane 有：

```text
MemoryCompactionService
```

文件：

```text
companion_v01/memory_compaction_service.py
```

它做两级压缩：

```text
raw messages -> summary
summaries -> semantic summary
```

### 1. raw messages -> summary

当未总结消息达到阈值：

```text
SUMMARY_TRIGGER_COUNT = 30
SUMMARY_BATCH_SIZE = 20
```

就取最老的一批未总结消息，让 aux 模型总结成：

```text
diary_summary
period_label
event_type
importance
key_events
core_facts
```

然后：

```text
写入 SQLite summaries 表
把原始消息标记为已总结
把 summary 写入向量库
```

### 2. summaries -> semantic summary

当未语义化的阶段摘要达到阈值：

```text
EPISODIC_COMPACT_TRIGGER_COUNT
EPISODIC_COMPACT_BATCH_SIZE
```

就生成长期语义记忆：

```text
semantic_summary
importance
stable_facts
recurring_topics
important_people
open_loops
```

然后写入向量库。

这样记忆会从：

```text
大量细碎对话
```

逐渐沉淀成：

```text
阶段事件
长期稳定事实
```

这才是一个能长期运行的伴侣项目需要的记忆结构。

---

## 三十八、semantic reinforcement：长期记忆强化

Akane 不一定每次都新建一条 semantic summary。

如果新的长期记忆和已有记忆有重叠，它会尝试强化旧记录。

判断依据包括：

```text
semantic_tags 重叠
recurring_topics 重叠
important_people 重叠
stable_facts 重叠
```

如果超过阈值：

```text
SEMANTIC_REINFORCEMENT_MIN_OVERLAP
```

就把 incoming record 合并进 existing record。

更新内容包括：

```text
semantic_summary
importance
stable_facts
recurring_topics
important_people
open_loops
source_summary_ids
reinforcement_count
last_reinforced_ts
```

这很像人类记忆：

```text
反复出现的事情会变成更稳定、更重要的长期印象。
```

从工程角度看，它解决的是：

```text
长期记忆无限膨胀
重复主题到处都是
```

所以语义强化不是装饰功能，它是长期记忆系统很重要的一环。

---

## 三十九、embedding reindex：重建向量索引

项目启动时，Akane 会检查：

```python
total_records = self.store.count_vectorizable_records()
current_entries = self.vector_store.count_entries()
```

如果数据库里可向量化记录更多，而向量库里数量不够：

```text
后台启动 reindex 线程
```

重建逻辑：

```python
batch_iterators = (
    (self.store.iter_messages_for_vector_reindex(batch_size), build_raw_vector_entry),
    (self.store.iter_summaries_for_vector_reindex(batch_size), build_summary_vector_entry),
    (self.store.iter_semantic_summaries_for_vector_reindex(batch_size), build_semantic_summary_vector_entry),
)

for batches, entry_builder in batch_iterators:
    for record_batch in batches:
        entries = [entry_builder(record) for record in record_batch]
        self.vector_store.upsert_entries(entries)
```

为什么需要 reindex？

```text
换了 embedding 模型
向量库损坏
版本升级
之前没开向量库
导入旧数据
```

这就是索引系统的常见维护能力。

关系型数据库里有 index rebuild。

向量数据库里也要有 vector reindex。

---

## 四十、RAG 调试信息

Akane 的 `/think` debug 会展示：

```text
router_output
router_timing
retrieval_result
verifier_output
verifier_timing
memory_snippets
selected_memory_snippets
```

RAG 出问题时，不要只看最终回答。

要顺着链路查：

```text
1. router 有没有判断要检索？
2. rewritten_query 写得好不好？
3. keywords 有没有具体实体？
4. time_hint 有没有错？
5. semantic_search 有没有召回？
6. keyword_search 有没有召回？
7. RRF 后 top4 是什么？
8. snippet 渲染是否包含足够上下文？
9. verifier 有没有误杀？
10. confirmed_snippets 有没有进入 final prompt？
```

这就是调试 RAG 的基本路线。

---

## 四十一、一个完整迷你 RAG 示例

下面写一个可运行的小 RAG。

它不用 LLM，只用规则模拟 router 和 verifier。

```python
import math


MEMORIES = [
    {
        "id": "m1",
        "text": "用户正在学习 Python 工程化和 FastAPI。",
        "tags": ["python", "fastapi", "工程化"],
    },
    {
        "id": "m2",
        "text": "用户正在跟着 MiniMind 学习 Transformer 架构。",
        "tags": ["minimind", "transformer", "pytorch"],
    },
    {
        "id": "m3",
        "text": "用户喜欢把知识点整理成一个系统笔记文件集中复习。",
        "tags": ["笔记", "复习", "学习方法"],
    },
]


VOCAB = ["python", "fastapi", "minimind", "transformer", "笔记", "复习", "学习方法"]


def embed(text: str) -> list[float]:
    lowered = text.lower()
    return [1.0 if word in lowered else 0.0 for word in VOCAB]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def router(question: str) -> dict:
    need = any(word in question for word in ["之前", "记得", "我在学", "我喜欢"])
    return {
        "need_retrieval": need,
        "rewritten_query": question,
        "keywords": [word for word in VOCAB if word in question.lower()],
    }


def retrieve(query: str, limit: int = 2) -> list[dict]:
    query_vec = embed(query)
    hits = []
    for memory in MEMORIES:
        score = cosine(query_vec, embed(memory["text"] + " " + " ".join(memory["tags"])))
        hits.append({**memory, "score": score})
    hits.sort(key=lambda item: item["score"], reverse=True)
    return [hit for hit in hits[:limit] if hit["score"] > 0]


def verifier(question: str, hits: list[dict]) -> list[str]:
    if not hits:
        return []
    return [hit["text"] for hit in hits]


def build_prompt(question: str, snippets: list[str]) -> str:
    memory_text = "\n".join(f"- {item}" for item in snippets) or "(无)"
    return f"""可用回忆片段：
{memory_text}

用户问题：
{question}

请基于可用回忆片段自然回答。"""


question = "你还记得我之前怎么复习知识点的吗？"
route = router(question)

if route["need_retrieval"]:
    hits = retrieve(route["rewritten_query"])
    snippets = verifier(question, hits)
else:
    snippets = []

prompt = build_prompt(question, snippets)
print(prompt)
```

你会看到最终 prompt 里塞入了相关记忆：

```text
用户喜欢把知识点整理成一个系统笔记文件集中复习。
```

这就是 Akane RAG 的迷你版。

---

## 四十二、写一个 RRF 测试

RAG 里很多东西可以测试。

比如 RRF：

```python
import unittest


def fuse_with_rrf(semantic_ids, keyword_ids, k=60):
    semantic_rank = {source_id: index + 1 for index, source_id in enumerate(semantic_ids)}
    keyword_rank = {source_id: index + 1 for index, source_id in enumerate(keyword_ids)}
    all_ids = []
    for source_id in semantic_ids + keyword_ids:
        if source_id not in all_ids:
            all_ids.append(source_id)

    result = []
    for source_id in all_ids:
        score = 0.0
        if source_id in semantic_rank:
            score += 1 / (k + semantic_rank[source_id])
        if source_id in keyword_rank:
            score += 1 / (k + keyword_rank[source_id])
        result.append((source_id, score))
    return sorted(result, key=lambda item: item[1], reverse=True)


class RRFTests(unittest.TestCase):
    def test_dual_hit_wins(self):
        fused = fuse_with_rrf(
            semantic_ids=["a", "b"],
            keyword_ids=["b", "c"],
        )

        self.assertEqual(fused[0][0], "b")


if __name__ == "__main__":
    unittest.main()
```

Akane 的 `tests/test_vector_store.py` 里也测了类似逻辑：

```text
semantic 命中 a, b
keyword 命中 b, c
融合后 b 应该排在最前
```

这类测试很有价值。

因为 RAG 的排序逻辑一旦改坏，最终回答质量会明显下降。

---

## 四十三、写一个 router 规则测试

Akane 的 router 不只靠 LLM，也有规则。

所以规则也要测。

```python
import unittest


def should_retrieve(text: str) -> bool:
    markers = ["记得", "之前", "上次", "回忆", "说过什么"]
    return any(marker in text for marker in markers)


class RouterRuleTests(unittest.TestCase):
    def test_memory_question_should_retrieve(self):
        self.assertTrue(should_retrieve("你还记得我之前说过什么吗？"))
        self.assertTrue(should_retrieve("上次那个项目叫什么来着？"))

    def test_new_fact_should_not_retrieve(self):
        self.assertFalse(should_retrieve("我昨天没睡好"))
        self.assertFalse(should_retrieve("我以前学过 C 语言"))


if __name__ == "__main__":
    unittest.main()
```

Akane 的 `tests/test_retrieval_service.py` 里也有类似测试：

```text
过去记忆问题应该 hard route
新的过去事实陈述不应该 hard route
明显记忆测试句不应该写入向量库
```

这说明：

```text
RAG 系统里的规则判断也需要测试。
```

---

## 四十四、RAG 常见坑

### 1. 只做向量检索，不做关键词检索

专有名词、日期、文件名可能搜不稳。

更稳的是：

```text
向量检索 + 关键词检索
```

### 2. 不做 query rewrite

用户原话可能太口语。

比如：

```text
那个呢？你再想想
```

直接搜很差。

需要改写成具体实体。

### 3. 不做 verifier

检索结果可能“相关但不能回答”。

直接塞给模型会诱导模型编。

### 4. 把太多片段塞进 prompt

片段越多不一定越好。

太多会带来噪声。

### 5. 不排除当前可见上下文

当前消息或最近消息已经在 prompt 里了。

再检索回来会重复，甚至让模型误以为这是长期记忆。

### 6. embedding 模型换了但不重建索引

不同模型的向量空间不同。

换模型后要换 collection 或 reindex。

### 7. 不存 metadata

没有 metadata，就很难做：

```text
用户隔离
时间过滤
类型过滤
回查原始记录
```

---

## 四十五、Akane 源码阅读路线

建议按这个顺序读。

### 1. 先读 text_utils

文件：

```text
companion_v01/text_utils.py
```

重点：

```text
tokenize
extract_semantic_tags
infer_time_of_day
detect_time_of_day_from_text
render_chat_timeline
```

先理解文本怎么变成标签和时间线。

### 2. 再读 embedding_provider

文件：

```text
companion_v01/embedding_provider.py
companion_v01/huggingface_provider.py
```

重点：

```text
BaseEmbeddingProvider
HashedEmbeddingProvider
CachedEmbeddingProvider
HuggingFaceEmbeddingProvider
```

理解文本怎么变向量。

### 3. 再读 vector_entry_builder

文件：

```text
companion_v01/vector_entry_builder.py
```

重点：

```text
raw message 如何转 entry
summary 如何转 entry
semantic summary 如何转 entry
metadata 里有哪些字段
```

理解业务记录怎么进入向量库。

### 4. 再读 vector_store

文件：

```text
companion_v01/vector_store.py
```

重点：

```text
upsert_entries
semantic_search
keyword_search
_bm25_score
_build_where
fuse_with_rrf
```

理解“查”的底层。

### 5. 再读 retrieval_service

文件：

```text
companion_v01/retrieval_service.py
```

重点：

```text
run
run_explicit
_build_router_output
_retrieve_memories
_verify_memories
_run_retrieval_chain
_build_memory_snippets
_rerank_fused_hits
```

理解完整 RAG pipeline。

### 6. 再读 memory_compaction_service

文件：

```text
companion_v01/memory_compaction_service.py
```

重点：

```text
schedule_summary_cycle
_summarize_batch
_semanticize_summary_batch
_select_semantic_reinforcement_target
_reinforce_semantic_summary_record
```

理解长期记忆怎么沉淀。

### 7. 最后回到 engine

文件：

```text
companion_v01/engine.py
```

重点：

```text
_build_embedding_provider
_maybe_start_embedding_reindex
_run_embedding_reindex
_run_pre_retrieval_pipeline
_upsert_raw_record
_build_final_response
```

理解 RAG 如何嵌入整个聊天流程。

---

## 四十六、这一篇的知识点清单

学完这一篇，你应该能看懂：

```text
RAG = 检索增强生成
Indexing 和 Retrieval 是两条不同链路
embedding 是文本到向量
余弦相似度用于判断向量方向相近
HuggingFace embedding 是真实语义向量
Hashed embedding 是无模型兜底
Cached provider 是缓存优化
ChromaDB collection 类似向量表
metadata 用于用户隔离和时间过滤
raw / summary / semantic_summary 是三层记忆
vector_entry_builder 负责把业务记录变成向量记录
semantic_search 负责语义召回
keyword_search + BM25 负责关键词召回
RRF 负责融合多个检索器的排名
rerank 负责根据业务意图微调排序
router 负责判断是否要检索和改写 query
verifier 负责判断检索结果是否真的能回答
confirmed_snippets 最终进入 final prompt
memory compaction 负责长期记忆沉淀
embedding reindex 负责索引重建
RAG 系统必须有测试和 debug 信息
```

---

## 四十七、和后面 Agent 的关系

下一篇是：

```text
09_ToolCalling与Agent工程.md
```

RAG 和 Agent 的关系很近，但不是一回事。

RAG 更像：

```text
回答前查资料
```

Tool Calling 更像：

```text
回答中调用工具做动作
```

Agent 更进一步：

```text
模型能在多个步骤里规划、调用工具、观察结果、继续决策
```

Akane 里 `retrieve_memory` 这个工具会把 RAG 和 Tool Calling 接起来：

```text
模型觉得还需要查记忆
-> 发出 retrieve_memory tool_call
-> RetrievalService 检索
-> 把结果作为 followup_context
-> 模型继续回答
```

但这部分放到第 09 篇讲会更清楚。

这一篇你先牢牢抓住：

```text
RAG 是给模型补资料。
Tool Calling 是让模型做动作。
Agent 是把多个动作串成流程。
```

---

## 四十八、最终压缩版

Akane 的 RAG 可以压缩成一条线：

```text
历史数据入库
-> 文本转向量
-> ChromaDB 保存向量和 metadata
-> 用户问题触发 router
-> query rewrite + keywords + time_hint
-> semantic search + keyword search
-> RRF 融合 + rerank
-> snippet 渲染
-> verifier 选择可靠片段
-> confirmed_snippets 进入最终 prompt
-> LLM 基于记忆自然回答
```

如果只记一句话：

```text
RAG 不是把所有资料塞给模型，而是在正确时机找出少量最可能有用的资料，再让模型基于这些资料生成。
```

你读 Akane 这部分时，别先陷在细节里。

先盯住四个问题：

```text
什么东西被索引？
用户问题被改写成什么 query？
召回了哪些候选？
最终哪些 snippet 真的进了 prompt？
```

这四个问题答清楚，RAG 的主干就通了。

