# Creator Kit Quick Start

## Run The Current Sample

From the repo root:

```powershell
python launch_akane_memory_v01.py
```

Then in another terminal:

```powershell
cd desktop_pet_next
npm install
npm run tauri -- dev
```

The desktop pet reads the first sample character metadata from:

`desktop_pet_creator_kit/characters/akane_sample/character.json`

Every folder under `desktop_pet_creator_kit/characters/` appears in the
settings window's character-pack selector.

The kit currently includes two samples:

- `akane_sample`
- `mika_sample`

## Create A New Pack

From the Creator Kit folder:

```powershell
cd desktop_pet_creator_kit
npm run create
```

For a scripted setup, pass the core fields directly:

```powershell
npm run create -- --id my_character --name Mika --user-title 主人
```

Chinese outfit and emotion names are fine:

```powershell
npm run create -- --id my_character --name Mika --outfit 猫娘 --emotion 正常
```

This creates:

```text
characters/my_character/
  character.json
  character.toml
  persona.md
  assets/
    characters/
      default/
        README.md
```

Then put the default portrait image here:

```text
characters/my_character/assets/characters/default/normal.png
```

If you already have a folder of expression images, you can draft the pack in one
step:

```powershell
npm run create -- --from-images ./raw_images --id my_character --name Mika --export
```

The command copies supported images (`png`, `jpg`, `jpeg`, `webp`) into the
pack, infers emotion ids from file names, writes `character.json`,
`character.toml`, `persona.md`, validates the result, and exports a zip when
`--export` is present. Direct child folders under `raw_images` become outfit
folders. You can also use the shorthand:

```powershell
npm run draft -- ./raw_images --id my_character --name Mika --export
```

## Check The Character Pack

From the Creator Kit folder:

```powershell
cd desktop_pet_creator_kit
npm run check
```

To check a copied template pack:

```powershell
npm run check -- ./templates/character_pack
```

## Export A Character Pack

From the Creator Kit folder:

```powershell
npm run export -- ./characters/my_character
```

The export command runs the validator first, then writes a zip file into
`desktop_pet_creator_kit/dist/`.

## Import A Character Pack

From the Creator Kit folder:

```powershell
npm run import -- ./dist/my_character.zip
```

Use `--as new_pack_id` to install under a different folder name, and `--force`
to overwrite an existing pack after validation passes.

In the Tauri settings window, you can also use `导入 zip` or drag an exported
Creator Kit zip onto the character-pack area. The desktop pet refreshes the
runtime pack list after import and can apply the new pack immediately.

For paid handoff work, see `docs/paid_delivery_checklist.md`.
For install testing, see `docs/install_and_test.md`.

## Edit A Pack

After creation, edit:

```text
characters/my_character/character.json
characters/my_character/character.toml
characters/my_character/persona.md
```

Reload resources or reopen settings, then choose the new pack from the
character-pack selector.

## What You Can Change First

For the first MVP, the safest fields to edit are:

- `identity.name`
- `identity.app_name`
- `identity.user_title`
- `dialogue.input_placeholder`
- `dialogue.local_click_lines`
- `persona.md`
- `emotion_aliases`
- `appearance.music_emotion`
- `play_feedback`
- `care.shop_items`
- `care.work`
- `appearance.default_outfit`
- `appearance.default_emotion`

After editing metadata or replacing images, use `重载资源` or reopen the app if
the current WebView has cached an older asset.

## Current Limit

The desktop pet refreshes character folders at runtime, and scans pack assets
from:

`desktop_pet_creator_kit/characters/akane_sample/assets/characters/<outfit>/<emotion>.<png|jpg|jpeg|webp>`

Replace `akane_sample` with the active pack folder. If that folder has no
images, the app falls back to the bundled `desktop_pet_next/src/assets/characters/猫娘/`
folder.
