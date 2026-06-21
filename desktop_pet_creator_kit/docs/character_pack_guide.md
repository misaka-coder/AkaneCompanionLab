# Character Pack Guide V0.1

## Goal

A character pack should describe who the pet is without requiring code edits.
The first runtime contract is deliberately small so the desktop pet can become
configurable before the engine is fully split.

Runtime packs live under:

```text
desktop_pet_creator_kit/characters/<pack_id>/
```

The settings window refreshes those folders at runtime and lets you select the
active pack.

## Create Command

The recommended starting point is the creator command:

```powershell
cd desktop_pet_creator_kit
npm run create -- --id my_character --name Mika --user-title 主人
```

It writes the required metadata files, creates the standard asset directories,
and runs the validator once. If no images have been added yet, validation will
finish with a warning that the app will use bundled fallback art.

The pack id should be a stable folder id such as `my_character`. Outfit and
emotion names can be creator-facing names such as `猫娘` or `正常`, because those
map directly to image folders and file names.

If the creator already has expression images, draft the pack from that folder:

```powershell
npm run create -- --from-images ./raw_images --id my_character --name Mika --export
```

The source folder can contain image files directly for the default outfit, and
direct child folders for additional outfits:

```text
raw_images/
  正常.png
  开心.webp
  思考中.jpg
  睡衣/
    normal.png
    sleepy.png
```

File names become emotion ids. The command copies the images, infers default
and music emotions when possible, writes metadata/persona drafts, validates the
pack, and exports a zip when `--export` is present.

## Required Files

```text
my_character/
  character.json
  character.toml
  persona.md
  relationships/        # Optional shareable character knowledge
  events/               # Optional shareable character knowledge
  assets/
    characters/
      default/
        normal.png
        happy.png
  _local/               # Private runtime data; never exported
    memory/
```

`character.json` is the current runtime file.

`character.toml` is the future authoring shape and mirrors the same fields in a
more creator-friendly format.

`persona.md` is desktop-pet persona reference text. In `desktop_pet` mode the
backend reads it, together with `character.json`, and adds it to the final prompt
for the selected pack only. `qq_text` also uses the selected character pack,
while Web scene modes keep their separate prompt profiles.

## Optional Context Libraries

Character packs may declare zero or more creator-defined context libraries.
Folder types are not hardcoded: each library explains what it contains and when
the character should read it.

In the Tauri character workshop, use the `角色资料` tab and choose
`新建资料库`. Fill in the library name, what it stores, and when the character
should read it. The workshop creates the folder and updates
`context_libraries` automatically, so ordinary creators do not need to edit
JSON. The JSON below remains the portable file format and advanced editing
surface.

```json
{
  "context_libraries": [
    {
      "folder": "relationships",
      "name": "人物关系",
      "description": "记录我与重要人物的关系、态度和共同经历。",
      "load_when": "谈到具体人物或共同经历时读取。",
      "aliases": {
        "魔理沙": ["雾雨魔理沙", "黑白"]
      }
    },
    {
      "folder": "spell_cards",
      "name": "符卡资料",
      "description": "记录符卡、招式和使用背景。",
      "load_when": "讨论战斗或具体招式时读取。"
    }
  ]
}
```

Each declared folder is a direct child of the character pack. Markdown files
inside these declared folders are shareable character knowledge. `_local` is a
reserved folder name and cannot be declared as a context library.
inside it stay flat:

```text
my_character/
  character.json
  relationships/
    魔理沙.md
    八云紫.md
  spell_cards/
    梦想封印.md
```

`aliases` is optional. Its key is a Markdown file name without `.md`, and its
value is a list of precise alternative names. Use it for stable names such as
`黑白 -> 魔理沙`; avoid broad words such as `书`, `麻烦`, or `日常`, which would
cause unrelated conversations to load the file.

At runtime the backend injects the declared purpose, loading guidance, and exact
file targets into the selected character's prompt. A direct file-name mention
or configured alias in the current user message is loaded automatically. The
model can batch-read
additional targets such as `relationships/魔理沙` with the internal
`load_character_context` tool. It cannot list arbitrary directories or read
files outside the active pack. Empty or missing libraries inject no prompt.

The final response debug payload records only matched terms, targets, statuses,
and tool rounds under `_debug.character_context`. It does not include file
contents or local absolute paths.

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
  },
  "play_feedback": {
    "throw_fast": {
      "emotion": "shock",
      "bubble": { "text": "啊啊啊飞起来啦！", "duration_ms": 1500 }
    },
    "throw_light": {
      "emotion": "confused",
      "bubble": { "text": "", "duration_ms": 0 }
    },
    "wall_hit": {
      "emotion": "confused",
      "bubble": { "text": "撞到了。", "duration_ms": 1200 }
    },
    "land": {
      "emotion": "",
      "bubble": { "text": "", "duration_ms": 0 }
    }
  },
  "care": {
    "enabled": true,
    "initial_coins": 20,
    "initial_hunger": 55,
    "initial_energy": 70,
    "initial_affection": 10,
    "work": {
      "enabled": true,
      "duration_seconds": 20,
      "reward_coins_min": 6,
      "reward_coins_max": 12,
      "min_hunger": 20,
      "min_energy": 25,
      "hunger_cost": 12,
      "energy_cost": 25,
      "start_feedback": {
        "emotion": "normal",
        "bubble": { "text": "我出去转一圈，很快回来。", "duration_ms": 1800 }
      },
      "complete_feedback": {
        "emotion": "happy",
        "bubble": { "text": "我回来啦，带回 {reward} 枚金币。", "duration_ms": 2200 }
      }
    },
    "shop_items": [
      {
        "id": "strawberry_cake",
        "name": "草莓蛋糕",
        "description": "小小一块，适合当作投喂测试。",
        "price": 8,
        "effects": { "hunger": 18, "energy": 8, "affection": 4 },
        "feedback": {
          "emotion": "happy",
          "bubble": { "text": "甜的！", "duration_ms": 1800 }
        }
      }
    ]
  }
}
```

## Emotion Rules

At least one neutral/default emotion is required. For the current Akane sample,
that emotion is `正常`.

Recommended emotion aliases let the backend use English intent labels like
`thinking`, `happy`, or `music` while the asset names stay creator-friendly.

`appearance.music_emotion` is used by the desktop pet when local music or system
media, such as QQ Music, is actively playing. Temporary reply, TTS, drag, and
physics expressions still take priority; when they end, the pet returns to this
music expression if playback is still active.

`play_feedback` controls lightweight desktop-pet play reactions:

- `throw_fast`: released with enough speed to fly under physics.
- `throw_light`: dragged and released gently.
- `wall_hit`: bounces into the side of the work area.
- `land`: bounces on the floor.

Each entry can set an `emotion` and an optional local `bubble`. The emotion may
be a real image id or an abstract alias such as `shock`; add that alias under
`emotion_aliases` so every character pack can map it to its own art. Empty
emotion or empty bubble text means "do not override that part".

`care` controls the optional shop, feeding, and short work/outgoing loop in
`desktop_pet_next`.
Set `care.enabled` to `true` and add `shop_items` for characters that should
have a visible shop. Each item has an `id`, `name`, `price`, optional
`description`, numeric `effects.hunger` / `effects.energy` /
`effects.affection`, and optional local `feedback` with an emotion plus bubble.
Recent feeding events are also sent as desktop-pet context on the next LLM turn,
so the character can naturally know what the user just fed her.

`care.work` is optional. When enabled, the shop UI shows an outgoing/work
button. `duration_seconds` controls how long the local task lasts, and
`reward_coins_min` / `reward_coins_max` define the random coin reward when the
character returns. `min_hunger` / `min_energy` gate whether she can go out, and
`hunger_cost` / `energy_cost` are consumed when the task starts. `start_feedback`
and `complete_feedback` use the same local emotion/bubble shape as shop items;
`{reward}` inside the complete bubble is replaced by the actual coin reward.
While she is out, the pet view hides and the Tauri window becomes click-through
so it does not block the desktop. Packs without `care` simply do not show a
playable shop, so minimal character packs still work.

## Asset Layout

The current desktop runtime scans:

```text
assets/characters/<outfit>/<emotion>.<png|jpg|jpeg|webp>
```

Each folder under `assets/characters/` becomes a selectable outfit. Each image
inside that folder becomes an emotion id. For example:

```text
assets/characters/default/normal.png
assets/characters/default/happy.png
assets/characters/winter/normal.png
```

This creates two outfits: `default` and `winter`.

For the desktop pet, these character-pack assets are the resource source for
both sides of the runtime loop: the frontend displays images from the selected
pack, and the backend uses the same pack manifest to tell the AI which emotions
are available. Web resources under `web/assets` remain separate and continue to
serve the Web client.

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
creator handoff. The private `_local/` directory is always excluded, including
its memory mirror and any other machine-local runtime data.

## Samples

- `characters/akane_sample/` is the main Akane runtime sample and includes the
  catgirl portrait set used by the desktop pet.
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

When an existing pack is overwritten, its `_local/` directory is preserved and
reattached to the newly installed pack. Imported zip entries under `_local/`
are ignored, so shared packs cannot inject or replace private local memory.

The Tauri settings window has the same first install path for Creator Kit
exported zips. Use `导入 zip` or drag a zip onto the character-pack area. New
packs are refreshed and applied at runtime after import. The settings window also
has an `打开目录` helper for opening the install folder after import.

## Next Planned Slices

1. Add buyer-facing setup notes for non-developer delivery.
2. Polish installer and import feedback for non-technical creators.
