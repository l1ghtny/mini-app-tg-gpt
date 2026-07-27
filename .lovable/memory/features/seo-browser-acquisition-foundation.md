---
name: SEO browser-acquisition foundation
description: Public SEO routes, browser-first CTAs, prompt handoff, and verified-claim rules for the acquisition site.
type: feature
---

# SEO browser-acquisition foundation

## Context

The SEO site must acquire browser users without abandoning the Telegram Mini App or claiming product behavior that is not production-verified.

## Decisions

- Lead the homepage with research, source verification, documents, writing, and code rather than model count.
- Treat the web application as the primary CTA and Telegram as the companion surface.
- Publish pricing, model catalogue, features, help, and practical data/security information before authentication.
- Keep exact limits dynamic or defer to the production application because model-specific entitlements change independently.
- Preserve `AI with UI` until the Lightny AI brand decision is explicit.
- Pass SEO prompt examples to the browser app as a URL-encoded `prompt` query parameter; the app must consume it without automatically submitting.
- Generate `robots.txt` and sitemap URLs from `SITE_URL`; the temporary Lovable domain remains only as the fallback until a canonical domain is configured.

## How to apply

- New SEO pages should provide original evidence or a concrete workflow and transfer relevant context into the app.
- Telegram links remain start-only deep links and must not receive UTM or prompt payloads.
- Verify public prices, model names, availability, provider routing, and account-linking language against production before release.
- Do not publish data-location, encryption, retention, training, VPN, or unlimited-use claims without technical and legal evidence.

## Follow-ups

- Frontend: read `prompt` from the SEO handoff URL, prefill the composer, and require a deliberate send action.
- SEO/deployment: set the final `SITE_URL`, submit sitemap to Yandex Webmaster and Google Search Console, and add consent-aware funnel analytics.
- Product/legal: publish reviewed privacy, terms, public offer, refund, file-processing, deletion, and account-linking pages.
