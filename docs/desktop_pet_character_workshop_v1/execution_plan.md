# Desktop Pet Character Workshop V1 Execution Plan

This plan is for implementing the creator-facing desktop pet character workshop without damaging the existing project.

Primary design document: `docs/desktop_pet_character_workshop_v1_design.md`
Agent prompt pack: `docs/desktop_pet_character_workshop_v1_agent_prompts.md`

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

## Phase 0: Repo Mapping And Safety Baseline

Goal: understand exact current boundaries before editing.

Read:

- `desktop_pet_next/src/main.js`
- `desktop_pet_next/src/settings.js`
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
- `desktop_pet_next/src/settings.js`
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
- `desktop_pet_next/src/settings.js`
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

## Integration Order

Recommended order:

1. Phase 1 schema and validation.
2. Phase 2 per-character runtime state.
3. Phase 3 workshop shell.
4. Phase 4 calibration.
5. Phase 5 memory isolation.
6. Phase 6 test chat.
7. Phase 7 polish.

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
