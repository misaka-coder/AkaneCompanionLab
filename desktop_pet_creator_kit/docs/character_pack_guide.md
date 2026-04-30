# Character Pack Guide V0.1

## Goal

A character pack should describe who the pet is without requiring code edits.
The first runtime contract is deliberately small so the desktop pet can become
configurable before the engine is fully split.

Runtime packs live under:

```text
desktop_pet_creator_kit/characters/<pack_id>/
```

The settings window discovers those folders at build/dev time and lets you
select the active pack.

## Required Files

```text
my_character/
  character.json
  character.toml
  persona.md
  assets/
    characters/
      default/
        normal.png
        happy.png
```

`character.json` is the current runtime file.

`character.toml` is the future authoring shape and mirrors the same fields in a
more creator-friendly format.

`persona.md` is reference text for later backend persona extraction.

## Minimal Runtime Fields

```json
{
  "schema_version": "akane.character.v0.1",
  "identity": {
    "id": "my_character",
    "name": "Mika",
    "app_name": "Mika Pet",
    "user_title": "主人"
  },
  "appearance": {
    "default_outfit": "default",
    "default_emotion": "normal",
    "music_emotion": "listening"
  },
  "dialogue": {
    "input_placeholder": "和 Mika 说点什么……",
    "local_click_lines": [
      { "text": "我在哦。", "emotion": "normal" }
    ]
  }
}
```

## Emotion Rules

At least one neutral/default emotion is required. For the current Akane sample,
that emotion is `正常`.

Recommended emotion aliases let the backend use English intent labels like
`thinking`, `happy`, or `music` while the asset names stay creator-friendly.

## Asset Layout

The current desktop runtime scans:

```text
assets/characters/<outfit>/<emotion>.png
```

Each folder under `assets/characters/` becomes a selectable outfit. Each image
inside that folder becomes an emotion id. For example:

```text
assets/characters/default/normal.png
assets/characters/default/happy.png
assets/characters/winter/normal.png
```

This creates two outfits: `default` and `winter`.

## Validation

From `desktop_pet_creator_kit/`:

```powershell
npm run check
```

The validator checks `characters/akane_sample/` by default. To check a copied
pack:

```powershell
npm run check -- ./characters/my_character
```

The current checks cover required metadata, click-line shape, asset directory
layout, and default outfit/default emotion consistency.

## Export

From `desktop_pet_creator_kit/`:

```powershell
npm run export -- ./characters/my_character
```

The exporter validates the pack first, then writes:

```text
dist/my_character.zip
```

The zip contains the pack folder, an `akane-export.json` manifest, and a small
`INSTALL.md` handoff note. This is the first delivery shape for paid setup or
creator handoff.

## Samples

- `characters/akane_sample/` is the main Akane runtime sample.
- `characters/mika_sample/` is a second no-art sample for checking multi-pack
  selection, export, import, and paid-delivery flow without requiring a new art
  set.

## Import

From `desktop_pet_creator_kit/`:

```powershell
npm run import -- ./dist/my_character.zip
```

The importer extracts to a temporary folder, validates the pack, then installs
it into `characters/<pack_id>/`. Existing packs are not overwritten unless
`--force` is provided:

```powershell
npm run import -- ./dist/my_character.zip --as my_character_v2 --force
```

The Tauri settings window has the same first install path for Creator Kit
exported zips. Use `导入 zip` or drag a zip onto the character-pack area. New
packs are available after the app restarts or the dev/build process refreshes
the `characters/` folder.

## Next Planned Slices

1. Move backend persona loading into the pack.
2. Add runtime zip install flow from the settings window.
3. Add buyer-facing setup notes for non-developer delivery.
