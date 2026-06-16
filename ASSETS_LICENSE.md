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

## Excluded From the Public Alpha

Unless a later revision of this file explicitly says otherwise, the public
Alpha export excludes:

- Akane and other character portraits
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
