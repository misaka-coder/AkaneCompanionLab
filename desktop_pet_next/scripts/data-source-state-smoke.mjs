import assert from "node:assert/strict";
import test from "node:test";
import { createBackendControlCenterSource } from "../src/control-center/data-sources.js";
import { CONTROL_CENTER_ACTIONS } from "../src/control-center/action-router.js";

function response(data, status = 200) {
  return { ok: status >= 200 && status < 300, status, headers: new Headers({ "content-type": "application/json" }),
    async json() { return data; } };
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}

test("desktop actions without a desktop bridge fail locally without an inert HTTP fallback", async () => {
  const requests = [];
  const source = createBackendControlCenterSource({ fetchImpl: async (url) => {
    requests.push(String(url));
    return response({ ok: true, status: "executed" });
  } });
  for (const actionId of [CONTROL_CENTER_ACTIONS.advancedProbeClickThrough, CONTROL_CENTER_ACTIONS.windowClose,
    CONTROL_CENTER_ACTIONS.musicNext, "unknown.action"]) {
    const result = await source.runAction(actionId);
    assert.equal(result.ok, false);
    assert.equal(result.status, "not-implemented");
    assert.equal(result.actionId, actionId);
    assert.equal(result.refresh, false);
  }
  assert.deepEqual(requests, []);
});

test("real backend task control keeps its route and success or failure without a desktop bridge", async () => {
  for (const [body, status, expected] of [
    [{ ok: true, status: "completed", job_status: "stopped" }, 200, true],
    [{ ok: false, status: "forbidden", reason: "fixture_denied" }, 403, false],
    [{ ok: false, status: "not_found" }, 404, false]
  ]) {
    const requests = [];
    const source = createBackendControlCenterSource({ sessionId: "desktop-current", profileUserId: "master",
      fetchImpl: async (url, options) => {
        requests.push({ url: new URL(url), body: JSON.parse(options.body) });
        return response(body, status);
      }
    });
    const result = await source.runAction(CONTROL_CENTER_ACTIONS.chatJobControl, { jobId: "job-fixture", action: "stop" });
    assert.equal(result.ok, expected);
    assert.equal(result.status, body.status);
    assert.equal(requests.length, 1);
    assert.ok(requests[0].url.pathname.endsWith("/admin/plugins/jobs/job-fixture/control"));
    assert.equal(requests[0].url.searchParams.get("session_id"), "desktop-current");
    assert.equal(requests[0].url.searchParams.get("profile_user_id"), "master");
    assert.deepEqual(requests[0].body, { action: "stop" });
  }
});

test("setting updates keep an HTTP rejection even when the body claims success", async () => {
  const source = createBackendControlCenterSource({ fetchImpl: async () => response({ ok: true, status: "saved" }, 503) });
  const result = await source.updateSetting("fixture", true);
  assert.equal(result.ok, false);
  assert.equal(result.status, "http-503");
  const available = createBackendControlCenterSource({ fetchImpl: async () => response({ ok: true, status: "saved" }) });
  assert.deepEqual(await available.updateSetting("fixture", true), { ok: true, status: "saved" });
});

test("a slow failed session request cannot replace the latest session error state", async () => {
  const old = deferred(), started = deferred();
  const source = createBackendControlCenterSource({
    fetchImpl: async (url, options) => {
      const pathname = new URL(url).pathname;
      if (pathname === "/health") return response({ status: "ok", root_binding: "valid", instance_id: "fixture" });
      if (pathname === "/sessions/ensure") {
        const sessionId = JSON.parse(options.body).session_id;
        if (sessionId === "old") { started.resolve(); return old.promise; }
        return response({ ok: true, session_id: sessionId });
      }
      return response({ sections: { outputs: [] } });
    }
  });
  const previous = source.readChatSession({ sessionId: "old" });
  await started.promise;
  const current = await source.readChatSession({ sessionId: "current" });
  assert.equal(current.session_id, "current");
  old.reject(new Error("fixture-old-session-failure"));
  assert.equal(await previous, null);
  assert.equal(source.getChatSessionError(), "");
});

test("voice save and audition share parameters while retaining their distinct payloads", async () => {
  const requests = [];
  const source = createBackendControlCenterSource({ fetchImpl: async (url, options) => {
    requests.push({ path: new URL(url).pathname, body: JSON.parse(options.body) });
    return response({ ok: true });
  } });
  const fields = { providerId: "provider.tts.gpt_sovits.local", voiceProfileId: "fixture",
    displayName: "fixture-name", text: "试音", promptText: "提示", parallelInfer: false, topK: 0,
    emotionVoiceMap: { happy: "fixture-happy" } };
  await source.runAction(CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave, fields);
  await source.runAction(CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest, fields);
  const [save, audition] = requests;
  assert.ok(save.path.endsWith("/voice-profiles/fixture/config"));
  assert.ok(audition.path.endsWith("/tts-test"));
  for (const { body } of requests) {
    assert.equal(body.parallelInfer, "false");
    assert.equal(body.topK, "0");
    assert.equal(body.promptText, "提示");
    assert.deepEqual(body.emotionVoiceMap, fields.emotionVoiceMap);
  }
  assert.equal(save.body.displayName, "fixture-name");
  assert.equal(save.body.text, undefined);
  assert.equal(audition.body.displayName, undefined);
  assert.equal(audition.body.text, "试音");
});
