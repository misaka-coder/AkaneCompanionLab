# 研究型学习笔记 Agent：样本驱动版

这套提示词原本是阶段化、多文件流程。现在默认改为“样本驱动”：

```text
少读规则，多读用户已经认可的高质量笔记样本。
```

核心入口：

```text
docs/research_learning_agent_prompts/00_claude_code_entry.md
```

推荐在 Claude Code 中输入：

```text
请读取 docs/research_learning_agent_prompts/00_claude_code_entry.md，
然后作为研究型学习笔记 Agent 工作。
我接下来会给你一个学习目标，请先阅读 references/samples 下的参考样本，
再规划和写作。
```

## 样本库

样本已经复制到：

```text
docs\research_learning_agent_prompts\references\samples
```

其中：

- `akane_project` 更适合参考项目拆解、工程模块、源码结合方式。
- `numerical` 更适合参考基础知识体系、代码紧跟概念、从零铺垫的方式。

入口文件会要求模型先读 `references/samples/sample_index.md`，再按任务类型读取 2-4 篇具体样本。

## 旧阶段文件

以下文件保留为备用参考，不再默认要求全部读取：

- `01_runtime_core.md`
- `02_clarify.md`
- `03_planner.md`
- `04_source_research.md`
- `05_repo_analysis.md`
- `06_knowledge_mapping.md`
- `07_note_writer.md`
- `08_note_reviewer.md`
- `09_state_workspace.md`
- `10_style_examples.md`

如果某次测试发现模型又开始跑偏，再从这些文件里抽一小段规则补回入口提示词。

## 测试建议

更好的验证方式：

1. 给一个真实但不太细的学习目标。
2. 要求它先读 `references/samples/sample_index.md` 和几篇相关样本。
3. 让它先输出“本次写作策略”，不要直接写全文。
4. 确认策略后，再让它写入文件。
5. 审稿时重点看：是否像系统笔记，是否站在用户视角讲清卡点，是否有真实资料或项目连接。
