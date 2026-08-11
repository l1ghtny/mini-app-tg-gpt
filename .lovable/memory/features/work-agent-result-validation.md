---
name: Work agent result validation
description: Agentic Work returns the requested deliverable, verifies it against explicit criteria, and retries at most once with durable cost accounting.
type: feature
---

# Context

The first general-agent beta task requested a five-item product checklist but returned unsupported claims that checks had already passed. The executor only rejected empty output, so a fluent status report could be stored as a successful result even when it was not the requested deliverable.

# Decision

- New planner outputs include one to six acceptance criteria inside each existing JSONB `expected_outputs` item. This is additive and requires no database migration.
- Historical plans without criteria remain executable; their output descriptions are the fallback contract.
- The executor must return the deliverable itself and must not claim inspection, testing, confirmation, fixes, or other completed work without source or tool evidence.
- GPT-5.6 Luna reviews every agentic Markdown draft with a strict structured response against the request, expected outputs, criteria, language, and actual evidence inventory.
- A failed review supplies bounded correction instructions for one replacement draft. A second failed review ends the run with `work_run_validation_failed`; there is no unbounded retry loop.
- Generation and review responses are costed and committed individually in the existing provider operation before the workflow continues.

# Apply in future changes

- Add acceptance criteria to new output kinds instead of special-casing individual workflow names in the executor.
- Treat the reviewer as a deliverable/evidence boundary, not as a style grader.
- Keep retries bounded and include their maximum token and tool-call cost in the pre-run budget reservation.
- Preserve the response shape for historical plans and older frontends during rolling deployment.

# Constraints

- Beta and production share PostgreSQL; do not introduce a migration for criteria while the existing JSONB output contract is sufficient.
- Provider cost remains recorded even when final validation fails and the user's request allowance is refunded.
- This validation slice improves inline agent results; it does not add general file-generation capabilities.
