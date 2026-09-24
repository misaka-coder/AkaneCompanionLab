/* Generated from companion_v01.scene.contracts; run npm run scene:contracts. */

export type Protocol = "scene_v1";
export type ProfileUserId = string;
export type SessionId = string;
export type CharacterPackId = string;
export type CharacterName = string;
export type Revision = number;
export type OutfitId = string;
export type BackgroundId = string;
export type MusicId = string;
export type Position = "center" | "right";
export type Enabled = boolean;
export type Reason = string;
export type Coins = number;
export type Hunger = number;
export type Energy = number;
export type Affection = number;
export type Id = string;
export type Name = string;
export type Price = number;
export type Description = string;
export type Shop = ShopItem[];
export type Revision1 = string;
export type Id1 = string;
export type Name1 = string;
export type Url = string;
export type Backgrounds = Asset[];
export type Id2 = string;
export type Name2 = string;
export type Emotions = Asset[];
export type Scale = number;
export type AnchorX = number;
export type AnchorY = number;
export type Outfits = Outfit[];
export type Music = Asset[];
export type RequestId = string;
export type ExpectedRevision = number;
export type Kind = "equip" | "background" | "music" | "position" | "buy" | "feed" | "touch" | "claim_allowance";
export type Target = string;
export type Count = number;
export type Ok = boolean;
export type Reason1 = string;
export type Duplicate = boolean;
export type EventId = string;
export type Kind1 = string;
export type Target1 = string;
export type Quantity = number;
export type Ok1 = boolean;
export type Reason2 = string;
export type ItemName = string;
export type Protocol1 = "scene_presentation_v1";
export type TurnId = string;
export type Generation = number;
export type ResourceRevision = string;
export type Speech = string;
export type EmotionId = string;
export type MotionId = "idle" | "nod" | "shake" | "bounce";
export type Advance = "click" | "auto" | "audio_end";
export type BeatId = string;
export type Sequence = number;
export type Beats = Beat[];
export type Diagnostics = string[];
export type TurnId1 = string;
export type BeatId1 = string;
export type Generation1 = number;
export type Kind2 =
  "display_started" | "text_revealed" | "audio_started" | "audio_completed" | "audio_interrupted" | "interrupted";
export type RequestId1 = string;
export type Generation2 = number;
export type Message = string;
export type EventId1 = string;
export type RequestId2 = string;
export type Name3 = string;
export type Kind3 = "background" | "outfit" | "music";
export type Data = string;
export type Name4 = string;
export type Price1 = number;
export type Description1 = string;
export type Icon = string;
export type ItemId = string;
export type StoryId = string;
export type Title = string;
export type Description2 = string;
export type CoverImage = string;
export type HasActiveRun = boolean;
export type CurrentNodeId = string;
export type Status = string;
export type CompletedEndings = string[];
export type Stories = StoryItemSummary[];
export type RunId = string;
export type StoryId1 = string;
export type CurrentNodeId1 = string;
export type Status1 = "in_progress" | "completed" | "failed";
export type NodeId = string;
export type Kind4 = "script" | "choice" | "agent" | "conversation" | "ending";
export type Title1 = string;
export type Speech1 = string;
export type Speaker = string;
export type EmotionId1 = string;
export type MotionId1 = "idle" | "nod" | "shake" | "bounce";
export type BackgroundId1 = string;
export type MusicId1 = string;
export type OutfitId1 = string;
export type NextNodeId = string;
export type Id3 = string;
export type Label = string;
export type NextNodeId1 = string;
export type Condition = string;
export type ActionKind = string;
export type ActionTarget = string;
export type ActionCount = number;
export type CostCoins = number;
export type CostLabel = string;
export type Options = StoryChoiceOption[];
export type PromptObjective = string;
export type EndingTitle = string;
export type EndingSummary = string;
export type Speech2 = string;
export type EmotionId2 = string;
export type MotionId2 = "idle" | "nod" | "shake" | "bounce";
export type Advance1 = "click" | "auto" | "audio_end";
export type Beats1 = ModelBeat[];
export type MaxTurns = number;
export type SuggestedTurns = number;
export type QuickReactions = string[];
export type History = string[];
export type CompletedEndings1 = string[];
export type ConversationTurns = {
  [k: string]: string;
}[];
export type TurnCount = number;
export type StoryId2 = string;
export type ForceRestart = boolean;
export type RunId1 = string;
export type ExpectedNodeId = string;
export type ChoiceId = string;
export type UserMessage = string;
export type AdvanceOnly = boolean;
export type RunId2 = string;
export type EndingId = string;
export type FileName = string;
export type Content = string;

/**
 * Export root, not an endpoint wrapper.
 */
export interface SceneWire {
  snapshot: Snapshot;
  action_request: ActionRequest;
  action_result: ActionResult;
  presentation: Presentation;
  receipt: Receipt;
  turn_request: TurnRequest;
  cancel_request: CancelRequest;
  import_request: ImportRequest;
  add_shop_item_request: AddShopItemRequest;
  story_catalog_response: StoryCatalogResponse;
  story_run_state: StoryRunState;
  story_start_request: StoryStartRequest;
  story_step_request: StoryStepRequest;
  story_complete_ending_request: StoryCompleteEndingRequest;
  story_import_request: StoryImportRequest;
}
export interface Snapshot {
  protocol: Protocol;
  identity: Identity;
  character_name: CharacterName;
  room: RoomState;
  care: CareState;
  shop: Shop;
  catalog: Catalog;
}
export interface Identity {
  profile_user_id: ProfileUserId;
  session_id: SessionId;
  character_pack_id: CharacterPackId;
}
export interface RoomState {
  revision: Revision;
  outfit_id: OutfitId;
  background_id: BackgroundId;
  music_id: MusicId;
  position: Position;
}
export interface CareState {
  enabled: Enabled;
  reason: Reason;
  coins: Coins;
  hunger: Hunger;
  energy: Energy;
  affection: Affection;
  inventory: Inventory;
}
export interface Inventory {
  [k: string]: number;
}
export interface ShopItem {
  id: Id;
  name: Name;
  price: Price;
  description: Description;
  effects: Effects;
}
export interface Effects {
  [k: string]: number;
}
export interface Catalog {
  revision: Revision1;
  backgrounds: Backgrounds;
  outfits: Outfits;
  music: Music;
}
export interface Asset {
  id: Id1;
  name: Name1;
  url: Url;
}
export interface Outfit {
  id: Id2;
  name: Name2;
  emotions: Emotions;
  scale: Scale;
  anchor_x: AnchorX;
  anchor_y: AnchorY;
}
export interface ActionRequest {
  identity: Identity;
  request_id: RequestId;
  expected_revision: ExpectedRevision;
  kind: Kind;
  target: Target;
  count: Count;
}
export interface ActionResult {
  ok: Ok;
  reason: Reason1;
  duplicate: Duplicate;
  event: Fact | null;
  snapshot: Snapshot;
}
export interface Fact {
  event_id: EventId;
  kind: Kind1;
  target: Target1;
  quantity: Quantity;
  ok: Ok1;
  reason: Reason2;
  item_name: ItemName;
  effects: Effects1;
}
export interface Effects1 {
  [k: string]: number;
}
export interface Presentation {
  protocol: Protocol1;
  turn_id: TurnId;
  generation: Generation;
  resource_revision: ResourceRevision;
  beats: Beats;
  diagnostics: Diagnostics;
}
export interface Beat {
  speech: Speech;
  emotion_id: EmotionId;
  motion_id: MotionId;
  advance: Advance;
  beat_id: BeatId;
  sequence: Sequence;
}
export interface Receipt {
  identity: Identity;
  turn_id: TurnId1;
  beat_id: BeatId1;
  generation: Generation1;
  kind: Kind2;
}
export interface TurnRequest {
  identity: Identity;
  request_id: RequestId1;
  generation: Generation2;
  message: Message;
  event_id: EventId1;
}
export interface CancelRequest {
  identity: Identity;
  request_id: RequestId2;
}
export interface ImportRequest {
  identity: Identity;
  name: Name3;
  kind: Kind3;
  data: Data;
}
export interface AddShopItemRequest {
  identity: Identity;
  name: Name4;
  price: Price1;
  description: Description1;
  effects: Effects2;
  icon: Icon;
  item_id: ItemId;
}
export interface Effects2 {
  [k: string]: number;
}
export interface StoryCatalogResponse {
  stories: Stories;
}
export interface StoryItemSummary {
  story_id: StoryId;
  title: Title;
  description: Description2;
  cover_image: CoverImage;
  has_active_run: HasActiveRun;
  current_node_id: CurrentNodeId;
  status: Status;
  completed_endings: CompletedEndings;
}
export interface StoryRunState {
  run_id: RunId;
  story_id: StoryId1;
  current_node_id: CurrentNodeId1;
  status: Status1;
  current_node: StoryNode;
  variables: Variables;
  history: History;
  completed_endings: CompletedEndings1;
  presentation: Presentation | null;
  conversation_turns: ConversationTurns;
  turn_count: TurnCount;
}
export interface StoryNode {
  node_id: NodeId;
  kind: Kind4;
  title: Title1;
  speech: Speech1;
  speaker: Speaker;
  emotion_id: EmotionId1;
  motion_id: MotionId1;
  background_id: BackgroundId1;
  music_id: MusicId1;
  outfit_id: OutfitId1;
  next_node_id: NextNodeId;
  options: Options;
  prompt_objective: PromptObjective;
  ending_title: EndingTitle;
  ending_summary: EndingSummary;
  beats: Beats1;
  max_turns: MaxTurns;
  suggested_turns: SuggestedTurns;
  quick_reactions: QuickReactions;
}
export interface StoryChoiceOption {
  id: Id3;
  label: Label;
  next_node_id: NextNodeId1;
  condition: Condition;
  action_kind: ActionKind;
  action_target: ActionTarget;
  action_count: ActionCount;
  cost_coins: CostCoins;
  cost_label: CostLabel;
}
export interface ModelBeat {
  speech: Speech2;
  emotion_id: EmotionId2;
  motion_id: MotionId2;
  advance: Advance1;
}
export interface Variables {
  [k: string]: string;
}
export interface StoryStartRequest {
  identity: Identity;
  story_id: StoryId2;
  force_restart: ForceRestart;
}
export interface StoryStepRequest {
  identity: Identity;
  run_id: RunId1;
  expected_node_id: ExpectedNodeId;
  choice_id: ChoiceId;
  user_message: UserMessage;
  advance_only: AdvanceOnly;
}
export interface StoryCompleteEndingRequest {
  identity: Identity;
  run_id: RunId2;
  ending_id: EndingId;
}
export interface StoryImportRequest {
  identity: Identity;
  file_name: FileName;
  content: Content;
}
