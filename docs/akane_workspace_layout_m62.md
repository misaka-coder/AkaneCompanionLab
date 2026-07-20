# Akane Workspace Layout M62

Status: phase-0 catalog implemented.
Date: 2026-07-09

## Goal

The `<workspace>` workspace now contains product apps, reusable packages,
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
channelcore-onebot
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

## Package Location Independence (M64 update)

The hard sibling dependencies recorded by the original M62 inventory have been
removed. `AkaneCompanionLab` now installs exact versioned wheels from a complete
wheelhouse or configured package index. Petdesk package manifests use exact
`0.1.0` releases, and runtime/operator scripts require `-RuntimeDir` or
`PETDESK_RUNTIME_ROOT` instead of deriving `..\petdesk-runtime`.

The repositories may therefore be moved independently. The `packages/`
junction catalog remains an optional source-navigation aid for maintainers; it
is not part of installation, import resolution, runtime launch, or release
acceptance. See `package_artifact_independence_m64.md` for the enforced gate.

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
channelcore-onebot
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

`petdesk-runtime` is resolved only from an explicit parameter or environment
configuration, so moving its source tree does not change Akane's dependency
contract.

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
<workspace>\packages\
```

The catalog uses Windows directory junctions to point to existing package
repositories. It is a convenience view only; no Akane install or launch script
may depend on these junctions.

Catalog shape:

```text
packages/
  README.md
  core/
    capcore -> <workspace>\capcore
    capcore-host-utils -> <workspace>\capcore-host-utils
    charpack-core -> <workspace>\charpack-core
    memcore -> <workspace>\memcore
    promptpack-core -> <workspace>\promptpack-core
  capcore/
    capcore-adapter-comfyui -> <workspace>\capcore-adapter-comfyui
    capcore-adapter-mcp -> <workspace>\capcore-adapter-mcp
    capcore-adapter-python -> <workspace>\capcore-adapter-python
    capcore-adapter-speech -> <workspace>\capcore-adapter-speech
    capcore-provider-anthropic -> <workspace>\capcore-provider-anthropic
    capcore-provider-native-tools -> <workspace>\capcore-provider-native-tools
    capcore-provider-openai -> <workspace>\capcore-provider-openai
  petdesk/
    petcore-protocol -> <workspace>\petcore-protocol
    petdesk-character-host -> <workspace>\petdesk-character-host
    petdesk-live2d-pixi-driver -> <workspace>\petdesk-live2d-pixi-driver
    petdesk-runtime -> <workspace>\petdesk-runtime
```

Important rule:

```text
Do not run broad recursive format/test/build commands from <workspace>\packages.
It is a navigation catalog and will duplicate the same repos through junctions.
Run tooling from each real repository path until physical migration is done.
```

## Physical Migration Plan

### M63: path-compatible resolver (superseded)

The earlier plan proposed resolving package source from both locations:

```text
..\packages\core\memcore
..\packages\core\promptpack-core
..\packages\core\charpack-core
..\packages\core\capcore
..\packages\capcore\capcore-adapter-*
..\packages\capcore\capcore-provider-*
..\packages\petdesk\petdesk-runtime
```

M64 supersedes that plan. There is no source-path fallback: Python dependencies
come from wheels/index releases, npm dependencies come from versioned package
artifacts, and petdesk source/build roots are explicit operator inputs.

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
Get-ChildItem <workspace>\packages -Recurse -Attributes ReparsePoint
```

No runtime, backend, QQ bot, or package test process needs to be started for
this phase.
