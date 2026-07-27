---
name: Browser-first product-market strategy
description: Durable positioning, differentiation, brand, and rollout direction for the web and Telegram product.
type: feature
---

# Browser-first product-market strategy

## Context

The product is migrating from a Telegram-first bot toward a complete web application while retaining the Telegram Mini App. Russian AI aggregators already compete heavily on model count, media breadth, payment convenience, and generic “all neural networks” messaging. The product needs a narrower, evidence-backed reason to win and a launch sequence that does not send traffic into an untrusted or unmeasured browser flow.

## Decision direction

Position the product as a focused AI workspace for research, documents, writing, and code. Lead with reliable sources, file workflows, curated model guidance, clear request allowances without token math, and continuity between the browser and Telegram. The browser is the primary acquisition, activation, and payment surface; Telegram is the companion surface.

Do not enter a model-count or video/music breadth race. The first differentiation work is automatic model recommendation with request-pool impact, stronger sourced research, document citations and exports, verified cross-surface continuity, and request packs. Claude integration and side-by-side comparison are parity work.

The durable customer promise is: for text, one completed answer uses one clear request from a named model group. Tokens, cached-input discounts, provider cost, and routing thresholds remain internal. Keep current public prices and request allowances while exact cached-input/cache-write telemetry and provider-cost reconciliation are implemented; the previous recommendation to cut Premium Flagship requests was based on an invalid uncached-input estimate and is withdrawn.

Use `Lightny AI` as the current master-brand recommendation, subject to ownership, trademark, live domain availability, and customer testing. Preserve the `@AIwithUIbot` username and deep links for continuity. The proposed canonical architecture is `lightny.ru`, `app.lightny.ru`, and `api.lightny.ru`, with `lightny.ai` only as a defensive redirect if acquired.

## How to apply

- Use `docs/product-strategy/README.md` as the index and `08-execution-roadmap.md` as the implementation sequence.
- Keep public product claims synchronized with deployed production behavior.
- Do not promise shared web/Telegram history until account linking is E2E-verified.
- Make pricing, catalogue, limits, file behavior, provider routing, deletion, and renewal rules understandable before signup or purchase.
- Prefer original benchmarks, real workflows, and prompt handoff over scaled generic SEO content.
- Evaluate roadmap decisions against activated and retained useful tasks, contribution margin, and reliability—not raw message volume or traffic.
- SEO prompt cards hand off an optional `prompt` query parameter to the browser app. The frontend should validate and prefill it but must not automatically submit it.

## Constraints and gotchas

- Competitor features, prices, domain availability, and model catalogues are time-sensitive and must be rechecked.
- Legal, privacy, public-offer, refund, file-processing, and data-location statements require qualified review and technical verification.
- `lightny.ru` appeared registered at the July 2026 check, but registrar control and ownership must be confirmed.
- The local Sonar fallback copy in `app/services/model_registry.py` contains corrupted user-visible text and should be fixed before launch even if production database copy has already been corrected.
