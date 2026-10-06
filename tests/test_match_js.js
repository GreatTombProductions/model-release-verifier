#!/usr/bin/env node
/* test_match_js.js — run the SHARED case table through the JS mirror (parity
 * with tests/test_match.py) and a differential parity check against the
 * Python reference over extra real-ish claims. Zero diffs allowed. */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

// matcher.js resolution: source layout keeps it under frontend/; the deployed
// repo (gh-pages) flattens frontend/ to the site root. Support both so this
// suite runs wherever it ships.
const MATCHER_CANDIDATES = [
  path.join(__dirname, "..", "frontend", "matcher.js"),
  path.join(__dirname, "..", "matcher.js"),
];
const MATCHER_PATH = MATCHER_CANDIDATES.find((p) => fs.existsSync(p));
if (!MATCHER_PATH) {
  console.error("matcher.js not found in either layout:\n  " + MATCHER_CANDIDATES.join("\n  "));
  process.exit(1);
}
const MRV = require(MATCHER_PATH);

const HERE = __dirname;
// Each case names its fixture: "aug" (Aug 2026 pages, default) or "oct" (Oct 2026 pages).
const FIXTURES = { aug: "fixture_index.json", oct: "fixture_index_oct.json" };
const INDEXES = Object.fromEntries(Object.entries(FIXTURES).map(([k, f]) => [k, JSON.parse(fs.readFileSync(path.join(HERE, f), "utf8"))]));
const CASES = JSON.parse(fs.readFileSync(path.join(HERE, "cases.json"), "utf8")).cases;
// Shipped-index checks (source layout: data/generated/; published site: data/).
const LIVE_INDEX = [path.join(HERE, "..", "data", "generated", "index.json"), path.join(HERE, "..", "data", "index.json")].find((p) => fs.existsSync(p));
INDEXES.live = JSON.parse(fs.readFileSync(LIVE_INDEX, "utf8"));
for (const c of JSON.parse(fs.readFileSync(path.join(HERE, "cases_live.json"), "utf8")).cases) CASES.push({ ...c, fixture: "live" });

let failures = 0;

// 1. shared case table through the JS mirror
for (const c of CASES) {
  const got = MRV.verifyClaim(c.claim, INDEXES[c.fixture || "aug"]);
  if (got.verdict === c.verdict) {
    console.log(`OK   [${got.verdict.padStart(13)}] ${c.claim}`);
  } else {
    failures++;
    console.log(`FAIL [${got.verdict.padStart(13)} != ${c.verdict.padStart(13)}] ${c.claim}`);
    console.log(`      -> ${got.reason.slice(0, 200)}`);
  }
}

// 2. differential parity: extra claims through BOTH implementations — the
//    full result objects (verdict/reason/model/vendor/claimed_date/claim_class)
//    must be identical, not just the verdict.
const extra = [
  "Anthropic is retiring claude-opus-4-1 in august 2026",
  "kimi-k3 is out now",
  "DeepSeek deepseek-v4-flash is generally available",
  "gpt-5.6-sol was announced",
  "Claude Sonnet 5 is not yet released",
  "moonshot kimi k2.6 launch",
  "OpenAI o3 preview",
  "deepseek-v4-pro is live",
  "claude-mythos-preview will be retired",
  "GPT-4.1 Nano shuts down October 2026",
  "Claude Fable 5 release date August 2026",
  "Anthropic Claude Haiku 4.5 available now",
  "DeepSeek V4-Flash available",
  "OpenAI chatgpt-4o-latest deprecated in February",
  "kimi k3 was launched on August 1",
  "Anthropic sunset claude-opus-5",
  "claude-opus-4-1-20250805 retired August 5 2026",
  "OpenAI gpt-5.6-terra introduced in 2026",
  "deepseek-v4-pro shipped",
  "Kimi K2.6 discontinued",
].map((claim) => [claim, "aug"]).concat([
  "Claude Opus 5.5 GA",
  "deepseek-flash retired",
  "DeepSeek V4 Flash discontinued",
  "gpt-6-astra preview",
  "Claude Sonnet 4.5 sunset November 2026",
  "GPT-6 Luna, released.",
  "DeepSeek-V4.1-Flash is live",
  "OpenAI released GPT-6.1 Sol Pro",
  "Anthropic sunset claude-opus-4 in June 2026",
  "claude-sonnet-5-5 retired on 2027-01-01",
].map((claim) => [claim, "oct"])).concat(
  CASES.filter((c) => c.fixture === "live").map((c) => [c.claim, "live"]));

const PY = path.join(HERE, "..", "pipeline");
for (const [claim, fx] of extra) {
  const pyOut = execSync(
    `python3 -c "import json,sys; sys.path.insert(0,'${PY}'); from match import verify_claim; print(json.dumps(verify_claim(sys.argv[1], json.load(open('${fx === "live" ? LIVE_INDEX : path.join(HERE, FIXTURES[fx])}')))))" "${claim}"`,
    { encoding: "utf8", cwd: HERE }
  ).trim();
  let py;
  try { py = JSON.parse(pyOut); } catch (e) { console.log("PY PARSE FAIL:", pyOut.slice(0, 200)); failures++; continue; }
  const js = MRV.verifyClaim(claim, INDEXES[fx]);
  const pick = (o) => JSON.stringify([
    o.verdict, o.reason, o.model, o.vendor, o.claimed_date, o.claim_class,
  ]);
  if (pick(py) === pick(js)) {
    console.log(`OK   parity [${js.verdict.padStart(13)}] ${claim}`);
  } else {
    failures++;
    console.log(`FAIL parity ${claim}`);
    console.log(`      py: ${pick(py).slice(0, 220)}`);
    console.log(`      js: ${pick(js).slice(0, 220)}`);
  }
}

if (failures) {
  console.log(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log(`\nALL PASS (${CASES.length} cases + ${extra.length} parity checks)`);
