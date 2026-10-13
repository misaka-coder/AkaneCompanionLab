"""Capability schema limits and built-in provider/workflow catalog specifications."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


CONFIG_SCHEMA_VERSION = 1
PROFILE_CONFIG_PATH_TEMPLATE = "users_data/<profile_user_id>/capabilities/capabilities.yaml"
HOST_MCP_REGISTRY_PATH_TEMPLATE = "users_data/capabilities/mcp_servers.yaml"
PROFILE_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
APPROVAL_MODE_TRUSTED_AUTO_ALLOW = "trusted_auto_allow"
APPROVAL_MODE_ASK_EACH_TIME = "ask_each_time"
APPROVAL_MODE_DISABLED = "disabled"
APPROVAL_FAMILY_OPERATIONS = "ops"
APPROVAL_FAMILY_EXTENSIONS = "extensions"
APPROVAL_POLICY_DEFAULT_MODE = APPROVAL_MODE_ASK_EACH_TIME

APPROVAL_MODES = {
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
    APPROVAL_MODE_ASK_EACH_TIME,
    APPROVAL_MODE_DISABLED,
}

APPROVAL_DISABLED_STATUSES = {
    "configured",
    "disabled",
    "error",
    "invalid_config",
    "misconfigured",
    "missing_config",
    "missing_executor",
    "missing_model",
    "missing_slot_mapping",
    "missing_workflow",
    "unavailable",
    "unreachable",
    "unsupported_platform",
}

PUBLIC_PROVIDER_FIELDS = {"enabled", "endpoint", "updatedAt", "lastHealth"}
PUBLIC_WORKFLOW_FIELDS = {"enabled", "workflowPath", "slotMapping", "updatedAt"}

PRIVATE_VOICE_PROFILE_FIELDS = {
    "providerId",
    "enabled",
    "displayName",
    "textLang",
    "promptLang",
    "mediaType",
    "refAudioPath",
    "promptText",
    "emotionVoiceMap",
    "streamingMode",
    "parallelInfer",
    "splitBucket",
    "batchSize",
    "topK",
    "topP",
    "temperature",
    "speedFactor",
    "fragmentInterval",
    "textSplitMethod",
    "irodoriVoiceId",
    "updatedAt",
}

PRIVATE_MCP_SERVER_FIELDS = {
    "enabled",
    "displayName",
    "catalogDescription",
    "activationMode",
    "pinnedTools",
    "transport",
    "command",
    "args",
    "cwd",
    "env",
    "url",
    "headers",
    "tools",
    "lowRiskAllowlist",
    "lastDiscovery",
    "updatedAt",
}

MCP_PROFILE_OVERRIDE_FIELDS = {
    "enabled",
    "activationMode",
    "pinnedTools",
    "lowRiskAllowlist",
}

PUBLIC_APPROVAL_POLICY_FIELDS = {"capabilityModes", "updatedAt"}
WORKFLOW_PATH_MAX_LENGTH = 220
WORKFLOW_SLOT_MAX_LENGTH = 80
VOICE_PROFILE_TEXT_MAX_LENGTH = 300
VOICE_PROFILE_PATH_MAX_LENGTH = 500
VOICE_MODEL_FOLDER_MAX_LENGTH = 500
VOICE_MODEL_FOLDER_MAX_SCAN_FILES = 240
VOICE_MODEL_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg"}
VOICE_MODEL_CONFIG_FILENAMES = ("tts_infer.yaml", "tts_infer.yml")
VOICE_MODEL_GPT_EXTENSIONS = {".ckpt"}
VOICE_MODEL_SOVITS_EXTENSIONS = {".pth"}
EMOTION_VOICE_ID_RE = re.compile(r"^[\w.-]{1,80}$", re.UNICODE)
EMOTION_VOICE_ALIAS_MAX_COUNT = 16
MCP_SERVER_ID_MAX_LENGTH = 80
MCP_SERVER_TEXT_MAX_LENGTH = 240
MCP_SERVER_PATH_MAX_LENGTH = 500
MCP_SERVER_ARG_MAX_LENGTH = 240
MCP_SERVER_ARG_MAX_COUNT = 24
MCP_SERVER_ENV_MAX_COUNT = 12
MCP_SERVER_HEADER_MAX_COUNT = 12
MCP_TOOL_NAME_MAX_LENGTH = 80
MCP_TOOL_DESCRIPTION_MAX_LENGTH = 240
MCP_ENV_PLACEHOLDER_RE = re.compile(r"\$\{[A-Z_][A-Z0-9_]{0,79}\}")
MCP_SECRET_MARKERS = ("api_key", "password", "secret", "token")
WORKFLOW_SLOT_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
WORKFLOW_COMFYUI_SLOT_PATH_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}\.inputs\.[A-Za-z0-9_.-]{1,120}$")
WORKFLOW_ASSET_HANDLE_MAX_LENGTH = 120
WORKFLOW_ASSET_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
MCP_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
MCP_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]{1,80}$")
WORKFLOW_CONFIG_FILE_MAX_BYTES = 4 * 1024 * 1024
HealthChecker = Callable[[str, int, float], tuple[bool, str]]


@dataclass(frozen=True)
class ProviderConfigSpec:
    id: str
    name: str
    type: str
    adapter: str
    default_endpoint: str
    used_by: tuple[str, ...]
    risk: str = "medium"


@dataclass(frozen=True)
class WorkflowConfigSpec:
    id: str
    capability_id: str
    workflow_id: str
    name: str
    description: str
    type: str
    adapter: str
    provider_id: str
    group: str
    used_by: tuple[str, ...]
    risk: str
    target: str
    output: str
    default_workflow_path: str
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]


CONFIGURABLE_PROVIDER_SPECS: tuple[ProviderConfigSpec, ...] = (
    ProviderConfigSpec(
        id="provider.comfyui.local",
        name="本地 ComfyUI",
        type="asset_processor",
        adapter="comfyui",
        default_endpoint="http://127.0.0.1:8188",
        used_by=("workshop", "image", "desktop_pet"),
    ),
    ProviderConfigSpec(
        id="provider.tts.gpt_sovits.local",
        name="本地 GPT-SoVITS",
        type="tts_provider",
        adapter="gpt_sovits",
        default_endpoint="http://127.0.0.1:9880",
        used_by=("voice", "desktop_pet"),
    ),
    ProviderConfigSpec(
        id="provider.tts.irodori.local",
        name="本地 Irodori-TTS",
        type="tts_provider",
        adapter="irodori",
        default_endpoint="http://127.0.0.1:9890",
        used_by=("voice", "desktop_pet", "qq"),
    ),
    ProviderConfigSpec(
        id="provider.asr.openai_compat.local",
        name="本地 OpenAI 兼容 ASR",
        type="asr_provider",
        adapter="openai_compat_asr",
        default_endpoint="http://127.0.0.1:8000",
        used_by=("voice", "desktop_pet"),
    ),
)

CONFIGURABLE_PROVIDER_BY_ID = {spec.id: spec for spec in CONFIGURABLE_PROVIDER_SPECS}

CONFIGURABLE_WORKFLOW_SPECS: tuple[WorkflowConfigSpec, ...] = (
    WorkflowConfigSpec(
        id="workflow.workshop.portrait.cutout",
        capability_id="workshop.portrait.cutout",
        workflow_id="workflow.comfyui.portrait_cutout",
        name="透明背景处理",
        description="角色工坊的立绘透明背景处理流程，绑定本地 ComfyUI 工作流后可用于角色素材整理。",
        type="asset_processor",
        adapter="comfyui",
        provider_id="provider.comfyui.local",
        group="workshop",
        used_by=("workshop", "desktop_pet"),
        risk="medium",
        target="character_pack_assets",
        output="transparent_png",
        default_workflow_path="workflows/comfyui/portrait_cutout.json",
        required_slots=("input_image_handle", "output_image_handle"),
        optional_slots=("mask_output_handle", "background_color", "padding", "alpha_threshold"),
    ),
)

CONFIGURABLE_WORKFLOW_BY_ID = {spec.id: spec for spec in CONFIGURABLE_WORKFLOW_SPECS}
