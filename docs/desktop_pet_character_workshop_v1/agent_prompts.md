# Desktop Pet Character Workshop V1 Agent Prompts

This file contains prompts for vibe-coding agents and optional subagents. Use it with:

- `docs/desktop_pet_character_workshop_v1_design.md`
- `docs/desktop_pet_character_workshop_v1_execution_plan.md`

## When To Use Subagents

Use subagents for analysis, UI review, backend-storage review, or isolated implementation work.

Do not let multiple agents edit the same large file at the same time. `desktop_pet_next/src/main.js`, `desktop_pet_next/src/settings.js`, and `companion_v01/engine.py` are especially conflict-prone.

Good subagent split:

- Repo mapper: read-only impact analysis.
- Character pack/schema agent: creator kit and schema.
- Workshop UI agent: Tauri window and workshop pages.
- Calibration/rendering agent: visual layout and bubble anchor.
- Memory isolation agent: backend store and retrieval scope.
- QA/review agent: tests and regression audit.

Main integrator should own final merge, tests, and commit.

## Main Implementation Agent Prompt

```text
You are working in the AkaneCompanionLab repository.

Goal:
Implement the creator-facing Desktop Pet Character Workshop V1. This project is shifting the desktop pet mainline to `desktop_pet_next`. The old Electron `desktop_pet` is frozen and should not receive new feature work.

Read first:
- docs/desktop_pet_character_workshop_v1_design.md
- docs/desktop_pet_character_workshop_v1_execution_plan.md
- desktop_pet_next/README.md
- desktop_pet_creator_kit/README.md
- companion_v01/desktop_pet_character_resources.py
- companion_v01/store.py

Product direction:
- Akane remains the default demo character, but custom creator characters are the product focus.
- First complete loop: create/import character -> configure persona fields -> upload outfits/expressions -> calibrate display -> switch to character -> character speaks with independent identity and memory.
- The Character Workshop should be a standalone window. Settings only keeps quick switch/status controls.
- `desktop_pet_next` is the mainline.
- `desktop_pet` is frozen.

Hard constraints:
- Do not store character names or user labels in the database `role` field. Roles remain user/assistant/system/tool.
- Character identity is stored separately as character_id, speaker_name, and user_label or equivalent.
- Character memories are isolated by default by profile_user_id + character_id.
- Shared user profile is optional and permissioned; do not implicitly leak shared memory into every character.
- Music library is global/shared, but current character controls how music is discussed.
- Do not hardcode Akane in new generic character UI or backend behavior when active character data exists.
- Preserve existing character packs and old data.
- Do not delete user assets.
- Keep changes incremental and tested.

Implementation approach:
1. Inspect the current code paths and write a short implementation note.
2. Start with character pack schema and runtime state boundaries.
3. Build standalone workshop shell.
4. Add persona form and pack save.
5. Add portrait/expression management and calibration.
6. Add or complete character-scoped memory.
7. Add tests and update docs.

Before editing:
- Run git status.
- Identify unrelated dirty files and do not revert or stage them.

Verification:
- Run relevant Python unit tests.
- Run desktop_pet_next npm build.
- Run creator kit validation.
- If Rust/Cargo is available, run cargo check for Tauri.

Deliverable:
- Working implementation or a clearly scoped partial implementation.
- Summary of changed files.
- Tests run and results.
- Known gaps.
```

## Subagent 1: Repo Mapper Prompt

Use this as a read-only first pass.

```text
You are a read-only repository mapper for AkaneCompanionLab.

Goal:
Map the current desktop pet character-pack, settings, runtime-state, and backend-memory paths before implementation.

Read:
- docs/desktop_pet_character_workshop_v1_design.md
- docs/desktop_pet_character_workshop_v1_execution_plan.md
- desktop_pet_next/src/main.js
- desktop_pet_next/src/settings.js
- desktop_pet_next/src/character-profile.js
- desktop_pet_next/src/visual-renderer.js
- desktop_pet_next/src-tauri/src/main.rs
- desktop_pet_creator_kit/scripts/*.mjs
- companion_v01/desktop_pet_character_resources.py
- companion_v01/store.py
- companion_v01/engine.py
- companion_v01/vector_store.py

Do not edit files.

Output:
1. Current flow for character pack selection.
2. Current flow for `/think` requests and character_pack_id.
3. Current state persistence model.
4. Current import/export and validation capabilities.
5. Current database tables affected by character isolation.
6. Top 10 implementation risks.
7. Recommended first files to edit.
```

## Subagent 2: Character Pack And Schema Prompt

```text
You are implementing character-pack schema and validation for Akane desktop pet.

Goal:
Make creator-facing character packs support V1 workshop fields while preserving all existing v0.1 packs.

Relevant files:
- desktop_pet_creator_kit/templates/character_pack/character.json
- desktop_pet_creator_kit/scripts/create-character-pack.mjs
- desktop_pet_creator_kit/scripts/validate-character-pack.mjs
- desktop_pet_creator_kit/scripts/export-character-pack.mjs
- desktop_pet_creator_kit/scripts/import-character-pack.mjs
- desktop_pet_next/src/character-profile.js
- desktop_pet_next/src-tauri/src/main.rs
- companion_v01/desktop_pet_character_resources.py
- tests/test_desktop_pet_character_resources.py

Requirements:
- Add or parse v0.2 fields:
  - identity.self_reference
  - identity.relationship
  - persona_form
  - layout
  - optional voice
- Keep v0.1 backwards compatible.
- Existing `akane_sample` and `mika_sample` must still validate.
- Export/import must include new files and layout.
- Reject path traversal and invalid zips.
- Add tests for v0.2 parsing and backend persona context.

Do not:
- Change old Electron desktop_pet.
- Force Web to consume the new schema yet.
- Break existing creator kit commands.

Verification:
- python -m unittest tests.test_desktop_pet_character_resources
- cd desktop_pet_creator_kit && npm run check:sample

Output:
- Summary of schema changes.
- Compatibility notes.
- Tests run.
```

## Subagent 3: Workshop UI Prompt

```text
You are implementing the standalone Character Workshop window for desktop_pet_next.

Goal:
Add a creator-facing UI where users can create, import, edit, export, and apply character packs.

Relevant files:
- desktop_pet_next/src-tauri/src/main.rs
- desktop_pet_next/workshop.html
- desktop_pet_next/src/workshop.js
- desktop_pet_next/src/workshop.css
- desktop_pet_next/src/main.js
- desktop_pet_next/src/settings.js
- desktop_pet_next/src/character-profile.js

UX requirements:
- Standalone window, not a hidden settings tab.
- Entry points:
  - pet quick menu: 角色工坊
  - settings character page: 打开角色工坊
- Pages:
  1. Character List
  2. Basic Identity
  3. Persona Form
  4. Portraits And Expressions
  5. Display Calibration
  6. Test Chat
  7. Import / Export
- The first version may stub advanced calibration/test chat only if the create/edit/apply loop works.
- Make custom-character capability obvious.
- Use active character name, not hardcoded Akane, where appropriate.

Implementation requirements:
- Use existing Tauri command/event style.
- Reuse character pack list/import/export capabilities where possible.
- Do not edit backend engine for UI-only work.
- Do not break settings.

Verification:
- cd desktop_pet_next && npm run build
- Manual: open workshop, create pack, edit identity/persona, apply pack.

Output:
- Changed files.
- UI flow summary.
- Known gaps.
```

## Subagent 4: Calibration And Renderer Prompt

```text
You are implementing portrait layout calibration for desktop_pet_next.

Goal:
Make full-body, half-body, and portrait-style character images display well by supporting automatic initial placement plus manual calibration.

Relevant files:
- desktop_pet_next/src/visual-renderer.js
- desktop_pet_next/src/main.js
- desktop_pet_next/src/workshop.js
- desktop_pet_next/src/workshop.css
- desktop_pet_next/src-tauri/src/main.rs
- desktop_pet_creator_kit/scripts/validate-character-pack.mjs

Requirements:
- Add a renderer layout boundary:
  - setLayout(layout)
  - apply portrait scale/offset/fit
  - apply bubble anchor data used by UI
- Calibration should save per outfit.
- Future per-expression override should be possible.
- Automatic initial calibration should use image alpha bounds if available, otherwise image bounds.
- Manual editor should support:
  - drag portrait
  - scale portrait
  - drag bubble anchor
  - preview short/long bubble
  - reset to auto
- Runtime should restore each character/outfit's calibration.

Do not:
- Require cutout transparent images.
- Require Live2D.
- Break existing static image expression switching.

Verification:
- cd desktop_pet_next && npm run build
- Manual full-body image test.
- Manual half-body image test.

Output:
- Layout schema used.
- Renderer changes.
- Manual test notes.
```

## Subagent 5: Memory Isolation Prompt

```text
You are implementing character-scoped memory for Akane desktop pet.

Goal:
Multiple custom desktop-pet characters must not share memories by default.

Relevant files:
- companion_v01/store.py
- companion_v01/engine.py
- companion_v01/routes/sessions.py
- companion_v01/routes/think.py
- companion_v01/vector_store.py
- companion_v01/vector_entry_builder.py
- tests/

Requirements:
- Keep database role values standard: user, assistant, system, tool.
- Add character identity/scope fields or an equivalent explicit scope:
  - character_id
  - speaker_name
  - user_label
- Filter desktop-pet chat history, sessions, summaries, semantic summaries, eval turns, and vector retrieval by profile_user_id + character_id.
- Existing legacy data should not leak into every custom character.
- Shared user profile should be a separate permissioned seam, not implicit.
- Non-desktop clients should remain compatible.

Recommended:
- Add SQLite migrations safely.
- Add indexes for profile_user_id + character_id.
- Add unit tests for two characters under the same user.

Do not:
- Store character name in role.
- Fake profile_user_id as the only isolation strategy unless clearly documented as a temporary bridge.
- Delete or rewrite existing db content.

Verification:
- python -m unittest tests.test_desktop_pet_backend_contract
- Add/run new memory isolation tests.

Output:
- Migration summary.
- Query/filter summary.
- Tests run.
```

## Subagent 6: QA And Regression Review Prompt

```text
You are reviewing the Desktop Pet Character Workshop V1 implementation.

Goal:
Find regressions, memory leaks between characters, hardcoded Akane labels, broken import/export, and UI flows that hide customization.

Read:
- docs/desktop_pet_character_workshop_v1_design.md
- docs/desktop_pet_character_workshop_v1_execution_plan.md
- changed files in the PR/branch

Review priorities:
1. Character memory isolation.
2. Database role correctness.
3. Character switching state restore.
4. Import/export safety.
5. Workshop discoverability.
6. Portrait calibration robustness.
7. Music shared-library behavior.
8. Old Electron not accidentally changed.

Run if possible:
- python -m unittest tests.test_desktop_pet_character_resources
- python -m unittest tests.test_desktop_pet_backend_contract
- python -m unittest tests.test_desktop_pet_frontend_contract
- cd desktop_pet_creator_kit && npm run check:sample
- cd desktop_pet_next && npm run build
- cargo check --manifest-path desktop_pet_next/src-tauri/Cargo.toml

Output findings first, ordered by severity, with file/line references.
If no issues are found, say so and list residual risks.
```

## Handoff Template

Every agent should finish with:

```text
Summary:
- ...

Files changed:
- path

Tests:
- command: result

Manual checks:
- ...

Known gaps:
- ...

Risk notes:
- ...
```

## Stop Conditions

Stop and ask before continuing if:

- A migration would delete or rewrite existing user data.
- A change requires editing old Electron `desktop_pet`.
- A change requires removing existing creator kit commands.
- A change would make Web and desktop forcibly share one renderer.
- The implementation would store display names in `role`.
- The agent cannot tell which character owns a memory record.
