"""One formal slash syntax for QQ commands, dispatch and help."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class QQCommandDefinition:
    name: str
    category: str
    usage: str
    description: str
    kind: str
    group_only: bool = False
    owner_only: bool = False
    care_only: bool = False


COMMANDS = (
    QQCommandDefinition("help", "帮助", "/help [分类] [文字] [页码]", "查看指令分类。", "help"),
    QQCommandDefinition(
        "模型", "模型", "/模型 [列表|名称|默认]", "查看当前模型，或切换当前会话的模型。", "selector", owner_only=True
    ),
    QQCommandDefinition(
        "思考", "模型", "/思考 [开|关]", "查看或设置思考开关，需模型支持。", "thinking", owner_only=True
    ),
    QQCommandDefinition("缓存", "模型", "/缓存 [昨天|日期]", "查看当前会话当天的缓存命中图。", "cache", owner_only=True),
    QQCommandDefinition("角色", "角色", "/角色 [列表|ID|默认|内置]", "查看或切换当前会话角色。", "selector"),
    QQCommandDefinition("服装", "角色", "/服装 [列表|ID|默认]", "查看或切换当前会话服装。", "selector"),
    QQCommandDefinition("回复", "回复", "/回复 [文字|语音|双发|默认]", "查看或切换当前会话的回复方式。", "reply"),
    QQCommandDefinition(
        "搭话",
        "群聊",
        "/搭话 [关闭|活跃|自然]",
        "活跃：近期参与后接话；自然：也会低频主动观察。",
        "attention",
        group_only=True,
    ),
    QQCommandDefinition("识图", "群聊", "/识图 [开|关]", "查看或设置本群识图开关。", "vision", group_only=True),
    QQCommandDefinition("私人记忆", "群聊", "/私人记忆 [开|关|状态]", "接收新的主人私人交流；仅主人可修改。", "private_memory", group_only=True),
    QQCommandDefinition(
        "表情", "群聊", "/表情 [开|关]", "本群表情图片投递；查看和修改需管理员或主人。", "emotion", group_only=True
    ),
    QQCommandDefinition("表情包", "角色", "/表情包 [情绪名]", "附带 QQ 商城表情，绑定到当前角色的情绪。", "mface"),
    QQCommandDefinition("签到", "养成", "/签到", "每日签到领取金币。", "simple", care_only=True),
    QQCommandDefinition(
        "养成", "养成", "/养成", "查看个人好感、金币、兑换额度及角色状态图。", "simple", care_only=True
    ),
    QQCommandDefinition("商店", "养成", "/商店 [页码] [文字]", "查看可购买物品与兑换额度。", "paged_list", care_only=True),
    QQCommandDefinition(
        "购买", "养成", "/购买 <物品> [×数量]", "例如 /购买 三色团子 ×2。", "required_item", care_only=True
    ),
    QQCommandDefinition("背包", "养成", "/背包 [页码] [文字]", "查看个人物品、数量与用途。", "paged_list", care_only=True),
    QQCommandDefinition("投喂", "养成", "/投喂 <物品> [×数量]", "使用背包里的物品。", "required_item", care_only=True),
    QQCommandDefinition(
        "供奉", "养成", "/供奉 [物品|状态]", "每日供奉奖励，或供奉背包物品。", "offering", care_only=True
    ),
    QQCommandDefinition("抽签", "养成", "/抽签", "抽取御神签。", "simple", care_only=True),
    QQCommandDefinition("赛钱", "养成", "/赛钱 <金币数>", "把金币投入赛钱箱。", "donate", care_only=True),
    QQCommandDefinition("赛钱箱", "养成", "/赛钱箱", "查看赛钱箱与香火状态。", "simple", care_only=True),
    QQCommandDefinition("额度", "养成", "/额度", "查看当前额度。", "simple", care_only=True),
    QQCommandDefinition(
        "材料",
        "材料",
        "/材料 [清理|删除原文件] [目标]",
        "省略目标为全部；目标可填最近、名称或句柄。清理保留原文件。",
        "workspace",
    ),
    QQCommandDefinition("停止", "材料", "/停止", "停止当前任务，仅任务发起者或主人可用。", "simple"),
    QQCommandDefinition(
        "权限", "权限", "/权限 [本机|扩展|全部] [开|询问|关]", "查看或设置访问权限。", "access", owner_only=True
    ),
    QQCommandDefinition("审批", "权限", "/审批", "查看待审批请求。", "simple", owner_only=True),
    QQCommandDefinition(
        "批准", "权限", "/批准 [编号]", "批准请求；只有一项时可省略编号。", "approval", owner_only=True
    ),
    QQCommandDefinition(
        "拒绝", "权限", "/拒绝 [编号]", "拒绝请求；只有一项时可省略编号。", "approval", owner_only=True
    ),
)
_BY_NAME = {command.name: command for command in COMMANDS}
_CATEGORIES = ("模型", "角色", "回复", "群聊", "养成", "材料", "权限", "插件")
_COMMAND_TOKEN = re.compile(r"[\w.-]+", re.UNICODE)
_MFACE_MEDIA_PLACEHOLDER = re.compile(r"\[(?:图片|表情|文件|语音|QQ系统表情[^\]]*|QQ商城表情[^\]]*)\]")


@dataclass(frozen=True, slots=True)
class QQHelpRequest:
    category: str = ""
    text_only: bool = False
    page: int = 1


@dataclass(frozen=True, slots=True)
class QQCommandResolution:
    definition: QQCommandDefinition
    payload: dict[str, Any] | None = None
    error: str = ""
    help_request: QQHelpRequest | None = None


def split_qq_command(message: str) -> tuple[str, str] | None:
    text = str(message or "").strip()
    if text.startswith("["):
        # The face being bound may precede /表情包 in the message chain.
        without_media = _MFACE_MEDIA_PLACEHOLDER.sub("", text).strip()
        if re.match(r"[/／]表情包(?:\s|$)", without_media):
            text = without_media
    if not text.startswith(("/", "／")):
        return None
    parts = text[1:].split(None, 1)
    if not parts or _COMMAND_TOKEN.fullmatch(parts[0]) is None:
        return None
    return parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""


def _parse_help(args: str) -> QQHelpRequest | None:
    category, text_only, page = "", False, 1
    seen_page = False
    for part in args.split():
        if part == "文字" and not text_only:
            text_only = True
        elif re.fullmatch(r"[1-9]\d?", part) and not seen_page:
            page, seen_page = int(part), True
        elif not category and part in _CATEGORIES:
            category = part
        else:
            return None
    return QQHelpRequest(category, text_only, page)


def resolve_qq_command(message: str) -> QQCommandResolution | None:
    parts = split_qq_command(message)
    if parts is None or (definition := _BY_NAME.get(parts[0])) is None:
        return None
    name, args = parts
    kind = definition.kind
    payload: dict[str, Any] | None = None
    if kind == "help":
        request = _parse_help(args)
        return QQCommandResolution(
            definition, help_request=request, error="" if request else f"用法：{definition.usage}"
        )
    if kind == "selector":
        if not args:
            payload = {"action": "current"}
        elif args == "列表":
            payload = {"action": "list"}
        elif args == "默认":
            payload = {"action": "default"}
        elif name == "角色" and args == "内置":
            payload = {"action": "builtin"}
        else:
            field = {"模型": "model", "角色": "pack_id", "服装": "outfit_id"}[name]
            payload = {"action": "switch", field: args}
    elif kind == "reply":
        modes = {"文字": "text", "语音": "voice", "双发": "both"}
        if not args or args == "默认":
            payload = {"action": "current" if not args else "default"}
        elif args in modes:
            payload = {"action": "switch", "reply_mode": modes[args]}
    elif kind in {"vision", "emotion", "thinking", "private_memory"}:
        actions = {"": "status", "开": "on", "关": "off"}
        if kind == "private_memory":
            actions["状态"] = "status"
        if args in actions:
            action = actions[args]
            if kind in {"vision", "thinking"}:
                action = {"status": "status" if kind == "vision" else "current", "on": "enable", "off": "disable"}[
                    action
                ]
            payload = {"action": action}
    elif kind == "attention":
        actions = {"": "status", "关闭": "off", "活跃": "engaged", "自然": "adaptive"}
        if args in actions:
            payload = {"action": actions[args]}
    elif kind == "paged_list":
        page, text_only, valid = 1, False, True
        seen_page = False
        for word in args.split():
            if word == "文字" and not text_only:
                text_only = True
            elif re.fullmatch(r"[1-9]\d?", word) and not seen_page:
                page, seen_page = int(word), True
            else:
                valid = False
                break
        if valid:
            payload = {"action": "shop_list" if name == "商店" else "backpack", "page": page, "text_only": text_only}
    elif kind == "cache":
        from companion_v01.cache_usage import cache_day
        try:
            payload = {"action": "cache", "day": cache_day(args).isoformat()}
        except ValueError:
            payload = None
    elif kind == "simple":
        if not args:
            payload = {
                "action": {
                    "签到": "checkin",
                    "养成": "status",
                    "抽签": "lottery",
                    "赛钱箱": "shrine",
                    "额度": "quota",
                    "审批": "list",
                    "停止": "stop",
                }[name]
            }
    elif kind == "required_item":
        if args:
            payload = {"action": "buy" if name == "购买" else "feed"}
            if args:
                payload["item_text"] = args
    elif kind == "offering":
        payload = {"action": "offering_status" if args == "状态" else "offering"}
        if args and args != "状态":
            payload["item_name"] = args
    elif kind == "donate":
        if re.fullmatch(r"[1-9]\d{0,5}", args):
            payload = {"action": "saiken", "amount": int(args)}
    elif kind == "mface":
        cleaned_args = _MFACE_MEDIA_PLACEHOLDER.sub("", args).strip()
        payload = {"emotion": re.sub(r"\s+", "_", cleaned_args)[:80] or "happy"}
    elif kind == "workspace":
        if not args:
            payload = {"action": "list"}
        else:
            operation, _, target = args.partition(" ")
            if operation in {"清理", "删除原文件"} and len(target) <= 120:
                payload = {
                    "action": "clear",
                    "target": "latest" if target == "最近" else target or "current",
                    "kind": "any",
                    "delete_storage": operation == "删除原文件",
                }
    elif kind == "access":
        words = args.split()
        scopes = {"本机": "ops", "扩展": "extensions", "全部": "all"}
        actions = {"开": "on", "询问": "ask", "关": "off"}
        if not args:
            payload = {"action": "status", "scope": "all"}
        elif len(words) in {1, 2} and (scope := scopes.get(words[0])):
            if len(words) == 1:
                payload = {"action": "status", "scope": scope}
            elif words[1] in actions:
                payload = {"action": actions[words[1]], "scope": scope}
    elif kind == "approval":
        if not args or re.fullmatch(r"[A-Za-z0-9_-]+", args):
            payload = {"action": "approve" if name == "批准" else "deny", "selector": args}
    if name == "审批" and payload:
        payload["selector"] = ""
    return QQCommandResolution(definition, payload, "" if payload is not None else f"用法：{definition.usage}")


@dataclass(frozen=True, slots=True)
class QQHelpEntry:
    usage: str
    description: str


@dataclass(frozen=True, slots=True)
class QQHelpDocument:
    title: str
    entries: tuple[QQHelpEntry, ...]
    footer: tuple[str, ...]
    category: str = ""
    page: int = 1
    page_count: int = 1
    theme: str = "neutral"

    def as_text(self) -> str:
        lines = [self.title, ""]
        for entry in self.entries:
            lines.extend((entry.usage, entry.description, ""))
        return "\n".join((*lines, *self.footer)).strip()


def build_qq_help(
    request: QQHelpRequest,
    *,
    is_group: bool,
    is_master: bool,
    master_configured: bool,
    care_enabled: bool,
    plugin_commands: tuple[str, ...] = (),
    character_pack_id: str = "",
    directed_commands: tuple[QQCommandDefinition, ...] = (),
) -> QQHelpDocument:
    visible = tuple(
        command
        for command in (*COMMANDS, *directed_commands)
        if command.kind != "help"
        and (is_group or not command.group_only)
        and (care_enabled or not command.care_only)
        and (is_master or not command.owner_only)
        and (master_configured or command.category != "权限")
    )
    plugins = tuple(
        sorted(
            {
                command
                for command in plugin_commands
                if (parts := split_qq_command(command)) is not None and parts[0] not in _BY_NAME
            }
        )
    )
    available = tuple(
        category
        for category in _CATEGORIES
        if any(command.category == category for command in visible) or (category == "插件" and plugins)
    )
    if request.category and request.category not in available:
        raise ValueError("当前会话没有这类可用指令。发送 /help 查看分类。")
    footer = ["群聊先 @ 目标 Bot" if is_group else "私聊直接发送"]
    if request.category == "群聊":
        footer.append("修改群设置：群主、管理员或主人；私人记忆仅主人。")
    if not request.category:
        summaries = {
            "模型": "模型切换 · 思考设置",
            "角色": "角色 · 服装 · 表情包",
            "回复": "文字 · 语音 · 双发",
            "群聊": "搭话 · 识图 · 表情 · 私人记忆",
            "养成": "签到 · 商店 · 背包 · 投喂",
            "材料": "工作台 · 任务控制",
            "权限": "访问权限 · 能力审批",
            "插件": "已启用的插件指令",
        }
        entries = tuple(QQHelpEntry(f"/help {category}", summaries[category]) for category in available)
        footer.append("文字版：/help 文字")
        title = "指令手册"
    elif request.category == "插件":
        entries = tuple(QQHelpEntry(command, "参数按插件说明填写。") for command in plugins)
        title = "插件"
    else:
        entries = tuple(
            QQHelpEntry(command.usage, command.description)
            for command in visible
            if command.category == request.category
        )
        title = request.category
    page_size = 8 if not request.category else 12
    page_count = max(1, (len(entries) + page_size - 1) // page_size)
    if request.page > page_count:
        raise ValueError(f"这个分类共 {page_count} 页。发送 /help {request.category} 1 查看第一页。")
    if page_count > 1:
        other_page = request.page + 1 if request.page < page_count else 1
        footer.append(f"{request.page}/{page_count} 页 · 翻页 /help {request.category} {other_page}")
    if request.category:
        page_suffix = f" {request.page}" if request.page > 1 else ""
        footer.append(f"首页 /help · 文字版 /help {request.category} 文字{page_suffix}")
    offset = (request.page - 1) * page_size
    return QQHelpDocument(
        title,
        entries[offset : offset + page_size],
        tuple(footer),
        request.category,
        request.page,
        page_count,
        "cecilia" if character_pack_id == "cecilia" else "neutral",
    )
