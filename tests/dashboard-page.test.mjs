// Run with:  node --test   (from the repo root)
// The dashboard page's script, run against a stand-in for the browser: its render functions get states shaped like
// /ui/api/state and their HTML is checked.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('../src/dashboard.html', import.meta.url), 'utf8');
const script = /<script>([\s\S]*)<\/script>/.exec(html)[1];

export function page({ fetch = async () => { throw new Error('offline'); } } = {}) {
  const classList = () => {
    const s = new Set();
    return { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c), toggle: (c, on) => ((on ?? !s.has(c)) ? s.add(c) : s.delete(c)) };
  };
  const els = {};
  const el = (id) => (els[id] ||= { id, innerHTML: '', className: '', hidden: false, open: false, classList: classList(),
    addEventListener() {}, focus() {}, querySelector: () => null, querySelectorAll: () => [] });
  const ctx = {
    document: { getElementById: el, hidden: true, body: { classList: classList() }, addEventListener() {}, querySelector: () => null,
      querySelectorAll: () => [], contains: () => true, activeElement: null },
    location: { hash: '' }, history: { state: null, pushState() {}, back() {} },
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false }), requestAnimationFrame: (f) => f(), setTimeout: () => 0, clearTimeout() {},
    fetch, confirm: () => false, console, Intl, Date, Math, Number, String, JSON, Map, Set, Object, Array, Promise,
    encodeURIComponent, decodeURIComponent,
  };
  vm.createContext(ctx);
  vm.runInContext(script, ctx);
  return { ctx, els };
}

const GB = 1024 ** 3;
export const GLANCE = {
  ih: 'b'.repeat(40), idx: 2, title: 'Chernobyl', subtitle: 'Season 1 · Episode 3 · Open Wide, O Earth', poster: 'P', backdrop: 'BG',
  level: 'good', word: 'Smooth', ahead: '4 min 20 s', complete: false, position: 0.384, ahead_end: 0.45,
  strip: Array(54).fill(1).concat(Array(66).fill(0)), seeks: [0.38], elapsed: '25:10', remaining: '−40:20', duration_s: 3930,
  stats: '31 Mbps · file needs 15 · 38 peers', chips: ['MKV', 'HEVC', '4K'], verified: true,
};
export const STATE = {
  now: 1e9, phone_url: null, problem: null, glance: GLANCE, idle_text: 'Nothing is playing', up_next: null,
  shelf: { used: 4.4 * GB, budget: 15 * GB, free: 22 * GB, items: [], idle_bytes: 0, idle_count: 0 }, titles: {}, chain: [],
  recent: null, log: [], prefetch: [],
};

test('the glance says what plays and how much is ready', () => {
  const { ctx } = page();
  const h = ctx.glanceHTML(GLANCE, 'Nothing is playing');
  for (const s of ['Smooth', 'Chernobyl', 'Open Wide, O Earth', '4 min 20 s', 'ready ahead of you', '25:10', '−40:20', '31 Mbps']) {
    assert.ok(h.includes(s), s);
  }
  assert.match(h, /aria-label="Chernobyl: up to 25:10; 4 min 20 s downloaded ahead"/);
  assert.ok(ctx.glanceHTML({ ...GLANCE, complete: true }, '').includes('The rest is on this Mac'));
  assert.ok(ctx.glanceHTML(null, 'Nothing is playing').includes('Nothing is playing'));
});

test('a problem goes to the banner with its advice', () => {
  const { ctx } = page();
  const h = ctx.problemHTML({ level: 'warn', title: "The TV's Wi-Fi is slow to answer", advice: 'Restarting the TV usually clears it.', step: 'wifi' });
  assert.match(h, /banner-warn/);
  assert.ok(h.includes('The TV&#39;s Wi-Fi is slow to answer') && h.includes('Restarting the TV'));
  assert.equal(ctx.problemHTML(null), '');
});

test('text from torrents is escaped everywhere', () => {
  const { ctx } = page();
  const evil = '<img src=x onerror=alert(1)>"&';
  const h = ctx.glanceHTML({ ...GLANCE, title: evil, subtitle: evil, chips: [evil] }, '') + ctx.problemHTML({ level: 'bad', title: evil, advice: evil });
  assert.ok(!h.includes('<img src=x'));
  assert.ok(h.includes('&lt;img src=x onerror=alert(1)&gt;&quot;&amp;'));
});

test('render fills the regions and writes each only when it changed', () => {
  const { ctx, els } = page();
  ctx.render(STATE);
  assert.ok(els.glance.innerHTML.includes('Chernobyl'));
  assert.ok(els['glance-bg'].innerHTML.includes('src="BG"'));
  const before = els['glance-bg'].innerHTML;
  els['glance-bg'].innerHTML = 'untouched';
  ctx.render({ ...STATE, glance: { ...GLANCE, ahead: '4 min 22 s' } });
  assert.equal(els['glance-bg'].innerHTML, 'untouched', 'the backdrop is not rewritten when only the figures change');
  assert.ok(els.glance.innerHTML.includes('4 min 22 s'));
  assert.ok(before);
});

test('a failed poll keeps the page and says the server is unreachable', async () => {
  const { ctx, els } = page();
  ctx.render(STATE);
  await ctx.poll();
  assert.ok(els.glance.innerHTML.includes('Chernobyl'), 'the last render stays');
  assert.match(els.problem.innerHTML, /Can't reach the torrent server/);
});
