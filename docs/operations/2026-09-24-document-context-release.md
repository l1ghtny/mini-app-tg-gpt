# Document awareness repair — 24 September 2026

## Incident and cause

Owner screenshots showed one ready selected file while Astra then Terra denied seeing it. Read-only owner-scoped production records confirm the initial Astra request used no tools before the current attachment was created. Both later Terra requests used Auto after attachment, but made zero file-search calls. Provider metadata confirms the index contains one completed file with no failures. No customer content or settings were changed during diagnosis.

The shared model context exposed a generic search function without explicitly stating that selected files are accessible through it. Historical assistant denials remained in the conversation. The earlier synthetic acceptance asked explicitly to read a file and extract named facts; it did not cover these short follow-ups.

## Repair

- Current ready attachment count is supplied to both OpenAI and Anthropic after tool permissions are applied and after context compression.
- Models are told to use file search for attachment questions/follow-ups, and that earlier missing-file statements do not represent the current attachment state. Retrieved documents remain untrusted evidence.
- Auto stays automatic. No forced tool choice, permission override, frontend change, or customer conversation rewrite.
- Ingestion accepts only the provider's completed status as ready and retains the actual file ID plus index ID for cleanup; failed/cancelled results cannot appear ready.

## Release gate

What's New: required and covered by the existing same-day document repair announcement (`2026-09-24-document-selection`, migrations xw0e1f2a3b50/51). This corrects the already-announced attachment/retrieval and readiness behavior. Keep that single useful announcement; no duplicate feed item or schema migration.

## Acceptance

Pending: backend tests, production deployment, cheap Terra test using a missing-file turn followed by attachment and only “This one” with Auto, cleanup, beta deployment. Existing user document/chat remain untouched.
