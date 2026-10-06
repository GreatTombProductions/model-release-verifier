'use strict';
// Drives the page the way a reader would: example links and typed claims,
// checking the rendered verdict card, the coverage table and the capture receipts.
const assert = require('assert');
const { chromium } = require('playwright');

(async () => {
  const base = process.env.SMOKE_BASE;
  const origin = new URL(base).origin;
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  const foreign = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (new URL(r.url()).origin !== origin) foreign.push(r.url()); });
  const verdict = async claim => {
    await page.fill('#claim', claim);
    await page.click('#verify-btn');
    await page.waitForSelector('#result .verdict-head .badge');
    return {
      badge: (await page.locator('#result .verdict-head .badge').getAttribute('class')).replace('badge', '').trim(),
      reason: await page.locator('#result .reason').textContent(),
    };
  };
  try {
    const response = await page.goto(base + (base.includes('?') ? '' : `?v=${Date.now()}`), { waitUntil: 'networkidle' });
    assert(response.ok(), `index returned ${response.status()}`);
    await page.waitForFunction(() => document.getElementById('built-date').textContent !== '—');
    assert.strictEqual(await page.locator('#coverage-table tbody tr').count(), 4);
    assert((await page.locator('#coverage-table').textContent()).includes('only retirements stated in its docs'));
    const captures = await page.locator('#captures li').allTextContents();
    assert.strictEqual(captures.length, 6, `captures: ${captures.length}`);
    for (const c of captures) assert(/[0-9a-f]{64}/.test(c), `no receipt in: ${c}`);

    await page.click('.ex[data-claim="Anthropic released Claude Opus 5.5"]');
    await page.waitForSelector('#result .verdict-head .badge.confirmed');
    assert((await page.locator('#result').textContent()).includes('claude-opus-5-5'));

    let v = await verdict('DeepSeek retired deepseek-v4-flash');
    assert.strictEqual(v.badge, 'confirmed', v.reason);
    assert((await page.locator('#result .evidence').first().textContent()).includes('served by the DeepSeek-V4.1-Flash model'));
    v = await verdict('Anthropic will retire Claude Sonnet 4.5 on November 30, 2026');
    assert.strictEqual(v.badge, 'confirmed', v.reason);
    v = await verdict('OpenAI released GPT-6.1 Sol.');
    assert.strictEqual(v.badge, 'confirmed', v.reason);
    v = await verdict('DeepSeek released V4-Flash on July 31');
    assert.strictEqual(v.badge, 'partial', v.reason);
    v = await verdict('Google released gemini-3 on May 1');
    assert.strictEqual(v.badge, 'unverifiable', v.reason);

    assert.deepStrictEqual(errors, []);
    assert.deepStrictEqual(foreign, [], `third-party requests: ${foreign}`);
    console.log(`Browser smoke passed (${base}): coverage 4 vendors, 6 capture receipts, example + 5 typed claims, no errors, no third-party requests.`);
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
