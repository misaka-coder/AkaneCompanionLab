import assert from "node:assert/strict";

import {
  botScopedPath,
  buildBotBackendBaseUrl,
  normalizeBotId
} from "../src/bot-routing.js";
import { createBackendControlCenterSource } from "../src/control-center/data-sources.js";

assert.equal(normalizeBotId("finance"), "finance");
assert.equal(normalizeBotId("../finance"), "");
assert.equal(botScopedPath("personal", "/think"), "/api/bots/personal/think");
assert.equal(botScopedPath("finance", "/desktop-pet/vision/clip"), "/api/bots/finance/desktop-pet/vision/clip");
assert.equal(botScopedPath("finance", "/api/qq/self-check"), "/api/bots/finance/qq/self-check");
assert.equal(
  botScopedPath("finance", "/api/bots/finance/tts"),
  "/api/bots/finance/tts"
);
assert.equal(
  buildBotBackendBaseUrl("http://127.0.0.1:9999", "finance"),
  "http://127.0.0.1:9999/api/bots/finance"
);

const requestedUrls = [];
const source = createBackendControlCenterSource({
  baseUrl: "http://127.0.0.1:9999",
  expectedInstanceId: "local-default",
  botId: "finance",
  petState: { boundBotId: "finance" },
  fetchImpl: async (input) => {
    const url = String(input);
    requestedUrls.push(url);
    const path = new URL(url).pathname;
    const body = path === "/health"
      ? { status: "ok", instance_id: "local-default", root_binding: "valid" }
      : path === "/api/bots"
        ? { ok: true, bots: [{ botId: "finance", displayName: "Finance", available: true }] }
        : path === "/api/bots/finance/plugins/catalog"
          ? { ok: true, status: "active", generation: 2, plugins: [{
              plugin_id: "example.http-query", version: "0.1.0", source: "managed", manageable: true,
              enabled: true, runtime_status: "active", surfaces: ["desktop"],
              contributions: {
                capabilities: [], commands: [], event_handlers: [], hooks: [], background_services: [], prompt_blocks: [], skills: [],
                connections: [{ name: "query_api", version: 1, description: "HTTP query", schema: { type: "object", properties: {
                  endpoint: { type: "string" }, credential: { type: "string" }
                }, required: ["endpoint", "credential"] }, private_fields: ["credential"] }]
              }, permissions: []
            }] }
        : path === "/api/bots/finance/admin/plugins/example.http-query/connections/query_api"
          ? { ok: true, status: "configured", revision: 2, enabled: true,
              connection: { name: "query_api" }, values: { endpoint: "https://example.test/query" },
              configured_private_fields: ["credential"], private_fields: ["credential"] }
        : path === "/api/bots/finance/control-center/settings-catalog"
          ? { categories: [] }
          : {};
    return {
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => body,
      text: async () => JSON.stringify(body)
    };
  }
});

assert.equal((await source.readBotCatalog()).bots[0].botId, "finance");
assert.deepEqual(await source.readSettingsCatalog(), { categories: [] });
const pluginCatalog = await source.readPluginCatalog();
assert.equal(pluginCatalog.ok, true);
assert.equal(pluginCatalog.data.plugins[0].connection_configs[0].revision, 2);
assert.deepEqual(pluginCatalog.data.plugins[0].connection_configs[0].values, { endpoint: "https://example.test/query" });
assert.deepEqual(pluginCatalog.data.plugins[0].connection_configs[0].configured_private_fields, ["credential"]);
assert.doesNotMatch(JSON.stringify(pluginCatalog), /secret|credential-value|token-value/);
assert(requestedUrls.some((url) => new URL(url).pathname === "/health"));
assert(requestedUrls.some((url) => new URL(url).pathname === "/api/bots"));
assert(requestedUrls.some((url) => new URL(url).pathname === "/api/bots/finance/control-center/settings-catalog"));
assert(requestedUrls.some((url) => new URL(url).pathname === "/api/bots/finance/plugins/catalog"));
assert(requestedUrls.some((url) => new URL(url).pathname === "/api/bots/finance/admin/plugins/example.http-query/connections/query_api"));
assert(!requestedUrls.some((url) => new URL(url).pathname === "/api/bots/finance/health"));

console.log("bot routing smoke: ok");
