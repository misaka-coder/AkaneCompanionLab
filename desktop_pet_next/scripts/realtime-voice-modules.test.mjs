import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

import * as api from "../src/realtime-voice-client.js";
import { RealtimeVoicePlaybackQueue } from "../src/realtime-voice/playback.js";
import { RealtimeVoiceCallResources } from "../src/realtime-voice/capture-resources.js";
import { RealtimeVoiceSession } from "../src/realtime-voice/single-session.js";
import { RealtimeVoiceCallSession } from "../src/realtime-voice/call-session.js";
import { RealtimeVoiceCallTurn } from "../src/realtime-voice/call-turn.js";
import { buildVoiceWebSocketUrl, supportsRealtimeVoiceCapture } from "../src/realtime-voice/protocol.js";
import { encodeFloat32PcmAsWav } from "../src/realtime-voice/pcm.js";

test("legacy client exports the original API and the same implementation objects", () => {
  const expected = { RealtimeVoicePlaybackQueue, RealtimeVoiceCallResources, RealtimeVoiceSession,
    RealtimeVoiceCallSession, RealtimeVoiceCallTurn, buildVoiceWebSocketUrl, supportsRealtimeVoiceCapture, encodeFloat32PcmAsWav };
  assert.deepEqual(Object.keys(api).sort(), Object.keys(expected).sort());
  for (const [name, implementation] of Object.entries(expected)) assert.equal(api[name], implementation);
});

test("compatibility entrypoint contains only static exports", () => {
  const source = ts.createSourceFile("realtime-voice-client.js", readFileSync(new URL("../src/realtime-voice-client.js", import.meta.url), "utf8"), ts.ScriptTarget.Latest, true);
  assert.ok(source.statements.every(statement => ts.isExportDeclaration(statement) && statement.moduleSpecifier));
});

test("implementations never import the compatibility entrypoint", () => {
  const directory = new URL("../src/realtime-voice/", import.meta.url);
  for (const name of readdirSync(directory).filter(name => name.endsWith(".js"))) {
    const source = ts.createSourceFile(name, readFileSync(new URL(name, directory), "utf8"), ts.ScriptTarget.Latest, true);
    for (const statement of source.statements) {
      if (ts.isImportDeclaration(statement) || ts.isExportDeclaration(statement)) {
        assert.ok(!statement.moduleSpecifier?.text.endsWith("realtime-voice-client.js"), name);
      }
    }
  }
});
