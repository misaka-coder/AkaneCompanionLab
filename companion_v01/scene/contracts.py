"""Versioned wire types. Exported to the scene client; no engine dependency."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Identity(Contract):
    profile_user_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    character_pack_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")


class Asset(Contract):
    id: str
    name: str
    url: str


class Outfit(Contract):
    id: str
    name: str
    emotions: list[Asset]
    scale: float = 1
    anchor_x: float = 0.5
    anchor_y: float = 1


class Catalog(Contract):
    revision: str
    backgrounds: list[Asset]
    outfits: list[Outfit]
    music: list[Asset] = Field(default_factory=list)


class CareState(Contract):
    enabled: bool
    reason: str = ""
    coins: int = 0
    hunger: int = 0
    energy: int = 0
    affection: int = 0
    inventory: dict[str, int] = Field(default_factory=dict)


class ShopItem(Contract):
    id: str
    name: str
    price: int
    description: str = ""
    effects: dict[str, int]


class RoomState(Contract):
    revision: int = 0
    outfit_id: str = ""
    background_id: str = ""
    music_id: str = ""
    position: Literal["center", "right"] = "center"


class Snapshot(Contract):
    protocol: Literal["scene_v1"] = "scene_v1"
    identity: Identity
    character_name: str
    room: RoomState
    care: CareState
    shop: list[ShopItem]
    catalog: Catalog


class ActionRequest(Contract):
    identity: Identity
    request_id: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=0)
    kind: Literal["equip", "background", "music", "position", "buy", "feed", "touch", "claim_allowance"]
    target: str = Field(default="", max_length=120)
    count: int = Field(default=1, ge=1, le=999)


class Fact(Contract):
    event_id: str
    kind: str
    target: str
    quantity: int = 1
    ok: bool
    reason: str
    item_name: str = ""
    effects: dict[str, int] = Field(default_factory=dict)


class ActionResult(Contract):
    ok: bool
    reason: str
    duplicate: bool = False
    event: Fact | None = None
    snapshot: Snapshot


class ModelBeat(Contract):
    speech: str = Field(min_length=1, max_length=3000)
    emotion_id: str = Field(default="normal", max_length=100)
    motion_id: Literal["idle", "nod", "shake", "bounce"] = "idle"
    advance: Literal["click", "auto", "audio_end"] = "click"


class ModelPresentation(Contract):
    beats: list[ModelBeat] = Field(min_length=1, max_length=24)


class Beat(ModelBeat):
    beat_id: str
    sequence: int


class Presentation(Contract):
    protocol: Literal["scene_presentation_v1"] = "scene_presentation_v1"
    turn_id: str
    generation: int
    resource_revision: str
    beats: list[Beat]
    diagnostics: list[str] = Field(default_factory=list)


class Receipt(Contract):
    identity: Identity
    turn_id: str
    beat_id: str
    generation: int
    kind: Literal["display_started", "text_revealed", "audio_started", "audio_completed", "audio_interrupted", "interrupted"]


class CancelRequest(Contract):
    identity: Identity
    request_id: str = Field(min_length=8, max_length=100)


class TurnRequest(Contract):
    identity: Identity
    request_id: str = Field(min_length=8, max_length=100)
    generation: int = Field(ge=1)
    message: str = Field(default="", max_length=6000)
    event_id: str = Field(default="", max_length=100)


class ImportRequest(Contract):
    identity: Identity
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["background", "outfit", "music"]
    data: str = Field(min_length=1, max_length=28_000_000)


class AddShopItemRequest(Contract):
    identity: Identity
    name: str = Field(min_length=1, max_length=60)
    price: int = Field(ge=0, le=999999)
    description: str = Field(default="", max_length=200)
    effects: dict[str, int] = Field(default_factory=dict)
    icon: str = Field(default="cookie", max_length=30)
    item_id: str = Field(default="", max_length=80)


class StoryChoiceOption(Contract):
    id: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=200)
    next_node_id: str = Field(min_length=1, max_length=60)
    condition: str = Field(default="", max_length=100)
    action_kind: str = Field(default="", max_length=60)
    action_target: str = Field(default="", max_length=120)
    action_count: int = Field(default=1, ge=1, le=999)
    cost_coins: int = Field(default=0, ge=0, le=999999)
    cost_label: str = Field(default="", max_length=60)


class StoryNode(Contract):
    node_id: str = Field(min_length=1, max_length=60)
    kind: Literal["script", "choice", "agent", "conversation", "ending"]
    title: str = Field(default="", max_length=100)
    speech: str = Field(default="", max_length=3000)
    speaker: str = Field(default="", max_length=80)
    emotion_id: str = Field(default="normal", max_length=100)
    motion_id: Literal["idle", "nod", "shake", "bounce"] = "idle"
    background_id: str = Field(default="", max_length=200)
    music_id: str = Field(default="", max_length=200)
    outfit_id: str = Field(default="", max_length=200)
    next_node_id: str = Field(default="", max_length=60)
    options: list[StoryChoiceOption] = Field(default_factory=list)
    prompt_objective: str = Field(default="", max_length=2000)
    ending_title: str = Field(default="", max_length=100)
    ending_summary: str = Field(default="", max_length=1000)
    beats: list[ModelBeat] = Field(default_factory=list)
    max_turns: int = Field(default=8, ge=1, le=50)
    suggested_turns: int = Field(default=3, ge=1, le=20)
    quick_reactions: list[str] = Field(default_factory=list)


class StoryPack(Contract):
    story_id: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_.-]+$")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    cover_image: str = Field(default="", max_length=200)
    initial_node_id: str = Field(min_length=1, max_length=60)
    background_id: str = Field(default="", max_length=200)
    music_id: str = Field(default="", max_length=200)
    outfit_id: str = Field(default="", max_length=200)
    nodes: dict[str, StoryNode] = Field(default_factory=dict)


class StoryRunState(Contract):
    run_id: str = Field(min_length=8, max_length=100)
    story_id: str = Field(min_length=1, max_length=60)
    current_node_id: str = Field(min_length=1, max_length=60)
    status: Literal["in_progress", "completed", "failed"] = "in_progress"
    current_node: StoryNode
    variables: dict[str, str] = Field(default_factory=dict)
    history: list[str] = Field(default_factory=list)
    completed_endings: list[str] = Field(default_factory=list)
    presentation: Presentation | None = None
    conversation_turns: list[dict[str, str]] = Field(default_factory=list)
    turn_count: int = 0


class StoryItemSummary(Contract):
    story_id: str
    title: str
    description: str
    cover_image: str
    has_active_run: bool = False
    current_node_id: str = ""
    status: str = "not_started"
    completed_endings: list[str] = Field(default_factory=list)


class StoryCatalogResponse(Contract):
    stories: list[StoryItemSummary]


class StoryStartRequest(Contract):
    identity: Identity
    story_id: str = Field(min_length=1, max_length=60)
    force_restart: bool = False


class StoryStepRequest(Contract):
    identity: Identity
    run_id: str = Field(min_length=8, max_length=100)
    expected_node_id: str = Field(min_length=1, max_length=60)
    choice_id: str = Field(default="", max_length=60)
    user_message: str = Field(default="", max_length=3000)
    advance_only: bool = False


class StoryCompleteEndingRequest(Contract):
    identity: Identity
    run_id: str = Field(min_length=8, max_length=100)
    ending_id: str = Field(min_length=1, max_length=60)


class StoryImportRequest(Contract):
    identity: Identity
    file_name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=5, max_length=10_000_000)


class SceneWire(Contract):
    """Export root, not an endpoint wrapper."""
    snapshot: Snapshot
    action_request: ActionRequest
    action_result: ActionResult
    presentation: Presentation
    receipt: Receipt
    turn_request: TurnRequest
    cancel_request: CancelRequest
    import_request: ImportRequest
    add_shop_item_request: AddShopItemRequest
    story_catalog_response: StoryCatalogResponse
    story_run_state: StoryRunState
    story_start_request: StoryStartRequest
    story_step_request: StoryStepRequest
    story_complete_ending_request: StoryCompleteEndingRequest
    story_import_request: StoryImportRequest
