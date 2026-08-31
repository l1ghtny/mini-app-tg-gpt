# Lightny Work MVP quality bench

This suite evaluates Work as a product, not just the model call. It sends synthetic
tasks through the same authenticated document, Work conversation, clarification,
cancellation, follow-up, artifact, and download APIs used by the application.

The first version contains 15 cases:

- 3 current web-research tasks;
- 3 document synthesis and extraction tasks;
- 2 spreadsheet analysis tasks;
- 2 polished file-generation tasks;
- 2 clarification controls (one necessary and one unnecessary);
- 2 conversational revision and result-reuse tasks;
- 1 cancellation and redirect recovery task.

All fixtures are synthetic and safe to upload to beta. Never add customer prompts,
documents, access tokens, or downloaded customer artifacts to this directory.

## What is measured

The automated graders use observable product facts:

- API completion and terminal Work status;
- presence of the requested deliverable;
- response language;
- required and forbidden tool use;
- necessary or unnecessary `ask_user` behavior;
- source and citation count plus citation-to-source integrity;
- requested artifact type, successful download, and structural validity;
- recorded cost and end-to-end execution time;
- multi-turn success for revisions and recovery.

Automated checks cannot establish semantic correctness or usefulness. A separate
human rubric scores usefulness, correctness, completeness, readability, and whether
the result needed a corrective follow-up. Do not replace that review with a model
judge until it has been calibrated against enough human decisions.

## Offline validation

```bash
poetry run python -m evals.work_quality validate
poetry run pytest tests/test_work_quality_evals.py -q
```

This validates every case and fixture without making provider calls.

## Safe live beta run

Use a short-lived access token belonging to a beta account. Keep it in the process
environment; never pass it as a command argument or save it in a file.

```bash
export LIGHTNY_EVAL_BEARER_TOKEN="..."

# Run the balanced Work MVP acceptance profile before spending allowance on all cases.
poetry run python -m evals.work_quality run \
  --profile mvp-core

# Run all 15 cases only after the smoke set is healthy.
poetry run python -m evals.work_quality run
```

For a one-off local run, `--token-file /tmp/lightny-work-eval.jwt` avoids
exporting the token into the parent shell. Browser logins use an HttpOnly session
cookie instead, which can be supplied with
`--session-cookie-file /tmp/lightny-work-eval.session`. Credential files must
contain only the value, be readable only by the current user, and be deleted
immediately after the evaluation.

The `mvp-core` profile covers sourced research, document synthesis, XLSX and DOCX
delivery, material clarification, and cancellation/recovery. A text-only smoke run
is not sufficient evidence for Work MVP readiness.

The runner is deliberately sequential to respect the per-user active-run limit. It
uploads only a case's synthetic files, waits for ingestion, executes the scenario,
downloads generated artifacts through the Russian-IP-friendly application route,
records a redaction-safe observation, and deletes the uploaded synthetic documents.
Results are written under `.eval-results/`, which is ignored by Git.

The runner does not print or persist the bearer token. A failed cleanup is recorded in
the observation so it is visible rather than silently ignored.

## Score and review

```bash
poetry run python -m evals.work_quality human-template \
  --output .eval-results/work/human.json

# Set reviewed=true and replace the placeholder 3/5 values after reviewing each
# result in the Work UI. Unreviewed rows stay pending and do not affect human gates.
poetry run python -m evals.work_quality score \
  --observations .eval-results/work/observations \
  --human .eval-results/work/human.json \
  --output-dir .eval-results/work/report
```

`report.json` is machine-readable. `report.md` is the decision document for the next
slice. The initial MVP gates are:

| Gate | Threshold |
| --- | ---: |
| Accepted cases that complete successfully | at least 90% |
| Automated case pass rate | at least 80% |
| Generated files structurally valid | 100% |
| Citations resolving to recorded sources | at least 95% |
| Human-rated useful without correction | at least 70% |

These are release gates, not public performance claims. Keep failed observations and
their Sentry/run identifiers locally long enough to diagnose them, then remove them
under the normal beta data-retention policy.

## How this guides product work

Fix the largest repeated failure class before adding another broad capability:

1. system or recovery failures;
2. missing, corrupt, or unreachable artifacts;
3. unsupported claims or broken evidence;
4. missed or needless clarification;
5. poor deliverable usefulness/readability;
6. latency or cost outliers.

Add a regression case whenever a beta incident reveals a new user-visible failure.
Do not tune prompts to exact expected prose: assert the product outcome and preserve
variation in valid answers.
