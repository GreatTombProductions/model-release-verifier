// app.js — Model Release Status Verifier UI.
// Loads data/index.json (built by pipeline/build.py), verifies claims via
// matcher.js (the JS mirror of pipeline/match.py — coupled pair), and
// renders verdict cards with quoted evidence.

(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  const DATA_BASE = "data/";
  let INDEX = null;

  const VERDICT_LABELS = {
    confirmed: "Confirmed",
    contradicted: "Contradicted",
    partial: "Partially confirmed",
    unverifiable: "Unverifiable",
  };

  function esc(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtDate(iso) {
    if (!iso) return "";
    if (iso.length === 7) {
      const [y, m] = iso.split("-");
      const names = ["", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"];
      return `${names[parseInt(m, 10)]} ${y}`;
    }
    const d = new Date(iso + "T00:00:00Z");
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
  }

  function fmtFetched(ts) {
    if (!ts) return "";
    // YYYYMMDDTHHMMSSZ -> readable
    const m = ts.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
    if (!m) return ts;
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]));
    return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) +
      " " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";
  }

  function renderCaptures() {
    const list = $("#captures");
    if (!list || !INDEX || !INDEX.coverage) return;
    const rows = [];
    for (const c of Object.values(INDEX.coverage)) {
      for (const sfc of Object.values(c.surfaces || {})) {
        rows.push(`<li><a href="${esc(sfc.url)}" target="_blank" rel="noopener">${esc(sfc.url.replace(/^https:\/\//, ""))}</a> · ` +
          (sfc.status === "ok"
            ? `fetched ${esc(fmtFetched(sfc.fetched_at))} · <code>${esc(sfc.raw_sha256 || "no receipt")}</code>`
            : `unreachable at build time`) + `</li>`);
      }
    }
    list.innerHTML = rows.join("");
  }

  function renderCoverage() {
    const tbody = $("#coverage-table tbody");
    if (!INDEX || !INDEX.coverage) return;
    const order = ["Anthropic", "OpenAI", "DeepSeek", "Kimi"];
    tbody.innerHTML = order.map((v) => {
      const c = INDEX.coverage[v];
      if (!c) return "";
      return `<tr>
        <td>${esc(v)}</td>
        <td>${c.surfaces && Object.keys(c.surfaces).some((k) => k.endsWith("_models") || k.endsWith("_docs") || k.endsWith("_platform"))
          ? '<span class="check">✓ published</span>' : '<span class="x">✗ none</span>'}</td>
        <td>${c.has_lifecycle_page ? '<span class="check">✓ published</span>'
          : (INDEX.models || []).some((m) => m.vendor === v && m.kind === "lifecycle")
            ? '<span class="gap">✗ none — only retirements stated in its docs can be checked (documented gap)</span>'
            : '<span class="gap">✗ none — retirement claims unverifiable (documented gap)</span>'}</td>
      </tr>`;
    }).join("");
  }

  function renderResult(v) {
    const box = $("#result");
    box.classList.remove("hidden");
    const label = VERDICT_LABELS[v.verdict] || v.verdict;

    let meta = "";
    if (v.model) meta += `<div class="meta-row"><span class="meta-k">Model</span><span class="meta-v">${esc(v.model)}</span></div>`;
    if (v.vendor) meta += `<div class="meta-row"><span class="meta-k">Vendor</span><span class="meta-v">${esc(v.vendor)}</span></div>`;
    if (v.claimed_date) meta += `<div class="meta-row"><span class="meta-k">Claimed date</span><span class="meta-v">${esc(fmtDate(v.claimed_date))}</span></div>`;
    if (v.date_unparsed) meta += `<div class="meta-row"><span class="meta-k">Date</span><span class="meta-v">mentioned without a year — not checked</span></div>`;
    if (v.claim_class) meta += `<div class="meta-row"><span class="meta-k">Claim class</span><span class="meta-v">${esc(v.claim_class)}</span></div>`;

    const matches = (v.matches || []).slice(0, 4);
    const evidence = matches.map((m) => `
      <div class="evidence">
        <div class="ev-head">
          <span class="badge ${esc(m.status)}">${esc(m.status)}</span>
          <code>${esc(m.model_id)}</code>
          <span class="ev-vendor">${esc(m.vendor)}</span>
        </div>
        <p class="ev-quote">“${esc(m.evidence || "")}”</p>
        <p class="ev-meta">
          ${m.retirement ? `Retirement: ${esc(fmtDate(m.retirement))} · ` : ""}
          ${m.announced ? `Announced: ${esc(fmtDate(m.announced))} · ` : ""}
          ${m.replacement ? `Replacement: ${esc(m.replacement)} · ` : ""}
          ${m.retirement_not_before ? `Retirement not before: ${esc(fmtDate(m.retirement_not_before))} · ` : ""}
          Fetched: ${esc(fmtFetched(m.fetched_at))} ·
          <a href="${esc(m.source_url)}" target="_blank" rel="noopener">source page ↗</a>
        </p>
      </div>`).join("");

    box.innerHTML = `
      <div class="verdict-card ${esc(v.verdict)}">
        <div class="verdict-head">
          <span class="badge ${esc(v.verdict)}">${esc(label)}</span>
        </div>
        <p class="reason">${esc(v.reason)}</p>
        ${meta ? `<div class="meta">${meta}</div>` : ""}
        ${evidence ? `<h4>Vendor evidence</h4>${evidence}` : ""}
      </div>`;
  }

  function verify() {
    const claim = $("#claim").value.trim();
    if (!claim) return;
    if (!INDEX) return;
    renderResult(window.MRVMatcher.verifyClaim(claim, INDEX));
  }

  async function init() {
    try {
      const resp = await fetch(`${DATA_BASE}index.json`);
      INDEX = await resp.json();
      $("#built-date").textContent = INDEX.built || "—";
      renderCoverage();
      renderCaptures();
    } catch (e) {
      $("#result").classList.remove("hidden");
      $("#result").innerHTML = `<div class="verdict-card unverifiable"><p class="reason">Could not load the model index — is the data/ folder deployed with the site?</p></div>`;
      return;
    }

    $("#verify-btn").addEventListener("click", verify);
    $("#claim").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) verify();
    });
    document.querySelectorAll(".ex").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        $("#claim").value = a.dataset.claim;
        verify();
        $("#verifier").scrollIntoView({ behavior: "smooth" });
      });
    });
  }

  init();
})();
