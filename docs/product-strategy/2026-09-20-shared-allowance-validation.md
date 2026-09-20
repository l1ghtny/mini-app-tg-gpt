# Local shared allowance validation

20 September 2026. Synthetic accounts and an isolated schema in the test database;
no production database mutation. Provider requests were real. Test media/document
objects used the configured provider and R2 storage. The local API provider guard
was USD 3 at published-rate accounting. This report does not certify an invoice
or a deployed beta.

## Completed checks

- All seven chat models completed a streamed arithmetic reply through the actual
  application adapter: Luna, Terra, Sol, Astra, Sonnet 5, Opus 5 and Fable 5.1.
- Claude Sonnet used the Luna web-search bridge and returned source URLs.
- A synthetic 166-byte document was uploaded, indexed, attached and searched by
  Sonnet. It recovered the corrected 42,000-ruble budget, obsolete 50,000-ruble
  budget and 15 October deadline, with a filename citation.
- At the 20th and 40th reply of seeded long conversations, Sonnet retained the
  corrected budget, deadline and codename through Luna summaries. Summary cost
  was recorded separately in the included/background pool.
- Terra generated a blue cup using Flare. Sonnet subsequently edited that owned
  image to red, preserving its cream background. The final image rendered in the
  real chat UI through the authenticated image path.
- Final preflight checks rejected a tiny cap before admission, rejected an estimate
  reused for a changed prompt, and hid an image budget too small to run. Fresh
  paid text tasks without tools do not reserve Luna capacity. Duplicate
  sends returned the same assistant ID. The live recovery check returned a 307
  active-stream redirect, resumed without replaying the last received event and
  stopped Claude generation with a cancelled terminal event. Unknown usage stayed
  pending at zero customer charge.
- Browser review covered 390×844 mobile and 1280×900 desktop, Russian/dark and
  English/light layouts, model selection, expanded plan examples, image display
  and spending confirmation. Cancelling the confirmation restored the draft
  before any provider call. Exhaustion has component/accounting coverage; a
  deployed seeded-exhaustion walkthrough remains pending.
- The isolated PostgreSQL migration was applied twice successfully. Alembic has
  one head, `xt7b8c9d0e1f`. Accounting tests cover concurrent final-balance sends,
  exactly-once settlement, month rollover, child ceilings, included Luna,
  platform-funded failures, crash recovery, unknown supplier exposure and the
  aggregate guard. Image tests check ownership and new-image reference isolation.

Final automated results: **21 allowance backend tests, 11 existing backend unit tests and 312 frontend tests passed**. App-project TypeScript, focused Ruff/ESLint, Python compilation and Vite build passed.

## Measured examples used by the pricing screen

Medium reasoning; fresh text chats; customer rate card `2026-09-18-v1`.
Start has 1,250,000 units. Percentages are measured examples, not guaranteed counts.

| Task | Model | Actual units | Start share |
|---|---|---:|---:|
| Polite short Russian rewrite | Terra | 1,396 | 0.11% |
| Café website brief, up to 250 words | Sonnet 5 | 7,542 | 0.60% |
| Extract facts from the short synthetic document | Sonnet 5 + two searches | 11,514 | 0.92% |
| Blue cup image, low quality, 1024 square | Terra + Flare | 9,707 | 0.78% |

The UI publishes the first three with actual output or an explicitly identified
document excerpt. The low-quality image is not used to imply default-medium
image pricing. The long-chat paid portions were about 0.33% of Premium each;
that controlled fact-retrieval task is not an estimate for all long conversations.

## Limitations and fixes discovered

OpenAI's hosted image tool returned an image without the usage needed by this
accounting path. Flare now uses the Images endpoint internally while remaining a
chat tool; its actual text/image/output counters settle the attempt. Unknown
attempts from the earlier experiment remain recorded rather than guessed away.

The local public CDN could not find test-schema image records. Owned Flare edit
references therefore read validated owned R2 objects, without arbitrary URL fetch.
Uploaded-image vision still needs deployed-beta verification. Direct tool checks
on one text model do not certify every other model/tool combination.

The test database route was briefly unavailable during final checks. A subsequent
full isolated suite passed; no database reset or public-schema cleanup was used.
The repository-wide backend suite was not run because its root fixture drops
the test database's public schema. Use the isolated fixture below.

## Reproducible checks

With the normal test environment configured (never production DB):

```sh
poetry run pytest --confcutdir=tests/allowance tests/allowance -q
poetry run alembic heads
poetry run python -m compileall -q app
```

The accounting fixture creates and removes only its own random schema. Run the
paired frontend Vitest suite, `tsc --noEmit --project tsconfig.app.json`, ESLint on
changed files and a Vite production build. Build/lint warnings about existing
large chunks, mixed imports and Fast Refresh exports are distinct from failures.
