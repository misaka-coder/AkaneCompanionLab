# Akane Workspace Layout M62

Status: phase-0 catalog implemented.
Date: 2026-07-09

## Goal

The `F:\Akane` workspace now contains product apps, reusable packages,
provider/adapter packages, petdesk runtime packages, archived releases, caches,
reference projects, and experiments in one flat directory. That made sense
while extracting packages quickly, but it is now hard to scan.

M62 records the current workspace shape and introduces a no-break catalog
layout before any physical migration.

## Current Scan

Top-level git repositories:

```text
AkaneCompanionLab
AkaneCompanionLab_public_alpha_audit
AkaneCompanionLab-public-alpha
AkaneCompanionLab-public-alpha-20260611-final
AkaneMemoryDesk
Artemis伙伴
capcore
capcore-adapter-comfyui
capcore-adapter-mcp
capcore-adapter-python
capcore-adapter-speech
capcore-host-utils
capcore-provider-anthropic
capcore-provider-native-tools
capcore-provider-openai
charpack-core
langchain
memcore
NapCatQQ
petcore-protocol
petdesk-character-host
petdesk-live2d-pixi-driver
petdesk-runtime
promptpack-core
QQbot
```

Top-level non-git directories:

```text
_refs
哀鸿同人
AI产品开发
AIgame制作
akane_whisper_cache
akane-input-companion
AkaneData
data
galgame
memcore-companion
OpenSourceStudy
package-smokes
ui-review
```

Top-level loose files seen in the scan:

```text
_tmp_yuque_dania.html
galgame.zip
```

## Existing Hard Dependencies

Do not physically move the extracted package repos yet. `AkaneCompanionLab`
currently expects sibling package paths in active scripts and requirements:

```text
requirements.txt:
  -e ../capcore
  -e ../capcore-adapter-mcp
  -e ../capcore-adapter-python
  -e ../capcore-adapter-speech
  -e ../capcore-adapter-comfyui
  -e ../charpack-core
  -e ../promptpack-core
  -e ../capcore-provider-native-tools
  -e ../capcore-provider-openai
  -e ../memcore

scripts/bootstrap_akane_windows.ps1:
  ..\capcore
  ..\capcore-adapter-mcp
  ..\capcore-adapter-python
  ..\capcore-adapter-speech
  ..\capcore-adapter-comfyui
  ..\capcore-provider-native-tools
  ..\capcore-provider-openai
  ..\charpack-core
  ..\promptpack-core
  ..\memcore

petdesk release scripts:
  ..\petdesk-runtime
```

Several docs also record these sibling paths. They should be updated only after
the scripts support the new layout.

## Classification

### Product Apps

These are runnable products or product experiments:

```text
AkaneCompanionLab
AkaneMemoryDesk
Artemis伙伴
akane-input-companion
QQbot
NapCatQQ
```

Recommended final home:

```text
apps/
```

Physical migration should wait until launch scripts, shortcuts, and local IDE
workspaces are reviewed.

### Core Reusable Packages

These are host-neutral or near host-neutral packages:

```text
memcore
promptpack-core
charpack-core
capcore
capcore-host-utils
```

Recommended final home:

```text
packages/core/
```

### Capcore Adapters And Providers

These packages extend the capability/tool ecosystem:

```text
capcore-adapter-comfyui
capcore-adapter-mcp
capcore-adapter-python
capcore-adapter-speech
capcore-provider-anthropic
capcore-provider-native-tools
capcore-provider-openai
```

Recommended final home:

```text
packages/capcore/
```

### Petdesk Packages

These are the reusable desktop pet/runtime packages:

```text
petcore-protocol
petdesk-character-host
petdesk-live2d-pixi-driver
petdesk-runtime
```

Recommended final home:

```text
packages/petdesk/
```

`petdesk-runtime` is still resolved as a sibling of `AkaneCompanionLab` by the
release scripts, so physical migration needs a compatibility pass first.

### Archives

These are release snapshots or audit copies:

```text
AkaneCompanionLab_public_alpha_audit
AkaneCompanionLab-public-alpha
AkaneCompanionLab-public-alpha-20260611-final
package-smokes
galgame.zip
_tmp_yuque_dania.html
```

Recommended final home:

```text
archives/
```

### Data And Caches

These may be referenced by running services or local tools:

```text
AkaneData
akane_whisper_cache
data
```

Recommended final home:

```text
data-cache/
```

Do not move these until config and runtime path ownership are audited.

### References And Experiments

These are learning/reference/experiment workspaces:

```text
_refs
OpenSourceStudy
langchain
ui-review
AI产品开发
AIgame制作
galgame
memcore-companion
哀鸿同人
```

Recommended final home:

```text
refs/
experiments/
```

Move personal/reference directories only after confirming there are no active
IDE workspaces or scripts depending on the old paths.

## Phase-0 Execution

M62 creates a no-break package catalog under:

```text
F:\Akane\packages\
```

The catalog uses Windows directory junctions to point to existing package
repositories. No original repository is moved in this phase, so the current
Akane sibling-path scripts keep working.

Catalog shape:

```text
packages/
  README.md
  core/
    capcore -> F:\Akane\capcore
    capcore-host-utils -> F:\Akane\capcore-host-utils
    charpack-core -> F:\Akane\charpack-core
    memcore -> F:\Akane\memcore
    promptpack-core -> F:\Akane\promptpack-core
  capcore/
    capcore-adapter-comfyui -> F:\Akane\capcore-adapter-comfyui
    capcore-adapter-mcp -> F:\Akane\capcore-adapter-mcp
    capcore-adapter-python -> F:\Akane\capcore-adapter-python
    capcore-adapter-speech -> F:\Akane\capcore-adapter-speech
    capcore-provider-anthropic -> F:\Akane\capcore-provider-anthropic
    capcore-provider-native-tools -> F:\Akane\capcore-provider-native-tools
    capcore-provider-openai -> F:\Akane\capcore-provider-openai
  petdesk/
    petcore-protocol -> F:\Akane\petcore-protocol
    petdesk-character-host -> F:\Akane\petdesk-character-host
    petdesk-live2d-pixi-driver -> F:\Akane\petdesk-live2d-pixi-driver
    petdesk-runtime -> F:\Akane\petdesk-runtime
```

Important rule:

```text
Do not run broad recursive format/test/build commands from F:\Akane\packages.
It is a navigation catalog and will duplicate the same repos through junctions.
Run tooling from each real repository path until physical migration is done.
```

## Physical Migration Plan

### M63: path-compatible resolver

Add resolver support so `AkaneCompanionLab` can find dependencies in both
places:

```text
..\packages\core\memcore
..\packages\core\promptpack-core
..\packages\core\charpack-core
..\packages\core\capcore
..\packages\capcore\capcore-adapter-*
..\packages\capcore\capcore-provider-*
..\packages\petdesk\petdesk-runtime
```

The old sibling paths should remain as fallback during the transition.

### M64: move low-risk archives and references

Move obvious archives and loose files after checking for local references:

```text
archives/
refs/
experiments/
```

### M65: physical package migration

Only after M63 is validated:

- remove the junction catalog entries for the package being migrated;
- move the real repository into the target package folder;
- run Akane bootstrap/check-only tests;
- run package-local validation for moved packages;
- keep a short compatibility note for anyone with old IDE workspace paths.

## Validation

Validation for M62 should include:

```powershell
git diff --check -- docs\akane_workspace_layout_m62.md
Get-ChildItem F:\Akane\packages -Recurse -Attributes ReparsePoint
```

No runtime, backend, QQ bot, or package test process needs to be started for
this phase.
