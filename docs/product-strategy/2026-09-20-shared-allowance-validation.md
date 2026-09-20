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
  before any provider call. Seeded zero-balance guidance was also inspected at both sizes, including
  exhausted Luna. The synthetic preview counters were restored afterward.
- The isolated PostgreSQL migration was applied twice successfully. Alembic has
  one head, `xt7b8c9d0e1f`. Accounting tests cover concurrent final-balance sends,
  exactly-once settlement, month rollover, child ceilings, included Luna,
  platform-funded failures, crash recovery, unknown supplier exposure and the
  aggregate guard. Image tests check ownership and new-image reference isolation.

Final automated results: **32 allowance backend tests, 11 existing backend unit tests and 316 frontend tests passed**. App-project TypeScript, focused Ruff/ESLint, Python compilation and Vite build passed.

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
Owned uploaded-image vision now uses validated storage bytes for both providers,
including format preparation, ownership and size checks. Local vision passed on
all seven models; deployed routing and retention still need acceptance.

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

## Pre-beta refinement and full local capability smoke test

The final local matrix passed all 35 combinations: seven advertised chat models
against text, uploaded-image vision, web search, document search and Flare image
generation. These are integration smoke tests, not a ranking of reasoning quality.
All six Low/Medium/High generation and same-conversation edit cases completed.
The High edit was visually inspected: the blue cup became red with its shape and
cream background preserved.

Fable 5.1 rejects forced tool_choice=tool/any. Its adapter now exposes only the
required tool with auto selection and an explicit instruction. The app verifies
that the required tool was actually called; skipping it cannot settle as success.
See [Anthropic tool-choice constraints](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools).

Reservations now depend on selected image quality and reference inputs. Text
output capacity adapts to remaining reservation: 4,096 tokens normally, 8,192 at
high effort, 2,048 for the initial required-tool routing turn, subject to model
and available-budget caps. This bounds hidden reasoning plus visible output;
it does not guarantee that complex work completes inside those caps. Incomplete
responses fail visibly and remain platform-funded in this beta. No silent model
substitution or disabling of Fable's required thinking is used.

Unconfirmed paid requests are capped at 5% of the monthly grant; explicit image
requests and sufficiently expensive possible automatic image calls require the
existing spending confirmation. Actual usage, not the reservation, is deducted.
Missing or inconsistent image usage stays unknown rather than being priced as zero.

With a separate synthetic Start fixture, a Low image completed with 60,000 units
(4.8%) remaining; a short Terra reply completed with 20,000 (1.6%). Zero paid
balance rejected Terra before generation but allowed Luna. Exhausting both pools
returned the Luna fair-use error. Signed-estimate replay after prompt changes,
tiny request caps, duplicate send identity, SSE resume and cancellation passed.

Recalculated all 134 known provider attempts in the synthetic preview snapshot
from their input/cache/output and tool counters with no mismatches. Five unknown
attempts from interruptions/earlier experiments remain explicit. This validates
rate-card arithmetic, not reconciliation to a provider invoice.

## Actual complete image-task usage

Fresh Terra generation followed by a Sonnet edit, low reasoning, 1024×1024.
Includes text-model routing/final answer, image prompt and reference processing.
One sample per case: these are examples, not quotas or guaranteed prices.

| Task | Actual units | Start | Plus | Premium | Max |
|---|---:|---:|---:|---:|---:|
| Low generation | 9,850 | 0.79% | 0.39% | 0.16% | 0.04% |
| Medium generation | 17,615 | 1.41% | 0.70% | 0.28% | 0.07% |
| High generation | 56,726 | 4.54% | 2.27% | 0.91% | 0.23% |
| Low edit | 19,492 | 1.56% | 0.78% | 0.31% | 0.08% |
| Medium edit | 24,161 | 1.93% | 0.97% | 0.39% | 0.10% |
| High edit | 63,852 | 5.11% | 2.55% | 1.02% | 0.26% |

The quality picker shows the output-only approximate share and identifies prompt
and reference processing as additional. Recent tasks distinguish image generation,
image editing, web search and document search. Shared users never see the old
image-energy meter, including users with an existing private-tier energy balance.

No remote push, beta deployment, production migration or payment change was made.
