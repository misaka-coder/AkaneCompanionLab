"""SDK-only deterministic turn game for the long-task contract."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from akane_plugin import EventReceipt, Plugin, ToolContext


PLUGIN_ID = "example.sdk-turn-game"
STATE_EVENT = "game.state_changed"
_games: dict[str, dict[str, Any]] = {}
_games_lock = asyncio.Lock()


def _state(game: dict[str, Any]) -> dict[str, Any]:
    return {
        "game_id": game["game_id"],
        "state_version": game["state_version"],
        "turn": game["turn"],
        "player_hp": game["player_hp"],
        "enemy_hp": game["enemy_hp"],
        "available_actions": list(game["available_actions"]),
        "status": game["status"],
    }


def _checkpoint_value(game: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"schema": 1, "task_id": game["task_id"], "state": state or _state(game)}


def _game_from_checkpoint(task, checkpoint: Any, game_id: str) -> dict[str, Any] | None:
    if not isinstance(checkpoint, dict) or checkpoint.get("schema") != 1:
        return None
    if str(checkpoint.get("task_id") or "") != task.task_id:
        return None
    state = checkpoint.get("state")
    required = ("state_version", "turn", "player_hp", "enemy_hp", "available_actions", "status")
    if not isinstance(state, dict) or str(state.get("game_id") or "") != game_id:
        return None
    if any(key not in state for key in required) or not isinstance(state.get("available_actions"), list):
        return None
    recovered = {
        "game_id": game_id,
        "task_id": task.task_id,
        "state_version": int(state["state_version"]),
        "turn": int(state["turn"]),
        "player_hp": int(state["player_hp"]),
        "enemy_hp": int(state["enemy_hp"]),
        "available_actions": list(state["available_actions"]),
        "status": str(state["status"]),
        "task": task,
    }
    if task.initial.status in {"cancelled", "stale"}:
        recovered["status"] = "cancelled"
    return recovered


async def _load_game(game_id: str, task_id: str, ctx: ToolContext, *, recover: bool = False):
    game = _games.get(game_id)
    if game is not None:
        return game
    if not task_id:
        return None
    task = await ctx.task.open(task_id, recover=recover)
    if recover:
        game = _game_from_checkpoint(task, task.initial.checkpoint, game_id)
        if game is not None:
            _games[game_id] = game
    return game


async def _save_checkpoint(game: dict[str, Any], state: dict[str, Any] | None = None):
    value = _checkpoint_value(game, state)
    return await game["task"].checkpoint(value, version=int(value["state"]["state_version"]))


async def _ensure_bindings(ctx: ToolContext) -> None:
    for subscription_id in (f"{PLUGIN_ID}.observe", f"{PLUGIN_ID}.timeline"):
        await ctx.events.bind(subscription_id)


async def _publish(ctx: ToolContext, game: dict[str, Any], changed_by: str) -> EventReceipt:
    state = _state(game)
    return await ctx.events.emit(
        STATE_EVENT,
        {**state, "changed_by": changed_by},
        event_key=f"{game['game_id']}:state:{game['state_version']}",
    )


async def _observe_and_request(ctx: ToolContext, game: dict[str, Any]):
    state = _state(game)
    observed = await ctx.observe(f"game:{game['game_id']}", state)
    if game["status"] != "running":
        return {
            "status": "rejected",
            "reason": "game_not_running",
            "state": state,
            "observation": observed.as_dict(),
        }
    turn = await ctx.request_turn(
        "Choose one legal action for the current game state.",
        {"game": state, "rules": "Submit an action with the exact state_version you observed."},
        observations={f"game:{game['game_id']}": observed.version},
        stale="reject",
        coalesce_key=f"game:{game['game_id']}",
    )
    return {"status": turn.status, "state": state, "observation": observed.as_dict(), "turn": turn.as_dict()}


plugin = Plugin(
    PLUGIN_ID,
    permissions=("event.emit", "context.observe", "agent.turn.request"),
)


@plugin.tool
async def create_game(ctx: ToolContext) -> dict[str, Any]:
    """Create one deterministic game and its host-owned long task."""
    async with _games_lock:
        game_id = f"game_{uuid.uuid4().hex}"
        task = await ctx.task.create(
            "game.match", {"game_id": game_id}, idempotency_key=f"game:{game_id}", pause_mode="cooperative"
        )
        if not task.task_id or task.initial.status == "rejected":
            return {"status": task.initial.status, "reason": task.initial.reason, "task": task.initial.as_dict()}
        started = await task.update(status="running")
        if started.status != "running":
            return {"status": started.status, "reason": started.reason, "task": started.as_dict()}
        game = {
            "game_id": game_id, "task": task, "task_id": task.task_id, "state_version": 1, "turn": 1,
            "player_hp": 10, "enemy_hp": 10,
            "available_actions": ["attack", "defend", "heal"], "status": "running",
        }
        checkpoint = await _save_checkpoint(game)
        if checkpoint.status not in {"running", "created"}:
            return {"status": checkpoint.status, "reason": checkpoint.reason, "task": checkpoint.as_dict()}
        _games[game_id] = game
        started = await task.status()
    await _ensure_bindings(ctx)
    event = await _publish(ctx, game, "create_game")
    return {"status": "created", "state": _state(game), "task": started.as_dict(), "event": event.as_dict()}


@plugin.tool
async def observe_state(game_id: str, task_id: str = "", ctx: ToolContext = None) -> dict[str, Any]:
    """Observe the latest game state without requesting a model turn."""
    async with _games_lock:
        game = await _load_game(game_id, task_id, ctx, recover=bool(task_id))
        if game is None:
            return {"status": "rejected", "reason": "game_not_found", "game_id": game_id}
        state = _state(game)
        task = await game["task"].status()
    observed = await ctx.observe(f"game:{game_id}", state)
    return {"status": "observed", "state": state, "task": task.as_dict(), "observation": observed.as_dict()}


@plugin.tool
async def event_status(dispatch_id: str, ctx: ToolContext) -> EventReceipt:
    """Read the complete host receipt for a state publication."""
    return await ctx.events.status(dispatch_id)


@plugin.tool
async def request_game_turn(game_id: str, task_id: str = "", ctx: ToolContext = None) -> dict[str, Any]:
    """Explicitly ask the normal host Agent queue to choose the next action."""
    async with _games_lock:
        game = await _load_game(game_id, task_id, ctx, recover=bool(task_id))
        if game is None:
            return {"status": "rejected", "reason": "game_not_found", "game_id": game_id}
        task = await game["task"].status()
        if task.status != "running" or game["status"] != "running":
            return {"status": "rejected", "reason": "game_not_running", "state": _state(game), "task": task.as_dict()}
        return await _observe_and_request(ctx, game)


@plugin.tool
async def submit_action(game_id: str, action: str, state_version: int, task_id: str = "", ctx: ToolContext = None) -> dict[str, Any]:
    """Apply one action only when it matches the current business version."""
    async with _games_lock:
        game = await _load_game(game_id, task_id, ctx, recover=bool(task_id))
        if game is None:
            return {"status": "rejected", "reason": "game_not_found", "game_id": game_id}
        task = await game["task"].status()
        before = _state(game)
        if task.status != "running" or game["status"] != "running":
            return {"status": "rejected", "reason": "game_not_running", "side_effect_applied": False, "state": before, "task": task.as_dict()}
        if state_version != game["state_version"]:
            return {
                "status": "stale", "reason": "game_state_changed", "retryable": True,
                "side_effect_applied": False, "latest_state_version": game["state_version"],
                "next_action": "observe_state", "state": before,
            }
        if action not in game["available_actions"]:
            return {"status": "rejected", "reason": "action_unavailable", "side_effect_applied": False, "state": before}
        candidate = dict(game)
        candidate["available_actions"] = list(game["available_actions"])
        if action == "attack":
            candidate["enemy_hp"] -= 3
        elif action == "defend":
            candidate["enemy_hp"] -= 1
        else:
            candidate["player_hp"] = min(10, candidate["player_hp"] + 2)
            candidate["enemy_hp"] -= 2
        if candidate["enemy_hp"] > 0:
            candidate["player_hp"] -= 2 if action == "attack" else 1
        candidate["turn"] += 1
        candidate["state_version"] += 1
        if candidate["enemy_hp"] <= 0:
            candidate["status"] = "won"
            candidate["available_actions"] = []
        elif candidate["player_hp"] <= 0:
            candidate["status"] = "lost"
            candidate["available_actions"] = []
        checkpoint = await _save_checkpoint(candidate)
        if checkpoint.status not in {"running", "created"}:
            return {"status": checkpoint.status, "reason": checkpoint.reason, "side_effect_applied": False, "state": before, "task": checkpoint.as_dict()}
        game.update({key: candidate[key] for key in ("player_hp", "enemy_hp", "turn", "state_version", "status", "available_actions")})
        if candidate["status"] in {"won", "lost"}:
            await game["task"].update(status="completed", result=_state(game))
        after = _state(game)
        task_after = await game["task"].status()
    await _ensure_bindings(ctx)
    event = await _publish(ctx, game, f"action:{action}")
    return {"status": "applied", "action": action, "side_effect_applied": True, "state": after, "task": task_after.as_dict(), "event": event.as_dict()}


@plugin.tool
async def pause_game(game_id: str, task_id: str = "", ctx: ToolContext = None) -> dict[str, Any]:
    """Pause only after a checkpointed cooperative boundary."""
    async with _games_lock:
        game = await _load_game(game_id, task_id, ctx, recover=bool(task_id))
        if game is None:
            return {"status": "rejected", "reason": "game_not_found", "game_id": game_id}
        checkpoint = await _save_checkpoint(game)
        if checkpoint.status not in {"running", "created"}:
            return {"status": checkpoint.status, "reason": checkpoint.reason, "task": checkpoint.as_dict(), "state": _state(game)}
        paused = await game["task"].pause_at_boundary()
        if paused.status != "paused":
            return {"status": paused.status, "reason": paused.reason, "task": paused.as_dict(), "state": _state(game)}
        next_state = _state(game)
        next_state["status"] = "paused"
        next_state["state_version"] += 1
        candidate = dict(game)
        candidate.update({key: next_state[key] for key in ("status", "state_version")})
        checkpoint = await _save_checkpoint(candidate, next_state)
        if checkpoint.status not in {"paused", "running"}:
            return {"status": checkpoint.status, "reason": checkpoint.reason, "task": checkpoint.as_dict(), "state": _state(game)}
        game.update({key: next_state[key] for key in ("status", "state_version")})
        paused = await game["task"].status()
        after = _state(game)
    await _ensure_bindings(ctx)
    event = await _publish(ctx, game, "pause_game")
    return {"status": "paused", "state": after, "task": paused.as_dict(), "event": event.as_dict()}


@plugin.tool
async def resume_game(game_id: str, task_id: str = "", ctx: ToolContext = None) -> dict[str, Any]:
    """Resume from the latest state and explicitly request a fresh turn."""
    async with _games_lock:
        game = await _load_game(game_id, task_id, ctx, recover=bool(task_id))
        if game is None:
            return {"status": "rejected", "reason": "game_not_found", "game_id": game_id}
        resumed = await game["task"].resume()
        if resumed.status == "created":
            resumed = await game["task"].update(status="running")
        if resumed.status != "running":
            return {"status": resumed.status, "reason": resumed.reason, "task": resumed.as_dict(), "state": _state(game)}
        next_state = _state(game)
        next_state["status"] = "running"
        next_state["state_version"] += 1
        candidate = dict(game)
        candidate.update({key: next_state[key] for key in ("status", "state_version")})
        checkpoint = await _save_checkpoint(candidate, next_state)
        if checkpoint.status not in {"running", "created"}:
            return {"status": checkpoint.status, "reason": checkpoint.reason, "task": checkpoint.as_dict(), "state": _state(game)}
        game.update({key: next_state[key] for key in ("status", "state_version")})
        resumed = await game["task"].status()
        after = _state(game)
        await _ensure_bindings(ctx)
        event = await _publish(ctx, game, "resume_game")
        turn = await _observe_and_request(ctx, game)
    return {"status": "resumed", "state": after, "task": resumed.as_dict(), "event": event.as_dict(), "turn": turn}


@plugin.tool
async def cancel_game(game_id: str, task_id: str = "", ctx: ToolContext = None) -> dict[str, Any]:
    """Cancel the host task; subsequent actions and turns are rejected."""
    async with _games_lock:
        game = await _load_game(game_id, task_id, ctx, recover=bool(task_id))
        if game is None:
            return {"status": "rejected", "reason": "game_not_found", "game_id": game_id}
        checkpoint = await _save_checkpoint(game)
        if checkpoint.status not in {"running", "paused", "created"}:
            return {"status": checkpoint.status, "reason": checkpoint.reason, "task": checkpoint.as_dict(), "state": _state(game)}
        cancelled = await game["task"].cancel()
        if cancelled.status == "cancelling":
            # The game owns this cooperative boundary. Confirm cancellation
            # only after the checkpoint above and before publishing no more
            # domain actions.
            cancelled = await game["task"].update(status="cancelled")
        if cancelled.status not in {"cancelled", "stale"}:
            return {"status": cancelled.status, "reason": cancelled.reason, "task": cancelled.as_dict(), "state": _state(game)}
        game.update({"status": "cancelled", "state_version": game["state_version"] + 1})
        cancelled = await game["task"].status()
        after = _state(game)
    await _ensure_bindings(ctx)
    event = await _publish(ctx, game, "cancel_game")
    return {"status": "cancelled", "state": after, "task": cancelled.as_dict(), "event": event.as_dict()}


@plugin.tool
async def republish_state(game_id: str, task_id: str = "", ctx: ToolContext = None) -> dict[str, Any]:
    """Republish the same business version to demonstrate event-key idempotency."""
    async with _games_lock:
        game = await _load_game(game_id, task_id, ctx, recover=bool(task_id))
        if game is None:
            return {"status": "rejected", "reason": "game_not_found", "game_id": game_id}
        await _ensure_bindings(ctx)
        first = await _publish(ctx, game, "republish_state")
        second = await _publish(ctx, game, "republish_state")
    return {"status": second.status, "first": first.as_dict(), "second": second.as_dict(), "state": _state(game)}


@plugin.on(STATE_EVENT, name="observe", sources=(PLUGIN_ID,), scope="conversation")
async def observe_event(event, ctx):
    state = dict(event.data)
    observation = await ctx.observe(f"game:{state['game_id']}", state)
    return {
        "event_id": event.event_id,
        "event_version": event.version,
        "state_version": state["state_version"],
        "observation": observation.as_dict(),
    }


@plugin.on(STATE_EVENT, name="timeline", sources=(PLUGIN_ID,), scope="conversation", persistence="timeline")
async def timeline_event(event, ctx):
    return {"event_id": event.event_id, "state_version": event.data["state_version"]}


def create_plugin():
    return plugin


__all__ = ["create_plugin"]
