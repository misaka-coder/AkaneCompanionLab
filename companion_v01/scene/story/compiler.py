"""Story Markdown Compiler: compiles structured Markdown documents into StoryPack contracts."""
from __future__ import annotations

import re
from typing import Any
import yaml

from ..contracts import StoryChoiceOption, StoryNode, StoryPack


class StoryCompilationError(ValueError):
    """Raised when a story Markdown document fails validation."""
    pass


KEY_MAP = {
    "角色": "speaker",
    "说话人": "speaker",
    "speaker": "speaker",
    "表情": "emotion_id",
    "情绪": "emotion_id",
    "emotion": "emotion_id",
    "emotion_id": "emotion_id",
    "动作": "motion_id",
    "motion": "motion_id",
    "motion_id": "motion_id",
    "背景": "background_id",
    "场景": "background_id",
    "background": "background_id",
    "background_id": "background_id",
    "音乐": "music_id",
    "bgm": "music_id",
    "music": "music_id",
    "music_id": "music_id",
    "服装": "outfit_id",
    "装扮": "outfit_id",
    "衣服": "outfit_id",
    "outfit": "outfit_id",
    "outfit_id": "outfit_id",
    "costume": "outfit_id",
    "下一幕": "next_node_id",
    "下一个": "next_node_id",
    "下个节点": "next_node_id",
    "跳转": "next_node_id",
    "next": "next_node_id",
    "next_node_id": "next_node_id",
    "标题": "title",
    "提示": "title",
    "title": "title",
    "目标": "prompt_objective",
    "演出目标": "prompt_objective",
    "引导目标": "prompt_objective",
    "objective": "prompt_objective",
    "prompt_objective": "prompt_objective",
    "结局标题": "ending_title",
    "结局名": "ending_title",
    "ending_title": "ending_title",
    "结局总结": "ending_summary",
    "结局描述": "ending_summary",
    "ending_summary": "ending_summary",
    "最大轮数": "max_turns",
    "max_turns": "max_turns",
    "建议轮数": "suggested_turns",
    "suggested_turns": "suggested_turns",
    "快捷反应": "quick_reactions",
    "气泡": "quick_reactions",
    "quick_reactions": "quick_reactions",
}

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NODE_HEADER_RE = re.compile(r"^##\s+([A-Za-z0-9_.-]+)(?:\s+\[([A-Za-z0-9_.-]+)\])?\s*$", re.MULTILINE)
CHOICE_LINE_RE = re.compile(r"^-\s+(?:“|\")?(.*?)(?:”|\")?\s*->\s*([A-Za-z0-9_.-]+)\s*$", re.MULTILINE)


def compile_story_markdown(content: str) -> StoryPack:
    """Compile a Markdown script document into a validated StoryPack contract."""
    raw_text = content.strip()
    if not raw_text:
        raise StoryCompilationError("Story content cannot be empty.")

    # 1. Parse YAML frontmatter
    fm_match = FRONTMATTER_RE.match(raw_text)
    if not fm_match:
        raise StoryCompilationError("Missing YAML frontmatter (enclosed by '---') at top of document.")

    fm_raw = fm_match.group(1)
    try:
        frontmatter = yaml.safe_load(fm_raw) or {}
    except Exception as exc:
        raise StoryCompilationError(f"Failed to parse YAML frontmatter: {exc}")

    if not isinstance(frontmatter, dict):
        raise StoryCompilationError("Frontmatter must be a key-value YAML dictionary.")

    story_id = str(frontmatter.get("story_id") or "").strip()
    if not story_id:
        raise StoryCompilationError("Frontmatter is missing required field: 'story_id'.")
    if not re.match(r"^[A-Za-z0-9_.-]+$", story_id):
        raise StoryCompilationError(f"Invalid story_id '{story_id}': must only contain letters, numbers, _, -, .")

    title = str(frontmatter.get("title") or "").strip()
    if not title:
        raise StoryCompilationError("Frontmatter is missing required field: 'title'.")

    description = str(frontmatter.get("description") or "").strip()
    cover_image = str(frontmatter.get("cover_image") or "").strip()
    initial_node_id = str(frontmatter.get("initial_node_id") or "").strip()
    background_id = str(frontmatter.get("background") or frontmatter.get("background_id") or frontmatter.get("背景") or frontmatter.get("场景") or "").strip()
    music_id = str(frontmatter.get("bgm") or frontmatter.get("music") or frontmatter.get("music_id") or frontmatter.get("音乐") or "").strip()
    outfit_id = str(frontmatter.get("outfit") or frontmatter.get("outfit_id") or frontmatter.get("服装") or frontmatter.get("装扮") or "").strip()

    # 2. Slice body into nodes delimited by '## <node_id> [kind]'
    body_text = raw_text[fm_match.end():]
    header_matches = list(NODE_HEADER_RE.finditer(body_text))
    if not header_matches:
        raise StoryCompilationError("No story nodes found. Each node must begin with '## <node_id> [kind]'.")

    nodes: dict[str, StoryNode] = {}
    for i, match in enumerate(header_matches):
        node_id = match.group(1).strip()
        kind_tag = (match.group(2) or "").strip().lower()

        # Find the slice of text belonging to this node
        start_pos = match.end()
        end_pos = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(body_text)
        node_body = body_text[start_pos:end_pos].strip()

        node = _parse_single_node(node_id=node_id, kind_tag=kind_tag, body=node_body)
        if node.node_id in nodes:
            raise StoryCompilationError(f"Duplicate node_id '{node.node_id}' found.")
        nodes[node.node_id] = node

    # 3. Default initial_node_id if not explicitly provided
    if not initial_node_id:
        initial_node_id = header_matches[0].group(1).strip()

    # Propagate frontmatter defaults to initial node if not explicitly overridden on that node
    if initial_node_id in nodes:
        init_node = nodes[initial_node_id]
        if background_id and not init_node.background_id:
            init_node.background_id = background_id
        if music_id and not init_node.music_id:
            init_node.music_id = music_id
        if outfit_id and not init_node.outfit_id:
            init_node.outfit_id = outfit_id

    pack = StoryPack(
        story_id=story_id,
        title=title,
        description=description,
        cover_image=cover_image,
        initial_node_id=initial_node_id,
        background_id=background_id,
        music_id=music_id,
        outfit_id=outfit_id,
        nodes=nodes,
    )
    validate_story_pack_graph(pack)
    return pack


def validate_story_pack_graph(pack: StoryPack) -> None:
    """Validate that initial_node_id and all next_node_id / choice targets exist in pack.nodes."""
    if not pack.initial_node_id or pack.initial_node_id not in pack.nodes:
        raise StoryCompilationError(
            f"initial_node_id '{pack.initial_node_id}' does not exist in declared nodes: {list(pack.nodes.keys())}"
        )

    for nid, node in pack.nodes.items():
        if node.next_node_id and node.next_node_id not in pack.nodes:
            raise StoryCompilationError(
                f"Node '{nid}' references nonexistent next_node_id '{node.next_node_id}'."
            )
        for opt in node.options:
            if opt.next_node_id not in pack.nodes:
                raise StoryCompilationError(
                    f"Node '{nid}' choice '{opt.label}' references nonexistent next_node_id '{opt.next_node_id}'."
                )


def _parse_single_node(node_id: str, kind_tag: str, body: str) -> StoryNode:
    """Parse key-values, dialogue, choices, and agent prompts from a node body."""
    lines = body.splitlines()
    fields: dict[str, Any] = {}
    content_lines: list[str] = []
    choice_lines: list[str] = []
    in_choice_block = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not in_choice_block:
                content_lines.append("")
            continue

        # Check choice block header
        if stripped.startswith("?") or stripped.startswith("选择肢") or stripped.startswith("选项:") or stripped.startswith("选项："):
            in_choice_block = True
            continue

        if in_choice_block:
            # Lines matching '- label -> target'
            if stripped.startswith("-") or stripped.startswith("*"):
                choice_lines.append(stripped)
            continue

        # Check key-value pairs (e.g. '角色: Akane', '下一幕: intro_2')
        colon_idx = -1
        for sep in [":", "："]:
            pos = line.find(sep)
            if pos > 0 and (colon_idx == -1 or pos < colon_idx):
                colon_idx = pos

        if colon_idx > 0:
            key_candidate = line[:colon_idx].strip().lower()
            val_candidate = line[colon_idx + 1:].strip()
            if key_candidate in KEY_MAP:
                canonical_field = KEY_MAP[key_candidate]
                fields[canonical_field] = val_candidate
                continue

        # Ordinary text paragraph / dialogue line
        content_lines.append(line)

    # Reassemble speech text
    speech_text = "\n".join(content_lines).strip()

    # Parse choices with optional bracket tags [condition: ...] [action: feed:dango] [cost: 7]
    options: list[StoryChoiceOption] = []
    for idx, c_line in enumerate(choice_lines, start=1):
        m = CHOICE_LINE_RE.match(c_line)
        if m:
            raw_label = m.group(1).strip()
            target_node_id = m.group(2).strip()

            cond_str = ""
            action_kind = ""
            action_target = ""
            action_count = 1
            cost_coins = 0
            cost_label = ""

            for tag in re.findall(r"\[(.*?)\]", raw_label):
                tag_str = tag.strip()
                colon = tag_str.find(":") if ":" in tag_str else tag_str.find("：")
                if colon > 0:
                    t_key = tag_str[:colon].strip().lower()
                    t_val = tag_str[colon + 1:].strip()
                    if t_key in {"condition", "cond", "条件"}:
                        cond_str = t_val
                    elif t_key in {"action", "动作"}:
                        parts = t_val.split(":") if ":" in t_val else t_val.split()
                        action_kind = parts[0].strip() if len(parts) > 0 else ""
                        if len(parts) > 1:
                            action_target = parts[1].strip()
                        if len(parts) > 2:
                            try:
                                action_count = int(parts[2])
                            except ValueError:
                                pass
                    elif t_key in {"cost", "花费", "消耗", "金币"}:
                        try:
                            num_match = re.search(r"\d+", t_val)
                            cost_coins = int(num_match.group(0)) if num_match else 0
                        except Exception:
                            cost_coins = 0
                        cost_label = t_val

            clean_label = re.sub(r"\[.*?\]", "", raw_label).strip()
            options.append(
                StoryChoiceOption(
                    id=f"opt_{idx}",
                    label=clean_label or raw_label,
                    next_node_id=target_node_id,
                    condition=cond_str,
                    action_kind=action_kind,
                    action_target=action_target,
                    action_count=action_count,
                    cost_coins=cost_coins,
                    cost_label=cost_label,
                )
            )

    # Determine kind
    kind = kind_tag
    if kind in {"conversation", "多轮对话", "对话"}:
        kind = "conversation"
    elif not kind:
        if options:
            kind = "choice"
        elif fields.get("ending_title") or fields.get("ending_summary"):
            kind = "ending"
        elif fields.get("max_turns") or fields.get("suggested_turns") or fields.get("quick_reactions"):
            kind = "conversation"
        elif fields.get("prompt_objective"):
            kind = "agent"
        else:
            kind = "script"

    if kind not in {"script", "choice", "agent", "conversation", "ending"}:
        raise StoryCompilationError(f"Node '{node_id}' has unknown kind '{kind}'. Must be script, choice, agent, conversation, or ending.")

    motion_id = fields.get("motion_id", "idle")
    if motion_id not in {"idle", "nod", "shake", "bounce"}:
        motion_id = "idle"

    quick_reactions: list[str] = []
    if fields.get("quick_reactions"):
        raw_qr = str(fields["quick_reactions"])
        if any(sep in raw_qr for sep in ["/", "|", "\n", ";", "；"]):
            quick_reactions = [q.strip() for q in re.split(r"[/|\n;；]", raw_qr) if q.strip()]
        else:
            quick_reactions = [q.strip() for q in re.split(r"[,，]", raw_qr) if q.strip()]

    max_turns = 8
    if fields.get("max_turns"):
        try:
            max_turns = int(fields["max_turns"])
        except ValueError:
            pass

    suggested_turns = 3
    if fields.get("suggested_turns"):
        try:
            suggested_turns = int(fields["suggested_turns"])
        except ValueError:
            pass

    return StoryNode(
        node_id=node_id,
        kind=kind,  # type: ignore[arg-type]
        title=fields.get("title", ""),
        speech=speech_text,
        speaker=fields.get("speaker", "Akane"),
        emotion_id=fields.get("emotion_id", "normal"),
        motion_id=motion_id,
        background_id=fields.get("background_id", ""),
        music_id=fields.get("music_id", ""),
        outfit_id=fields.get("outfit_id", ""),
        next_node_id=fields.get("next_node_id", ""),
        options=options,
        prompt_objective=fields.get("prompt_objective", ""),
        ending_title=fields.get("ending_title", ""),
        ending_summary=fields.get("ending_summary", ""),
        max_turns=max_turns,
        suggested_turns=suggested_turns,
        quick_reactions=quick_reactions,
    )
