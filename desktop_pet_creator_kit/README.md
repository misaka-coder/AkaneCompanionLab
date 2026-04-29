# Akane Desktop Pet Creator Kit

This folder is the productization layer for the desktop pet line. It keeps
character-pack examples, templates, and creator-facing docs close to the
current prototype without forking the whole Tauri app.

## Current Shape

- `characters/akane_sample/`
  - The first runtime character pack used by `desktop_pet_next`.
- `templates/character_pack/`
  - A copyable starter pack for a new character.
- `docs/`
  - Quick start and character-pack authoring notes.
- `scripts/`
  - Local validation tools for creator-facing character packs.

## Check A Pack

From this folder:

```powershell
npm run check
```

That checks `characters/akane_sample/` by default. To check another copied pack:

```powershell
npm run check -- ./templates/character_pack
```

## Try Another Pack In The App

Copy `templates/character_pack/` into `characters/<your_pack_id>/`, edit
`character.json`, then run the desktop pet. The settings window lists every
folder under `characters/` as a selectable character pack.

## First MVP Boundary

The first Creator Kit slice is intentionally small:

- character identity
- user title
- default outfit and emotion
- local click lines
- basic asset contract notes
- external character images under `assets/characters/<outfit>/<emotion>.png`
- a local pack validator
- a settings-window character pack selector
- setup and replacement docs

The desktop client still reuses the existing `desktop_pet_next` code. If a
character pack has no external images yet, the app falls back to the bundled
catgirl assets. Pack selection, backend persona loading, and installer polish
come later.

## Product Hypothesis

The first paid offer should be:

> Turn your own character into a Windows AI desktop pet.

Recommended validation path:

1. Ship a working Akane sample.
2. Let 3-5 creators try replacing the character pack.
3. Offer paid setup/customization service before building a full marketplace.
4. Promote the Creator Kit only after the replacement workflow is repeatable.
