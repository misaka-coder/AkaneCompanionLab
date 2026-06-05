# Desktop Pet Character Workshop V1

This directory is the handoff entry for the next desktop pet mainline work.

## Read First

1. `design.md` - product direction, data model, memory rules, renderer boundaries, and acceptance criteria.
2. `execution_plan.md` - implementation phases and verification checklist.
3. `agent_prompts.md` - prompts for Claude Code or other coding agents, including optional subagent splits.

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
- Repair pass: fixed desktop runtime layout lookup to use the active character profile instead of an undefined registry, kept all workshop tab panels inside the main shell, made v0.1 packs gain `persona_form` on save, constrained portrait file writes to safe character-pack paths, and made `control-center-lab.html` the default settings entry with `AKANE_LEGACY_SETTINGS=1` as rollback.
- Memory isolation pass: character switching keeps per-pack runtime state in `pet_state.json`; backend memory reads/writes now scope raw chat, episodic summaries, semantic summaries, sessions, eval turns, and vector search by `character_pack_id` when present. Music/gift libraries still use the shared user profile.

## Claude Code Quick Start

1. Run `git status --short` and avoid touching unrelated dirty files.
2. Read this README, then `design.md`, `execution_plan.md`, and `agent_prompts.md`.
3. Begin with the repository mapping phase from `execution_plan.md`; do not jump straight into UI edits.
4. Stage only files related to the current phase.
5. Preserve extensibility: prefer small service boundaries and data schemas over one-off hardcoded character logic.
