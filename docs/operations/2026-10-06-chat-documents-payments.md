# Chat document results and payment correctness

Owner authorized this implementation and the preceding usage/settings release.
Public checkout stays closed pending owner decisions on renewal policy and prices;
this release does not activate purchases or move funds.

## Customer behavior

With Auto tools, ordinary shared-allowance chats can create DOCX and PDF files.
Files appear under the answer and can be downloaded for five days. Follow-up
requests in the same chat can create a new version after the model reads the
complete original source. Old clients and chats with tools disabled receive no
document tools. XLSX, PPTX and a visual document editor are outside this scope.

Text-writing tokens use the normal model allowance. Rendering and file handling
incur no AI-provider charge; hosting and private storage remain infrastructure costs. Files are bounded
to 5 MB, structured source to 100 KB, and active storage to 40 files/20 MB per
account. Unsupported PDF characters or oversized table cells return a limitation;
the model must preserve the content and offer DOCX or a smaller document.

Authenticated download POST returns a five-minute signed attachment link, capped
at file expiry. R2 paths contain only UUIDs. Source and storage keys are excluded
from public metadata. Account export includes the source; deletion removes it.
Cleanup retains ambiguous PUT keys and rotates failed deletions to prevent
starvation. Existing uploaded-file retention remains independent.

Paid tiers have an explicit allowance plan mapping. Confirmation/refund effects
are separate from provider status, serialized per account and deduplicated per
payment. Confirmation validates provider ID, amount and success. Payments link
to their subscription and purchased interval. Legacy refunds without that link
require reconciliation instead of guessing a subscription. Pending refunds are
shown as pending. Refund windows use confirmed time when known and UTC offsets.
Mapped purchases preserve unrelated private gifts; closed shared checkout is
enforced before legacy bank calls.

## Contract and deployment

Frontend adds optional `document_output_supported=true` only after the allowance
snapshot advertises `document_generation_available`. Backend defaults to false.
Persisted `generated_document` parts and SSE `document` events carry canonical
metadata and an explicit assistant message ID. All three stream consumers handle
the new event and replay without attaching a late event to another answer.

Schema 71 adds payment lifecycle fields and maps historical Basic to Start;
schema 72 adds private chat results. No historical payment times/links are
fabricated. Deploy the frontend first, then the backend/migrations with document
generation disabled. Enable `CHAT_DOCUMENT_GENERATION_ENABLED` in the existing
production/beta environment Secrets only after both binaries are available and
private storage is configured. Production references the same three dedicated
private-storage Secret keys already used for audio and cleanup; no credentials
are copied into source or broadened.
Verify live create, revise, reload/replay, ownership and download bytes before
publishing the announcement. Roll back by disabling the feature; retain schema
and metadata so existing files remain downloadable until expiry.

Backend version: deployed 2.4.1 -> 2.5.0, compatible capability plus payment fixes.
Frontend version: deployed 2.4.0 -> 2.5.0, file results and pending refund states.
What's New: required, grouped document capability and refund behavior. Draft below;
schema73 stages the feed item inactive. The release operator activates only
`2026-10-06-chat-documents`, setting publication/update times to the activation
time, after live acceptance passes. Preserve beta Work and the shared migration
history. Backend2.5.1 adds the missing production storage references; frontend
stays2.5.0 for this configuration correction.

## Announcement draft

EN title: Documents you can download

EN body: Ask for a DOCX or PDF in a chat with Auto tools. The file appears under
the answer; request changes in the same chat to get a new version. Each file is
available for five days, with its download deadline shown beside it. Refunds that
the bank is still processing now show as pending.

RU title: Документы, которые можно скачать

RU body: В чате с инструментами «Авто» попросите создать DOCX или PDF. Файл
появится под ответом. Чтобы получить новую версию, попросите внести изменения
в том же чате. Файлы хранятся пять дней; рядом с каждым указан срок скачивания.
Если банк ещё обрабатывает возврат, приложение покажет, что он в процессе.

## Launch gates still open

Owner purchase policy/prices, corresponding offer/receipts and merchant acceptance,
Telegram Stars for digital purchases inside Telegram, acquisition/referral checks,
truthful SEO catalog and legal facts, production Metrica counter/goals, indexing
readiness and an owner-set Yandex ads budget. No money has been moved.
