# Document generation recovery — 24 September 2026

User authorized fixing the reported mid-generation failures and release to production, then beta. Existing approval covers a bounded real production document check with Terra.

## Cause and change

The matched incident showed successful retrieval followed by very small remaining answer capacity, or a hard abort on a third tool call in a Fable batch. Failed requests were not charged. The original streamed OpenAI incomplete reason was not retained, so token exhaustion is not asserted as proven for every historical failure.

- Bound each search's entire JSON evidence payload to 2,048 tokens; interleave ranked passages from all searched files and remove duplicate passages.
- After document retrieval, answer from the returned excerpts rather than entering another retrieval round. Preserve the existing two-executed-tool limit; return an explicit non-executed result for excess batch calls so every call has a matching result and the model can finish.
- Include bounded document continuation input, routing output and per-store search costs in usage estimates. Bind signed quotes to current ready stores; preserve Auto, explicit spend caps, confirmation thresholds and separate Luna accounting.
- Retain streamed completion status and incomplete reason in attempt metadata. Emit additive `response_capacity_exceeded` / `provider_response_incomplete` error codes. Existing frontend parser accepts error codes; its generic inline presentation remains compatible. A richer localized recovery UI is a separate follow-up.

No accounting limits or failed-request refunds changed. Search results remain excerpts, not a claim that complete files were reviewed.

What's New gate: no new entry required. This restores the document-answering behavior already announced in `2026-09-24-document-selection`; it adds no new control or user workflow. Keep the existing notice and shared schema unchanged.

## Validation

Nine focused regressions cover large two-file payloads, Astra single-search/final-answer flow, Fable excess batch calls, Auto variants, quote changes, explicit caps, completion metadata and final-answer capacity within the observed incident ceiling. The full allowance suite passed (156 tests, isolated per-test schemas), followed by two additional incident-budget cases. Focused regressions: nine passed. Ruff and whitespace checks passed. An initial sandboxed DB run could not connect; the authorized network-enabled run passed. Production acceptance is pending.

## Deployment

Pending production release, exact runtime verification, two tiny synthetic documents with Terra/Auto under a 50,000-unit cap, then beta release. User incident conversations and files are not replayed or modified.
