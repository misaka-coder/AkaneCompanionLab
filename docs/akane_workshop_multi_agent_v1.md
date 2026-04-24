# Akane Workshop Multi-Agent Blueprint V1

## 1. Goal

Akane Workshop is a future architecture for long-running, multi-step tasks.

The goal is not to replace Akane with many agents. Akane remains the only front-facing companion and coordinator. Specialist agents are background workrooms that handle scoped execution, such as video preparation, audio processing, transcription, document generation, and resource cleanup.

This design should be used when a task is too long, too multi-step, or too parallel for Akane to handle comfortably inside normal chat turns.

## 2. Core Principle

```text
Akane is the foreground self.
Specialist agents are background workrooms.
The resource store is the shared blackboard.
The task workspace is the process memory.
Final user-facing communication always returns to Akane.
```

Short tasks should still be handled directly by Akane through existing tools. Workshop should be reserved for tasks that benefit from background execution, dependency tracking, or parallel subtasks.

## 3. When To Use Workshop

Use Workshop for tasks like:

- Download a video, extract audio, transcribe it, summarize it, and generate a PDF.
- Batch-process multiple audio files into voice-training material.
- Process several documents, merge results, and export multiple formats.
- Run a long media pipeline where Akane should keep chatting with the user while work continues.

Do not use Workshop for simple tasks like:

- Convert one document to PDF.
- Trim one audio file.
- Send one generated file.
- Inspect one image or one attachment.

## 4. Agent Split

Agents should be split by professional workroom, not by single tool.

Recommended initial split:

- `video_agent`: video links, downloaded videos, video metadata, audio extraction, frame extraction.
- `audio_agent`: audio conversion, trimming, normalization, stem separation, cleanup, slicing, dataset preparation.
- `speech_agent`: transcription, subtitles, lyrics drafts, timestamped text.
- `document_agent`: markdown, txt, docx, pdf, xlsx, tables, formatting, summaries.
- `resource_agent`: generated-file organization, batch sending preparation, temporary artifact cleanup.

Avoid splitting into one agent per tool. That creates too much orchestration overhead.

## 5. Task Workspace

Every Workshop task should create a task workspace.

The workspace is not Akane's long-term memory. It is a temporary process memory for one task.

Suggested shape:

```json
{
  "task_id": "task_001",
  "owner": "Akane",
  "status": "running",
  "raw_request": {
    "source_message_id": "msg_123",
    "text": "Akane，把这个视频下载下来，转成文字稿，总结成 PDF 发我。"
  },
  "normalized_goal": "下载视频，提取音频，转写为文字稿，生成摘要 PDF，并发送给用户。",
  "success_criteria": [
    "视频已下载",
    "音频已提取",
    "文字稿已生成",
    "摘要 PDF 已生成",
    "最终文件已发送"
  ],
  "constraints": [
    "不删除原始文件",
    "高风险操作需要询问 Akane 或用户"
  ],
  "steps": [],
  "artifacts": [],
  "events": [],
  "pending_question": null
}
```

The rule is:

```text
Raw request is preserved.
Akane translates it into a normalized goal.
Each agent receives a scoped brief.
```

## 6. Agent Briefs

Agents should not receive the full user conversation by default.

Each agent receives:

- A short total-goal summary.
- Its own concrete subtask.
- Required input resource IDs.
- Expected output resource types.
- Safety and failure rules.

Example:

```json
{
  "agent": "audio_agent",
  "task": "从视频提取出的音频中分离人声，并把结果写入生成资源区。",
  "total_goal_summary": "用户想把一个视频整理成文字稿和 PDF。",
  "inputs": ["gen_audio_001"],
  "expected_outputs": ["vocal_audio", "instrumental_audio"],
  "constraints": [
    "不要删除原始音频",
    "不要直接发送文件给用户"
  ]
}
```

## 7. Resource Sharing

All agents and Akane share the same resource store, but they should not share the same full prompt context.

```text
Resource Store: shared files, images, audio, video, generated artifacts.
Resource Projection: role-specific rendered view of those resources.
```

Akane gets a conversational view:

- Recent focused images may include full visual observation.
- Focused documents may include full text.
- Audio/video usually shows metadata and available capabilities.
- Collapsed files/images become summary cards.

Specialist agents get an execution view:

- `audio_agent` gets audio paths, metadata, and audio tools.
- `document_agent` gets relevant document text and formatting requirements.
- `video_agent` gets URLs, video paths, metadata, and video tools.

Principle:

```text
Share resources, not full context.
Default to manifest view; expand only what the task needs.
```

## 8. Tool And Delivery Policy

Workshop agents should not directly send files or chat with the user.

Agents can:

- Read assigned resources.
- Call their specialist tools.
- Write outputs into the generated resource area.
- Update task status.
- Ask Akane for clarification.

Agents should not:

- Send generated files to the user.
- Delete long-term memory.
- Modify Akane persona cards.
- Modify gifts or world assets unless explicitly assigned.
- Talk directly to the user.

Generation and delivery must be separated:

```text
Specialist agent creates artifact -> generated resource area.
Akane reviews task result -> sends selected files to user.
Akane decides what to clean up.
```

This prevents a background agent from sending half-finished intermediate files.

## 9. Agent Communication

V1 should avoid direct agent-to-agent communication.

Agents coordinate through the task workspace:

```text
video_agent writes video output.
audio_agent reads that output once dependency is ready.
speech_agent reads the prepared audio.
document_agent reads transcript and summary.
```

If an agent has a problem, it writes a task event or pending question.

Example:

```json
{
  "event_type": "task_question",
  "task_id": "task_001",
  "from_agent": "speech_agent",
  "priority": "normal",
  "requires_user": false,
  "message": "音频太长，建议先切成 10 分钟片段后转写。"
}
```

Akane decides whether to authorize the next step or ask the user.

## 10. Question Handling

Questions from agents should be classified by risk.

Low risk:

- Rename an output file.
- Split a long audio file.
- Retry a failed conversion.
- Use docx instead of pdf if pdf generation fails.

Akane may answer automatically.

Medium risk:

- Ambiguous source resource.
- Output format unclear.
- Large task scope change.
- Platform requires a login or cookie.

Akane should ask the user naturally when appropriate.

High risk:

- Delete many files.
- Overwrite originals.
- Use private credentials.
- Upload sensitive files to an external service.
- Run high-cost operations.

The task must pause until user confirmation.

## 11. Task Events

Background agents should not interrupt Akane's current response directly.

They should write events to a queue:

```json
{
  "task_id": "task_001",
  "event_type": "completed",
  "priority": "normal",
  "message": "视频总结任务完成。",
  "artifacts": ["gen_041", "gen_042"]
}
```

Akane can receive these events in the next turn prompt, or the client can push an active notification if the mode supports it.

## 12. Cleanup

Akane should own cleanup decisions.

Cleanup should distinguish:

- `clean_scratch`: remove agent scratchpads and detailed execution logs.
- `clean_temp_artifacts`: remove temporary chunks, intermediate wav files, retry leftovers.
- `archive_result`: keep final generated outputs and a short task summary.
- `purge_task`: remove task workspace entirely after explicit confirmation or safe completion.

Final outputs should not be deleted automatically unless the user or Akane clearly decides they are no longer needed.

## 13. Minimal V1 Implementation Path

Recommended implementation order:

1. Define `task_workspace` persistence and status model.
2. Add `delegate_task`, `inspect_task`, `answer_task_question`, `cleanup_task`.
3. Add a single specialist agent first, preferably `document_agent` or `audio_agent`.
4. Ensure specialist outputs always go into generated resources and never auto-send.
5. Add task event injection into Akane's prompt.
6. Add delivery through existing batch send tools.
7. Add more agents only after one full workflow is stable.

Do not start with all agents at once.

## 14. Design Constitution

- Akane remains the only front-facing subject.
- Specialist agents are background workrooms.
- User raw request is preserved for traceability.
- Akane translates raw request into normalized goals and scoped briefs.
- Agents do not directly talk to the user.
- Agents do not directly send files.
- Agents coordinate through task workspace and shared resources.
- Resource content is projected per role, not blindly shared in full.
- Questions and risks return to Akane.
- Akane decides final delivery and cleanup.

## 15. Implementation Status (2026-04-23)

The first implementation slice is now in code. It focuses on a usable control
plane plus one constrained worker loop, not a full multi-worker swarm.

Done:

- Added `TaskWorkerService` as the background workshop executor.
- Added `delegate_task` so Akane can hand complex tasks to `document_agent`, `media_agent`, `speech_agent`, or `resource_agent`.
- Worker prompts include task workspace, attachment workspace, generated file workspace, and recent tool feedback.
- Workers use a restricted tool pool and cannot directly send files to users.
- Tool outputs flow back into generated resources / attachment workspace and are recorded on the task workspace.
- Worker completion, blocking, tool execution, and round-limit events are written as task events for Akane to see later.

Current boundary:

- V1 runs one delegated worker task at a time per background lane.
- Final user-facing explanation and delivery still belong to Akane.
- If a task needs active notification without a new user message, that should be added later at the client/gateway layer.
