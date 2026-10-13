"""Host-owned speakers and service bindings for one ordinary agent turn.

The legacy memory APIs call their domain argument character_pack_id. Service
domains use a reserved prefix; resource/persona resolution stays host-owned.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import re
from typing import Any

from .desktop_pet_character_resources import load_character_dialogue_services
from .qq_commands import COMMANDS, QQCommandDefinition, split_qq_command

_ID = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_SERVICE_PREFIX = "service:"


@dataclass(frozen=True)
class DialogueTarget:
    kind: str
    speaker_id: str
    service_target: str = ""

    @property
    def memory_domain(self) -> str:
        if self.kind == "character":
            return self.speaker_id
        return f"{_SERVICE_PREFIX}{self.speaker_id}:{self.service_target}"

    @property
    def resource_pack_id(self) -> str:
        return self.speaker_id if self.kind == "character" else ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ServicePersona:
    id: str
    name: str
    command: str
    persona: str
    tools: tuple[str, ...]


@dataclass(frozen=True)
class DirectedInput:
    target: DialogueTarget | None
    body: str
    error: str = ""


class DialogueRegistry:
    def __init__(self, character_resources: Any, service_dir: Path | None = None):
        self.character_resources = character_resources
        self.service_dir = service_dir or Path(__file__).parent / "assets" / "dialogue_services"
        self.services: dict[str, ServicePersona] = {}
        self.errors: dict[str, str] = {}
        commands: set[str] = set()
        for path in sorted(self.service_dir.glob("*.json")):
            try:
                if path.is_symlink() or path.stat().st_size > 16384:
                    raise ValueError("service_definition_invalid")
                raw = json.loads(path.read_text(encoding="utf-8"))
                service_id = raw["id"]
                name, command, persona = raw["name"], raw["command"], raw["persona"]
                tools = raw["tools"]
                command_parts = split_qq_command(f"/{command}") if isinstance(command, str) else None
                if (
                    not isinstance(service_id, str)
                    or not _ID.fullmatch(service_id)
                    or not isinstance(name, str)
                    or not 1 <= len(name) <= 60
                    or not command_parts
                    or command_parts != (command.casefold(), "")
                    or len(command) > 60
                    or not isinstance(persona, str)
                    or not 1 <= len(persona) <= 8000
                    or not isinstance(tools, list)
                    or not tools
                    or any(not isinstance(t, str) or not _ID.fullmatch(t) for t in tools)
                    or service_id in self.services
                    or command.casefold() in commands
                ):
                    raise ValueError("service_definition_invalid")
                self.services[service_id] = ServicePersona(service_id, name, command, persona, tuple(tools))
                commands.add(command.casefold())
            except (OSError, ValueError, KeyError, TypeError):
                self.errors[path.stem] = "service_definition_invalid"

    def packs(self) -> dict[str, str]:
        loader = getattr(self.character_resources, "list_character_packs", None)
        resolver = getattr(self.character_resources, "_resolve_pack_dir", None)
        packs = {}
        for pack in loader() if callable(loader) else []:
            pack_id = str(pack.get("pack_id") or "") if isinstance(pack, dict) else ""
            if not _ID.fullmatch(pack_id) or (callable(resolver) and resolver(pack_id) is None):
                continue
            key = pack_id.casefold()
            # A case-insensitive slash token cannot choose an ambiguous ID.
            packs[key] = "" if key in packs else pack_id
        return packs

    def validate(self, target: DialogueTarget) -> DialogueTarget:
        packs = self.packs()
        if target.kind == "character":
            valid = bool(target.speaker_id) and target.speaker_id in packs.values() and not target.service_target
        else:
            valid = (
                target.kind == "service"
                and target.speaker_id in self.services
                and (not target.service_target or target.service_target in packs.values())
            )
        if not valid:
            raise ValueError("dialogue_target_unavailable")
        return target

    def restore(self, value: Any) -> DialogueTarget:
        if not isinstance(value, dict) or set(value) != {"kind", "speaker_id", "service_target"}:
            raise ValueError("dialogue_target_invalid")
        if any(not isinstance(v, str) for v in value.values()):
            raise ValueError("dialogue_target_invalid")
        return self.validate(DialogueTarget(**value))

    def from_domain(self, domain: str) -> DialogueTarget | None:
        if not is_service_domain(domain):
            return None
        parts = domain.split(":")
        if len(parts) != 3 or parts[1] not in self.services or (parts[2] and not _ID.fullmatch(parts[2])):
            raise ValueError("dialogue_target_unavailable")
        return DialogueTarget("service", parts[1], parts[2])

    def parse(self, message: str, *, default_pack: str, plugin_broker: Any = None) -> DirectedInput | None:
        parts = split_qq_command(message)
        if parts is None:
            return None
        command, body = parts
        if command in {c.name.casefold() for c in COMMANDS}:
            return None
        if plugin_broker is not None and plugin_broker.handles("/" + command):
            return None
        packs = self.packs()
        services = [s for s in self.services.values() if s.command.casefold() == command]
        if services and command in packs:
            return DirectedInput(None, body, "dialogue_command_conflict")
        if services:
            target = DialogueTarget("service", services[0].id, default_pack)
        elif command in packs:
            target = DialogueTarget("character", packs[command])
        else:
            return None
        try:
            self.validate(target)
            if target.kind == "service":
                self.service_persona(target)
        except ValueError as exc:
            return DirectedInput(None, body, str(exc))
        return DirectedInput(target, body, "dialogue_body_required" if not body.strip() else "")

    def service_persona(self, target: DialogueTarget) -> ServicePersona:
        """Keep the host definition and work domain, customize only presentation."""
        self.validate(target)
        base = self.services[target.speaker_id]
        customizations = (load_character_dialogue_services(self.character_resources, target.service_target)
                          if target.service_target else {})
        if len(customizations) > 16 or set(customizations) - self.services.keys():
            raise ValueError("service_customization_invalid")
        override = customizations.get(base.id, {})
        if not isinstance(override, dict) or set(override) - {"name", "persona"}:
            raise ValueError("service_customization_invalid")
        for key, limit in (("name", 60), ("persona", 2000)):
            if key in override and (not isinstance(override[key], str) or not 1 <= len(override[key].strip()) <= limit):
                raise ValueError("service_customization_invalid")
        return replace(base, name=override.get("name", base.name).strip(),
                       persona="\n".join(part for part in (base.persona, override.get("persona", "").strip()) if part))

    def persona_context(self, domain: str) -> dict[str, str] | None:
        target = self.from_domain(domain)
        if target is None:
            return None
        self.validate(target)
        service = self.service_persona(target)
        identity_builder = getattr(self.character_resources, "build_character_identity", None)
        identity = (
            identity_builder(target.service_target) if target.service_target and callable(identity_builder) else {}
        )
        owner_name = str(identity.get("assistant_name") or target.service_target or "当前主角色")
        return {
            "system_context": f"你是{service.name}。\n{service.persona}\n本次服务对象：{owner_name}。",
            "reference_context": "",
            "resource_context": "",
            "active_id": service.id,
        }

    def help_commands(self, plugin_broker: Any = None) -> tuple[QQCommandDefinition, ...]:
        entries = []
        if self.packs():
            entries.append(
                QQCommandDefinition(
                    "角色包ID", "角色", "/<角色包ID> 正文", "只对话这一句；ID 见 /角色 列表。", "directed"
                )
            )
        for service in self.services.values():
            parsed = self.parse(f"/{service.command} 正文", default_pack="", plugin_broker=plugin_broker)
            if parsed is not None and not parsed.error:
                entries.append(
                    QQCommandDefinition(
                        service.command, "角色", f"/{service.command} 正文", "单次对话，服务当前主角色。", "directed"
                    )
                )
        return tuple(entries)


def is_service_domain(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_SERVICE_PREFIX)


def registry_for(engine: Any) -> DialogueRegistry:
    registry = getattr(engine, "dialogue_registry", None)
    if not isinstance(registry, DialogueRegistry):
        resources = getattr(engine, "desktop_pet_character_resources", None)
        if type(resources).__name__ in {"Mock", "MagicMock"}:
            resources = None
        registry = DialogueRegistry(resources)
        try:
            engine.dialogue_registry = registry
        except Exception:
            pass
    return registry


def trusted_target(payload: Any) -> DialogueTarget | None:
    value = payload.get("_host_dialogue_target") if isinstance(payload, dict) else None
    return value if isinstance(value, DialogueTarget) else None
