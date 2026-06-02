# 09 State Workspace

研究型学习笔记 Agent 必须维护共享任务区和状态机。

## 状态机

推荐状态：

```text
idle
-> clarifying
-> planning
-> waiting_for_confirmation
-> source_discovery
-> source_triage
-> repo_analysis
-> knowledge_mapping
-> note_generation
-> note_review
-> revision
-> delivery
```

## 状态说明

- `idle`：等待用户输入学习目标。
- `clarifying`：追问用户基础、目标、风格和约束。
- `planning`：生成初步学习路线。
- `waiting_for_confirmation`：等待用户确认路线。
- `source_discovery`：搜索资料、项目、文档、论文。
- `source_triage`：筛选资料，决定哪些值得用。
- `repo_analysis`：分析开源项目或本地项目。
- `knowledge_mapping`：把项目内容转成知识模块。
- `note_generation`：生成系统笔记。
- `note_review`：审稿，检查全面性、教学性和真实性。
- `revision`：根据审稿结果修改。
- `delivery`：交付笔记和下一步建议。

## 共享任务区模板

```text
用户目标：
用户基础：
用户偏好：
时间限制：
当前状态：
已确认路线：
候选资料：
候选项目：
已采用资料：
已放弃资料：
知识模块草案：
当前正在生成的笔记：
审稿意见：
下一步：
```

## 使用规则

1. 每个阶段开始前，先判断当前状态。
2. 每个阶段结束后，更新共享任务区。
3. 子任务只能围绕共享任务区工作，不能擅自改变用户目标。
4. 如果新信息改变了路线，需要回到 `planning` 或 `waiting_for_confirmation`。
5. 如果资料不足，需要回到 `source_discovery` 或明确提示资料不足。
6. 如果笔记审稿不通过，需要进入 `revision`，不能直接交付。

