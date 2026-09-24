"""Packaged tools, connection form, skill and supervised game event listener."""

from copy import deepcopy
import json
from pathlib import Path

from akane_plugin import Plugin, ToolContext

from .bridge import EVENT_TYPE, PLUGIN_ID, READ_TOOLS, MinecraftBridge


ROOT = Path(__file__).resolve().parent
CONNECTION_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["endpoint", "token", "companion"], "properties": {
        "endpoint": {"type": "string", "default": "http://127.0.0.1:8765/mcp",
                     "description": "本机 Numen MCP 地址，仅允许回环 HTTP /mcp。"},
        "token": {"type": "string", "minLength": 8, "maxLength": 1024, "description": "Numen Bearer 密钥"},
        "companion": {"type": "string", "minLength": 1, "maxLength": 64, "default": "Reimu",
                      "description": "默认同伴名称或 ID；可在 QQ 或桌宠聊天选择当前世界其他同伴。"},
        "timeout_seconds": {"type": "number", "minimum": 5, "maximum": 60, "default": 15},
        "session_minutes": {"type": "integer", "minimum": 0, "maximum": 120, "default": 0,
                            "description": "0 表示持续到主人停止或目标完成；正数为可选会话时限（分钟）。"},
    }}


def create_plugin():
    plugin = Plugin(PLUGIN_ID, version="0.1.5", permissions=("network.read", "event.emit", "context.observe"))
    bridge = MinecraftBridge()
    plugin.connection("numen", schema=CONNECTION_SCHEMA, private_fields=("token",),
                      description="Minecraft / Numen 本机同伴连接，仅指定主人可用。")

    @plugin.tool(description="Minecraft 同伴连接、游戏状态、控制会话与事件监听状态；只允许主人查询。", owner_only=True)
    async def status(ctx: ToolContext, companion: str = ""):
        return await bridge.inspect(ctx, companion)

    @plugin.tool(description="列出当前 Minecraft 世界中可控制的同伴名称和 ID。换世界、新建同伴或目标不存在时先查询；不要反复调用旧目标。", owner_only=True)
    async def list_companions(ctx: ToolContext):
        return await bridge.companions(ctx)

    @plugin.tool(description="按主人明确要求在当前世界创建 Minecraft 同伴；随后用 list_companions 确认。",
                 input_schema={"type": "object", "additionalProperties": False,
                               "properties": {"name": {"type": "string", "pattern": "^[A-Za-z0-9_]{3,16}$"}},
                               "required": ["name"]},
                 effects=("network",), risk="medium", confirm="first_time", owner_only=True)
    async def create_companion(payload, ctx: ToolContext):
        return await bridge.manage_companion("create_companion", payload, ctx)

    @plugin.tool(description="永久删除主人指定的 Minecraft 同伴，物品掉落，不能撤销。仅在主人明确要求删除时调用。",
                 effects=("network",), risk="high", confirm="always", owner_only=True)
    async def delete_companion(ctx: ToolContext, companion: str):
        return await bridge.manage_companion("delete_companion", {"companion": companion}, ctx)

    @plugin.tool(description="主人开始 Minecraft 陪玩并绑定当前聊天，返回后续动作必需的 control_id。"
                 "主人改变目标时也调用本工具更新 goal；同一聊天保留控制编号与在途任务。"
                 "只有主人明确要求开始或接管时调用；收到旧事件不可自行重连。",
                 effects=("network",), risk="medium", confirm="first_time", owner_only=True)
    async def start_session(ctx: ToolContext, goal: str, take_over: bool = False, companion: str = ""):
        return await bridge.start({"goal": goal, "take_over": take_over, "companion": companion}, ctx)

    @plugin.tool(description="主人停止 Minecraft 陪玩：撤销控制会话、停止事件续聊，并请求停止当前游戏任务。"
                 "以 body_stopped 确认结果；停止不需要旧 control_id。", effects=("network",), owner_only=True)
    async def stop_session(ctx: ToolContext):
        return await bridge.stop(ctx)

    def register_remote(item):
        name = item["name"]
        schema = deepcopy(item["inputSchema"])
        schema.setdefault("properties", {}).pop("companion", None)
        schema["required"] = [key for key in schema.get("required", []) if key != "companion"]
        schema["additionalProperties"] = False
        writing = name not in READ_TOOLS
        if not writing:
            schema["properties"]["companion"] = {"type": "string",
                "description": "可选，当前世界的同伴名称或 ID；省略时使用当前控制目标或默认同伴。先用 list_companions 查看。"}
        if writing:
            schema["properties"]["control_id"] = {"type": "string", "minLength": 1,
                "description": "当前 start_session 返回的控制编号；不猜测、不沿用已停止会话。"}
            if name != "blueprint":
                schema["required"].append("control_id")

        async def invoke(payload, ctx: ToolContext):
            return await bridge.invoke(name, payload, ctx)

        plugin.tool(invoke, name=name, input_schema=schema,
            description="Minecraft 同伴（仅主人）: " + item["description"],
            effects=("network",), risk="medium" if writing else "low",
            confirm="first_time" if writing else "never", owner_only=True)

    for item in json.loads((ROOT / "numen-tools.json").read_text(encoding="utf-8")):
        if item["name"] in {"list_companions", "create_companion", "delete_companion", "get_events"}:
            continue  # Managed selection/lifecycle above; one background inbox consumer.
        register_remote(item)

    @plugin.on(EVENT_TYPE, name="game_events", sources=(PLUGIN_ID,), scope="conversation")
    async def game_events(event, ctx):
        return await bridge.game_event(event, ctx)

    @plugin.background("game_listener")
    async def game_listener(ctx):
        await bridge.run(ctx)

    plugin.skill("minecraft-companion", root=ROOT / "skills" / "minecraft-companion")
    return plugin
