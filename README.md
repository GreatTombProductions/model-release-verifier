# Model Release Status Verifier

Check an AI model release claim against the vendor's own pages — confirmed, contradicted, or unverifiable, with the evidence quoted.

AI news aggregators and SEO trackers invent or inflate model release identities. This tool verifies a pasted claim against the model lists and deprecation schedules that **the vendors publish themselves**:

- **Anthropic** — model overview + deprecations page
- **OpenAI** — models page + deprecations page
- **DeepSeek** — API docs model list (no deprecation page — documented gap)
- **Kimi / Moonshot** — platform page model list (no deprecation page — documented gap)

## Verdicts

- **confirmed** — the vendor's own page supports the claim
- **contradicted** — the vendor's page shows a different state, or no such model
- **partial** — one part confirmed, another uncheckable (existence yes; release dates are almost never published)
- **unverifiable** — no vendor surface exists for this claim class (a documented gap, never assumed clean)

## How it works

The vendor pages are fetched at build time and parsed into a static model index (see `pipeline/`). All matching happens in your browser — no requests leave the page, no data is collected. Dates in model ids (`gpt-4o-2024-05-13`) are distinguished from dates claimed about the model.

## Data freshness

The index is rebuilt by running `python3 pipeline/build.py` (fetch included). The build date and per-source fetch timestamps are shown on the site.

## License

MIT
