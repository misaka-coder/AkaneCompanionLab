# Asset Licensing

The Apache License 2.0 in `LICENSE` primarily covers the project's source code,
scripts, tests, and original technical documentation. It does **not**
automatically grant rights to character artwork, portraits, scene images,
stickers, audio, fonts, Live2D models, trademarks, or user-provided character
packs.

## Public Alpha Export

The supported public release is produced with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\export_public_alpha.ps1
```

That export excludes media whose redistribution rights have not been
documented. It generates neutral placeholder PNG files where the source tree
requires an image in order to build or demonstrate the resource protocol.
Those generated placeholder files are licensed under Apache-2.0 as part of the
export tooling.

The one documented exception is the bundled `akane_v1` demo character pack: its
portrait images ship as real artwork (not placeholders) so the desktop pet has
a working default character out of the box. Their terms are recorded in
"Bundled Demo Character: akane_v1" below. Every other image in the public
release remains a neutral placeholder.

## Excluded From the Public Alpha

Unless a later revision of this file explicitly says otherwise, the public
Alpha export excludes:

- character portraits other than the bundled `akane_v1` demo pack documented below
- scene and background illustrations
- stickers and decorative control-center illustrations
- music and other audio
- Live2D sample model data
- imported or user-created character packs, including local `reimu` content
- presentation and research media with unverified redistribution terms

Possessing a local copy of these files does not imply permission to publish,
redistribute, sublicense, or sell them.

## Fan, AI-Generated, and Character Assets

AI-generated images are still subject to the character, franchise, trademark,
and source-material boundaries they depict. A locally generated Touhou-inspired
portrait, for example, may be usable as private fan content, but it is not
automatically Apache-2.0 content and is not automatically safe for third-party
commercial reuse.

Fan-content packs may be distributed only when their own terms are clear and
compatible with the intended release. When such a pack is included, document it
as a separate asset collection instead of mixing it into the source-code
license.

## User-Provided Packs

Character packs created or imported by users remain the responsibility of
their authors. Contributors must only submit packs and assets they created or
have permission to redistribute. A pack is not relicensed merely because it is
loaded by AkaneCompanionLab.

## Bundled Demo Character: akane_v1

The desktop pet ships one default character pack, `akane_v1`, so a fresh install
has a working face and persona without any extra download. Its portrait images
are original, AI-generated artwork created for this project; they do not depict
any pre-existing franchise character.

| Field | Value |
| --- | --- |
| Path | `desktop_pet_creator_kit/characters/akane_v1/assets/characters/default/*.png` |
| Title | Akane (akane_v1 demo character) |
| Author | Akane Companion Lab contributors |
| Source | Original AI-generated artwork created for AkaneCompanionLab |
| License | Bundled-use only — see grant below |
| Changes | Generated and curated for this project |
| Redistribution | Allowed only as part of AkaneCompanionLab |

**Bundled-use grant.** These portrait images are made available **only** for use
together with AkaneCompanionLab: you may run, fork, and redistribute the
application with these images included. You may **not** extract them for use in
other projects, sublicense them, sell them, or redistribute them separately from
this application. All other rights are reserved by the authors. This grant is
independent of the Apache-2.0 license that covers the source code.

## Adding Assets

A distributable asset must have an entry containing:

| Field | Required information |
| --- | --- |
| Path | Repository-relative path |
| Title | Asset or collection name |
| Author | Copyright holder or creator |
| Source | Original source URL or creation record |
| License | Exact license or written permission |
| Changes | Cropping, editing, conversion, or generation details |
| Redistribution | Whether source and packaged forms may be redistributed |

Assets without this information stay outside the public release.
