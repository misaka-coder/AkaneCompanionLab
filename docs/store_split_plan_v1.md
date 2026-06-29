# store/ 拆分方案 v1

## 目标

将 `store/core.py`（5900 行，~80 个方法）按数据库表域拆分为独立模块，保持 `from .store import MemoryStore` 不受影响。

## 拆分策略

每个模块包含与该表相关的 **CRUD 方法**，以模块级函数 + `store` 参数的形式：
```python
# store/messages.py
def add_message(store, ...): ...
def get_unsummarized_messages(store, ...): ...
```

`core.py` 中的 `MemoryStore` 类保持方法签名不变，函数体变成薄转发：
```python
class MemoryStore:
    def add_message(self, ...):
        from .messages import add_message as _fn
        return _fn(self, ...)
```

## 模块划分

| # | 模块 | 相关表 | 方法数 | 预估行 | 风险 |
|---|------|--------|--------|--------|------|
| 1 | `core.py` | 初始化、连接、schema、migration | ~10 | ~700-1000 | 基线 |
| 2 | `row_mappers.py` | `_row_to_*` 函数 | ~8 | ~100 | 低 |
| 3 | `normalizers.py` | `_normalize_*` 函数 | ~5 | ~60 | 低 |
| 4 | `json_utils.py` | loads/dumps JSON 对象 | ~3 | ~30 | 低 |
| 5 | `sessions.py` | `chat_sessions` | 7 | ~150 | 低 |
| 6 | `messages.py` | `chat_messages` | 15 | ~350 | 低 |
| 7 | `summaries.py` | `memory_summaries` | 8 | ~250 | 低 |
| 8 | `semantic.py` | `memory_semantic_summaries` | 5 | ~250 | 中（update 字段多、JSON 多） |
| 9 | `reminders.py` | `reminders` | 4 | ~160 | 低 |
| 10 | `attachments.py` | `attachment_inbox_items` | 10 | ~650 | 中（状态机复杂） |
| 11 | `workspace_files.py` | `workspace_file_states` | 2 | ~90 | 低 |
| 12 | `generated_files.py` | `generated_files` | 7 | ~400 | 低 |
| 13 | `music_timeline.py` | `desktop_music_timelines` | 3 | ~200 | 低 |
| 14 | `task_workspace.py` | `task_workspaces`, `task_workspace_events` | 7 | ~400 | 低 |
| 15 | `persona.py` | `persona_cards`, `persona_events`, `persona_session_states` | 10 | ~450 | 低 |
| 16 | `gifts.py` | `user_media_assets` | 12 | ~800 | 中（礼品状态逻辑复杂） |
| 17 | `vision.py` | `vision_observations` | 2 | ~150 | 低 |
| 18 | `eval.py` | `eval_turns` | 3 | ~50 | 低 |
| 19 | `vector.py` | 向量重建迭代器 | 3 | ~80 | 低 |

## 执行顺序

```
sessions → eval → reminders → workspace_files → vision
  → messages ← 核心表，提前暴露转发模式是否稳定
  → summaries → semantic
  → persona → music_timeline → generated_files
  → task_workspace → attachments → gifts（最后）
```

按风险递增，每步可回退。

## 关键约束

- **不改 DB schema**：`_init_db()` 留在 `core.py`，含 migrations
- **不改方法签名**：转发层签名必须与原始 `MemoryStore` 方法一致（参数名、默认值、`*`、返回类型）
- **保持 `from .store import MemoryStore` 可工作**：`__init__.py` 从 `core` 导入
- **每个模块不能自己保存连接**：统一用 `store._connect()`
- **搬迁前后对比方法签名**：`git diff` 检查参数名、默认值、返回类型完整
- **共享 helper 先提取**：`row_mappers.py` / `normalizers.py` / `json_utils.py` 先于业务模块

## 验证命令

每拆一块跑对应链路：

```powershell
python -m unittest tests.test_store
python -m unittest tests.test_memory_timeline
python -m unittest tests.test_retrieval_service
python -m unittest tests.test_attachment_inbox
python -m unittest tests.test_generated_files
python -m unittest tests.test_gift_system tests.test_gift_assets
python -m unittest tests.test_task_workspace
python -m unittest tests.test_persona_system
git diff --check
```

## 风险控制

- **不一步到位**：每块搬迁后立即验证，别等到全拆完再跑
- **出问题就回退**：`git checkout -- store/core.py` 恢复
- **core.py 不追求极限瘦身**：700-1000 行也是成功，目标是把业务 CRUD 搬走
