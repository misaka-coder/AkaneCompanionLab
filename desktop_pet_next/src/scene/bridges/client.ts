import { fetch as nativeFetch } from "@tauri-apps/plugin-http";
import { invoke } from "@tauri-apps/api/core";
import type {
  ActionRequest,
  ActionResult,
  Connection,
  Identity,
  Snapshot,
  StoryCatalogResponse,
  StoryRunState,
} from "../domain/types";
import { validate } from "../contracts/validate";
import { isTauri } from "./connection";
import { abortable } from "./cancellation";

function formatErrorMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const b = body as Record<string, unknown>;
  if (typeof b.detail === "string" && b.detail.trim()) return b.detail.trim();
  if (Array.isArray(b.detail) && b.detail.length > 0) {
    return b.detail
      .map((item: unknown) => {
        if (!item || typeof item !== "object") return String(item);
        const err = item as { loc?: (string | number)[]; msg?: string; type?: string };
        const loc = Array.isArray(err.loc) ? err.loc.filter((p) => p !== "body").join(".") : "";
        return (loc ? `${loc}: ` : "") + (err.msg || err.type || "参数校验失败");
      })
      .join("; ");
  }
  if (typeof b.reason === "string" && b.reason.trim()) return b.reason.trim();
  if (typeof b.message === "string" && b.message.trim()) return b.message.trim();
  return fallback;
}

export class SceneClient {
  readonly identity: Identity;
  readonly base: string;
  constructor(readonly connection: Connection) {
    const url = new URL(connection.backend);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password)
      throw Error("请填写有效的宿主地址。");
    const profileId = String(connection.profileId || "").trim();
    const sessionId = String(connection.sessionId || "").trim();
    const characterId = String(connection.characterId || "").trim();
    if (!profileId || !sessionId || !characterId)
      throw Error("请先绑定用户、会话和角色包。");
    if (connection.botId && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(connection.botId))
      throw Error("Bot 标识格式不正确。");
    this.base =
      url.toString().replace(/\/$/, "") + (connection.botId ? `/api/bots/${encodeURIComponent(connection.botId)}` : "");
    this.identity = {
      profile_user_id: profileId,
      session_id: sessionId,
      character_pack_id: characterId,
    };
  }

  async request(path: string, payload?: unknown, signal?: AbortSignal): Promise<unknown> {
    if (isTauri() && payload !== undefined && path !== "/scene/snapshot") {
      const result = await abortable(
        invoke<{ ok: boolean; status?: string; reason?: string; body: string }>("backend_admin_request", {
          request: { url: this.base + path, method: "POST", body: JSON.stringify(payload) },
        }),
        signal,
      );
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      let body: unknown = null;
      try {
        body = JSON.parse(result.body);
      } catch {
        body = result.body;
      }
      if (!result.ok) {
        console.error("Scene API Error (Tauri):", path, result.status, body);
        throw Error(formatErrorMessage(body, result.reason || `宿主请求失败（${result.status || "error"}）`));
      }
      return body;
    }
    const response = await (isTauri() ? nativeFetch : fetch)(this.base + path, {
      method: payload === undefined ? "GET" : "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      console.error("Scene API Error (HTTP):", path, response.status, body);
      throw Error(formatErrorMessage(body, `宿主请求失败（${response.status}）`));
    }
    return body;
  }

  async snapshot(signal?: AbortSignal): Promise<Snapshot> {
    if (this.connection.instanceId) {
      const health = (await this.request("/health", undefined, signal)) as Record<string, unknown>;
      if (health.instance_id !== this.connection.instanceId || health.root_binding !== "valid")
        throw Error("宿主身份不匹配，请重新连接。");
    }
    return validate<Snapshot>("Snapshot", await this.request("/scene/snapshot", this.identity, signal));
  }

  async action(
    kind: ActionRequest["kind"],
    target: string,
    revision: number,
    requestId: string,
    count = 1,
    signal?: AbortSignal,
  ) {
    const payload: Record<string, unknown> = {
      identity: this.identity,
      request_id: requestId,
      expected_revision: revision,
      kind,
      target,
    };
    if (count > 1) {
      payload.count = count;
    }
    return validate<ActionResult>(
      "ActionResult",
      await this.request(
        "/scene/actions",
        payload,
        signal,
      ),
    );
  }

  async storyCatalog(signal?: AbortSignal): Promise<StoryCatalogResponse> {
    return validate<StoryCatalogResponse>(
      "StoryCatalogResponse",
      await this.request("/scene/story/catalog", this.identity, signal),
    );
  }

  async storyStart(storyId: string, forceRestart = false, signal?: AbortSignal): Promise<StoryRunState> {
    return validate<StoryRunState>(
      "StoryRunState",
      await this.request(
        "/scene/story/start",
        { identity: this.identity, story_id: storyId, force_restart: forceRestart },
        signal,
      ),
    );
  }

  async storyStep(
    runId: string,
    expectedNodeId: string,
    choiceId = "",
    userMessage = "",
    advanceOnly = false,
    signal?: AbortSignal,
  ): Promise<StoryRunState> {
    return validate<StoryRunState>(
      "StoryRunState",
      await this.request(
        "/scene/story/step",
        {
          identity: this.identity,
          run_id: runId,
          expected_node_id: expectedNodeId,
          choice_id: choiceId,
          user_message: userMessage,
          advance_only: advanceOnly,
        },
        signal,
      ),
    );
  }

  async storyCompleteEnding(runId: string, endingId: string, signal?: AbortSignal): Promise<StoryRunState> {
    return validate<StoryRunState>(
      "StoryRunState",
      await this.request(
        "/scene/story/complete_ending",
        {
          identity: this.identity,
          run_id: runId,
          ending_id: endingId,
        },
        signal,
      ),
    );
  }

  async storyReset(storyId: string, signal?: AbortSignal): Promise<boolean> {
    const res = (await this.request(
      "/scene/story/reset",
      { identity: this.identity, story_id: storyId, force_restart: true },
      signal,
    )) as { ok?: boolean };
    return Boolean(res?.ok);
  }

  async importStory(fileName: string, content: string, signal?: AbortSignal): Promise<StoryCatalogResponse> {
    return (await this.request(
      "/scene/story/import",
      { identity: this.identity, file_name: fileName, content },
      signal,
    )) as StoryCatalogResponse;
  }

  asset(url: string): string {
    if (!url) return "";
    if (url.startsWith("data:") || url.startsWith("blob:")) return url;
    if (url.startsWith("http://") || url.startsWith("https://")) return url;
    const path = url.startsWith("/") ? url : `/scene/assets/${url}`;
    const resolved = new URL(path, this.connection.backend);
    return resolved.toString();
  }
}
