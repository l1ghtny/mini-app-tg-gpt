---
name: Dual-surface web migration
description: AI with UI is one product with a full web app and a Telegram Mini App; SEO and contracts must support both.
type: feature
---

## Context

Telegram remains a useful interface, but acquisition and paid conversion are moving toward a standalone web application. The frontend now has a browser auth path based on email magic links; Telegram auth remains automatic inside the Mini App.

## Decision

- Treat the browser app as the primary acquisition surface and Telegram as a first-class alternative.
- Keep one backend, model catalog, entitlement system, conversation store, and billing model.
- Keep Telegram deep links start-only.
- The SEO repo is `/Users/lightny/0/coding/PersonalProjects/chat-search-link` and belongs to the mini-gpt-telegram source topology.
- SEO copy must say that Perplexity Sonar is connected through Perplexity API, not merely describe an imitation.

## Apply in future changes

- Any auth, profile, subscription, conversation, document, image, or model-catalog change must be checked in both browser and Telegram containers.
- Product copy should name AI with UI as the product; provider brands describe connected models, not ownership.
- Preserve route compatibility for existing Telegram-focused SEO URLs while presenting the web app as the primary CTA.

## Constraints and gotchas

- A Telegram identity and a new email identity can become separate users unless the email is explicitly attached to the authenticated Telegram user. Existing collisions return `account_merge_required` and preserve both accounts.
- Do not promise shared history across surfaces until the account-linking UX is verified end to end.
- Web auth is feature-flagged and requires callback URL, email sender, SMTP, and CORS configuration.
- Model and entitlement availability is dynamic; UI and SEO should defer exact limits to the backend catalog.
