# Work MVP completion plan

Last updated: 2026-08-31

## Decision

Finish and validate the existing conversation-native Work product before adding
providers, workflow catalogues, team features, or broader autonomous behavior.

The first workable version is one dependable surface that can:

1. produce sourced web research;
2. synthesize attached documents;
3. turn supplied data or notes into a valid editable XLSX or DOCX artifact;
4. ask one material clarification and resume durably;
5. cancel, redirect, retry, and reconnect without losing work or charging twice.

This is the smallest scope that tests the product promise: finished, reusable work
rather than another fluent chat answer.

## What is already built

The current beta branch already has the required platform foundation: durable Work
threads and runs, resumable activity, human clarification, document inputs, web and
file evidence, generated artifacts and downloads, cost controls, cancellation, and
failure persistence. The remaining milestone is product reliability and usefulness,
not another architecture layer.

The earlier deterministic-job plan is therefore superseded. Structured comparison
workbooks remain an important acceptance case, but they are one deliverable inside
conversation-native Work rather than a separate workflow product.

## Evidence snapshot

The saved beta observations cover 7 of 15 synthetic cases:

- system success: 100%;
- automated pass: 85.7%;
- citation integrity: 100%;
- average recorded cost: $0.0957;
- p90 recorded cost: $0.1642;
- average duration: 44.36 seconds;
- one document case missed its minimum citation count;
- no artifact case was observed;
- no output received human review.

This proves that basic research and document execution can work. It does not yet
prove artifact delivery, necessary clarification, recovery, or user usefulness.

## Executable MVP gate

Use the versioned `mvp-core` evaluation profile as the release boundary:

```bash
poetry run python -m evals.work_quality run --profile mvp-core
```

It contains six cases:

| Product behavior | Case |
| --- | --- |
| Sourced current research | `web_openai_agent_evals` |
| Multi-document synthesis | `docs_product_decision_memo` |
| Calculated editable workbook | `sheet_sales_analysis` |
| Editable action-plan document | `artifact_action_plan_docx` |
| Necessary question and durable resume | `clarify_material_audience` |
| Cancellation and redirected follow-up | `recovery_cancel_then_redirect` |

The first invited pilot starts only when:

- system success is at least 90%;
- automated case pass is at least 80%;
- every requested artifact downloads and passes structural validation;
- citation integrity is at least 95%;
- at least 70% of human-reviewed results are useful without correction;
- there is no lost run, inaccessible result, duplicate charge, or missing refund.

Passing automation alone is insufficient. Review every core-profile result with the
included human worksheet before deciding that the version is workable.

## Completion sequence

### 1. Establish the baseline

- Run `mvp-core` on beta with a short-lived test credential.
- Complete the human worksheet for all six outputs.
- Preserve redaction-safe observations, run IDs, cost, and duration locally.
- Group failures by system/recovery, artifact, evidence, clarification, usefulness,
  then cost/latency.

### 2. Fix only demonstrated blockers

- Fix the largest repeated failure class first.
- Add a regression case for every real user-visible failure.
- Re-run the failing case, then the whole profile.
- Avoid prompt tuning to exact prose; verify the requested outcome and artifact.

### 3. Run a monitored pilot

Invite 5–10 named users to complete three jobs: sourced research, document synthesis,
and an editable file deliverable. Observe whether they can start, understand progress,
recover, download, revise, and reuse the result without founder intervention.

Pilot evidence must include first-task completion, second-task return, correction
requests, inaccessible results, refunds, support load, cost per useful result, and the
job users would be disappointed to lose.

### 4. Choose the commercial wedge

After the pilot, keep the job with the strongest repeated use and healthy contribution
margin. Improve its onboarding, examples, and output quality before expanding Work.

## Explicitly deferred

- Claude, DeepSeek, Qwen, or a model-count race;
- arbitrary autonomous tools or browser/code environments;
- team workspaces, approvals, and enterprise administration;
- video, music, voice, and broader media generation;
- public unlimited Work quotas;
- large acquisition spend before the pilot demonstrates repeat use.

## Immediate next move

Run the six-case beta profile, complete human review, and use its first reproducible
failure as the next implementation task. Do not select another broad feature in
advance of that evidence.
