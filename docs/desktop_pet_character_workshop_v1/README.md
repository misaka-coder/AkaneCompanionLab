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

## Claude Code Quick Start

1. Run `git status --short` and avoid touching unrelated dirty files.
2. Read this README, then `design.md`, `execution_plan.md`, and `agent_prompts.md`.
3. Begin with the repository mapping phase from `execution_plan.md`; do not jump straight into UI edits.
4. Stage only files related to the current phase.
5. Preserve extensibility: prefer small service boundaries and data schemas over one-off hardcoded character logic.
