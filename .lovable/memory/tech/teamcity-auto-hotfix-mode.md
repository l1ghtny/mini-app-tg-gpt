---
name: TeamCity auto hotfix mode
description: Backend deploy scripts resolve `RELEASE_MODE=auto` from TeamCity build metadata and hotfix commit markers.
type: tech
---

## Context / problem

Hotfix rollouts need to bypass paused canary steps without forcing operators to remember a manual TeamCity parameter flip every time.

At the same time, standard releases still need to pause in Argo Rollouts unless the shipped change set is intentionally marked as a hotfix.

## Decision taken

- Add `scripts/release/resolve_release_mode.sh` as the single resolver for `auto|normal|hotfix`.
- In TeamCity-backed runs, read the current build metadata from `TEAMCITY_BUILD_PROPERTIES_FILE` and query `changes(count,change(comment,...))` for the relevant source build.
- Resolve `auto` to `hotfix` when the source build contains exactly one VCS change.
- Resolve `auto` to `hotfix` when the latest shipped commit message is marked with `[hotfix]` or `#hotfix`.
- Keep explicit `RELEASE_MODE=normal` and `RELEASE_MODE=hotfix` as operator overrides.

## How to apply it in future changes

- Default TeamCity deploy configs to `env.RELEASE_MODE=auto` unless you are intentionally forcing rollout behavior.
- For deploy builds without their own VCS checkout, point `RELEASE_MODE_SOURCE_BUILD_ID_PROPERTY` at the upstream snapshot dependency build id property.
- Put the hotfix marker on the merge or direct commit that is actually being shipped, not on an earlier commit in the branch.

## Constraints / gotchas

- The resolver depends on TeamCity runtime properties being available; local shell runs fall back to git commit message inspection only.
- Frontend deploy builds cannot infer change counts from themselves when they have no VCS checkout; they must resolve against the snapshot dependency build id instead.
- The TeamCity MCP exposed in this workspace currently allows inspection and personal build queueing, but not direct mutation of build configuration settings.
