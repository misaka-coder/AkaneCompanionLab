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

## Next Planned Slices

1. Load external portrait assets from the character pack.
2. Add a pack validator.
3. Add a pack selector in settings.
4. Move backend persona loading into the pack.
5. Build a zip/export workflow for paid Creator Kit delivery.
