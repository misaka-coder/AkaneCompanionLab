# Desktop Pet Character Workshop V1

This directory is the handoff entry for the next desktop pet mainline work.

## Read First

1. `design.md` - product direction, data model, memory rules, renderer boundaries, and acceptance criteria.
2. `execution_plan.md` - implementation phases and verification checklist.
3. `workshop-ui-design.md` - post-V1 workshop visual redesign direction and implementation slices.
4. `character_lore_prompt_v1.md` - layered character prompt, structured local lore, automatic retrieval, on-demand relationship/event inspection, and low-barrier authoring rules.
5. `reimu_source_research_v1.md` - evidence-first source acquisition and character research plan for a canon-grounded Reimu pack.
6. `agent_prompts.md` - prompts for Claude Code or other coding agents, including optional subagent splits.

## Project Direction

- Treat `desktop_pet_next` as the new desktop pet mainline.
- Keep the old Electron `desktop_pet` frozen unless a task explicitly says otherwise.
- The V1 goal is a creator-facing character workshop: create/import characters, configure persona fields, upload and calibrate portraits, switch characters quickly, and keep memory isolated by default.
- Akane is the demo character, not a hard product constraint.

## Implementation Status

Updated: 2026-06-05

- Implemented the first Phase 1 skeleton in `desktop_pet_next`: per-character runtime state map, active `character_pack_id` request contract, v0.2 character-pack parsing/validation, and a minimal standalone workshop window.
- `desktop_pet_creator_kit` now creates v0.2 draft packs with `persona_form`, `layout`, `voice`, and extended identity fields while keeping v0.1 packs valid.
- `companion_v01` builds desktop-pet prompt context from v0.2 identity/persona fields and stores chat messages, summaries, semantic memories, sessions, eval turns, and vector metadata with `character_pack_id` so desktop-pet memory is isolated by default.
- The old Electron `desktop_pet` remains untouched by this slice.
- **Phase 3A** implemented: workshop persona form UI with tab navigation (角色列表 / 角色设定). The form loads identity fields (name, user_title, self_reference, relationship) and persona fields (personality_keywords, speaking_style, catchphrases, boundaries, proactive_style, extra_setting, example_lines) from the selected character pack's `character.json`. Drafts auto-save to `localStorage`.
- **Phase 3B** implemented: `save_character_pack` and `create_character_pack` Tauri commands. The workshop now saves persona edits directly to `character.json` on disk (merges identity + persona_form fields), and can create new character packs from the UI with a "新建角色" dialog.
- Repair pass: fixed desktop runtime layout lookup to use the active character profile instead of an undefined registry, kept all workshop tab panels inside the main shell, made v0.1 packs gain `persona_form` on save, constrained portrait file writes to safe character-pack paths, and made `control-center-lab.html` the sole settings implementation.
- Memory isolation pass: character switching keeps per-pack runtime state in `pet_state.json`; backend memory reads/writes now scope raw chat, episodic summaries, semantic summaries, sessions, eval turns, and vector search by `character_pack_id` when present. Music/gift libraries still use the shared user profile.
- **Phase 6 first slice** implemented: workshop "测试对话" tab sends `/think` with the selected `character_pack_id`, `client_mode=desktop_pet`, and an isolated workshop test profile/session. It renders speech segments, final emotion, prompt-field summary, and memory scope indicators without exposing full system prompts or reusing the production desktop-pet session/profile.
- **Phase 6 save-boundary slice** implemented: when testing the pack currently being edited, the workshop now requires persona changes to be written to `character.json` before calling `/think`; localStorage fallback remains available for ordinary draft saves but no longer pretends to update backend-visible prompt data during test chat.
- **Phase 6 apply/visual slice** implemented: the test panel can apply the selected pack to the desktop pet through the existing `setCharacterPack` settings command, and its `/think` request now includes backend-safe current visual metadata (pack id, outfit, emotion, available emotion ids, and saved numeric layout) while the UI shows a lightweight calibrated portrait preview.
- **Phase 7 demo-readiness slice** implemented: the workshop highlights "新建角色" / "导入角色包" when there are no custom packs yet, surfaces missing default outfit/emotion/portrait readiness in the list and portrait tabs, shows no-outfit/no-expression upload states, and adds small state feedback for save success, character switching, expression preview, and create-form validation.
- **Bubble calibration slice** implemented: saved per-outfit bubble anchors now affect the desktop-pet runtime bubble position, and the workshop calibration preview uses a draggable sample bubble plus lightweight style presets (`soft`, `paper`, `clear`, `dark`) instead of a decorative marker.
- **Context-library authoring slice** implemented: the workshop `角色资料` tab creates arbitrary character knowledge folders and writes their name, purpose, and loading guidance into `character.json` automatically. Creators no longer need to hand-edit `context_libraries`; a library enters the runtime prompt after it contains at least one Markdown file.
- **Post-V1 UI redesign baseline** drafted in `workshop-ui-design.md`: the workshop should become a quiet, efficient character editor with a compact character list, visible hierarchy, fewer card blocks, calmer typography/colors, and only light anime-flavored details.

## Workshop User Flow

- Create from UI: open `角色工坊`, choose `新建角色`, fill pack id/name, then edit identity/persona fields in `角色设定`.
- Import/export: use the header `导入` and `导出` controls. Imports validate the zip before installing; exports include `character.json`, persona data, layout, and portrait assets.
- Portrait readiness: the `立绘管理` tab warns when the default outfit, default emotion, or required expressions are missing. Upload at least one default outfit/emotion image before relying on desktop-pet switching.
- Memory isolation: desktop-pet chat memory is scoped by `character_pack_id`. Test chat uses a separate `workshop_test_*` session/profile and does not reuse the live desktop-pet conversation.

## Claude Code Quick Start

1. Run `git status --short` and avoid touching unrelated dirty files.
2. Read this README, then `design.md`, `execution_plan.md`, and `agent_prompts.md`.
3. Begin with the repository mapping phase from `execution_plan.md`; do not jump straight into UI edits.
4. Stage only files related to the current phase.
5. Preserve extensibility: prefer small service boundaries and data schemas over one-off hardcoded character logic.
