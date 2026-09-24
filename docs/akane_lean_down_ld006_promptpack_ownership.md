# Akane Lean-Down LD-006 Promptpack Ownership

Status: implemented
Date: 2026-07-09

## Decision

Akane keeps final chat prompt assembly owned by
`companion_v01.prompt_builder.PromptBuilder` for now.

`promptpack-core` remains the reusable primitive layer for:

- prompt blocks;
- prompt block registries;
- prompt profiles and section primitives;
- cache/audit helpers for future deliberate migrations.

Akane must not add a second final chat prompt assembler beside
`PromptBuilder.build_final_generation_context()`.

## Why

`PromptBuilder.build_final_generation_context()` is not just generic prompt
string joining. It currently owns Akane product composition:

- mode-specific JSON output contracts;
- persona state insertion around `[CURRENT ASSISTANT STATE - EMBODY THIS]`;
- desktop-pet / QQ / scene prompt differences;
- visible resource context placement;
- memcore and legacy memory layer placement;
- tool prompt context;
- prompt audit section names used by diagnostics.

Moving this whole path into `promptpack-core` now would be a risky rewrite, not
a lean-down cut.

## Boundary

Allowed:

- `companion_v01.prompt_blocks` can use `PromptBlock` and
  `PromptBlockRegistry` from `promptpack-core`.
- Akane product content, mode contracts, and final prompt ordering stay in
  `PromptBuilder`.

Not allowed:

- adding `PromptAssembler` into the final chat path while
  `PromptBuilder.build_final_generation_context()` remains authoritative;
- maintaining two prompt assembly paths with different ordering, audit names,
  or cache assumptions.

If Akane later adopts `PromptAssembler`, the migration must replace one
specific `PromptBuilder` assembly path in the same change window and update the
tests that guard this decision.

## Validation

Targeted validation:

```powershell
python -m unittest tests.test_package_reintegration_policy -v
python -m py_compile tests/test_package_reintegration_policy.py
ruff check tests/test_package_reintegration_policy.py
ruff format --check tests/test_package_reintegration_policy.py
git diff --check -- docs/package_reintegration_policy_m63.md docs/akane_lean_down_ld006_promptpack_ownership.md tests/test_package_reintegration_policy.py
```
