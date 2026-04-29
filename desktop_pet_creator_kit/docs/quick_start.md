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

## What You Can Change First

For the first MVP, the safest fields to edit are:

- `identity.name`
- `identity.app_name`
- `identity.user_title`
- `dialogue.input_placeholder`
- `dialogue.local_click_lines`
- `appearance.default_outfit`
- `appearance.default_emotion`

After editing, restart the Tauri dev app.

## Current Limit

The sample metadata is wired into the client, but the portrait images still come
from the bundled `desktop_pet_next/src/assets/characters/猫娘/` folder. Full
external asset import is planned for the next slice.
