# Third-Party Notices

AkaneCompanionLab depends on third-party software. Each dependency remains
under its own license; the repository's Apache-2.0 license does not replace
those terms.

## Package-Managed Dependencies

Python, npm, and Cargo dependencies are declared in:

- `requirements.txt`
- `requirements-ml.txt`
- `desktop_pet_next/package-lock.json`
- `desktop_pet_next/src-tauri/Cargo.lock`

Consult each package's metadata and upstream repository for its exact license.
Do not remove their copyright or attribution notices from redistributed
binaries.

## Live2D

The private development tree may contain Live2D Cubism runtime files and sample
model data. They are governed by Live2D's own agreements and are not licensed
under Apache-2.0.

The supported public Alpha export excludes:

- `web/assets/live2d/`

The old Web Live2D preview and its bundled runtime have been removed. Any retained
local sample model data remains subject to the applicable Live2D terms.

Official terms:

- https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html
- https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html

## PixiJS

The current Tauri scene uses package-managed PixiJS under the MIT License.
The old Web preview's bundled vendor build has been removed.

## Media and Character IP

Third-party characters, fan works, music, illustrations, and imported
character packs are not part of the Apache-2.0 grant. See
`ASSETS_LICENSE.md`.

This notice is a release boundary, not a claim that every optional dependency
or local-only file has been redistributed.
