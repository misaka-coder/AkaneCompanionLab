import { describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import { useStoryController } from "./story-controller";
import type { StoryCatalogResponse, StoryRunState } from "../domain/types";

describe("story-controller", () => {
  function makeMockClient() {
    return {
      storyCatalog: vi.fn(async (): Promise<StoryCatalogResponse> => ({
        stories: [
          {
            story_id: "twilight_tea_party",
            title: "黄昏茶会",
            description: "午后时光",
            cover_image: "bg.png",
            has_active_run: false,
            current_node_id: "",
            status: "not_started",
            completed_endings: [],
          },
        ],
      })),
      storyStart: vi.fn(async (storyId: string): Promise<StoryRunState> => ({
        run_id: "run_123",
        story_id: storyId,
        current_node_id: "intro_1",
        status: "in_progress",
        current_node: {
          node_id: "intro_1",
          kind: "script",
          title: "",
          speech: "欢迎回家。",
          speaker: "Akane",
          emotion_id: "normal",
          motion_id: "idle",
          background_id: "scenes/room.png",
          music_id: "bgm/tea.mp3",
          outfit_id: "builtin-水手服",
          next_node_id: "intro_2",
          options: [],
          prompt_objective: "",
          ending_title: "",
          ending_summary: "",
          beats: [],
          max_turns: 0,
          suggested_turns: 0,
          quick_reactions: [],
        },
        variables: {},
        history: ["intro_1"],
        completed_endings: [],
        conversation_turns: [],
        turn_count: 0,
        presentation: null,
      })),
      storyStep: vi.fn(async (): Promise<StoryRunState> => ({
        run_id: "run_123",
        story_id: "twilight_tea_party",
        current_node_id: "choice_1",
        status: "in_progress",
        current_node: {
          node_id: "choice_1",
          kind: "choice",
          title: "面对茶点……",
          speech: "要尝尝吗？",
          speaker: "Akane",
          emotion_id: "卖萌",
          motion_id: "idle",
          background_id: "",
          music_id: "",
          outfit_id: "",
          next_node_id: "",
          options: [
            {
              id: "opt_1",
              label: "品尝",
              next_node_id: "end_1",
              condition: "",
              action_kind: "",
              action_target: "",
              action_count: 0,
              cost_coins: 0,
              cost_label: "",
            },
          ],
          prompt_objective: "",
          ending_title: "",
          ending_summary: "",
          beats: [],
          max_turns: 0,
          suggested_turns: 0,
          quick_reactions: [],
        },
        variables: {},
        history: ["intro_1", "choice_1"],
        completed_endings: [],
        conversation_turns: [],
        turn_count: 0,
        presentation: null,
      })),
      storyReset: vi.fn(async () => true),
    };
  }

  it("loads catalog, starts story, and advances nodes", async () => {
    const client = makeMockClient();
    const room = {
      stories: [],
      activeStoryRun: null as StoryRunState | null,
      drawer: null,
      busy: false,
      error: "",
      emotion: "normal",
      storyBackground: "",
      storyMusic: "",
      storyOutfit: "",
    } as any;
    let completeHandler: ((p: any, auto: boolean) => void) | null = null;
    const playback = {
      play: vi.fn(),
      playBeats: vi.fn(),
      stop: vi.fn(),
      advance: vi.fn(),
      view: ref({ revealed: true, beat: null }),
      onComplete: vi.fn((fn) => {
        completeHandler = fn;
        return () => {};
      }),
    } as any;
    const notify = vi.fn();

    const controller = useStoryController(() => client as any, room, playback, notify);

    await controller.loadCatalog();
    expect(room.stories).toHaveLength(1);
    expect(room.stories[0].story_id).toBe("twilight_tea_party");

    await controller.startStory("twilight_tea_party");
    expect(client.storyStart).toHaveBeenCalledWith("twilight_tea_party", false);
    expect(room.activeStoryRun?.current_node_id).toBe("intro_1");
    expect(room.storyBackground).toBe("scenes/room.png");
    expect(room.storyMusic).toBe("bgm/tea.mp3");
    expect(room.storyOutfit).toBe("builtin-水手服");
    expect(playback.playBeats).toHaveBeenCalledTimes(1);

    completeHandler!(null, false);
    await Promise.resolve();
    expect(client.storyStep).toHaveBeenCalled();

    controller.finishStory();
    expect(room.activeStoryRun).toBeNull();
    expect(room.storyBackground).toBe("");
    expect(room.storyMusic).toBe("");
    expect(room.storyOutfit).toBe("");
    expect(playback.stop).toHaveBeenCalled();
  });

  it("auto advances script nodes on presentation complete", async () => {
    const client = makeMockClient();
    const room = {
      stories: [],
      activeStoryRun: null as StoryRunState | null,
      drawer: null,
      busy: false,
      error: "",
      emotion: "normal",
      storyBackground: "",
      storyMusic: "",
      storyOutfit: "",
    } as any;
    let completeHandler: ((p: any, auto: boolean) => void) | null = null;
    const playback = {
      playBeats: vi.fn(),
      stop: vi.fn(),
      advance: vi.fn(),
      view: ref({ revealed: true, beat: null }),
      onComplete: vi.fn((fn) => {
        completeHandler = fn;
        return () => {};
      }),
    } as any;
    const notify = vi.fn();

    const controller = useStoryController(() => client as any, room, playback, notify);
    await controller.startStory("twilight_tea_party");

    expect(completeHandler).toBeDefined();
    // Simulate presentation completion with auto=true
    completeHandler!({} as any, true);
    await Promise.resolve();
    expect(client.storyStep).toHaveBeenCalled();
  });

  it("discards late responses when story was exited while awaiting model", async () => {
    let resolveStep: (val: StoryRunState) => void;
    const delayedStepPromise = new Promise<StoryRunState>((resolve) => {
      resolveStep = resolve;
    });

    const client = {
      storyStart: vi.fn(async (): Promise<StoryRunState> => ({
        run_id: "run_async",
        story_id: "story_1",
        current_node_id: "agent_node",
        status: "in_progress",
        current_node: {
          node_id: "agent_node",
          kind: "agent",
          title: "",
          speech: "说点什么吧",
          speaker: "Akane",
          emotion_id: "normal",
          motion_id: "idle",
          background_id: "",
          music_id: "",
          outfit_id: "",
          next_node_id: "end",
          options: [],
          prompt_objective: "",
          ending_title: "",
          ending_summary: "",
          beats: [],
          max_turns: 0,
          suggested_turns: 0,
          quick_reactions: [],
        },
        variables: {},
        history: ["agent_node"],
        completed_endings: [],
        conversation_turns: [],
        turn_count: 0,
        presentation: null,
      })),
      storyStep: vi.fn(() => delayedStepPromise),
      request: vi.fn(async () => ({ ok: true })),
    };

    const room = {
      activeStoryRun: null as StoryRunState | null,
      drawer: null,
      busy: false,
      error: "",
      emotion: "normal",
      storyBackground: "",
      storyMusic: "",
      storyOutfit: "",
    } as any;
    const playback = {
      playBeats: vi.fn(),
      stop: vi.fn(),
      advance: vi.fn(),
      view: ref({ revealed: true, beat: null }),
      onComplete: vi.fn(() => () => {}),
    } as any;
    const notify = vi.fn();

    const controller = useStoryController(() => client as any, room, playback, notify);
    await controller.startStory("story_1");
    expect(room.activeStoryRun).not.toBeNull();

    // Trigger stepStory
    const stepPromise = controller.stepStory("", "你好呀");

    // While awaiting step, user finishes/exits story
    controller.finishStory();
    expect(room.activeStoryRun).toBeNull();

    // Late response now arrives from backend
    resolveStep!({
      run_id: "run_async",
      story_id: "story_1",
      current_node_id: "agent_reply",
      status: "in_progress",
      current_node: {
        node_id: "agent_reply",
        kind: "script",
        title: "",
        speech: "迟到的回复",
        speaker: "Akane",
        emotion_id: "normal",
        motion_id: "idle",
        background_id: "",
        music_id: "",
        outfit_id: "",
        next_node_id: "",
        options: [],
        prompt_objective: "",
        ending_title: "",
        ending_summary: "",
        beats: [],
        max_turns: 0,
        suggested_turns: 0,
        quick_reactions: [],
      },
      variables: {},
      history: ["agent_node", "agent_reply"],
      completed_endings: [],
      conversation_turns: [],
      turn_count: 0,
      presentation: null,
    });
    await stepPromise;

    // Room activeStoryRun must NOT be revived
    expect(room.activeStoryRun).toBeNull();
    expect(room.busy).toBe(false);
  });
});
