---
name: Editable voice transcription
description: Entitled subscribers can record beta voice messages that become editable composer text without changing the chat streaming contract.
type: feature
---

## Context

Voice input should behave like typing assistance, not like a separate message type. A recording must not be sent automatically, and unsent transcripts must not enter conversation history.

## Decision

- The frontend records up to five minutes or 10 MB using browser `MediaRecorder` formats supported by OpenAI (`webm` or `m4a/mp4`).
- `POST /api/v1/audio/transcriptions` accepts multipart audio, `duration_ms`, and a UUID `client_request_id`.
- The backend requires an active tier with a positive `monthly_transcription_minutes` allowance. Price is not used as the entitlement signal because private friend tiers can intentionally cost zero.
- Initial monthly allowances are Smooth tier 120 minutes, Basic 60, Advanced 180, and Premium 360. Other tiers default to zero.
- OpenAI `gpt-transcribe` returns text that is appended to the current composer draft for editing. It is never auto-sent.
- Raw audio is held in memory only. A successful transcript is cached in isolated Redis for ten minutes solely to make retries idempotent; PostgreSQL stores ledger and cost telemetry, not audio or transcript text.
- The frontend and backend feature flags default off. The restricted beta build/deployment enables both flags.

## Applying this later

- Preserve the existing message POST/SSE/resume contract; transcription remains a pre-send composer operation.
- Apply the additive `request_ledger` check-constraint migration through the normal production migration flow before enabling beta voice, because the beta lane never runs migrations.
- Test Chrome/Android Telegram with WebM/Opus and Safari/iOS with MP4/M4A before enabling production.
- Keep provider cost per minute configurable and validate beta duration/cost telemetry before changing the initial monthly allowances.
