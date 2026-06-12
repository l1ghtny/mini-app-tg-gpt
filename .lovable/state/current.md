# Current State

## Current objective

Add automatic TeamCity hotfix-mode resolution so deploys can bypass paused canary steps when the shipped change set is a single commit or the shipped merge/commit is explicitly flagged as a hotfix.

## In progress

- Update the checked-in backend deploy flow to resolve `RELEASE_MODE=auto` from TeamCity build metadata and git commit markers.
- Document the exact TeamCity parameter changes needed for backend and frontend deploy configs.
- Verify whether the currently exposed TeamCity MCP can mutate live build configuration settings or only inspect them.

## Completed

- Read the current backend rollout and TeamCity hotfix docs in the repo.
- Inspected the live TeamCity deploy configs:
  - `MiniAppTgGpt_Migration`
  - `TelegramMiniAppProject_TgMiniFrontendNewUI_Deploy`
- Confirmed both deploy configs are still server-side inline shell rather than versioned settings in git.
- Confirmed the backend deploy build has a VCS root and TeamCity change metadata on the deploy build itself.
- Confirmed the frontend deploy build has no direct VCS checkout, but it does have a snapshot dependency on `TelegramMiniAppProject_TgMiniFrontendNewUI_Build`.
- Confirmed TeamCity runtime properties expose the data needed for script-side detection through `TEAMCITY_BUILD_PROPERTIES_FILE`, including current build ids and dependency build metadata.

## Blockers and risks

- The TeamCity MCP exposed in this session does not currently provide a direct build-configuration update tool; available actions are read endpoints, personal build queueing, and log inspection.
- Because the frontend deploy build has no own VCS checkout, auto-detection there depends on resolving the source build id from the snapshot dependency metadata.
- The new resolver has not yet been exercised on a real TeamCity agent in this session.

## Next steps

- Syntax-check the new release resolver and backend deploy script locally if the shell environment supports `bash`.
- Apply the documented TeamCity parameter and step updates through a TeamCity write-capable surface or the UI:
  - backend: replace inline steps with repo entrypoints and default `env.RELEASE_MODE=auto`
  - frontend: default `env.RELEASE_MODE=auto` and set `env.RELEASE_MODE_SOURCE_BUILD_ID_PROPERTY=dep.TelegramMiniAppProject_TgMiniFrontendNewUI_Build.teamcity.build.id`
- Run one normal deploy and one flagged/single-change deploy to confirm the resolver switches modes as expected.
