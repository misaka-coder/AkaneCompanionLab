"""Stable feedback and cache contract for final-response generation."""

import re

FINAL_RESPONSE_TEMPERATURE = 0.8
FINAL_RESPONSE_RECOVERY_FEEDBACK = (
    "【宿主反馈】\n"
    "上一条输出没有形成有效的工具调用或可交付回复。\n"
    "请基于同一用户请求和已经存在的工具结果继续：如果仍需操作，请发出合法工具调用；"
    "如果任务已完成，请输出符合协议的交付回复。\n"
    "不要重复已完成的工具，不要讨论这次格式错误。"
)
FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK = (
    "【宿主反馈】\n"
    "上一条回复在 JSON 对象外还有用户可见文字；只取 JSON 里的内容会丢失那部分文字，因此本次没有交付。\n"
    "请保留完整答案，但只输出一个 JSON 对象，并沿用当前客户端的字段说明：该写的正文和朗读字段都要填全。"
    "不要重复已完成的工具，不要讨论这次格式修正。"
)
FINAL_RESPONSE_HISTORY_RECORD_FEEDBACK = (
    "【宿主反馈】\n"
    "上一条返回的是供回顾过去对话使用的历史显示记录。\n"
    "请基于当前用户请求和已有工具结果生成实际答复；最终答复使用当前客户端字段说明及完整 JSON 示例，"
    "各组显示文字、朗读文字和表达方式填写当前答复的实际内容。"
    "需要继续操作时，沿用现有工具调用协议。"
)
FINAL_TOOL_PROTOCOL_RECOVERY_FEEDBACK = (
    "【宿主反馈】\n"
    "上一条输出包含文本化的工具协议，没有形成可执行的原生工具调用，因此不会交付给用户。\n"
    "如果仍需操作，请使用当前提供的原生工具入口发出结构化工具调用；"
    "不要把 DSML、XML 标签或工具协议写进 speech。"
    "如果任务已完成，请正常输出可交付回复。不要重复已完成的工具。"
)
FINAL_RESPONSE_PLAIN_TEXT_FEEDBACK = (
    "【宿主反馈】\n"
    "连续多次输出仍没有形成有效的工具调用或可交付回复。"
    "本次请直接用纯文本向用户说明当前实际完成度、已有证据和具体阻塞；"
    "不要输出 JSON，不要调用工具，也不要把未完成事项写成已完成。"
)
_JSON_FRAGMENT_RE = re.compile(r'[{}]|"[A-Za-z_][A-Za-z0-9_]*"\s*:')
_TEXT_TOOL_PROTOCOL_RE = re.compile(
    r"(?is)(?:DSML.{0,160}(?:tool_calls?|invoke|parameter)|"
    r"(?:tool_calls?|invoke|parameter).{0,160}DSML)"
)
FINAL_PROMPT_CACHE_LAYOUT_VERSION = "responses-unified-timeline-v3"
