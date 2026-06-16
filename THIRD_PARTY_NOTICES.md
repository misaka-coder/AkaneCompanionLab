# Third-Party Notices

AkaneCompanionLab depends on third-party software. Each dependency remains
under its own license; the repository's Apache-2.0 license does not replace
those terms.

## Package-Managed Dependencies

Python, npm, and Cargo dependencies are declared in:

- `requirements.txt`
- `requirements-ml.txt`
- `desktop_pet/package-lock.json`
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

- `web/vendor/live2d/`
- `web/assets/live2d/`

Users who want Live2D support must obtain the required runtime and model files
from an authorized source and comply with the applicable Live2D terms.

Official terms:

- https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html
- https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html

## PixiJS

The private development tree may contain a bundled PixiJS build licensed under
the MIT License. The public Alpha export excludes the bundled Live2D vendor
directory; users should prefer package-managed or authorized upstream copies.

## Media and Character IP

Third-party characters, fan works, music, illustrations, and imported
character packs are not part of the Apache-2.0 grant. See
`ASSETS_LICENSE.md`.

This notice is a release boundary, not a claim that every optional dependency
or local-only file has been redistributed.
