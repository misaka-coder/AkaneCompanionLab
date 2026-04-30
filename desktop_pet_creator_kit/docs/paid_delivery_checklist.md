# Paid Delivery Checklist V0.1

Use this checklist when turning a creator's character into a paid Akane desktop
pet delivery.

## Before Taking The Order

- Confirm the buyer owns or has permission to use the character art.
- Confirm the target platform is Windows desktop.
- Confirm whether the order includes only a character pack or a bundled app
  handoff.
- Ask for the character name, user title, tone, forbidden topics, and sample
  lines.
- Ask for the minimum portrait set: default outfit plus at least one neutral
  emotion.

## Production

- Create a new folder under `characters/<pack_id>/`.
- Fill `character.json`, `character.toml`, and `persona.md`.
- Put portraits under `assets/characters/<outfit>/<emotion>.<png|jpg|jpeg|webp>`.
- Run:

```powershell
npm run check -- ./characters/<pack_id>
```

- Run the desktop app and select the pack in settings.
- Test local click lines, default outfit, default emotion, and music emotion.

## Export

- Export the pack:

```powershell
npm run export -- ./characters/<pack_id>
```

- Check the zip contents:

```powershell
tar -tf dist\<pack_id>.zip
```

- Keep the exported zip, source character folder, and agreed delivery notes
  together for handoff.

## Handoff

- Send the zip file.
- Send installation notes or provide a remote setup session.
- Tell the buyer that packs imported from the settings window are refreshed and
  applied at runtime.
- Tell the buyer they can also import with `npm run import`, then use the
  settings window to refresh/apply the installed pack.

## Acceptance Test

- The buyer can select the character pack in settings.
- The pet shows a valid default portrait or bundled fallback portrait.
- Click lines use the buyer's character voice.
- Music controls still work.
- The app can restart without losing the selected character pack.

## Follow-Up Offer

- Extra outfits.
- Extra emotion portraits.
- Persona tuning.
- Custom local lines.
- Bundled installer setup.
- Ongoing update support.
