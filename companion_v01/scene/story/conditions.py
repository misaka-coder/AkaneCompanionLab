"""Structured, safe condition evaluation for story branching.

Strictly avoids eval() or arbitrary expression execution.
Evaluates conditions against real snapshot facts (inventory, coins, equipment),
completed endings, and local story run variables.
"""
from __future__ import annotations

import re
from typing import Any, Sequence


_COMPARE_PATTERN = re.compile(r"^([\w:.-]+)\s*(>=|<=|==|!=|>|<)\s*(.+)$")


class ConditionResult(tuple):
    """A 2-tuple (is_satisfied: bool, reason: str) that also evaluates as a boolean."""
    def __new__(cls, is_satisfied: bool, reason: str = ""):
        return super().__new__(cls, (bool(is_satisfied), str(reason)))

    @property
    def ok(self) -> bool:
        return self[0]

    @property
    def reason(self) -> str:
        return self[1]

    def __bool__(self) -> bool:
        return self[0]


def evaluate_condition(
    condition_str: str,
    context: dict[str, Any] | None = None,
    *,
    inventory: dict[str, int] | None = None,
    coins: int = 0,
    equipment: str = "",
    completed_endings: Sequence[str] = (),
    variables: dict[str, Any] | None = None,
    story_id: str = "",
) -> ConditionResult:
    """Evaluate a single or composite condition safely without eval.

    Returns:
        ConditionResult(is_satisfied: bool, reason: str)
    """
    raw = (condition_str or "").strip()
    if not raw:
        return ConditionResult(True, "")

    # Populate from context dict if provided
    if context:
        if inventory is None:
            inventory = context.get("inventory")
        if coins == 0:
            coins = context.get("coins", 0)
        if not equipment:
            equipment = context.get("outfit_id") or context.get("equipment") or ""
        if not completed_endings:
            completed_endings = context.get("completed_endings") or ()
        if variables is None:
            variables = context.get("variables")
        if not story_id:
            story_id = context.get("story_id", "")

    # Logical OR: any:[...] or any(...) or ||
    if (raw.startswith("any:[") and raw.endswith("]")) or (raw.startswith("any(") and raw.endswith(")")):
        inner = raw[5:-1].strip() if raw.startswith("any:[") else raw[4:-1].strip()
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        if not parts:
            return ConditionResult(True, "")
        reasons = []
        for p in parts:
            res = evaluate_condition(
                p,
                inventory=inventory,
                coins=coins,
                equipment=equipment,
                completed_endings=completed_endings,
                variables=variables,
                story_id=story_id,
            )
            if res.ok:
                return ConditionResult(True, "")
            reasons.append(res.reason)
        return ConditionResult(False, "; ".join(reasons))

    if "||" in raw:
        parts = [p.strip() for p in raw.split("||") if p.strip()]
        if not parts:
            return ConditionResult(True, "")
        reasons = []
        for p in parts:
            res = evaluate_condition(
                p,
                inventory=inventory,
                coins=coins,
                equipment=equipment,
                completed_endings=completed_endings,
                variables=variables,
                story_id=story_id,
            )
            if res.ok:
                return ConditionResult(True, "")
            reasons.append(res.reason)
        return ConditionResult(False, "; ".join(reasons))

    # Logical AND: all:[...] or all(...) or comma / &&
    if (raw.startswith("all:[") and raw.endswith("]")) or (raw.startswith("all(") and raw.endswith(")")):
        inner = raw[5:-1].strip() if raw.startswith("all:[") else raw[4:-1].strip()
        parts = [p.strip() for p in inner.split(",") if p.strip()]
    elif "&&" in raw:
        parts = [p.strip() for p in raw.split("&&") if p.strip()]
    else:
        parts = [raw]

    if len(parts) > 1:
        for p in parts:
            res = evaluate_condition(
                p,
                inventory=inventory,
                coins=coins,
                equipment=equipment,
                completed_endings=completed_endings,
                variables=variables,
                story_id=story_id,
            )
            if not res.ok:
                return ConditionResult(False, res.reason)
        return ConditionResult(True, "")

    # Single atom condition
    atom = parts[0]
    inv = inventory or {}
    vars_dict = variables or {}

    # Match operator
    match = _COMPARE_PATTERN.match(atom)
    if match:
        left_key, op, right_val = match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
        # Strip quotes if string literal
        if (right_val.startswith('"') and right_val.endswith('"')) or (right_val.startswith("'") and right_val.endswith("'")):
            right_val = right_val[1:-1]

        # 1. inventory:<item_id> or inventory.<item_id> op count
        if left_key.startswith(("inventory:", "inventory.")):
            prefix_len = len("inventory:") if left_key.startswith("inventory:") else len("inventory.")
            item_id = left_key[prefix_len:].strip()
            current_qty = inv.get(item_id, 0)
            try:
                target_qty = int(right_val)
            except ValueError:
                return ConditionResult(False, f"invalid_inventory_target_qty:{right_val}")
            passed = _num_compare(current_qty, op, target_qty)
            if not passed:
                return ConditionResult(False, f"item_insufficient:{item_id}")
            return ConditionResult(True, "")

        # 2. coins op amount
        if left_key == "coins":
            try:
                target_coins = int(right_val)
            except ValueError:
                return ConditionResult(False, f"invalid_coins_target:{right_val}")
            passed = _num_compare(coins, op, target_coins)
            if not passed:
                return ConditionResult(False, f"coins_insufficient:{target_coins}")
            return ConditionResult(True, "")

        # 3. equipment / outfit op outfit_id
        if left_key in {"equipment", "outfit"}:
            passed = (equipment == right_val) if op == "==" else (equipment != right_val) if op == "!=" else False
            if not passed:
                return ConditionResult(False, f"equipment_mismatch:{right_val}")
            return ConditionResult(True, "")

        # 4. var:<key> or var.<key> or vars.<key> op value
        if left_key.startswith(("var:", "var.", "vars.")):
            if left_key.startswith("vars."):
                var_key = left_key[len("vars."):].strip()
            elif left_key.startswith("var."):
                var_key = left_key[len("var."):].strip()
            else:
                var_key = left_key[len("var:"):].strip()
            curr_val = vars_dict.get(var_key, "")
            # boolean normalize
            c_str = str(curr_val).lower()
            r_str = str(right_val).lower()
            if r_str in {"true", "false", "yes", "no"}:
                c_bool = c_str in {"1", "true", "yes", "on"}
                r_bool = r_str in {"1", "true", "yes", "on"}
                passed = (c_bool == r_bool) if op == "==" else (c_bool != r_bool) if op == "!=" else False
            elif r_str.isdigit() and c_str.isdigit():
                passed = _num_compare(int(c_str), op, int(r_str))
            else:
                passed = (curr_val == right_val) if op == "==" else (curr_val != right_val) if op == "!=" else False
            if not passed:
                return ConditionResult(False, f"variable_mismatch:{var_key}")
            return ConditionResult(True, "")

        # 5. Generic var name in vars_dict
        if left_key in vars_dict or not left_key.startswith(("inventory:", "inventory.", "completed:", "completed_endings.", "coins", "equipment", "outfit")):
            curr_val = vars_dict.get(left_key, "")
            c_str = str(curr_val).lower()
            r_str = str(right_val).lower()
            if r_str in {"true", "false", "yes", "no"}:
                c_bool = c_str in {"1", "true", "yes", "on"}
                r_bool = r_str in {"1", "true", "yes", "on"}
                passed = (c_bool == r_bool) if op == "==" else (c_bool != r_bool) if op == "!=" else False
            elif r_str.isdigit() and c_str.isdigit():
                passed = _num_compare(int(c_str), op, int(r_str))
            else:
                passed = (curr_val == right_val) if op == "==" else (curr_val != right_val) if op == "!=" else False
            if not passed:
                return ConditionResult(False, f"variable_mismatch:{left_key}")
            return ConditionResult(True, "")

    # Shorthand 1: inventory:<item_id> (meaning count >= 1)
    if atom.startswith(("inventory:", "inventory.")):
        prefix_len = len("inventory:") if atom.startswith("inventory:") else len("inventory.")
        item_id = atom[prefix_len:].strip()
        if inv.get(item_id, 0) < 1:
            return ConditionResult(False, f"item_insufficient:{item_id}")
        return ConditionResult(True, "")

    # Shorthand 2: completed:<ending_id> or completed_endings.has(...)
    if atom.startswith("completed_endings.has(") and atom.endswith(")"):
        e_id = atom[len("completed_endings.has("):-1].strip()
        target = f"{story_id}:{e_id}" if story_id else e_id
        if e_id not in completed_endings and target not in completed_endings:
            return ConditionResult(False, f"ending_not_completed:{e_id}")
        return ConditionResult(True, "")

    if atom.startswith("completed:"):
        rest = atom[len("completed:"):].strip()
        if ":" in rest:
            s_id, e_id = rest.split(":", 1)
            target = f"{s_id}:{e_id}"
        else:
            e_id = rest
            target = f"{story_id}:{e_id}" if story_id else e_id

        has_completed = e_id in completed_endings or target in completed_endings
        if not has_completed:
            return ConditionResult(False, f"ending_not_completed:{e_id}")
        return ConditionResult(True, "")

    # Shorthand 3: var boolean flag
    if atom.startswith(("var:", "var.", "vars.")):
        if atom.startswith("vars."):
            var_key = atom[len("vars."):].strip()
        elif atom.startswith("var."):
            var_key = atom[len("var."):].strip()
        else:
            var_key = atom[len("var:"):].strip()
        val = str(vars_dict.get(var_key, "")).lower()
        if val not in ("1", "true", "yes", "on"):
            return ConditionResult(False, f"variable_false:{var_key}")
        return ConditionResult(True, "")

    # Unknown condition syntax
    return ConditionResult(False, f"unsupported_condition_syntax:{atom}")


def _num_compare(actual: int, op: str, target: int) -> bool:
    if op == ">=":
        return actual >= target
    if op == "<=":
        return actual <= target
    if op == "==":
        return actual == target
    if op == "!=":
        return actual != target
    if op == ">":
        return actual > target
    if op == "<":
        return actual < target
    return False
