# Model Release Status Verifier

Check an AI model release claim against the vendor's own pages: confirmed, contradicted, partial or unverifiable, with the evidence quoted.

AI news aggregators and SEO trackers invent or inflate model release identities. This tool checks a pasted claim against the model lists and deprecation schedules that **the vendors publish themselves**:

- **Anthropic**: model overview + model deprecations page
- **OpenAI**: models page + deprecations page
- **DeepSeek**: API docs model table (no deprecation page; the table's footnote states which legacy names are retired)
- **Kimi / Moonshot**: platform page model links (no deprecation page)

## Verdicts

- **confirmed**: the vendor's own page supports the claim
- **contradicted**: the vendor's page shows a different state, or no such model
- **partial**: one part confirmed, another uncheckable (existence yes; release dates are almost never published)
- **unverifiable**: no vendor surface exists for this claim class (a documented gap, never assumed clean)

## How it works

The vendor pages are fetched at build time and parsed into a static model index (`data/index.json`). All matching happens in your browser: nothing leaves the page and nothing is collected. Dates inside model ids (`gpt-4o-2024-05-13`) are kept apart from dates claimed about the model, and a name written with spaces ("GPT-6.1 Sol") resolves to its id when the vendor lists that id.

The index is a snapshot. The page shows the build date and, for each vendor page, when it was fetched and the SHA-256 of the exact bytes used. Those captures are in `data/raw/`, each with a `.sha256` receipt. A vendor page that changed after the build date is not reflected until the next build.

## Repository layout

- `index.html`, `app.js`, `matcher.js`, `style.css`: the site
- `data/index.json`: the model index the site reads
- `data/raw/`: the vendor pages as captured, with receipts
- `pipeline/`: fetch (`pipeline/fetch.py`), parse (`pipeline/parser.py`), build (`pipeline/build.py`), and the Python reference matcher (`pipeline/match.py`)
- `tests/`: parser tests, the shared case table run through both matchers, differential parity, and a browser smoke test

## Verification

From the repository root:

- `python3 tests/test_parser.py`: parsers against fixtures for both page layouts seen so far, fail-closed checks on unknown wording, and every capture in `data/raw/`
- `python3 tests/test_match.py` and `node tests/test_match_js.js`: the shared case table (including checks against the shipped `data/index.json`) through the Python reference and the JavaScript matcher, plus a differential parity pass comparing the two implementations' full results
- `SMOKE_SITE=. python3 tests/run_browser_smoke.py`: serves this directory and drives the page in headless Chromium (needs Node with Playwright)

## Rebuilding

`python3 pipeline/fetch.py` captures the six pages into `data/raw/`. `python3 pipeline/build.py` then verifies each receipt and parses the newest captures. It stops if anything the vendors state differs from the reviewed pin in `pipeline/expected_semantics.json`. Inspect the parsed records, then rerun with `--accept-source-change`. In this repository, run it as `python3 pipeline/build.py --out data/index.json` so the site serves the result.

## License

MIT
