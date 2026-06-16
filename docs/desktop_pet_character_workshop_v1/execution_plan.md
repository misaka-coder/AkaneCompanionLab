# Desktop Pet Character Workshop V1 Execution Plan

This plan is for implementing the creator-facing desktop pet character workshop without damaging the existing project.

Primary design document: `docs/desktop_pet_character_workshop_v1/design.md`
Workshop UI redesign document: `docs/desktop_pet_character_workshop_v1/workshop-ui-design.md`
Agent prompt pack: `docs/desktop_pet_character_workshop_v1/agent_prompts.md`

## Ground Rules

- Do not modify old Electron `desktop_pet` unless fixing a regression caused by shared files.
- Treat `desktop_pet_next` as the new mainline.
- Keep changes incremental and reviewable.
- Avoid broad rewrites of `companion_v01/engine.py` unless a small seam is first created.
- Keep database roles standard: `user`, `assistant`, `system`, `tool`.
- Do not hardcode Akane in new generic character workflow.
- Never delete creator/user assets during migration.
- Add tests before or with risky storage changes.
- Preserve current music queue behavior.

## Current Implementation Notes

Updated: 2026-06-05

- Phase 1 skeleton implemented: v0.2 identity/persona/layout/voice fields are accepted by the creator kit validator, created by the draft script, normalized by `desktop_pet_next`, accepted by Tauri import validation, and available to the backend desktop-pet prompt context.
- Phase 2 skeleton implemented: `pet_state.json` now has a per-character runtime state map keyed by profile user plus character pack; character switching saves/restores session id, geometry, scale, opacity, outfit, and current emotion.
- Phase 3 shell implemented: `workshop.html` opens as a Tauri window from the pet quick menu and settings character page, lists installed character packs, and can emit apply-character commands.
- **Phase 3A** implemented: persona form UI with tab navigation. Loads identity + persona fields from selected character pack's `character.json`. Local draft save to `localStorage` with auto-save on blur and manual save button. Empty state when no pack selected. Example lines with dynamic add/remove.
- **Phase 3B** implemented: `save_character_pack` (deep-merge identity + persona_form into character.json on disk) and `create_character_pack` (new pack from template with directory structure). Workshop save button calls Tauri command first, falls back to localStorage. "新建角色" button opens a create dialog.
- **Phase 4A complete**: `upload_portrait_image` (magic-byte detection, atomic write, Unicode-safe IDs), `list_pack_assets`, `delete/rename` commands, `set_default_emotion` Tauri commands. Workshop "立绘管理" tab: outfit cards, upload, preview, delete/rename, set-default/music, missing-emotion warnings.
- **Phase 4B complete**: `save_calibration` + `resize_pet_window` Tauri commands. Workshop "显示校准" tab: outfit selector, 7 sliders (window W/H, scale, offset X/Y, bubble X/Y), auto-layout from image aspect ratio, real-time draggable bubble preview, and lightweight bubble style presets. `setLayout` added to visual-renderer and now applies portrait plus bubble layout; `applyCharacterLayout` in main.js uses signature-based debounce to avoid redundant window resizes. Atomic writes + validation on all new commands.
- **Phase 3 import/export complete**: `export_character_pack` Tauri command (recursive zip via `zip` crate, saves to desktop). Workshop header buttons: 📥 Import (file picker → `install_character_pack_zip_bytes`, refresh list), 📤 Export (call `export_character_pack`, show result, open folder).
- Repair pass complete: replaced the undefined `characterPackRegistry` layout lookup, restored valid workshop DOM nesting, made `save_character_pack` create missing `persona_form` for v0.1 packs, routed portrait asset paths through safe child resolution, changed empty sanitized asset ids to hard errors, and made `control-center-lab.html` the sole settings implementation.
- Memory isolation pass complete: `character_pack_id` is persisted on chat messages, chat sessions, episodic summaries, semantic summaries, eval turns, and vector metadata. Desktop-pet turn processing, background summary compaction, explicit `retrieve_memory`, and `/sessions` reads now scope to the active character when a pack id is present. Shared music/gift resources intentionally remain profile-scoped.
- **Phase 6 first slice complete**: workshop "测试对话" tab sends `/think` with the selected `character_pack_id`, `client_mode=desktop_pet`, an isolated `workshop_test_*` session/profile, and a minimal current visual payload. It renders user/assistant messages, streamed/final speech segments, final emotion, prompt-field summary, and memory scope indicators. Full system prompt text remains hidden.
- **Phase 6 save-boundary slice complete**: if the selected pack has unsaved persona edits, test chat now requires a successful file save before `/think`; ordinary save still falls back to localStorage, but test chat stops when the backend-visible `character.json` was not updated.
- **Phase 6 apply/visual slice complete**: test chat can apply the selected pack to the live desktop pet through the existing `setCharacterPack` settings command, and `current_visual` now carries outfit/emotion/available-emotion/layout metadata without local file paths.
- **Phase 7 demo-readiness slice complete**: first-use create/import prompts, pack readiness rows, no-outfit/no-expression states, missing default portrait warnings, lightweight save/switch/preview/error feedback, and README usage notes are implemented without adding fake runtime actions.
- **Post-V1 UI redesign baseline drafted**: `workshop-ui-design.md` captures the next visual pass as a quiet, efficient character editor: compact character rows instead of large cards, visible hierarchy instead of dropdown-driven structure, calmer typography/colors, and light anime detail without binding the UI to one character.

## Phase 0: Repo Mapping And Safety Baseline

Goal: understand exact current boundaries before editing.

Read:

- `desktop_pet_next/src/main.js`
- `desktop_pet_next/src/control-center-lab.js`
- `desktop_pet_next/src/control-center/data-sources.js`
- `desktop_pet_next/src/character-profile.js`
- `desktop_pet_next/src/visual-renderer.js`
- `desktop_pet_next/src-tauri/src/main.rs`
- `desktop_pet_creator_kit/scripts/*.mjs`
- `desktop_pet_creator_kit/templates/character_pack/character.json`
- `companion_v01/desktop_pet_character_resources.py`
- `companion_v01/resource_manifest.py`
- `companion_v01/engine.py`
- `companion_v01/store.py`
- desktop-pet tests under `tests/`

Deliverable:

- A short implementation note listing files to edit, risks, and test commands.

Do not edit code in this phase unless explicitly requested.

## Phase 1: Character Pack Schema And Validation

Goal: extend character pack metadata for workshop fields without breaking existing v0.1 packs.

Likely files:

- `desktop_pet_creator_kit/templates/character_pack/character.json`
- `desktop_pet_creator_kit/scripts/create-character-pack.mjs`
- `desktop_pet_creator_kit/scripts/validate-character-pack.mjs`
- `desktop_pet_creator_kit/scripts/export-character-pack.mjs`
- `desktop_pet_creator_kit/scripts/import-character-pack.mjs`
- `desktop_pet_next/src/character-profile.js`
- `desktop_pet_next/src-tauri/src/main.rs`
- `companion_v01/desktop_pet_character_resources.py`
- `tests/test_desktop_pet_character_resources.py`

Tasks:

1. Add backwards-compatible parsing for v0.2 fields:
   - `identity.self_reference`
   - `identity.relationship`
   - `persona_form`
   - `layout`
   - optional `voice`
2. Keep v0.1 packs valid.
3. Expose normalized profile data to `desktop_pet_next`.
4. Add validation warnings for missing persona fields, default image, invalid layout, and missing required expressions.
5. Ensure export/import includes new files and layout.

Acceptance:

- Existing `akane_sample` and `mika_sample` validate.
- A v0.2 test pack validates.
- Invalid zip/path traversal still fails.
- Backend persona context can include v0.2 generated persona text or `persona.md`.

Status:

- Implemented for v0.2 parsing, creator draft defaults, validator warnings, desktop normalization, Tauri import acceptance, and backend prompt-context exposure.
- Still pending export/import-specific tests for every v0.2 optional artifact beyond current zip validation paths.

Suggested tests:

```powershell
python -m unittest tests.test_desktop_pet_character_resources
cd desktop_pet_creator_kit
npm run check:sample
```

## Phase 2: Character Runtime State

Goal: switching characters restores each character's independent desktop state.

Likely files:

- `desktop_pet_next/src-tauri/src/main.rs`
- `desktop_pet_next/src/main.js`
- `desktop_pet_next/src/control-center-lab.js`
- `desktop_pet_next/src/control-center/data-sources.js`
- `desktop_pet_next/src/character-profile.js`
- `desktop_pet_next/README.md`
- tests may need updates in `tests/test_desktop_pet_frontend_contract.py`

Tasks:

1. Extend `pet_state.json` model to store:
   - global settings
   - per-character runtime state map
2. Persist per-character:
   - session id
   - window position
   - window size
   - scale
   - opacity if retained
   - outfit
   - current emotion
   - layout selection
3. On character switch:
   - save previous character state
   - load new character state
   - update `character_pack_id`
   - restore session id or create character-specific session
   - reload manifest
   - apply layout
4. Keep settings and pet snapshots accurate.

Acceptance:

- Switch from Akane to another pack and back.
- Each character restores its own session and window geometry.
- Backend requests carry the active `character_pack_id`.
- No UI text incorrectly says Akane when another character is active.

Status:

- Implemented the runtime map and request contract skeleton.
- Manual multi-pack switch/restart testing is still needed with at least one non-Akane pack.

Suggested tests:

```powershell
cd desktop_pet_next
npm run build
```

## Phase 3: Standalone Character Workshop Window

Goal: create a dedicated creator workflow window.

Likely files:

- `desktop_pet_next/src-tauri/src/main.rs`
- `desktop_pet_next/workshop.html`
- `desktop_pet_next/src/workshop.js`
- `desktop_pet_next/src/workshop.css`
- `desktop_pet_next/src/main.js`
- `desktop_pet_next/src/control-center-lab.js`
- `desktop_pet_next/src/control-center/data-sources.js`
- `desktop_pet_next/index.html` if menu wiring requires it

Tasks:

1. Add a Tauri window route for `workshop.html`.
2. Add commands/events:
   - open workshop
   - list character packs
   - create character pack
   - update character metadata
   - import/export zip
   - open pack folder
   - apply character
3. Build pages:
   - Character List
   - Basic Identity
   - Persona Form
   - Portraits And Expressions
   - Display Calibration placeholder or initial editor
   - Test Chat placeholder
   - Import / Export
4. Add entry points:
   - pet quick menu: `角色工坊`
   - settings character page: `打开角色工坊`
5. Ensure workshop uses active character copy but can edit inactive packs.

Acceptance:

- User can open workshop from the pet and settings.
- User can create a character pack.
- User can edit basic identity/persona fields and save.
- User can apply the character and see the pet switch.
- UI makes custom-character capability obvious.

Status:

- Phase 3A implemented: tab navigation, persona form with all identity + persona fields, example lines, localStorage draft auto-save, empty state.
- Phase 3B implemented: `save_character_pack` Tauri command (deep-merge into character.json), `create_character_pack` Tauri command (new pack from template), "新建角色" dialog in workshop UI, save button wired to file save with localStorage fallback. Build passes on both Rust and Vite sides.
- Import/export, portrait management, display calibration, test chat, apply-to-desktop, and demo-readiness flows are now implemented in later phases. Remaining work here is manual verification and visual redesign, not missing Phase 3 shell functionality.

Suggested tests:

```powershell
cd desktop_pet_next
npm run build
```

Manual:

- Open workshop.
- Create `test_creator_pack`.
- Edit name/user title/persona fields.
- Apply it.
- Restart app and confirm pack persists.

## Phase 4: Portrait Upload And Calibration

Goal: make full-body, half-body, and portrait images look intentional.

Likely files:

- `desktop_pet_next/src/workshop.js`
- `desktop_pet_next/src/workshop.css`
- `desktop_pet_next/src/visual-renderer.js`
- `desktop_pet_next/src/main.js`
- `desktop_pet_next/src-tauri/src/main.rs`
- `desktop_pet_creator_kit/scripts/validate-character-pack.mjs`

Tasks:

1. Add upload or import image files into:
   - `assets/characters/<outfit>/<emotion>.<ext>`
2. Add outfit/expression management.
3. Add automatic initial layout:
   - alpha bounds if image has alpha
   - full image bounds fallback
   - initial bubble anchor
   - initial window size based on aspect ratio
4. Add manual calibration:
   - drag portrait
   - scale portrait
   - drag bubble anchor
   - resize preview/window bounds
   - preview short/long bubbles
5. Save per-outfit layout, with future per-expression override support.
6. Runtime renderer applies layout.

Acceptance:

- Full-body image can be placed cleanly.
- Half-body image can have bubble moved away from face.
- Different character packs can have different window sizes.
- Calibration persists after switch/restart.

Suggested tests:

```powershell
cd desktop_pet_next
npm run build
```

Manual:

- Upload a full-body portrait and calibrate.
- Upload a half-body portrait and calibrate.
- Switch away and back.
- Confirm bubble remains stable.

## Phase 5: Character-Scoped Memory

Goal: custom characters do not leak memories into each other by default.

Likely files:

- `companion_v01/store.py`
- `companion_v01/engine.py`
- `companion_v01/routes/sessions.py`
- `companion_v01/routes/think.py`
- `companion_v01/vector_store.py`
- `companion_v01/vector_entry_builder.py`
- memory tests under `tests/`

Tasks:

1. Add `character_id`, `speaker_name`, and `user_label` fields where needed.
2. Add migration logic for existing SQLite db.
3. Resolve character identity for every desktop-pet turn.
4. Filter recent messages, episodic summaries, semantic summaries, eval turns, and sessions by character for desktop-pet mode.
5. Keep legacy non-desktop clients compatible.
6. Add shared user profile permission seam, even if UI comes later.
7. Ensure vector metadata includes character id, and retrieval filters by it.

Important:

- Do not put character display names into `role`.
- Do not make `profile_user_id` fake unless deliberately using a compatibility bridge. Prefer explicit character fields.

Acceptance:

- Two characters with the same user do not see each other's chat history or summaries.
- Switching back to a character restores that character's session.
- Shared music remains shared.
- Existing Akane legacy session still works.

Suggested tests:

```powershell
python -m unittest tests.test_desktop_pet_backend_contract tests.test_desktop_pet_character_resources
```

Add new tests:

- create two character ids
- add messages under both
- verify session lists and summaries filter correctly
- verify `/think` prompt context uses active character only

## Phase 6: Test Chat And Prompt Diagnostics

Goal: creators can test persona without leaving workshop.

Likely files:

- `desktop_pet_next/src/workshop.js`
- `desktop_pet_next/src/workshop.css`
- `desktop_pet_next/src/main.js`
- backend route if needed

Tasks:

1. Add a test chat panel.
2. Send `/think` with:
   - active or selected `character_pack_id`
   - test session id
   - `client_mode = desktop_pet`
3. Render:
   - speech segments
   - selected emotion
   - prompt-field summary
   - memory scope indicator
4. Keep system prompts hidden unless advanced diagnostics is enabled.

Acceptance:

- Creator edits persona form, tests a message, sees changed style.
- Test chat does not pollute production session unless explicitly applied.

Status:

- First slice implemented in `workshop.html` / `src/workshop.js` / `src/workshop.css`.
- The workshop saves dirty persona fields before sending a test message, then calls `/think` with `client_mode=desktop_pet` and the selected `character_pack_id`.
- Test calls use an isolated `workshop_test_*` session/profile instead of `state.sessionId` / `state.profileUserId`, so production desktop-pet memory scope is not reused.
- Prompt diagnostics currently show field-level summaries from `character.json` / form data only; full system prompts are intentionally not rendered.
- Save-boundary repair: when testing the pack currently being edited, localStorage fallback is not treated as enough because the backend prompt builder reads the on-disk character pack.
- Apply/visual repair: the test panel has an "应用到桌宠" control backed by the real settings event boundary, plus a small preview and backend-safe `current_visual` fields for the selected outfit, emotion, available emotions, and saved layout.

## Phase 7: Polish And Demo Readiness

Goal: make the capability obvious and attractive.

Tasks:

1. Improve first-use route:
   - if only default pack exists, highlight `创建角色` and `导入角色包`.
2. Improve empty states:
   - no expressions
   - invalid pack
   - missing default portrait
3. Replace hardcoded Akane labels where generic active-character text is needed.
4. Add subtle animation for:
   - character switching
   - expression preview
   - save success
   - invalid field warnings
5. Add README updates:
   - how to create a character from UI
   - how to import/export
   - how memory isolation works

Acceptance:

- A new viewer can find customization without reading source files.
- The workshop looks like the place where the product's main power lives.

Status:

- First-use route: when there are zero or one packs, the character list highlights `新建角色` and `导入角色包`.
- Empty/resource states: list and portrait tabs now show no-pack, no-outfit, no-expression, missing default outfit, missing default emotion, and missing default portrait states.
- Generic labels: workshop-specific UI does not hardcode Akane as the active character; Akane remains documented only as a demo/sample pack.
- Feedback: save success, character switching, expression preview, and create-form validation use small CSS animations.
- README now documents create/import/export and memory-isolation behavior for creator handoff.

## Phase 8: Workshop UI Redesign

Goal: improve the workshop's visual clarity and usability after the V1 functional loop is in place.

Primary document:

- `workshop-ui-design.md`

Tasks:

1. Replace the current blue/glass-heavy visual treatment with a calmer app-like surface.
2. Fix horizontal overflow across desktop and mobile widths.
3. Redesign the character list as compact rows instead of large rounded cards.
4. Keep primary hierarchy visible with tabs/rails/sections, not dropdown-first navigation.
5. Reduce form visual noise and make persona editing feel like a structured editor.
6. Make portraits, calibration, and test chat feel like task-specific tools.
7. Keep action boundaries unchanged; do not add fake bridges for visual completeness.

Acceptance:

- The first screen clearly shows the character list and selected character status.
- Users can find create/import/edit/apply/test without reading docs.
- The UI no longer reads as a pile of cards or a one-hue blue prototype.
- Text scale, color, and spacing are consistent across tabs.
- Deferred or unavailable actions remain honest and do not fake success.

Suggested tests:

```powershell
python -m unittest tests.test_desktop_pet_frontend_contract
cd desktop_pet_next
npm run build
```

Manual:

- Browser smoke `workshop.html` at desktop width and narrow viewport.
- Verify list, persona, portraits, calibration, and test tabs render without horizontal scroll.
- Verify create/import/export/apply buttons still hit their existing real boundaries.

## Integration Order

Recommended order:

1. Phase 1 schema and validation.
2. Phase 2 per-character runtime state.
3. Phase 3 workshop shell.
4. Phase 4 calibration.
5. Phase 5 memory isolation.
6. Phase 6 test chat.
7. Phase 7 polish.
8. Phase 8 workshop UI redesign.

Memory isolation is critical, but it can be started in parallel after the character id contract is stable.

## Verification Matrix

Run as applicable:

```powershell
python -m unittest tests.test_desktop_pet_character_resources
python -m unittest tests.test_desktop_pet_backend_contract
python -m unittest tests.test_desktop_pet_frontend_contract
cd desktop_pet_creator_kit
npm run check:sample
cd ..\desktop_pet_next
npm run build
npm run doctor
cargo check --manifest-path src-tauri\Cargo.toml
```

Some machines may not have Rust/Cargo. If `cargo check` cannot run due missing local dependency, report it instead of hiding it.

## Rollback Strategy

- Keep each phase in a separate commit when possible.
- Do not mix docs, backend storage, and frontend UI in one large commit unless necessary.
- If workshop UI fails, it should be possible to revert workshop files without breaking existing settings.
- If memory migration fails, the app should refuse destructive migration and keep the original db untouched.
- Character pack imports should write to a temp location first, then move into place only after validation.
