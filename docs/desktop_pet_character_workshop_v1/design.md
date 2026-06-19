# Desktop Pet Character Workshop V1 Design

Status: planning baseline; first runtime/schema/workshop-shell skeleton implemented 2026-06-05
Owner direction: creator-facing desktop pet first
Primary runtime: `desktop_pet_next`
Frozen runtime: `desktop_pet`

Implementation note 2026-06-05: `desktop_pet_next` now has an active-character runtime map, v0.2 character-pack metadata support, backend `character_pack_id` request contract, a standalone workshop shell, and database-level character-scoped chat memory. Music/gift resources remain user-profile scoped unless a later feature explicitly opts into per-character resource spaces.

## Purpose

Akane should become a creator-facing AI desktop pet system, not only an Akane-only companion demo. The first product slice is a low-friction character workshop where creators can define, import, tune, switch, and test custom characters.

The key value is not "a role that says it will accompany you". The key value is:

- A creator can build a character with a distinct identity, relationship, speaking style, prompt, portraits, expressions, voice, and memory space.
- The desktop pet can immediately switch into that character's world.
- The visible pet, backend prompt context, memories, dialogue display, and character assets all agree on the same active character.
- The system stays extensible for future Web, Live2D, richer voice, marketplace, and shared-memory features.

## Product Decisions

- `desktop_pet_next` is the new desktop pet mainline.
- The old Electron `desktop_pet` is frozen and kept usable, but it should not receive new workshop work.
- Akane remains the default demo character, but the product is not bound to Akane.
- The first complete loop is:
  1. Create or import a character.
  2. Configure character definition with form fields.
  3. Upload outfits and expression portraits.
  4. Calibrate window, scale, crop, hit area, and speech-bubble anchor.
  5. Switch to the character.
  6. The character immediately speaks with isolated identity and memory.
- The workshop should be a standalone window, not buried inside the current settings page.
- The settings page can keep quick switching, status, voice, music, and diagnostics.
- Web and desktop character packs can share a core schema later, but desktop should not wait for Web.
- Music files are globally shared. The active character controls how the listening experience is narrated.
- Character memory is isolated by default. A shared user profile can exist, but each character must opt in or the user must explicitly allow it.

## Non-Goals For V1

- Do not rebuild the whole backend.
- Do not make Web and desktop share one forced renderer.
- Do not make Live2D required.
- Do not make prompt protocol editable by creators.
- Do not merge old Electron and Tauri desktop clients.
- Do not make every advanced ability visible in the first workshop version.
- Do not store message `role` as character names or user labels.

## Target Users

Primary user: creator / customizer.

This user may understand prompt design, character writing, image assets, and role expression. The UI should respect that power-user workflow without forcing them to edit repo files by hand.

Expected creator actions:

- Build their own character.
- Tune personality and relation to the user.
- Add full-body, half-body, or portrait images.
- Add outfits and expression differences.
- Tune how the bubble behaves for awkward image ratios.
- Export and share a character pack.
- Switch between multiple characters without memory leakage.

## Existing Assets To Reuse

`desktop_pet_next` already contains useful desktop-pet primitives:

- Transparent frameless Tauri/WebView2 window.
- Static portrait renderer with CSS motion boundary.
- Character pack selection and zip import in settings.
- Runtime `character_pack_id` sent to `/think`.
- Resource manifest loading from backend.
- Local click reactions.
- Speech segments and TTS queue handling.
- Local music queue and lyric context.
- Desktop context, optional screen vision, and proactive wake.

`desktop_pet_creator_kit` already contains:

- `characters/<pack_id>/character.json`
- `persona.md`
- `assets/characters/<outfit>/<emotion>.<png|jpg|jpeg|webp>`
- create / draft / check / import / export scripts.

`companion_v01` already contains:

- `DesktopPetCharacterResourceService`
- desktop-pet prompt context from `character.json` + `persona.md`
- `/resource-manifest`
- `/desktop-pet/health`
- `/desktop-pet/diagnostics`
- `/think` with `client_mode = desktop_pet`
- SQLite-backed memory store.

The redesign should expose and complete these abilities instead of pretending they do not exist.

## Product Surface

### Desktop Pet Window

The pet window remains a pure desktop window:

- Transparent if creator provides cutout art.
- Background retained if creator uploads art with background.
- No forced scene frame for desktop.
- Window size can vary by character/layout.
- Character switching restores that character's last desktop state.

The pet window should stay focused on:

- The character image.
- Speech bubble.
- Compact quick menu.
- Quick input.
- Music drop/listening.
- Local reactions.

### Settings Window

Keep settings as a control center:

- Backend status.
- Quick character switch.
- Voice settings.
- Music controls.
- Desktop sensing.
- Ability diagnostics.
- Advanced/debug controls.

Do not overload settings with the full creator workflow.

### Character Workshop Window

New standalone window. Suggested pages:

1. Character List
2. Basic Identity
3. Persona Form
4. Portraits And Expressions
5. Display Calibration
6. Test Chat
7. Import / Export

The workshop should be reachable from:

- Desktop pet right-click menu: `角色工坊`
- Settings character page: `打开角色工坊`

## Character Workshop Pages

### 1. Character List

Purpose: make custom characters visible immediately.

Required actions:

- Create new character.
- Duplicate existing character.
- Import zip.
- Export zip.
- Delete or archive character.
- Apply / switch to character.
- Open character folder.

Each item should show:

- Character name.
- Pack id.
- Default portrait preview.
- Expression count.
- Outfit count.
- Last used time.
- Warning badges for missing prompt, missing default image, invalid schema, missing layout calibration.

### 2. Basic Identity

Fields:

- Character name.
- App/display name.
- Character self-reference.
- User title / default user label.
- Relationship summary.
- Short public description.
- Default outfit.
- Default expression.
- Music/listening expression.

Important rule:

- UI may display the user label and character name.
- Database `role` must remain `user`, `assistant`, `system`, or `tool`.

### 3. Persona Form

Creators own character definition. Program-owned protocol prompts stay in code.

Form fields:

- Relationship setup.
- Personality keywords.
- Speaking style.
- Catchphrases / common expressions.
- Boundaries and taboos.
- Proactive wake style.
- Example lines.
- Supplemental setting text.

Generated artifact:

- `persona.md` can be generated from these fields.
- Advanced mode may let creators edit `persona.md` directly.
- The app should preserve manual edits when possible.

Prompt layering:

1. Runtime protocol prompt: code-owned.
2. Desktop pet safety and output protocol: code-owned.
3. Character pack identity and available resources: generated from `character.json`.
4. Creator persona: `persona.md` or generated persona text.
5. Turn context: user message, current desktop state, music state, recent character-scoped memory.

### 4. Portraits And Expressions

Supported V1 shape:

```text
desktop_pet_creator_kit/characters/<pack_id>/
  character.json
  persona.md
  assets/
    characters/
      <outfit>/
        <emotion>.png
        <emotion>.webp
```

Required abilities:

- Add outfit.
- Rename outfit.
- Delete outfit.
- Add expression image.
- Rename expression id.
- Map aliases to expression ids.
- Select default expression.
- Select listening/music expression.
- Preview expression.
- Detect missing required/recommended expressions.

Recommended expression ids:

- normal / neutral
- thinking
- happy
- shy
- angry / pout
- confused
- sleepy / tired
- listening
- music
- touched / pet

Expression ids can be Chinese or English. Alias maps should bridge common English model outputs to creator-defined ids.

### 5. Display Calibration

Problem:

Full-body, half-body, portrait, and background-included images cannot share one fixed window or bubble position.

V1 calibration model:

- Save default calibration per outfit.
- Allow per-expression override later.
- Start with automatic initial placement.
- Always allow manual adjustment.

Calibration fields:

```json
{
  "layout": {
    "window": {
      "width": 420,
      "height": 620
    },
    "portrait": {
      "scale": 1.0,
      "offset_x": 0,
      "offset_y": 0,
      "fit": "contain",
      "anchor": "bottom_center"
    },
    "bubble": {
      "anchor_x": 0.5,
      "anchor_y": 0.12,
      "max_width": 300,
      "placement": "above_head"
    },
    "hit_test": {
      "mode": "alpha",
      "alpha_threshold": 20,
      "fallback": "portrait_bounds"
    }
  }
}
```

Automatic first pass:

- Use image alpha bounds if available.
- If no alpha, use full image bounds.
- Estimate character bounds.
- Place bubble near top third for full-body, near top edge for portrait/half-body.
- Set default window size based on image aspect ratio and a sane max.

Manual editor:

- Drag portrait.
- Scale portrait.
- Resize window preview.
- Drag bubble anchor.
- Preview short, medium, and long bubbles.
- Preview thinking, speaking, click, and music motions.
- Save calibration.
- Reset to automatic.

### 6. Test Chat

Purpose: let creators test without leaving the workshop.

Required actions:

- Send a test message as current user.
- Show raw speech, expression, and selected prompt summary.
- Preview with current portrait calibration.
- Show which character memory scope is active.
- Allow "apply and switch to desktop pet".

Do not expose full system prompts by default. Provide enough diagnostics to debug custom character behavior.

### 7. Import / Export

V1 must support zip import/export.

Export should include:

- `character.json`
- `persona.md`
- `assets/characters/**`
- optional `voice/**`
- optional `layout.json` or layout block inside `character.json`

Import should:

- Validate schema.
- Reject path traversal.
- Show warnings before apply.
- Allow overwrite only with explicit checkbox.
- Refresh `desktop_pet_next` pack registry.
- Not require restarting the pet when possible.

## Character Pack Schema V0.2 Proposal

The current `akane.character.v0.1` can migrate gradually. V0.2 should be backwards compatible where possible.

```json
{
  "schema_version": "akane.character.v0.2",
  "identity": {
    "id": "reimu_demo",
    "name": "博丽灵梦",
    "app_name": "Reimu Desktop Pet",
    "self_reference": "我",
    "user_title": "你",
    "relationship": "住在桌面边上的巫女，会吐槽但也会帮忙。"
  },
  "persona_form": {
    "personality_keywords": ["慵懒", "毒舌", "现实", "心软"],
    "speaking_style": "短句偏多，吐槽自然，不写解释性设定说明。",
    "catchphrases": ["真麻烦啊", "先说好，我可不是免费劳动力。"],
    "boundaries": "不要过度撒娇，不要把自己说成通用客服。",
    "proactive_style": "看到用户停顿时先轻轻吐槽一句，再问是否需要帮忙。",
    "example_lines": [
      {"text": "又卡住了？把问题说出来，省得你一个人绕圈。", "emotion": "normal"}
    ],
    "extra_setting": "补充世界观或创作者自由文本。"
  },
  "appearance": {
    "default_outfit": "default",
    "default_emotion": "normal",
    "music_emotion": "listening",
    "required_emotions": ["normal"],
    "recommended_emotions": ["thinking", "happy", "confused", "listening"]
  },
  "dialogue": {
    "input_placeholder": "和她说点什么……",
    "session_display_title": "角色桌宠对话",
    "tts_test_text": "语音播放测试。",
    "proactive_wake_prompt": "用户暂时没有说话。按角色风格自然轻声搭话。",
    "local_click_lines": [
      {"text": "嗯？有事就说。", "emotion": "normal"}
    ]
  },
  "play_feedback": {
    "throw_fast": {"emotion": "shock", "bubble": {"text": "啊啊啊飞起来啦！", "duration_ms": 1500}},
    "throw_light": {"emotion": "confused", "bubble": {"text": "", "duration_ms": 0}},
    "wall_hit": {"emotion": "confused", "bubble": {"text": "撞到了。", "duration_ms": 1200}},
    "land": {"emotion": "", "bubble": {"text": "", "duration_ms": 0}}
  },
  "emotion_aliases": {
    "normal": ["normal", "正常"],
    "thinking": ["thinking", "思考中", "困惑"],
    "happy": ["happy", "开心"],
    "listening": ["listening", "侧耳听"]
  },
  "layout": {
    "outfits": {
      "default": {
        "window": {"width": 420, "height": 620},
        "portrait": {"scale": 1.0, "offset_x": 0, "offset_y": 0, "fit": "contain"},
        "bubble": {"anchor_x": 0.5, "anchor_y": 0.12, "max_width": 300, "style": "soft"}
      }
    }
  },
  "voice": {
    "provider": "",
    "profile_id": "",
    "notes": ""
  },
  "assets": {
    "runtime_source": "character pack assets",
    "asset_root": "assets",
    "portrait_glob": "assets/characters/<outfit>/<emotion>.<png|jpg|jpeg|webp>"
  }
}
```

## Runtime State

Character switching should feel like entering another character's space.

Persist per character:

- Window size.
- Window position.
- Scale.
- Opacity if desired.
- Active outfit.
- Current emotion.
- Last session id.
- Bubble anchor/layout.
- Restore-latest preference if needed.

Current Tauri `PetState` is global. V1 should either:

1. Add a per-character state map, or
2. Store a separate state object by `profile_user_id + character_pack_id`.

Recommended structure:

```json
{
  "global": {
    "backendUrl": "http://127.0.0.1:9999",
    "profileUserId": "master",
    "musicLibraryMode": "shared"
  },
  "characters": {
    "akane_sample": {
      "sessionId": "desktop_pet_next_xxx",
      "x": 1200,
      "y": 380,
      "width": 340,
      "height": 560,
      "scale": 1.0,
      "outfit": "猫娘",
      "currentEmotion": "正常"
    }
  }
}
```

## Database And Memory Design

### Message Roles

Do not store character names in `role`.

Correct:

- `role = user`
- `role = assistant`
- `speaker_name = 博丽灵梦`
- `user_label = 你`
- `character_id = reimu_demo`

Incorrect:

- `role = 博丽灵梦`
- `role = 主人`

Reason:

- Standard roles keep LLM history, tool calling, adapters, exports, and future providers compatible.
- UI display can use `speaker_name` and `user_label`.

### Character Isolation

Current memory queries are mostly keyed by `profile_user_id` and `session_id`. V1 needs a character dimension.

Recommended new fields:

- `character_id TEXT NOT NULL DEFAULT ''`
- `speaker_name TEXT NOT NULL DEFAULT ''`
- `user_label TEXT NOT NULL DEFAULT ''`

Tables that should gain character dimension or equivalent scoped filtering:

- `chat_messages`
- `memory_summaries`
- `memory_semantic_summaries`
- `eval_turns`
- `chat_sessions`
- `reminders` if reminders should belong to a character
- `persona_cards` if retained for desktop-pet mode
- generated/debug state that restores latest final response

Recommended indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_chat_character_session_seq
ON chat_messages(profile_user_id, character_id, session_id, seq_no);

CREATE INDEX IF NOT EXISTS idx_summary_character_time
ON memory_summaries(profile_user_id, character_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_semantic_character_time
ON memory_semantic_summaries(profile_user_id, character_id, last_reinforced_ts DESC, importance DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_character_updated
ON chat_sessions(profile_user_id, character_id, updated_at DESC, created_at DESC);
```

Migration rule:

- Existing data without character id should map to `akane_sample` or a legacy blank scope, decided explicitly.
- Do not silently mix legacy data into every new character.
- If uncertain, keep legacy data attached to Akane demo only.

### Shared User Profile

Shared memory is separate from character memory.

Default:

- Characters do not read shared user profile unless enabled.

Future table concept:

```sql
CREATE TABLE IF NOT EXISTS shared_user_profile_facts (
  fact_id TEXT PRIMARY KEY,
  profile_user_id TEXT NOT NULL,
  fact_type TEXT NOT NULL,
  content TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS character_memory_permissions (
  profile_user_id TEXT NOT NULL,
  character_id TEXT NOT NULL,
  shared_profile_enabled INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(profile_user_id, character_id)
);
```

This supports Neuro/Evil-style independent memory with optional shared facts later.

## Backend Contract

Every desktop-pet request that can affect identity, memory, resource prompt, sessions, diagnostics, or latest-state restore should include:

```json
{
  "client_mode": "desktop_pet",
  "profile_user_id": "master",
  "real_user_id": "master",
  "session_id": "desktop_pet_next_xxx",
  "character_pack_id": "reimu_demo"
}
```

Backend should resolve:

- `character_id = sanitize(character_pack_id)`
- `speaker_name` and `user_label` from character pack identity.
- resource manifest from selected character pack.
- persona prompt context from selected character pack.
- memory scope from `profile_user_id + character_id`.

## Frontend Contract

The desktop client should treat the active character as a first-class runtime object:

```ts
type ActiveCharacterRuntime = {
  profileUserId: string;
  characterPackId: string;
  identity: {
    name: string;
    appName: string;
    userTitle: string;
  };
  sessionId: string;
  outfit: string;
  emotion: string;
  layout: CharacterLayout;
};
```

No desktop-pet call should fall back to hardcoded Akane identity when a valid active character exists.

## Visual Renderer Boundary

The renderer should keep a thin stable boundary:

- `setCharacterLabel(label)`
- `setExpression(entry)`
- `setMotion(motion)`
- `setLayout(layout)`
- `getStatus()`

Static images are V1. Live2D, Spine, or video portrait can be future renderers.

The app logic should not care whether the renderer is static or animated.

## Music Design

Music library is global per user/profile:

- One shared queue/library.
- Not duplicated per character.
- Current character's persona controls speech and commentary.

Request context should include current music state as it already does, but avoid hardcoded Akane labels in music UI and prompts when another character is active.

## Acceptance Criteria

V1 is acceptable when:

- A creator can create a new character from the UI without editing files manually.
- The creator can configure the persona fields and see generated/advanced persona text.
- The creator can upload at least one outfit with multiple expression images.
- The creator can calibrate layout and bubble anchor for a full-body image and a half-body image.
- Character switching restores each character's last session and layout.
- `/think` receives the active `character_pack_id`.
- The backend prompt uses the active character pack identity/persona.
- Chat history and memory retrieval do not leak between two test characters by default.
- Music still works and is shared across characters.
- Old Electron desktop pet still starts or remains untouched.
- `desktop_pet_next` build passes.

## Review Checklist

- Is Akane hardcoded in new user-facing copy where active character should be used?
- Does any new DB logic store `role` as a display name?
- Does any memory query ignore `character_id` after the migration?
- Can a creator understand where to create/import/export a character?
- Can a half-body portrait place the bubble in a sane position?
- Can a full-body portrait avoid being cramped by fixed window size?
- Can the app recover if a character pack is invalid?
- Can the user switch back to Akane and recover the previous window/session state?
