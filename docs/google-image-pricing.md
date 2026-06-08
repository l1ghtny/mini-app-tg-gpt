# Google Image Pricing

Canonical billing keys for Google image models are size-based only.

| Model | 512 | 1k | 2k |
| --- | ---: | ---: | ---: |
| `gemini-2.5-flash-image` | 1.0 | 2.0 | 4.0 |
| `gemini-3.1-flash-image-preview` | 1.0 | 2.0 | 4.0 |
| `gemini-3-pro-image-preview` | 1.0 | 2.0 | 4.0 |

Legacy `low` / `medium` / `high` Google pricing rows are ignored by runtime logic and deactivated by the release-readiness migration.
