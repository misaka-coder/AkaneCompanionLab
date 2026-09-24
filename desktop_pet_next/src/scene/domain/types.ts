export type {
  Identity,
  Snapshot,
  Catalog,
  RoomState,
  Asset,
  Outfit,
  Beat,
  Presentation,
  ActionRequest,
  ActionResult,
  Receipt,
  StoryCatalogResponse,
  StoryItemSummary,
  StoryRunState,
  StoryNode,
  StoryChoiceOption,
  StoryStartRequest,
  StoryStepRequest,
  StoryCompleteEndingRequest,
} from "../contracts/scene.generated";

export type Drawer =
  | "wardrobe"
  | "pantry"
  | "places"
  | "music"
  | "resources"
  | "story"
  | "connection"
  | "history"
  | null;
export type TouchRegion = "head" | "hand" | "shoulder";
export interface Connection {
  backend: string;
  botId: string;
  profileId: string;
  sessionId: string;
  characterId: string;
  instanceId: string;
}
