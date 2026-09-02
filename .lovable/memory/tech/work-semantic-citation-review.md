---
name: Work semantic citation review
description: Web-backed Work results must verify that cited pages support their adjacent claims.
type: tech
---

## Context

The beta-147 Work evaluation showed that citation IDs and URLs could resolve while a
cited page was semantically irrelevant to the adjacent claim. A delete-eval API page
was used as evidence for general evaluation design guidance.

## Decision

For Work drafts with web sources, give the result reviewer bounded context around each
citation and require it to browse the cited pages before passing the result. A URL,
title, or search snippet alone is not evidence that the page supports the claim.

## Future changes

- Keep referential citation integrity and semantic source grounding distinct.
- Reject or correct mismatches by replacing the source, narrowing the claim, or
  removing it; do not silently substitute support from an uncited page.
- Do not force reviewer web search for file-only or source-free Work results.
- Preserve reviewer tool calls in provider usage and cost telemetry.
