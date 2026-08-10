---
name: Work-run plans and outcome summaries
description: Stable backend plan codes and versioned result facts make workflow execution auditable without coupling public copy to the API.
type: feature
---

# Context

Work creation previously showed only the requested inputs, and completed runs exposed the artifact without a concise account of what the workflow actually did.

# Decision

- Each `WorkRunDefinition` owns an ordered tuple of stable `WorkRunPlanStep` codes.
- Enabled definitions are exposed additively through `WorkRunCapabilitiesResponse.plans`; public step text remains in frontend i18n.
- Spreadsheet completion stores a versioned JSON summary in the existing `WorkRun.result_summary` field. It records row, column, and source counts, normalization mode, and stable output-feature codes.
- The frontend treats capability plans as optional during rolling deployment and falls back to the current spreadsheet plan.
- The frontend parser accepts both the versioned summary and the older unversioned row/column/source shape so historical runs remain readable.

# Apply in future changes

- Add new plan or output codes instead of sending backend-authored public copy.
- Version the result-summary payload before changing the meaning of an existing field.
- Keep capability additions optional in the frontend until all deployed backends expose them.
- Do not require a database migration for presentation-only plan or summary evolution while `result_summary` remains sufficient.

# Constraints

- Beta and production share PostgreSQL and R2, so rollout order must remain safe in either direction.
- A plan describes bounded workflow intent; it is not permission for unconstrained model-generated code.
- The completed outcome must describe recorded execution facts, not promises or inferred quality.
