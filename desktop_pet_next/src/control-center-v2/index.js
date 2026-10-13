import { createControlCenterBridge } from "./bridge.js";
import { createInitialControlCenterState, createControlCenterStore } from "./store.js";
import { createActionController } from "./app/actions.js";
import { createModelController } from "./app/model.js";
import { createCapabilityDrafts } from "./app/capability-drafts.js";
import { createCapabilityController } from "./app/capabilities.js";
import { createVoiceController } from "./app/voice.js";
import { createChatController } from "./app/chat.js";
import { createPresentationController } from "./app/presentation.js";
import { createControlCenterRenderer } from "./app/render.js";
import { createSnapshotController } from "./app/snapshot.js";
import { bindControlCenterEvents, initialPageFromLocation } from "./app/events.js";
import "./styles.css";

const root = document.querySelector("#app");
const store = createControlCenterStore(createInitialControlCenterState({ activePage: initialPageFromLocation() }));
const bridge = createControlCenterBridge();
const actions = createActionController({ store, bridge });
const model = createModelController({ root, store, actions });
const drafts = createCapabilityDrafts({ root, store });
const voice = createVoiceController({ root, store, actions, drafts });
const capabilities = createCapabilityController({ root, store, actions, drafts, voice });
const chat = createChatController({ root, store, bridge, actions });
const presentation = createPresentationController({ root, store });
const renderer = createControlCenterRenderer({ root, store, model, drafts, voice, chat, presentation });
const snapshot = createSnapshotController({ store, bridge, actions, model, drafts, voice, chat, presentation });
const disposeEvents = bindControlCenterEvents({ root, store, actions, model, drafts, voice, capabilities,
  chat, presentation, renderer, snapshot });

bridge.onFileDrop(chat.attachPaths);
const unsubscribeStore = store.subscribe(renderer.render);
const unsubscribeBridge = bridge.subscribe(snapshot.receive);
void snapshot.start();

window.addEventListener("beforeunload", () => {
  unsubscribeStore(); unsubscribeBridge(); disposeEvents();
  actions.dispose(); chat.dispose(); voice.dispose(); presentation.dispose(); snapshot.dispose();
}, { once: true });
