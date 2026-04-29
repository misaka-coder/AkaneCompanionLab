# Character Pack Guide V0.1

## Goal

A character pack should describe who the pet is without requiring code edits.
The first runtime contract is deliberately small so the desktop pet can become
configurable before the engine is fully split.

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

## Next Planned Slices

1. Add a pack selector in settings.
2. Move backend persona loading into the pack.
3. Build a zip/export workflow for paid Creator Kit delivery.
