# Akane Desktop Pet Creator Kit

This folder is the productization layer for the desktop pet line. It keeps
character-pack examples, templates, and creator-facing docs close to the
current prototype without forking the whole Tauri app.

## Current Shape

- `characters/akane_sample/`
  - The first runtime character pack used by `desktop_pet_next`, including the Akane catgirl portrait set.
- `characters/mika_sample/`
  - A second no-art sample used to verify multi-pack selection and delivery.
- `templates/character_pack/`
  - A copyable starter pack for a new character.
- `docs/`
  - Quick start and character-pack authoring notes.
- `scripts/`
  - Local validation tools for creator-facing character packs.

## Create A Pack

From this folder:

```powershell
npm run create
```

That walks through a few prompts and writes a new folder under `characters/`.
For repeatable setup work, pass the key fields directly:

```powershell
npm run create -- --id my_character --name Mika --user-title 主人
```

The command generates `character.json`, `character.toml`, `persona.md`, and the
standard image folders. After that, put the default portrait at:

```text
characters/my_character/assets/characters/default/normal.png
```

If you already have expression images, create a draft pack directly from the
image folder:

```powershell
npm run create -- --from-images ./raw_images --id my_character --name Mika --export
```

Image file names become emotion ids, so `正常.png`, `开心.webp`, and
`思考中.jpg` become selectable emotions. Direct child folders become outfit
folders. `--export` writes the installable zip after validation. The shorthand
form is:

```powershell
npm run draft -- ./raw_images --id my_character --name Mika --export
```

## Check A Pack

From this folder:

```powershell
npm run check
```

That checks `characters/akane_sample/` by default. To check another copied pack:

```powershell
npm run check -- ./templates/character_pack
```

## Export A Pack

From this folder:

```powershell
npm run export
```

That validates and exports `characters/akane_sample/` into `dist/akane_sample.zip`.
To export another pack:

```powershell
npm run export -- ./characters/my_character
```

## Import A Pack

To install an exported zip into `characters/`:

```powershell
npm run import -- ./dist/akane_sample.zip
```

To install it under a new folder name or overwrite an existing pack:

```powershell
npm run import -- ./dist/akane_sample.zip --as my_character --force
```

The Tauri settings window also supports `导入 zip`, `打开目录`, and copying the
last installed pack path. See `docs/install_and_test.md` for the full testing
flow.

## Try Another Pack In The App

Copy `templates/character_pack/` into `characters/<your_pack_id>/`, edit
`character.json`, then run the desktop pet. The settings window lists every
folder under `characters/` as a selectable character pack.

## First MVP Boundary

The first Creator Kit slice is intentionally small:

- character identity
- user title
- default outfit and emotion
- a character-pack creator command
- local click lines
- basic asset contract notes
- external character images under `assets/characters/<outfit>/<emotion>.<png|jpg|jpeg|webp>`
- a local pack validator
- a settings-window character pack selector
- a second sample character pack
- a paid delivery checklist
- setup and replacement docs

The desktop client still reuses the existing `desktop_pet_next` code. Character
packs with images are now the desktop pet's primary resource source. The backend
also reads the selected pack's `character.json` and `persona.md` for
`desktop_pet` prompts, while QQ and Web keep their own prompt profiles. If a pack
has no images yet, the app falls back to local visible art so creators can still
test metadata while preparing assets. Installer polish comes later.

## Product Hypothesis

The first paid offer should be:

> Turn your own character into a Windows AI desktop pet.

Recommended validation path:

1. Ship a working Akane sample.
2. Let 3-5 creators try replacing the character pack.
3. Offer paid setup/customization service before building a full marketplace.
4. Promote the Creator Kit only after the replacement workflow is repeatable.
