/* matcher.js — Claim parsing + verdict logic. JS mirror of pipeline/match.py.
 *
 * COUPLED PAIR: any change to match.py MUST be mirrored here and verified by
 * tests/test_match_js.js (same case table + differential parity, zero diffs).
 *
 * Usable in Node (module.exports) and the browser (window.MRVMatcher).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.MRVMatcher = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // --- model-name -> vendor patterns (order matters) -----------------------
  const MODEL_PATTERNS = [
    ["Anthropic", /claude-[a-z0-9][a-z0-9.-]*/i],
    ["DeepSeek", /deepseek-[a-z0-9][a-z0-9.-]*/i],
    ["Kimi", /(?:kimi|moonshot)-[a-z0-9][a-z0-9.-]*/i],
    ["OpenAI", /chatgpt-[a-z0-9][a-z0-9.-]*/i],
    ["OpenAI", /(?:gpt-oss|gpt)-[a-z0-9][a-z0-9.-]*/i],
    ["OpenAI", /\bo[0-9]+(?:-[a-z0-9]+)?\b/i],
  ];

  const VENDOR_KEYWORDS = {
    Anthropic: ["anthropic", "claude"],
    OpenAI: ["openai", "chatgpt", "gpt"],
    DeepSeek: ["deepseek"],
    Kimi: ["kimi", "moonshot"],
  };

  const RETIREMENT_KEYWORDS = [
    "retired", "retirement", "retiring", "will retire", "to retire",
    "deprecated", "deprecation", "shut down", "shutdown", "sunset",
    "discontinued", "decommissioned", "end of life", "eol",
  ];
  const AVAILABILITY_KEYWORDS = [
    "released", "release", "launched", "launch", "available", "availability",
    "generally available", " ga", "ga ", "preview", "announced", "announce",
    "shipped", "introduced", "unveiled", "out now", "is live", "goes live",
  ];

  const DATE_PATTERNS = [
    /\b(\d{4})-(\d{2})-(\d{2})\b/,
    /\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b/,
    /\b(\d{1,2})\s+([A-Za-z]{3,9}),?\s+(\d{4})\b/,
    /\b([A-Za-z]{3,9})\s+(\d{4})\b/,
  ];

  const MONTHS = {
    january: 1, february: 2, march: 3, april: 4, may: 5, june: 6,
    july: 7, august: 8, september: 9, october: 10, november: 11, december: 12,
  };
  const MONTHS_ABBR = {
    jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
    jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
  };

  function monthNum(name) {
    const low = (name || "").toLowerCase();
    return MONTHS[low] || MONTHS_ABBR[low.slice(0, 3)] || null;
  }

  function normalizeModelId(mid) {
    return (mid || "").trim().toLowerCase().replace(/\s+/g, "-").replace(/-+/g, "-");
  }

  function baseId(mid) {
    /* strip -YYYY-MM-DD then -YYYYMMDD snapshot suffixes */
    mid = mid.replace(/-\d{4}-\d{2}-\d{2}$/, "");
    mid = mid.replace(/-\d{8}$/, "");
    return mid;
  }

  function normalizeFamily(name) {
    return (name || "").trim().toLowerCase().replace(/\s+/g, " ");
  }

  function vendorKeywordIn(claimLow, vendor) {
    return (VENDOR_KEYWORDS[vendor] || []).some((k) => claimLow.includes(k));
  }

  function identify(claim, index) {
    /* returns {model, vendor, method, span} | {model:null,...} */
    for (const [vendor, pat] of MODEL_PATTERNS) {
      const m = pat.exec(claim);
      if (m) return { model: normalizeModelId(m[0]), vendor, method: "id", span: [m.index, m.index + m[0].length] };
    }
    const claimLow = claim.toLowerCase();
    for (const [aliasKey, pair] of Object.entries(index.short_aliases || {})) {
      const pos = claimLow.indexOf(aliasKey);
      if (pos >= 0 && vendorKeywordIn(claimLow, pair[0])) {
        return { model: pair[1], vendor: pair[0], method: "alias", span: [pos, pos + aliasKey.length] };
      }
    }
    const families = {};
    for (const rec of index.models || []) {
      const fam = normalizeFamily(rec.family);
      if (fam) families[fam] = rec.vendor;
    }
    const sorted = Object.keys(families).sort((a, b) => b.length - a.length);
    for (const fam of sorted) {
      let pos = claimLow.indexOf(fam);
      while (pos >= 0) {
        const after = claimLow.slice(pos + fam.length, pos + fam.length + 1);
        if (!after || !/[a-z0-9.]/.test(after)) {
          return { model: fam, vendor: families[fam], method: "family", span: [pos, pos + fam.length] };
        }
        pos = claimLow.indexOf(fam, pos + 1);
      }
    }
    return { model: null, vendor: null, method: "none", span: null };
  }

  function extractDate(claim) {
    let m = DATE_PATTERNS[0].exec(claim);
    if (m) return `${m[1]}-${m[2]}-${m[3]}`;
    m = DATE_PATTERNS[1].exec(claim);
    if (m) {
      const mon = monthNum(m[1]);
      if (mon !== null) return `${m[3]}-${String(mon).padStart(2, "0")}-${String(parseInt(m[2], 10)).padStart(2, "0")}`;
    }
    m = DATE_PATTERNS[2].exec(claim);
    if (m) {
      const mon = monthNum(m[2]);
      if (mon !== null) return `${m[3]}-${String(mon).padStart(2, "0")}-${String(parseInt(m[1], 10)).padStart(2, "0")}`;
    }
    m = DATE_PATTERNS[3].exec(claim);
    if (m) {
      const mon = monthNum(m[1]);
      if (mon !== null) return `${m[2]}-${String(mon).padStart(2, "0")}`;
    }
    return null;
  }

  function dateMentionedWithoutYear(claim) {
    const low = claim.toLowerCase();
    const allMonths = Object.keys(MONTHS).concat(Object.keys(MONTHS_ABBR));
    const hasYear = /\b\d{4}\b/.test(low);
    if (hasYear) return false;
    return allMonths.some((mon) => new RegExp("\\b" + mon + "\\b", "i").test(low));
  }

  function claimClass(claim) {
    const low = claim.toLowerCase();
    if (RETIREMENT_KEYWORDS.some((k) => low.includes(k))) return "retirement";
    if (AVAILABILITY_KEYWORDS.some((k) => low.includes(k))) return "availability";
    return "unknown";
  }

  function sameMonthIso(a, b) {
    return a.slice(0, 7) === b.slice(0, 7);
  }

  function sameDayIso(a, b) {
    if (a.length === 10 && b.length === 10) return a === b;
    return a.slice(0, 7) === b.slice(0, 7);
  }

  function collectMatches(vendor, modelRef, index) {
    const ref = normalizeModelId(modelRef);
    const fam = normalizeFamily(modelRef);
    const out = [];
    for (const rec of index.models || []) {
      if (rec.vendor !== vendor) continue;
      const rid = normalizeModelId(rec.model_id);
      if (rid === ref || baseId(rid) === baseId(ref)) {
        out.push(rec);
        continue;
      }
      if (normalizeFamily(rec.family) === fam) out.push(rec);
    }
    return out;
  }

  function verifyClaim(claim, index) {
    const id = identify(claim, index);
    let dateClaim = claim;
    if (id.span) dateClaim = claim.slice(0, id.span[0]) + " " + claim.slice(id.span[1]);
    const dateIso = extractDate(dateClaim);
    const dateUnparsed = dateMentionedWithoutYear(dateClaim) && dateIso === null;
    const cls = claimClass(claim);

    if (id.model === null) {
      return {
        verdict: "unverifiable",
        reason: "No recognizable model name in the claim. Supported families: claude-* (Anthropic), gpt-*/o* (OpenAI), deepseek-*, kimi-*/moonshot-* (Kimi).",
        model: null, vendor: null, claimed_date: dateIso, date_unparsed: dateUnparsed,
        claim_class: cls, matches: [],
      };
    }

    const vendor = id.vendor;
    const modelRef = id.model;
    const coverage = (index.coverage || {})[vendor] || {};
    const hasLifecycle = !!coverage.has_lifecycle_page;
    const matches = collectMatches(vendor, modelRef, index);

    const base = {
      model: modelRef, vendor, claimed_date: dateIso, date_unparsed: dateUnparsed,
      claim_class: cls, matches,
    };

    if (matches.length === 0) {
      if (cls === "retirement" && !hasLifecycle) {
        return { ...base, verdict: "unverifiable", reason: (
          `${vendor} publishes no model-deprecation page, so retirement ` +
          `claims cannot be checked against a vendor surface. This is a ` +
          `documented gap, not a clean bill of health.`
        ) };
      }
      if (cls === "retirement") {
        return { ...base, verdict: "contradicted", reason: (
          `${vendor}'s deprecation page lists no retirement or deprecation ` +
          `for ${modelRef}. If a retirement was announced, the vendor ` +
          `has not published it.`
        ) };
      }
      if (cls === "availability" && hasLifecycle) {
        return { ...base, verdict: "contradicted", reason: (
          `${modelRef} does not appear on ${vendor}'s current-model page ` +
          `or its deprecation page. No vendor surface shows this model ` +
          `as available.`
        ) };
      }
      if (cls === "availability" && !hasLifecycle) {
        return { ...base, verdict: "contradicted", reason: (
          `${modelRef} does not appear on ${vendor}'s current-model ` +
          `documentation. (Note: ${vendor} publishes no lifecycle page, ` +
          `so retirement history cannot be checked.)`
        ) };
      }
      return { ...base, verdict: "unverifiable", reason: (
        `No vendor record found for ${modelRef}, and the claim class ` +
        `could not be determined.`
      ) };
    }

    if (cls === "availability") {
      const current = matches.filter((r) => r.kind === "current");
      const retired = matches.filter((r) => r.status === "retired");
      const deprecated = matches.filter((r) => r.status === "deprecated");
      if (current.length) {
        if (dateIso) {
          return { ...base, verdict: "partial", reason: (
            `${modelRef} is listed as a current ${vendor} model — ` +
            `existence confirmed. The claimed date (${dateIso}) could ` +
            `not be checked: ${vendor} does not publish release dates ` +
            `on its model pages.`
          ) };
        }
        if (dateUnparsed) {
          return { ...base, verdict: "partial", reason: (
            `${modelRef} is listed as a current ${vendor} model — ` +
            `existence confirmed. The claim mentions a date but no ` +
            `year, and ${vendor} does not publish release dates ` +
            `anyway — the date could not be checked.`
          ) };
        }
        return { ...base, verdict: "confirmed", reason: (
          `${modelRef} is listed on ${vendor}'s current-model page.`
        ) };
      }
      if (retired.length) {
        const ret = retired[0].retirement;
        return { ...base, verdict: "partial", reason: (
          `${vendor} records show ${modelRef} was released, but it is ` +
          `now listed as retired` + (ret ? ` (retirement ${ret})` : "") +
          ` — if the claim means 'currently available', it is wrong.`
        ) };
      }
      if (deprecated.length) {
        return { ...base, verdict: "partial", reason: (
          `${modelRef} exists but ${vendor} lists it as deprecated` +
          (deprecated[0].retirement ? `, retirement ${deprecated[0].retirement}` : "") +
          ` — not a current model.`
        ) };
      }
      const active = matches.filter((r) => r.status === "active");
      if (active.length) {
        const ret = active[0].retirement;
        return { ...base, verdict: "confirmed", reason: (
          `${vendor} lists ${modelRef} as active` + (ret ? ` with retirement ${ret}` : "") + `.`
        ) };
      }
      return { ...base, verdict: "unverifiable", reason: (
        `A vendor record exists for ${modelRef} but its status could not ` +
        `be classified.`
      ) };
    }

    if (cls === "retirement") {
      if (!hasLifecycle) {
        return { ...base, verdict: "unverifiable", reason: (
          `${vendor} publishes no model-deprecation page, so retirement ` +
          `claims cannot be checked. (The model may exist — see its ` +
          `current-model listing — but no retirement surface exists.)`
        ) };
      }
      const retired = matches.filter((r) => (r.status === "retired" || r.status === "deprecated") && r.retirement);
      if (retired.length === 0) {
        const deprNodate = matches.filter((r) => r.status === "deprecated" && !r.retirement);
        if (deprNodate.length) {
          if (claim.toLowerCase().includes("deprecat")) {
            return { ...base, verdict: "confirmed", reason: (
              `${vendor} lists ${modelRef} as deprecated` +
              (deprNodate[0].evidence ? ` — ${deprNodate[0].evidence}` : "")
            ) };
          }
          return { ...base, verdict: "contradicted", reason: (
            `${vendor} lists ${modelRef} as deprecated, with no ` +
            `retirement date published — it is not recorded as retired.`
          ) };
        }
        const current = matches.filter((r) => r.kind === "current");
        const boundRecs = matches.filter((r) => r.retirement_not_before);
        if (current.length && boundRecs.length === 0) {
          return { ...base, verdict: "contradicted", reason: (
            `${vendor}'s deprecation page lists no retirement for ` +
            `${modelRef}, and it is still listed as a current model.`
          ) };
        }
        if (boundRecs.length) {
          const bound = boundRecs[0].retirement_not_before;
          if (dateIso && dateIso < bound) {
            return { ...base, verdict: "contradicted", reason: (
              `${vendor} lists ${modelRef} as active with retirement ` +
              `not sooner than ${bound}. A retirement date of ` +
              `${dateIso} contradicts the vendor's own bound.`
            ) };
          }
          return { ...base, verdict: "partial", reason: (
            `${vendor} lists ${modelRef} as active with retirement ` +
            `not sooner than ${bound} — no actual retirement date is ` +
            `published, so the claim cannot be confirmed.`
          ) };
        }
        return { ...base, verdict: "contradicted", reason: (
          `${vendor}'s deprecation page lists no retirement for ${modelRef}.`
        ) };
      }
      const r = retired[0];
      if (dateIso) {
        if (sameDayIso(dateIso, r.retirement)) {
          return { ...base, verdict: "confirmed", reason: (
            `${vendor} records ${modelRef} retirement as ` +
            `${r.retirement} — matches the claimed date.`
          ) };
        }
        if (sameMonthIso(dateIso, r.retirement)) {
          return { ...base, verdict: "partial", reason: (
            `${vendor} records ${modelRef} retirement as ` +
            `${r.retirement}; the claim says ${dateIso}. Same ` +
            `month, different day — likely rounding in the claim.`
          ) };
        }
        return { ...base, verdict: "contradicted", reason: (
          `${vendor} records ${modelRef} retirement as ${r.retirement}, ` +
          `but the claim says ${dateIso}.`
        ) };
      }
      return { ...base, verdict: "confirmed", reason: (
        `${vendor} records ${modelRef} as ` +
        `${r.status} (retirement ${r.retirement}).`
      ) };
    }

    const current = matches.filter((r) => r.kind === "current");
    const retired = matches.filter((r) => r.status === "retired");
    if (current.length) {
      return { ...base, verdict: "partial", reason: (
        `${modelRef} is listed as a current ${vendor} model, but the ` +
        `claim's class (released? retired?) could not be determined — ` +
        `restate it with a status word.`
      ) };
    }
    if (retired.length) {
      return { ...base, verdict: "partial", reason: (
        `${vendor} lists ${modelRef} as retired (${retired[0].retirement}), ` +
        `but the claim's class could not be determined.`
      ) };
    }
    return { ...base, verdict: "unverifiable", reason: (
      `A vendor record exists for ${modelRef} but neither the record's ` +
      `status nor the claim's class could be determined.`
    ) };
  }

  return { verifyClaim, identify, extractDate, claimClass, normalizeModelId };
});
