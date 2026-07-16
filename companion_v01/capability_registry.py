from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol

from capcore import CapabilityToolSpec

from .client_protocol import ClientMode


DOCUMENT_ATTACHMENT_FORMATS = {
    "txt",
    "md",
    "markdown",
    "log",
    "lrc",
    "srt",
    "vtt",
    "json",
    "toml",
    "yaml",
    "yml",
    "csv",
    "ini",
    "cfg",
    "conf",
    "py",
    "js",
    "ts",
    "tsx",
    "jsx",
    "html",
    "css",
    "xml",
    "sql",
    "java",
    "c",
    "cpp",
    "h",
    "hpp",
    "cs",
    "go",
    "rs",
    "pdf",
    "docx",
    "xlsx",
}

DOCUMENT_GENERATED_FORMATS = {"txt", "md", "docx", "xlsx", "pdf", "json", "csv", "html"}
MEDIA_FORMATS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "mp4", "mov", "mkv", "webm", "avi"}
IMAGE_GENERATED_FORMATS = {"png", "jpg", "jpeg", "webp", "gif"}

COMMON_CLIENT_MODES = (ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D, ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)
WEB_SCENE_CLIENT_MODES = (ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D)
CHAT_FILE_CLIENT_MODES = (ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET)

COMMON_TOOL_NAMES = (
    "retrieve_memory",
    "read_memory_timeline",
    "load_character_context",
    "set_reminder",
    "list_reminders",
    "cancel_reminder",
    "manage_persona",
    "manage_task_workspace",
    "delegate_task",
)

WEB_SEARCH_TOOL_NAMES = ("web_search",)
DESKTOP_BROWSER_TOOL_NAMES = ("browser_page",)
DESKTOP_MUSIC_REQUEST_TOOL_NAMES = ("open_music_search",)
DESKTOP_WORKSPACE_TOOL_NAMES = (
    "list_workspace",
    "read_workspace",
    "focus_workspace",
    "register_workspace_items",
)

WEB_SCENE_TOOL_NAMES = (
    "call_npc",
    "check_inventory",
    "manage_gift",
    "manage_artifact",
)

REMOTE_MEDIA_TOOL_NAMES = ("fetch_media_from_url",)

ATTACHMENT_WORKSPACE_TOOL_NAMES = (
    "sync_attachment_workspace",
    "inspect_attachment",
    "retry_attachment",
    "clear_attachment_focus",
)

IMAGE_MATERIAL_TOOL_NAMES = ("load_material",)
IMAGE_GENERATION_TOOL_NAMES = ("generate_image",)
COVER_SONG_TOOL_NAMES = ("cover_song",)

DOCUMENT_WORKBENCH_TOOL_NAMES = (
    "read_attachment_section",
    "compose_file",
    "revise_generated_file",
    "apply_style_to_existing_file",
)

MEDIA_WORKBENCH_TOOL_NAMES = (
    "inspect_media_info",
    "separate_audio_stems",
    "clean_voice_track",
    "transcribe_media",
    "prepare_voice_dataset",
    "convert_media_file",
)

GENERATED_FILE_MANAGEMENT_TOOL_NAMES = (
    "inspect_generated_file",
    "manage_generated_file",
)

FILE_HANDOFF_TOOL_NAMES = ("send_file",)
CONVERSATION_FILE_AUTHORING_TOOL_NAMES = ("compose_file",)
QQ_STICKER_TOOL_NAMES = ("send_sticker",)


OPEN_BROWSER_TOOL_SPEC = CapabilityToolSpec(
    capability_id="open_browser",
    display_name="Open public page",
    description=(
        "当用户明确要求在自己的电脑上打开一个公开 HTTP(S) 网页时使用。"
        "这项能力只负责交给系统浏览器打开，不读取页面，不代表页面内容已被查看。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "minLength": 8,
                "maxLength": 1600,
                "description": "要交给用户系统浏览器打开的公开 HTTP(S) URL。",
            },
            "label": {
                "type": "string",
                "maxLength": 80,
                "description": "可选的页面简称，仅用于自然说明。",
            },
            "reason": {
                "type": "string",
                "maxLength": 120,
                "description": "为什么需要按用户要求打开该页面。",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["succeeded", "failed", "unavailable"]},
            "reason": {"type": "string"},
        },
        "required": ["status"],
        "additionalProperties": False,
    },
    risk="medium",
    confirm="first_time",
    effects=("external_url_open",),
    visible_in=("desktop", "qq"),
    spec_version="1.0.0",
    schema_version=1,
    execution_class="sync",
    idempotency="effectful",
    max_result_bytes=4096,
)


@dataclass(frozen=True)
class ExecutionReceipt:
    instance_id: str
    tool_id: str
    offer_id: str
    lease_epoch: str
    offer_expires_at: float
    spec_version: str
    schema_version: int
    schema_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "tool_id": self.tool_id,
            "offer_id": self.offer_id,
            "lease_epoch": self.lease_epoch,
            "offer_expires_at": self.offer_expires_at,
            "spec_version": self.spec_version,
            "schema_version": self.schema_version,
            "schema_hash": self.schema_hash,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ExecutionReceipt | None":
        if not isinstance(value, Mapping):
            return None
        try:
            receipt = cls(
                instance_id=str(value.get("instance_id") or "").strip(),
                tool_id=str(value.get("tool_id") or "").strip(),
                offer_id=str(value.get("offer_id") or "").strip(),
                lease_epoch=str(value.get("lease_epoch") or "").strip(),
                offer_expires_at=float(value.get("offer_expires_at") or 0),
                spec_version=str(value.get("spec_version") or "").strip(),
                schema_version=int(value.get("schema_version") or 0),
                schema_hash=str(value.get("schema_hash") or "").strip().lower(),
            )
        except (TypeError, ValueError):
            return None
        if not all(
            (
                receipt.instance_id,
                receipt.tool_id,
                receipt.offer_id,
                receipt.lease_epoch,
                receipt.spec_version,
                receipt.schema_hash,
            )
        ):
            return None
        return receipt


@dataclass(frozen=True)
class BrokerExecutionResult:
    status: str
    reason: str = ""
    model_feedback: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


class CapabilityOfferSource(Protocol):
    instance_id: str

    def resolve_receipt(self, spec: CapabilityToolSpec) -> ExecutionReceipt | None: ...

    def validate_receipt(self, spec: CapabilityToolSpec, receipt: ExecutionReceipt) -> str: ...

    def dispatch(
        self,
        *,
        spec: CapabilityToolSpec,
        receipt: ExecutionReceipt,
        invocation_id: str,
        arguments: Mapping[str, Any],
        timeout_seconds: float,
    ) -> BrokerExecutionResult: ...


class ExecutorBroker:
    """Minimal instance-owned broker for the first effectful satellite tool."""

    def __init__(self, offer_source: CapabilityOfferSource | None, *, clock=time.time) -> None:
        self.offer_source = offer_source
        self._clock = clock
        self._lock = threading.RLock()
        self._ledger: dict[str, BrokerExecutionResult | None] = {}

    def execute(
        self,
        *,
        spec: CapabilityToolSpec,
        receipt_value: Mapping[str, Any] | None,
        invocation_id: str,
        arguments: Mapping[str, Any],
        timeout_seconds: float = 15.0,
    ) -> BrokerExecutionResult:
        receipt = ExecutionReceipt.from_mapping(receipt_value)
        if receipt is None:
            return BrokerExecutionResult(
                status="rejected",
                reason="missing_execution_receipt",
                model_feedback="当前桌面动作没有有效的执行凭据，不能执行。",
            )
        if receipt.offer_expires_at <= float(self._clock()):
            return BrokerExecutionResult(
                status="unavailable_before_dispatch",
                reason="offer_expired",
                model_feedback="当前无法连接桌面执行器，请直接说明这次暂时不能打开网页。",
            )
        clean_invocation_id = str(invocation_id or "").strip()
        if not clean_invocation_id:
            return BrokerExecutionResult(status="rejected", reason="missing_invocation_id")
        with self._lock:
            if clean_invocation_id in self._ledger:
                existing = self._ledger[clean_invocation_id]
                if existing is None:
                    return BrokerExecutionResult(
                        status="running",
                        reason="duplicate_invocation_in_progress",
                        model_feedback="同一桌面动作已经在处理中，不会重复执行。",
                    )
                return existing
            self._ledger[clean_invocation_id] = None
            if len(self._ledger) > 512:
                terminal = [(key, value) for key, value in self._ledger.items() if value is not None]
                self._ledger = dict(terminal[-384:])
                self._ledger[clean_invocation_id] = None
        source = self.offer_source
        if source is None:
            result = BrokerExecutionResult(
                status="unavailable_before_dispatch",
                reason="executor_unavailable",
                model_feedback="当前无法连接桌面执行器，请直接说明这次暂时不能打开网页。",
            )
        else:
            reason = source.validate_receipt(spec, receipt)
            if reason:
                result = BrokerExecutionResult(
                    status="unavailable_before_dispatch",
                    reason=reason,
                    model_feedback="当前无法连接桌面执行器，请直接说明这次暂时不能打开网页。",
                )
            else:
                try:
                    result = source.dispatch(
                        spec=spec,
                        receipt=receipt,
                        invocation_id=clean_invocation_id,
                        arguments=dict(arguments),
                        timeout_seconds=max(1.0, min(30.0, float(timeout_seconds))),
                    )
                except Exception:
                    result = BrokerExecutionResult(
                        status="execution_unknown",
                        reason="executor_dispatch_failed",
                        model_feedback="桌面动作的执行结果暂时无法确认，请不要声称网页已经打开。",
                    )
        with self._lock:
            self._ledger[clean_invocation_id] = result
        return result


@dataclass(frozen=True)
class CapabilitySnapshot:
    client_mode: ClientMode
    has_any_attachment: bool = False
    has_document_attachment: bool = False
    has_media_attachment: bool = False
    has_image_attachment: bool = False
    has_generated_file: bool = False
    has_document_generated_file: bool = False
    has_media_generated_file: bool = False
    has_image_generated_file: bool = False
    has_workspace_file: bool = False
    has_document_workspace_file: bool = False
    has_media_workspace_file: bool = False
    has_image_workspace_file: bool = False
    has_cover_song_cache: bool = False
    has_pending_gift: bool = False


@dataclass(frozen=True)
class CapabilityModule:
    name: str
    layer: str
    modes: tuple[ClientMode, ...]
    tools: tuple[str, ...]
    light_hint: str
    trigger: Callable[[CapabilitySnapshot], bool]
    latent_reason: str = ""
    activation_hint: str = ""
    unavailable_reason: str = ""
    recovery_hint: str = ""

    def applies_to_mode(self, mode: ClientMode) -> bool:
        return mode in self.modes


@dataclass(frozen=True)
class CapabilitySelection:
    light_hints: tuple[str, ...]
    tool_names: tuple[str, ...]
    module_names: tuple[str, ...]
    layer_names: tuple[str, ...] = ()
    disclosures: tuple[CapabilityDisclosure, ...] = ()
    tool_specs: tuple[CapabilityToolSpec, ...] = ()
    execution_receipts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityDisclosure:
    """Small model-visible capability fact, separate from callable tool schemas."""

    capability_id: str
    state: str
    summary: str
    reason: str = ""
    activation: str = ""
    tool_names: tuple[str, ...] = ()
    unavailable_reason: str = ""
    recovery_hint: str = ""


def resolve_capability_disclosures(
    selection: CapabilitySelection,
    *,
    available_tool_names: tuple[str, ...] | list[str] | set[str],
) -> tuple[CapabilityDisclosure, ...]:
    """Apply runtime readiness results without exposing a hidden tool schema."""

    available = {str(name or "").strip() for name in available_tool_names if str(name or "").strip()}
    selected = set(selection.tool_names)
    resolved: list[CapabilityDisclosure] = []
    for disclosure in selection.disclosures:
        active_tools = selected.intersection(disclosure.tool_names)
        if disclosure.state != "ready" or not active_tools or active_tools.intersection(available):
            resolved.append(disclosure)
            continue
        resolved.append(
            replace(
                disclosure,
                state="unavailable",
                reason=(disclosure.unavailable_reason or "这项能力依赖的本地组件或外部服务当前没有通过可用性检查。"),
                activation=(disclosure.recovery_hint or "依赖恢复并通过下一次检查后，系统会自动重新开放对应工具。"),
            )
        )
    return tuple(resolved)


def _always(_: CapabilitySnapshot) -> bool:
    return True


def _has_any_attachment(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_any_attachment


def _has_document_context(snapshot: CapabilitySnapshot) -> bool:
    return (
        snapshot.has_document_attachment or snapshot.has_document_generated_file or snapshot.has_document_workspace_file
    )


def _has_media_context(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_media_attachment or snapshot.has_media_generated_file or snapshot.has_media_workspace_file


def _has_cover_song_context(snapshot: CapabilitySnapshot) -> bool:
    return _has_media_context(snapshot) or snapshot.has_cover_song_cache


def _has_image_context(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_image_attachment or snapshot.has_image_generated_file or snapshot.has_image_workspace_file


def _has_generated_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_generated_file


def _has_deliverable_file(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.has_any_attachment or snapshot.has_generated_file


def _is_web_scene(snapshot: CapabilitySnapshot) -> bool:
    return snapshot.client_mode in {ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D}


def _requests_external_browser_open(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    open_markers = ("打开", "浏览器", "open", "launch", "给我看", "跳转")
    target_markers = ("http://", "https://", "网页", "网站", "链接", "页面", "url")
    return any(marker in text for marker in open_markers) and any(marker in text for marker in target_markers)


class CapabilityRegistry:
    """Select lightweight ability hints and full tool instructions per turn."""

    def __init__(
        self,
        modules: tuple[CapabilityModule, ...] | None = None,
        *,
        offer_source: CapabilityOfferSource | None = None,
    ) -> None:
        self.modules = modules or self._default_modules()
        self.offer_source = offer_source

    def select(
        self,
        snapshot: CapabilitySnapshot,
        *,
        allowed_tool_names: tuple[str, ...] | None = None,
        hidden_tool_names: tuple[str, ...] = (),
        intent_text: str = "",
    ) -> CapabilitySelection:
        hints: list[str] = []
        tools: list[str] = []
        module_names: list[str] = []
        layer_names: list[str] = []
        disclosures: list[CapabilityDisclosure] = []
        seen_tools: set[str] = set()
        seen_hints: set[str] = set()
        seen_layers: set[str] = set()
        allowed = (
            {str(name or "").strip() for name in allowed_tool_names if str(name or "").strip()}
            if allowed_tool_names is not None
            else None
        )
        hidden = {str(name or "").strip() for name in hidden_tool_names if str(name or "").strip()}
        for module in self.modules:
            if not module.applies_to_mode(snapshot.client_mode):
                continue
            module_tools = tuple(
                tool_name
                for tool_name in module.tools
                if tool_name not in hidden and (allowed is None or tool_name in allowed)
            )
            if not module_tools:
                continue
            hint = module.light_hint.strip()
            if hint and hint not in seen_hints:
                seen_hints.add(hint)
                hints.append(hint)
            is_ready = module.trigger(snapshot)
            if hint and (is_ready or module.activation_hint.strip()):
                disclosures.append(
                    CapabilityDisclosure(
                        capability_id=module.name,
                        state="ready" if is_ready else "latent",
                        summary=hint,
                        reason="" if is_ready else module.latent_reason.strip(),
                        activation="" if is_ready else module.activation_hint.strip(),
                        tool_names=module_tools,
                        unavailable_reason=module.unavailable_reason.strip(),
                        recovery_hint=module.recovery_hint.strip(),
                    )
                )
            if not is_ready:
                continue
            module_names.append(module.name)
            layer = str(module.layer or "").strip()
            if layer and layer not in seen_layers:
                seen_layers.add(layer)
                layer_names.append(layer)
            for tool_name in module_tools:
                if tool_name in seen_tools:
                    continue
                seen_tools.add(tool_name)
                tools.append(tool_name)
        tool_specs: list[CapabilityToolSpec] = []
        execution_receipts: dict[str, Mapping[str, Any]] = {}
        if snapshot.client_mode in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}:
            receipt = self._resolve_offer_receipt(OPEN_BROWSER_TOOL_SPEC)
            browser_allowed = "open_browser" not in hidden and (allowed is None or "open_browser" in allowed)
            if receipt is not None and browser_allowed:
                if "open_browser" not in seen_tools:
                    tools.append("open_browser")
                    seen_tools.add("open_browser")
                if "desktop_browser_open" not in module_names:
                    module_names.append("desktop_browser_open")
                if "desktop_browser" not in seen_layers:
                    layer_names.append("desktop_browser")
                    seen_layers.add("desktop_browser")
                hint = OPEN_BROWSER_TOOL_SPEC.description
                if hint not in seen_hints:
                    hints.append(hint)
                    seen_hints.add(hint)
                disclosures.append(
                    CapabilityDisclosure(
                        capability_id="desktop_browser_open",
                        state="ready",
                        summary=hint,
                        tool_names=("open_browser",),
                    )
                )
                tool_specs.append(OPEN_BROWSER_TOOL_SPEC)
                execution_receipts["open_browser"] = receipt.as_dict()
            elif browser_allowed and _requests_external_browser_open(intent_text):
                disclosures.append(
                    CapabilityDisclosure(
                        capability_id="desktop_browser_open",
                        state="unavailable",
                        summary="可以按用户要求把公开网页交给其电脑上的系统浏览器打开。",
                        reason="当前没有在线且已授权的桌面执行器。",
                        activation="桌面客户端重新连接后，这项能力会自动恢复。",
                        tool_names=("open_browser",),
                    )
                )
        return CapabilitySelection(
            light_hints=tuple(hints),
            tool_names=tuple(tools),
            module_names=tuple(module_names),
            layer_names=tuple(layer_names),
            disclosures=tuple(disclosures),
            tool_specs=tuple(tool_specs),
            execution_receipts=execution_receipts,
        )

    def _resolve_offer_receipt(self, spec: CapabilityToolSpec) -> ExecutionReceipt | None:
        source = self.offer_source
        if source is None:
            return None
        try:
            return source.resolve_receipt(spec)
        except Exception:
            return None

    def tool_names_for_mode(self, mode: ClientMode) -> tuple[str, ...]:
        selected: list[str] = []
        seen: set[str] = set()
        for module in self.modules:
            if not module.applies_to_mode(mode):
                continue
            for tool_name in module.tools:
                if tool_name in seen:
                    continue
                seen.add(tool_name)
                selected.append(tool_name)
        return tuple(selected)

    def _default_modules(self) -> tuple[CapabilityModule, ...]:
        return (
            CapabilityModule(
                name="base",
                layer="common",
                modes=COMMON_CLIENT_MODES,
                tools=COMMON_TOOL_NAMES,
                light_hint="需要过去对话、长期事实、偏好或约定时用 retrieve_memory；需要具体日期/时段原始记录时用 read_memory_timeline。普通闲聊和稳定常识直接回复。你还可以设置/查看/取消提醒、维护表达侧面、记录任务或委派后台工坊。",
                trigger=_always,
            ),
            CapabilityModule(
                name="internet_access",
                layer="web",
                modes=COMMON_CLIENT_MODES,
                tools=WEB_SEARCH_TOOL_NAMES,
                light_hint="需要当前/最新/实时/近期的公开信息时用 web_search，不必等用户说“搜索”；例：日经指数、七月新番、最新模型价格。稳定常识和闲聊直接回复。不要访问私密、内网或登录内容。",
                trigger=_always,
                unavailable_reason="联网搜索服务当前正在检测，或没有通过所在网络节点的可用性检查。",
                recovery_hint="网络或搜索服务恢复后会自动重新开放；当前不要假装已经查到实时结果。",
            ),
            CapabilityModule(
                name="desktop_managed_browser",
                layer="desktop_browser",
                modes=(ClientMode.DESKTOP_PET,),
                tools=DESKTOP_BROWSER_TOOL_NAMES,
                light_hint="桌宠模式下，browser_page 会打开并操作 Akane 可见托管浏览器窗口，用于读取、滚动、按可见候选序号打开链接，以及经授权的点击/输入。不要接管用户手动打开的浏览器标签页，不要登录、下载、上传或访问私密/内网内容。",
                trigger=_always,
            ),
            CapabilityModule(
                name="desktop_music_request",
                layer="music_request",
                modes=(ClientMode.DESKTOP_PET,),
                tools=DESKTOP_MUSIC_REQUEST_TOOL_NAMES,
                light_hint="桌宠模式下，当用户明确要点歌或搜索一首歌来听时，可以用 open_music_search 打开公开音乐平台搜索页；它不代表已经播放成功，后续点击/输入仍按浏览器授权边界处理。",
                trigger=_always,
            ),
            CapabilityModule(
                name="desktop_file_workspace",
                layer="desktop_workspace",
                modes=(ClientMode.DESKTOP_PET,),
                tools=DESKTOP_WORKSPACE_TOOL_NAMES,
                light_hint="桌宠模式下，你始终拥有一个可主动查询的 Akane 文件工作区。用户提到刚放入、寻找、处理或清理某个文件时，先从 workspace:/ 调用 list_workspace 查询，不要先让用户提供本机绝对路径；你也可以批量读取、聚焦材料，并把文件原地登记为文档或媒体工具可用的附件 handle。",
                trigger=_always,
            ),
            CapabilityModule(
                name="remote_media_fetch",
                layer="shared_media",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=REMOTE_MEDIA_TOOL_NAMES,
                light_hint="你也可以先把公开音频/视频链接下载进当前工作台；如果用户只要原视频/原音频，下载后直接交付原文件，不要多做转写、转码或净化。",
                trigger=_always,
            ),
            CapabilityModule(
                name="attachment_workspace",
                layer="shared_attachment_workspace",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=ATTACHMENT_WORKSPACE_TOOL_NAMES,
                light_hint="你可以接收临时图片和文件，并整理当前工作台；文件交付由当前客户端自己的文件交付层处理。",
                trigger=_has_any_attachment,
            ),
            CapabilityModule(
                name="image_material_reload",
                layer="shared_image_material",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=IMAGE_MATERIAL_TOOL_NAMES,
                light_hint="需要重新观察当前会话较早的原图或生成图时，可以按 handle 加载原始图片；当前轮已经带图或摘要足够时不必重复加载。",
                trigger=_has_image_context,
                latent_reason="当前会话还没有可重新加载的图片材料。",
                activation_hint="用户上传图片或生成一张图片后，这项材料读取能力会自动开放。",
            ),
            CapabilityModule(
                name="image_generation",
                layer="shared_image_generation",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=IMAGE_GENERATION_TOOL_NAMES,
                light_hint="用户明确要文生图、图生图、融合多张图片或继续修改生成图时，可以调用已配置的云端图片生成能力；使用当前会话 img_/gen_ handle，不填写路径或 URL。",
                trigger=_always,
                unavailable_reason="当前配置的图片中转没有通过 Images API 可用性检查，因此没有暴露生图工具。",
                recovery_hint="中转恢复 Images API 或切换到支持生图的 provider 后，系统会自动重新开放；当前不要声称已经生成图片。",
            ),
            CapabilityModule(
                name="conversation_file_authoring",
                layer="shared_file_authoring",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=CONVERSATION_FILE_AUTHORING_TOOL_NAMES,
                light_hint="即使没有附件，你也可以把当前对话中已经整理好的内容直接生成文件并交给当前端；用户说开始/直接做/生成时，不要只口头承诺。",
                trigger=_always,
            ),
            CapabilityModule(
                name="qq_file_delivery",
                layer="qq_delivery",
                modes=(ClientMode.QQ_TEXT,),
                tools=FILE_HANDOFF_TOOL_NAMES,
                light_hint="在 QQ 里，你可以把已有工作台材料或生成文件发回给用户；只发送已有文件，不替代生成、转码或修改。",
                trigger=_has_deliverable_file,
            ),
            CapabilityModule(
                name="desktop_file_handoff",
                layer="desktop_workspace",
                modes=(ClientMode.DESKTOP_PET,),
                tools=FILE_HANDOFF_TOOL_NAMES,
                light_hint="在桌宠里，你可以把已有工作台材料或生成文件交给桌宠工作台打开、播放或继续处理；只交付已有文件，不替代生成、转码或修改。",
                trigger=_has_deliverable_file,
            ),
            CapabilityModule(
                name="sticker_pack",
                layer="qq_delivery",
                modes=(ClientMode.QQ_TEXT,),
                tools=QQ_STICKER_TOOL_NAMES,
                light_hint="你有一组静态表情包；聊天氛围适合时可以发送一张表情包，但不要为了展示功能而频繁发送。",
                trigger=_always,
            ),
            CapabilityModule(
                name="document_workbench",
                layer="shared_document",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=DOCUMENT_WORKBENCH_TOOL_NAMES,
                light_hint="你可以阅读、整理、转换和样式加工文本、Office、PDF 等文档。",
                trigger=_has_document_context,
                latent_reason="当前会话和可见工作区里还没有可处理的文档材料，因此没有展开文档读取与修改工具。",
                activation_hint="用户上传文档，或在桌宠的 Akane 工作区放入文档后会自动开放；若工作区文件尚无 handle，先登记再继续处理。当前对话内容仍可直接生成新文档。",
                unavailable_reason="文档处理组件当前没有通过可用性检查。",
                recovery_hint="文档组件恢复后会自动重新开放；已有材料无需重复上传。",
            ),
            CapabilityModule(
                name="media_workbench",
                layer="shared_media",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=MEDIA_WORKBENCH_TOOL_NAMES,
                light_hint="你可以处理音频/视频任务：转写、转码、降噪、分离人声、切片打包训练素材等。在 QQ 里这些媒体任务容易耗时，优先委派后台工坊；完成后再通知和交付。",
                trigger=_has_media_context,
                latent_reason="当前会话和可见工作区里还没有可处理的音频或视频，因此没有展开媒体处理工具。",
                activation_hint="用户上传音频/视频、提供可下载的公开媒体链接，或在桌宠的 Akane 工作区放入媒体文件后会自动开放；工作区文件可先登记为 handle。",
                unavailable_reason="媒体处理所需的本地组件当前没有通过可用性检查。",
                recovery_hint="媒体组件恢复后会自动重新开放；已有材料无需重复上传。",
            ),
            CapabilityModule(
                name="cover_song",
                layer="shared_media",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=COVER_SONG_TOOL_NAMES,
                light_hint=(
                    "你可以用本地角色音色翻唱用户提供的歌曲，并把转换后的人声与原伴奏重新混合成完整音频；"
                    "没有歌曲材料时请自然请用户发送，已完成的歌曲可以按歌名从缓存再次交付。"
                    "短任务直接调用工具完成；预计较久时可以委派后台工坊，不要否认已有能力。"
                ),
                trigger=_has_cover_song_context,
                latent_reason="当前还没有歌曲音频、视频或可复用的媒体结果，因此暂不展开翻唱工具。",
                activation_hint="用户上传一首歌、提供可下载的公开歌曲链接，或把歌曲放进桌宠的 Akane 工作区后即可触发；同时需要本机 RVC 服务和至少一个可用音色模型。",
                unavailable_reason="本机 RVC 服务、FFmpeg 或可用音色模型当前没有通过检查。",
                recovery_hint="启动本机 RVC 服务并准备可用音色模型后会自动重新开放；歌曲材料若已经存在，不需要再次上传。",
            ),
            CapabilityModule(
                name="generated_file_management",
                layer="shared_file_authoring",
                modes=CHAT_FILE_CLIENT_MODES,
                tools=GENERATED_FILE_MANAGEMENT_TOOL_NAMES,
                light_hint="你可以回看、交付、归档、删除或清理自己刚生成的文件。",
                trigger=_has_generated_file,
            ),
            CapabilityModule(
                name="web_scene_world",
                layer="web_scene",
                modes=WEB_SCENE_CLIENT_MODES,
                tools=WEB_SCENE_TOOL_NAMES,
                light_hint="你可以围绕当前场景、礼物、藏品和临时 NPC 参与小世界构建。",
                trigger=_is_web_scene,
            ),
        )


def is_document_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "document":
        return True
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    file_kind = str(detail.get("file_kind") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or detail.get("mime_type") or "").strip().lower()
    return file_kind in DOCUMENT_ATTACHMENT_FORMATS or mime_type.startswith("text/")


def is_media_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "audio":
        return True
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    file_kind = str(detail.get("file_kind") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or detail.get("mime_type") or "").strip().lower()
    return bool(detail.get("media_info")) or file_kind in MEDIA_FORMATS or mime_type.startswith(("audio/", "video/"))


def is_image_attachment(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    mime_type = str(item.get("mime_type") or "").strip().lower()
    return kind == "image" or mime_type.startswith("image/")


def is_document_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    return output_format in DOCUMENT_GENERATED_FORMATS


def is_media_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    return output_format in MEDIA_FORMATS


def is_image_generated_file(item: dict) -> bool:
    output_format = str(item.get("output_format") or item.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(item.get("mime_type") or "").strip().lower()
    return output_format in IMAGE_GENERATED_FORMATS or mime_type.startswith("image/")
