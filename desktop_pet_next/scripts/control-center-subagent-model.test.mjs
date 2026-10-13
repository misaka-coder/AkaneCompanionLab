import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MODEL_SERVICE_ACTIONS,
  applySubagentModelProvider,
  createModelServiceDraft,
  modelServiceOperation,
  modelServicePayload,
  normalizeModelServiceRuntime
} from "../src/control-center-v2/model-service.js";
import { createControlCenterStore, createInitialControlCenterState } from "../src/control-center-v2/store.js";
import { createActionController } from "../src/control-center-v2/app/actions.js";
import { createModelController } from "../src/control-center-v2/app/model.js";
import { modelRenderScope, isInteractingWithForm } from "../src/control-center-v2/app/render-policy.js";
import { renderModelService } from "../src/control-center-v2/components/model.js";
import { createControlCenterViewModel } from "../src/control-center-v2/view-model.js";

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
const tick = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

test("real view-model enables main, child and vision actions and preserves separate reasoning fields", async () => {
  const vm = createControlCenterViewModel({ sourceKind: "backend", modelRuntime: {
    status: "configured", providerId: "pinai", protocol: "responses", chatModel: "main-model",
    chatReasoningEffort: "max", chatThinkingMode: "enabled", chatMaxOutputTokens: 8192,
    useForVision: true, standaloneVision: false, visionConfigured: true, visionBaseUrl: "https://stale.example",
    subagent: { mode: "independent", model: "child-model", reasoningEffort: "high" },
    providers: [{ id: "pinai", label: "Main", protocol: "responses" }]
  } });
  const draft = createModelServiceDraft(vm.model);
  const state = { viewModel: vm, modelDraft: draft, actionStates: {} };
  const html = renderModelService(state);
  for (const id of Object.values(MODEL_SERVICE_ACTIONS)) {
    assert.equal(vm.actions[id].available, true, id);
    assert.ok(!html.match(new RegExp(`data-action="${id.replaceAll(".", "\\.")}"[^>]*disabled`)), id);
  }
  assert.ok(html.includes('data-model-field="chatReasoningEffort"'));
  assert.ok(html.includes('data-subagent-field="reasoningEffort"'));
  assert.equal(draft.standaloneVision, false);
  const payload = modelServicePayload(draft);
  assert.equal(payload.chatReasoningEffort, "max");
  assert.equal(payload.chatThinkingMode, "enabled");
  assert.equal(payload.chatMaxOutputTokens, 8192);
  assert.equal(payload.subagent.reasoningEffort, "high");
  assert.equal(payload.standaloneVision, false);
  // Losing connection disables all three groups consistently.
  const disconnected = createControlCenterViewModel({ modelRuntime: vm.model });
  for (const id of Object.values(MODEL_SERVICE_ACTIONS)) assert.equal(disconnected.actions[id].available, false);
});

test("normalizeModelServiceRuntime accurately projects subagent block across modes", () => {
  const providers = [
    { id: "openai", label: "OpenAI", protocol: "openai", baseUrl: "https://api.openai.com/v1" },
    { id: "anthropic", label: "Anthropic", protocol: "anthropic", baseUrl: "https://api.anthropic.com" }
  ];

  // 1. Missing subagent defaults to inherit
  const norm1 = normalizeModelServiceRuntime({ providerId: "openai", chatModel: "gpt-4o", providers }, true);
  assert.equal(norm1.subagent.mode, "inherit");
  assert.equal(norm1.subagent.model, "");
  assert.equal(norm1.subagent.configured, false);

  // 2. Shared service mode
  const norm2 = normalizeModelServiceRuntime({
    providerId: "openai",
    chatModel: "gpt-4o",
    subagent: {
      mode: "shared_service",
      model: "gpt-5-mini",
      thinkingMode: "enabled",
      reasoningEffort: "medium",
      maxOutputTokens: 2048,
      timeoutSeconds: 60,
      configured: true
    },
    providers
  }, true);
  assert.equal(norm2.subagent.mode, "shared_service");
  assert.equal(norm2.subagent.model, "gpt-5-mini");
  assert.equal(norm2.subagent.thinkingMode, "enabled");
  assert.equal(norm2.subagent.reasoningEffort, "medium");
  assert.equal(norm2.subagent.maxOutputTokens, 2048);
  assert.equal(norm2.subagent.timeoutSeconds, 60);
  assert.equal(norm2.subagent.configured, true);

  // 3. Independent mode
  const norm3 = normalizeModelServiceRuntime({
    providerId: "openai",
    chatModel: "gpt-4o",
    subagent: {
      mode: "independent",
      providerId: "anthropic",
      protocol: "anthropic",
      baseUrl: "https://api.anthropic.com",
      hasApiKey: true,
      model: "claude-haiku-4-5-20251001",
      timeoutSeconds: 45,
      configured: true
    },
    providers
  }, true);
  assert.equal(norm3.subagent.mode, "independent");
  assert.equal(norm3.subagent.providerId, "anthropic");
  assert.equal(norm3.subagent.protocol, "anthropic");
  assert.equal(norm3.subagent.baseUrl, "https://api.anthropic.com");
  assert.equal(norm3.subagent.hasApiKey, true);
  assert.equal(norm3.subagent.model, "claude-haiku-4-5-20251001");
  assert.equal(norm3.subagent.configured, true);
});

test("createModelServiceDraft and modelServicePayload construct payload with target and subagent fields", () => {
  const draft = createModelServiceDraft({
    providerId: "openai",
    chatModel: "gpt-4o",
    subagent: {
      mode: "independent",
      providerId: "anthropic",
      baseUrl: "https://api.anthropic.com",
      model: "claude-haiku",
      thinkingMode: "auto",
      reasoningEffort: "low",
      maxOutputTokens: 1024,
      timeoutSeconds: 30
    }
  });

  assert.equal(draft.subagent.mode, "independent");
  assert.equal(draft.subagent.model, "claude-haiku");
  assert.equal(draft.subagent.apiKey, ""); // New draft starts with empty key input

  const mainPayload = modelServicePayload(draft);
  assert.equal(mainPayload.target, undefined);
  assert.equal(mainPayload.subagent.mode, "independent");
  assert.equal(mainPayload.subagent.model, "claude-haiku");

  const subagentActionPayload = modelServicePayload(draft, "subagent");
  assert.equal(subagentActionPayload.target, "subagent");
  assert.equal(subagentActionPayload.subagent.providerId, "anthropic");

  // Switching provider updates subagent route
  const providers = [{ id: "anthropic", protocol: "anthropic", baseUrl: "https://api.anthropic.com" }, { id: "gemini", protocol: "gemini", baseUrl: "https://generativelanguage.googleapis.com" }];
  const switched = applySubagentModelProvider({ providers }, draft, "gemini");
  assert.equal(switched.subagent.providerId, "gemini");
  assert.equal(switched.subagent.protocol, "gemini");
  assert.equal(switched.subagent.baseUrl, "https://generativelanguage.googleapis.com");
});

test("modelServiceOperation routes subagent action IDs to correct backend endpoints", () => {
  assert.equal(modelServiceOperation(MODEL_SERVICE_ACTIONS.models), "models");
  assert.equal(modelServiceOperation(MODEL_SERVICE_ACTIONS.test), "test");
  assert.equal(modelServiceOperation(MODEL_SERVICE_ACTIONS.save), "save");
  assert.equal(modelServiceOperation(MODEL_SERVICE_ACTIONS.subagentModels), "models");
  assert.equal(modelServiceOperation(MODEL_SERVICE_ACTIONS.subagentTest), "test");
});

function subagentControllerHarness(runAction = async () => ({ ok: true, status: "completed" })) {
  const store = createControlCenterStore({
    ...createInitialControlCenterState(),
    viewModel: {
      shell: { connected: true },
      model: {
        available: true,
        connected: true,
        providers: [
          { id: "main-prov", label: "Main", protocol: "openai", baseUrl: "https://main.example" },
          { id: "sub-prov", label: "Sub", protocol: "anthropic", baseUrl: "https://sub.example" }
        ],
        subagent: { mode: "inherit", configured: true }
      }
    }
  });
  store.patch({
    modelDraft: createModelServiceDraft(store.getState().viewModel.model)
  });
  const callbacks = new Map();
  let sequence = 0;
  const timers = {
    setTimeout: (cb) => { callbacks.set(++sequence, cb); return sequence; },
    clearTimeout: (id) => callbacks.delete(id)
  };
  const actions = createActionController({ store, bridge: { runAction }, requestFrame: (cb) => cb(), timers });
  const fields = new Map();
  const root = {
    querySelector: (sel) => {
      const matchSub = sel.match(/data-subagent-field="([^"]+)"/);
      if (matchSub) return fields.get(`sub:${matchSub[1]}`) || null;
      const matchMain = sel.match(/data-model-field="([^"]+)"/);
      if (matchMain) return fields.get(`main:${matchMain[1]}`) || null;
      return null;
    }
  };
  const model = createModelController({ root, store, actions });

  const editSub = (field, value) => {
    const target = { value, dataset: { subagentField: field }, matches: (s) => s === "[data-subagent-field]" };
    fields.set(`sub:${field}`, target);
    model.handleInput({ target });
  };

  return { store, actions, model, fields, editSub };
}

test("subagent model probe dispatches target=subagent and updates modelSubagentModels separately", async () => {
  let dispatched = null;
  const h = subagentControllerHarness(async (actionId, payload) => {
    dispatched = { actionId, payload };
    return { ok: true, models: ["discovered-sub-1", "discovered-sub-2"] };
  });

  // Switch subagent mode to shared_service
  const modeTarget = { value: "shared_service", dataset: { subagentField: "mode" }, matches: (s) => s === '[data-subagent-field="mode"]' || s === "[data-subagent-field]" };
  h.fields.set("sub:mode", modeTarget);
  h.model.handleChange({ target: modeTarget });
  assert.equal(h.store.getState().modelDraft.subagent.mode, "shared_service");

  // 1. Normal probe without in-flight typing populates subagent models
  await h.model.runModelAction(MODEL_SERVICE_ACTIONS.subagentModels);
  assert.equal(dispatched.actionId, MODEL_SERVICE_ACTIONS.subagentModels);
  assert.equal(dispatched.payload.target, "subagent");

  const state1 = h.store.getState();
  assert.deepEqual(state1.modelModels, []);
  assert.deepEqual(state1.modelSubagentModels, ["discovered-sub-1", "discovered-sub-2"]);
  assert.equal(state1.modelDraft.subagent.model, "discovered-sub-1");

  // 2. Typing while probe is in flight fences late response
  const pending = deferred();
  const h2 = subagentControllerHarness((actionId, payload) => pending.promise);
  const modeTarget2 = { value: "shared_service", dataset: { subagentField: "mode" }, matches: (s) => s === '[data-subagent-field="mode"]' || s === "[data-subagent-field]" };
  h2.fields.set("sub:mode", modeTarget2);
  h2.model.handleChange({ target: modeTarget2 });

  const inFlightPromise = h2.model.runModelAction(MODEL_SERVICE_ACTIONS.subagentModels);
  await tick();
  h2.editSub("model", "typed-sub-model");

  pending.resolve({ ok: true, models: ["late-sub-1"] });
  await inFlightPromise;

  const state2 = h2.store.getState();
  assert.deepEqual(state2.modelSubagentModels, []);
  assert.equal(state2.modelDraft.subagent.model, "typed-sub-model");
});

test("subagent test action dispatches target=subagent", async () => {
  let dispatched = null;
  const h = subagentControllerHarness(async (actionId, payload) => {
    dispatched = { actionId, payload };
    return { ok: true, status: "connected" };
  });

  const modeTarget = { value: "independent", dataset: { subagentField: "mode" }, matches: (s) => s === '[data-subagent-field="mode"]' || s === "[data-subagent-field]" };
  h.fields.set("sub:mode", modeTarget);
  h.model.handleChange({ target: modeTarget });
  h.editSub("model", "claude-haiku");

  await h.model.runModelAction(MODEL_SERVICE_ACTIONS.subagentTest);
  assert.equal(dispatched.actionId, MODEL_SERVICE_ACTIONS.subagentTest);
  assert.equal(dispatched.payload.target, "subagent");
  assert.equal(dispatched.payload.subagent.mode, "independent");
  assert.equal(dispatched.payload.subagent.model, "claude-haiku");
});

test("vision probe and test keep their own target, list and edited model", async () => {
  const pending = deferred();
  const dispatched = [];
  const h = subagentControllerHarness(async (actionId, payload) => {
    dispatched.push({ actionId, payload });
    return actionId === MODEL_SERVICE_ACTIONS.visionModels ? pending.promise : { ok: true, status: "connected" };
  });
  h.store.patch({ modelDraft: { ...h.store.getState().modelDraft, chatReasoningEffort: "max", standaloneVision: true,
    visionModel: "vision-original", visionBaseUrl: "https://vision.example", subagent: { mode: "shared_service", reasoningEffort: "high" } } });
  const request = h.model.runModelAction(MODEL_SERVICE_ACTIONS.visionModels);
  await tick();
  assert.equal(dispatched[0].payload.target, "vision");
  const target = { value: "vision-edited", dataset: { modelField: "visionModel" }, matches: selector => selector === "[data-model-field]" };
  h.fields.set("main:visionModel", target);
  h.model.handleInput({ target });
  pending.resolve({ ok: true, models: ["late-vision"] });
  await request;
  assert.deepEqual(h.store.getState().modelVisionModels, []);
  assert.equal(h.store.getState().modelDraft.visionModel, "vision-edited");
  await h.model.runModelAction(MODEL_SERVICE_ACTIONS.visionTest);
  assert.equal(dispatched[1].payload.target, "vision");
  assert.equal(dispatched[1].payload.chatReasoningEffort, "max");
  assert.equal(dispatched[1].payload.subagent.reasoningEffort, "high");
  assert.equal(dispatched[1].payload.visionModel, "vision-edited");
});

test("subagent clearApiKey toggle and render-policy form detection", () => {
  const h = subagentControllerHarness();
  const toggleBtn = {
    dataset: { subagentToggle: "clearApiKey" },
    disabled: false,
    closest: (s) => (s === "button[data-subagent-toggle]" ? toggleBtn : null)
  };

  h.model.handleClick({ target: toggleBtn });
  assert.equal(h.store.getState().modelDraft.subagent.clearApiKey, true);

  h.model.handleClick({ target: toggleBtn });
  assert.equal(h.store.getState().modelDraft.subagent.clearApiKey, false);

  // Form interaction detection
  assert.equal(isInteractingWithForm({ matches: (s) => s.includes("[data-subagent-field]") }), true);
  assert.equal(isInteractingWithForm({ matches: (s) => s.includes("[data-model-field]") }), true);
  assert.equal(isInteractingWithForm({ matches: () => false }), false);

  // Render scope tracks subagent changes
  const scope1 = modelRenderScope(h.store.getState());
  h.store.patch({ modelSubagentModels: ["model-x"] });
  const scope2 = modelRenderScope(h.store.getState());
  assert.notEqual(scope1, scope2);
});

test("renderModelService generates valid HTML with subagent options", () => {
  const state = {
    actionStates: {},
    modelDraft: {
      providerId: "main-prov",
      chatModel: "gpt-4o",
      subagent: {
        mode: "independent",
        providerId: "sub-prov",
        baseUrl: "https://sub.example",
        apiKey: "",
        model: "claude-3-5",
        thinkingMode: "enabled",
        reasoningEffort: "high",
        maxOutputTokens: 4096,
        timeoutSeconds: 60,
        clearApiKey: false
      }
    },
    viewModel: {
      model: {
        connected: true,
        available: true,
        configured: true,
        providers: [{ id: "main-prov", label: "Main" }, { id: "sub-prov", label: "Sub" }],
        subagent: { mode: "independent", hasApiKey: true, configured: true }
      }
    }
  };

  const html = renderModelService(state);
  assert.ok(html.includes("SUBAGENT MODEL"));
  assert.ok(html.includes("子代理通用模型配置"));
  assert.ok(html.includes('data-subagent-field="mode"'));
  assert.ok(html.includes('data-subagent-field="providerId"'));
  assert.ok(html.includes('data-subagent-field="model"'));
  assert.ok(html.includes('data-subagent-field="thinkingMode"'));
  assert.ok(html.includes('data-subagent-field="reasoningEffort"'));
  assert.ok(html.includes("检测独立模型"));
  assert.ok(html.includes("测试子模型 API"));
  assert.ok(html.includes("清除子代理已保存的 API Key"));
});
