# Akane Lean-Down LD-009 Charpack Private Imports

Status: implemented
Date: 2026-07-09

## Decision

`companion_v01.desktop_pet_character_resources` is now only a compatibility
re-export layer over public `charpack-core` names.

It no longer imports underscored helpers from
`charpack_core.character_resources`.

## Why

The package reintegration rule is:

```text
Package reintegration must reduce owning implementations.
```

Depending on package-private helpers makes Akane look thin while still coupling
it to implementation details inside the package. That makes future package
cleanup risky and leaves maintainers unsure which code owns character resource
behavior.

## Runtime Shape

Akane keeps these compatibility imports:

- `CharacterPackResourceService`
- `DesktopPetCharacterResourceService`
- public prompt/resource constants
- `sanitize_character_pack_id`

Reusable character-pack behavior remains owned by `charpack-core`.

## Validation

Targeted validation:

```powershell
python -m unittest tests.test_package_reintegration_policy tests.test_desktop_pet_character_resources -v
python -m py_compile companion_v01/desktop_pet_character_resources.py tests/test_package_reintegration_policy.py
ruff check companion_v01/desktop_pet_character_resources.py tests/test_package_reintegration_policy.py
ruff format --check companion_v01/desktop_pet_character_resources.py tests/test_package_reintegration_policy.py
git diff --check -- companion_v01/desktop_pet_character_resources.py tests/test_package_reintegration_policy.py docs/package_reintegration_policy_m63.md docs/akane_lean_down_ld009_charpack_private_imports.md
```
