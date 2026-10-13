import { DEFAULT_BACKEND_URL } from "./constants.js";

export function createWorkshopState() {
  return {
    view: {
      instanceId: "",
      packs: [],
      activePackId: "",
      activeSessionId: "",
      activeCharacterName: "",
      backendUrl: DEFAULT_BACKEND_URL,
      profileUserId: "master",
      activeTab: "list",
      desktopScale: 1,
      /* Stable workshop target; desktop character changes must not replace it. */
      workspacePackId: "",
      /* cached full profile of the pack being edited (character.json object) */
      editingPackId: "",
      editingProfile: null,
      draftDirty: false,
      testPackId: "",
      testRunning: false,
      testMessages: [],
      testScopes: {},
      testAssetsByPack: {},
      testLastEmotion: "",
      testLastSegments: [],
      pendingApplyPackId: "",
      portraitCutoutCapability: {
        status: "unknown",
        label: "能力状态待同步",
        detail: "进入立绘管理页后会读取本地能力注册表。",
        configured: false,
        executionReady: false,
        canConfigure: false,
      },
      portraitCutoutRunning: false,
    },
    registryState: {
      lastWorkshopSnapshotSignature: "",
      refreshRequestToken: 0,
      snapshotRevision: 0,
      createDialogVisit: 0,
    },
    navigationState: {
      workspaceVisit: 0,
    },
    writeState: {
      registryRevision: 0,
      catalogOperation: "",
      contextSaves: new Set(),
      storySaves: new Set(),
      packExports: new Set(),
      calibrationSaves: new Set(),
      personaSaves: new Set(),
      portraitMutations: new Set(),
    },
    personaState: {
      personaRevisions: new Map(),
    },
    contextState: {
      contextDialogTarget: null,
    },
    storiesState: {
      storyDialogTarget: null,
    },
    calibrationState: {
      calibrationSlidersBound: false,
      calibrationRequestToken: 0,
      calibrationImageToken: 0,
      calibrationReadyTarget: null,
    },
    assetState: {
      portraitImageUrlCache: new Map(),
      portraitAssetRevisions: new Map(),
      testAssetRequests: new Map(),
    },
    portraitState: {
      portraitRequestToken: 0,
      portraitVisitToken: 0,
      portraitLoadedPackId: "",
      previewingEmotion: null,
    },
    cutoutState: {
      lastCutoutCapabilitySignature: "",
      lastCutoutCapabilitySyncedAt: 0,
      cutoutCapabilityRequest: null,
      cutoutCapabilityGeneration: 0,
    },
    testState: {
      activeTestRequest: null,
      testPreview: null,
      testPreviewTarget: "",
      testPreviewImageToken: 0,
    },
  };
}
