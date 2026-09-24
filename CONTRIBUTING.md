# Contributing

AkaneCompanionLab is an experimental learning and research project. Small,
well-tested changes are easier to review than broad rewrites.

## Before You Start

1. Read `AGENTS.md` and the relevant design document.
2. Search the existing code, tests, and call chain before editing.
3. Open an issue for changes that alter storage formats, public APIs, memory
   isolation, desktop permissions, or character-pack schemas.
4. Keep QQ, vision, TTS, local tools, and external providers optional.

## Local Setup

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python -m unittest discover tests
```

Desktop pet changes also require:

```bash
npm --prefix desktop_pet_next ci
npm --prefix desktop_pet_next run verify:control-center
cargo check --manifest-path desktop_pet_next/src-tauri/Cargo.toml
```

## Pull Requests

- Keep the change within one clear behavioral slice.
- Add or update tests for real call paths.
- Preserve structured failure states; do not turn unsupported behavior into
  fake success.
- Do not expose absolute paths, API keys, local storage paths, prompt contents,
  or user memory in logs and snapshots.
- Run `git diff --check`.
- Describe what changed, what was verified, and remaining risks.

## Prohibited Content

Do not submit:

- `.env` files, credentials, cookies, tokens, or private endpoints
- runtime logs, databases, user memories, local research corpora, or caches
- copyrighted music or media without redistribution permission
- third-party character artwork without documented authorization
- generated build output or dependency directories

By intentionally submitting a contribution, you agree that it may be licensed
under Apache License 2.0, unless the submission is clearly marked otherwise
and accepted by the maintainers.
