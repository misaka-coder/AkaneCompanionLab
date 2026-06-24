# Desktop Pet Entertainment Layer V1

Updated: 2026-06-18

This document records the design direction for bringing the old desktop-pet "playfulness" back into `desktop_pet_next`.

The goal is not to port the old implementation. The goal is to preserve the interaction soul: Akane should feel like a companion who can also be physically played with, teased, fed, and sent out into her own small life.

## Core Direction

The new project already has the stronger base: character packs, isolated memory, bubbles, TTS, ASR, desktop context, music, tool actions, NPC runtime, and Web scene support.

The missing layer is the entertainment feedback loop:

- user does something physical or playful;
- Akane reacts immediately with motion, expression, bubble, sound/TTS only when appropriate;
- lightweight state changes happen locally or through a structured backend service;
- meaningful patterns are summarized into memory later, instead of every small poke polluting long-term memory.

Treat this as an optional play layer attached to the serious companion base.

## Product Principles

1. Akane is both long-term companion and playable desktop creature.
2. Physical operations should be real interactions, not text descriptions like "I pat your head".
3. Local feedback comes first. LLM calls are debounced, summarized, or reserved for meaningful events.
4. Entertainment systems must be optional. Users who do not want hunger/feeding/roaming should be able to turn them down or off.
5. Character personality owns the reaction. Anger, sulking, teasing, and refusal are allowed when they match the character, but physical play must not lock out the user's next input by default.
6. Do not fake success. If a motion, sound, feed action, outing, or backend feature is not connected, degrade visibly and structurally.
7. Acceptance is based on what the user can see, hear, click, and feel, not only on fields existing in state.

## Old Project Reference Value

The old project is valuable as a handfeel reference, not as code to copy. Useful files from the historical project include:

- `frontend/src/come/bowen/mypet/ui/PhysicsEngine.java`
- `frontend/src/come/bowen/mypet/ui/PetInputController.java`
- `frontend/src/come/bowen/mypet/ui/FriendClient.java`
- `frontend/src/come/bowen/mypet/ui/ShopDialog.java`
- `frontend/src/come/bowen/mypet/ui/InventoryDialog.java`
- `frontend/src/come/bowen/mypet/ui/GameDialog.java`
- `services/backstage_life.py`

The strongest reusable ideas:

- throw, gravity, wall collision, and low-bounce landing;
- shocked expression when thrown hard;
- wall-hit feedback without blocking follow-up interaction;
- region-based touch reactions: head, cheek, hand, tail, body;
- repeated pokes cause character-specific combo feedback;
- drag rubbing creates affection feedback;
- shop -> inventory -> feed/use -> status change -> expression/bubble feedback;
- work/outing: Akane leaves, returns with money/items/status/story;
- simple minigames with immediate reward and visible reaction;
- backstage life events that later become something Akane can tell the user.

## Handfeel Baseline

Use the old parameters as the first tuning baseline, then adapt them to CSS pixels, device scale factor, and Tauri window behavior.

| Behavior | Baseline |
|---|---:|
| Physics tick | 20 ms |
| Gravity | 0.8 |
| Air/friction on ground | 0.94 |
| Bounce | 0.4 |
| Drag threshold | 5 px |
| Drag velocity factor | 0.4 |
| Fast throw threshold | velocity > 8 |
| Wall pain threshold | abs(vx) > 7 |
| Click combo interval | 800 ms |

Important handfeel note: low bounce is a feature. Akane should not behave like a rubber ball. She should feel light enough to throw, but still like a character with weight and pain feedback.

## Runtime Architecture

Keep V1 mostly in `desktop_pet_next`.

Recommended local modules, either as new files or as clearly separated blocks in `src/main.js` for the first slice:

- `playState`: current mode, velocity, combo counters, last touch, idle action flags.
- `physicsController`: drag sampling, throw release, gravity, floor, wall collision, damping.
- `touchController`: hit region detection, click/poke/rub/combo interpretation.
- `reactionController`: maps events to expression, CSS motion, bubble, optional TTS, optional backend event.
- `idleMotionController`: optional jump, hide, wander, float, and corner behavior.

Renderer responsibility:

- `src/visual-renderer.js` should accept richer motion names beyond `idle/thinking/speaking/click`.
- `src/styles.css` should own CSS-only motions such as `dragging`, `thrown`, `hit-wall`, `jump`, `hide-corner`, `float`.
- Missing portrait expressions must fall back to available aliases/default expression plus CSS motion and bubble.
- Local play bubbles and play expressions should come from character-pack `play_feedback`; unset values fall back to neutral defaults.

Native/Tauri responsibility:

- continue using existing window movement and native hit-test commands;
- do not move desktop actions into the backend;
- do not expose local absolute paths in logs, prompts, snapshots, or memory.

## Hit Testing And Character Differences

Current `desktop_pet_next` already has a useful base:

- frontend hitbox polygon;
- Rust-side Windows `WM_NCHITTEST`;
- transparent regions can return `HTTRANSPARENT`;
- `update_hit_regions` and `set_hit_test_enabled` already exist.

The limitation is that current hit testing is coarse. It does not yet know the exact visible alpha shape or semantic body parts for arbitrary character packs.

Phased route:

1. **V1 polygon route**
   - keep the current pet polygon;
   - make physical play and reactions work first;
   - preserve native transparent-region passthrough.
2. **V1.5 alpha route**
   - infer a tighter visible bounding region from portrait alpha;
   - optionally generate a coarse alpha mask or simplified polygon;
   - avoid blocking the desktop where the portrait is transparent.
3. **V2 semantic hit areas**
   - character packs may provide optional `hit_areas.json`;
   - areas use normalized coordinates so different portrait sizes work;
   - supported regions: `head`, `face`, `cheek`, `body`, `hand`, `tail`, `leg`, `accessory`.
4. **V3 workshop calibration**
   - add a visual editor in the character workshop;
   - creators can draw/adjust hit areas on top of each portrait.

Fallback rule: if a character pack has no semantic hit areas, the runtime estimates:

- top area -> head;
- upper-middle -> face/cheek;
- center -> body;
- side bands -> hand/arm;
- lower area -> leg/tail/unknown.

## Optional Play Modes

Entertainment should be configurable without turning the whole companion into a chore game.

Suggested settings:

- `playLayerEnabled`: master switch.
- `physicsEnabled`: drag/throw/gravity/collision.
- `idleMotionLevel`: `off`, `light`, `active`, `free`.
- `cultivationEnabled`: hunger/affection/energy/coins/inventory loop.
- `needsDecayEnabled`: whether hunger/energy decay over time.
- `llmEventReporting`: `off`, `important_only`, `summary`.

Mode meanings:

- `off`: no idle movement beyond ordinary breathing/click feedback.
- `light`: special motions only after user interaction.
- `active`: occasional jump, hide, or small wander while idle.
- `free`: optional floating/roaming behavior; never enable by default because it can block the user's view.

User-facing settings belong in `control-center-lab.html`, not `settings.html`.

## Phase 1: Physical Handfeel V1

This is the first execution slice.

Scope:

- drag threshold and velocity sampling;
- release-to-throw with inertia;
- gravity and damping;
- screen/work-area floor;
- left/right wall collision;
- low-bounce landing;
- hard throw shock expression;
- wall-hit bubble and expression;
- no forced dizzy/click lockout; users can repeatedly catch and throw the pet;
- character-pack `play_feedback` for throw, wall-hit, and landing expression/bubble text;
- local bubble/expression/CSS motion feedback;
- keep transparent-region passthrough working;
- one optional idle jump action.

Non-goals:

- full shop/inventory/economy;
- Live2D;
- MCP/tool expansion;
- exact pixel-perfect hit areas;
- full backend memory integration;
- NPC outings.

Acceptance:

- user can drag Akane and release her into a visible throw;
- she falls and lands without teleporting or drifting forever;
- wall collision produces immediate visible feedback;
- repeated throws remain responsive and do not block click/drag control;
- local physics bubbles stay neutral by default and can be overridden per character pack;
- when the portrait is not hit, the desktop should remain reachable as much as the current native hit-test permits;
- fallback expressions still produce an understandable reaction;
- `git diff --check` passes.

## Phase 2: Touch Regions V1

Scope:

- local region detection with estimated regions;
- single poke, double click, rub, and combo handling;
- head/cheek/body/hand/tail reaction mapping;
- drag rubbing affection feedback;
- debounced backend event for meaningful touches only.

Acceptance:

- head, cheek/body, and side/hand interactions feel different even without custom hit areas;
- repeated pokes trigger character-specific combo feedback rather than unlimited happy feedback;
- rubbing does not conflict with physical drag;
- bubbles, expression, motion, and optional TTS do not fight each other.

## Phase 3: Optional Cultivation Loop

Scope:

- optional visible status UI: hunger, energy, mood, affection, coins;
- feed/use item loop;
- lightweight inventory;
- status change floating text;
- gentle decay if enabled.

Design rule: no punishment pressure by default. Needs should create roleplay and interaction opportunities, not guilt.

Possible backend direction:

- create a structured service for pet status, inventory, coins, and events;
- do not reuse the old SQLite/Swing implementation;
- integrate with the existing gift/artifact system where practical.

## Phase 4: Outing And Backstage Life

Scope:

- explicit user button: work, walk, visit, explore;
- optional spontaneous outings later;
- Akane leaves or hides for a while;
- she returns with coins/items/mood/story;
- advanced version can let her talk to NPCs or another LLM for a few rounds;
- important outings become diary/memory candidates.

Acceptance:

- user has a clear button and visible control;
- Akane visibly leaves and returns;
- failures are structured, not fake success;
- returned story/status/items are consistent.

## Memory Rules

Do not write every touch into long-term memory.

Use four levels:

1. **Immediate event**
   - affects current motion, expression, bubble, and local status only.
2. **Short-term counters**
   - today: head pats, throws, wall hits, feed events, outings.
3. **Daily summary**
   - compresses playful events into one or two natural notes.
4. **Long-term preference**
   - only repeated and personality-relevant patterns survive, such as "the user likes physically playing with Akane" or "the user often uses feeding as affection".

Memory must record meaning, not raw spam.

## Reaction Policy

Local reaction examples:

- gentle head pat -> happy/pat expression, small bounce, affectionate bubble;
- cheek rub -> shy/annoyed expression depending on character;
- hard throw -> character-pack `throw_fast` expression and short bubble;
- wall hit -> character-pack `wall_hit` expression and bubble;
- repeated poke -> irritation, dodge, or sulk if configured;
- optional dizzy-like flavor can be expressed by a character pack, but should not block click/drag control;
- feed favorite item -> happy/eat/drink expression and status gain;
- low energy work request -> refuse with reason.

LLM event examples:

- do not report every click;
- report after a debounce if the user keeps interacting in a meaningful way;
- report special events such as hard wall hit, favorite food, outing return, or repeated playful interaction summaries;
- provide structured event data rather than only natural-language prompts when possible.

## Expanded Idea Backlog

These are not Phase 1 requirements, but they fit the direction:

- desktop props: cake, drink, ball, cushion, note;
- emotion inertia: Akane stays startled, sulky, or happy for a while;
- interaction combos: throw -> comfort pat, poke -> dodge, feed -> happy jump;
- corner hiding: she retreats to a screen edge after too much teasing;
- free floating: optional mode where she drifts around the desktop;
- desk patrol: slow wandering while idle;
- work companion mode: quieter presence while the user focuses;
- outing diary: she returns with short stories, NPC gossip, tiny items;
- mini games: rock-paper-scissors, quick reaction, rhythm tap, snack catch.

## Verification Checklist

Every implementation slice should verify:

- `git status --short` before editing;
- no unrelated files or generated artifacts touched;
- no local absolute paths, secrets, DBs, logs, caches, or build outputs added;
- desktop pet visible behavior works: bubble, expression, motion, and input remains responsive;
- TTS/music do not overlap with short local reactions unless explicitly intended;
- transparent hit testing still behaves acceptably;
- repeated trigger, failure, slow backend, role switch, and session switch do not expose fake state;
- at least `git diff --check` after edits;
- run the narrowest available smoke/build/test for the touched area.

## First Implementation Task

Start with **Physical Handfeel V1** in `desktop_pet_next`.

The first slice should make Akane throwable again before adding economy, Live2D, MCP expansion, or complex configuration. Once the physical loop feels alive, the rest of the entertainment systems have a body to attach to.
