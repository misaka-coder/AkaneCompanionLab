# Character Pack Install And Test

Use this page when installing a Creator Kit character pack for local testing or
early paid handoff.

## Option 1: Create A Folder Directly

Copy the template:

```powershell
cd desktop_pet_creator_kit
Copy-Item -Recurse templates/character_pack characters/my_character
```

Then edit:

```text
characters/my_character/character.json
characters/my_character/character.toml
characters/my_character/persona.md
```

Put portraits under:

```text
characters/my_character/assets/characters/default/normal.png
characters/my_character/assets/characters/default/happy.png
```

The folder name, `appearance.default_outfit`, and image filenames must match.
For example, `default_emotion = "normal"` needs:

```text
assets/characters/default/normal.png
```

## Option 2: Import From Command Line

```powershell
cd desktop_pet_creator_kit
npm run import -- ./dist/my_character.zip
```

Use `--force` to overwrite an existing folder:

```powershell
npm run import -- ./dist/my_character.zip --force
```

## Option 3: Import From Settings

Open the Tauri settings window, then use the character-pack area:

- `导入 zip` selects an exported Creator Kit zip.
- `覆盖同名` allows replacing an existing pack folder.
- `打开目录` opens `desktop_pet_creator_kit/characters/`.
- `复制路径` copies the last installed pack path.

After installing a new zip from the settings window, the desktop pet refreshes
the runtime character-pack list and applies the imported pack without restarting.

## Validation

Run:

```powershell
npm run check -- ./characters/my_character
```

Expected successful output ends with:

```text
Result: OK
```

Warnings about missing images are acceptable only when you intentionally want
the desktop pet to use bundled fallback art.

## Manual Smoke Test

- Start `desktop_pet_next`.
- Open settings.
- Select the character pack.
- Confirm the title, user title, default outfit, and default emotion.
- Click the pet and confirm local lines use the new character voice.
- Switch outfits or preview emotions if images are present.
- Drag music onto the pet and confirm music controls still work.
- Restart the app and confirm the selected pack persists.
