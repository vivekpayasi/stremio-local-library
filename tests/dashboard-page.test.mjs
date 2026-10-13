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
    window: { addEventListener() {} },
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

const IH = 'b'.repeat(40);
const ep = (n, name, done, extra = {}) => ({ season: 1, episode: n, label: `S01E0${n}`, name, file: `Chernobyl - S01E0${n}.mkv`, ih: IH, idx: n,
  length: 6.4 * GB, done, busy: false, job: null, release: 'Accid', tag: null, next: false, ...extra });
const JOB = { ih: IH, idx: 4, label: 'S01E04', state: 'downloading', done: 0.34, length: 6.4 * GB, message: null, mbps: 21.3, eta_s: 1500 };
export const SERIES = {
  key: 'tt7366338', title: 'Chernobyl', kind: 'series', year: '2019', poster: 'P', backdrop: 'BG', bytes: 3.8 * GB, last_used: 1,
  in_use: false, ihs: [IH], meta_line: '2019 · Accid · 3.8 GB here', summary: 'S1 · 1 episode here', sub: null, seasons: [1], releases: [],
  episodes: [ep(1, '1:23:45', 0), ep(2, 'Please Remain Calm', 0), ep(3, 'Open Wide, O Earth', 1), ep(4, 'The Happiness of All Mankind', 0.34, { job: JOB }),
    ep(5, 'Vichnaya Pamyat', 0, { next: true })],
};
export const MOVIE = {
  key: 'tt37287335', title: 'Obsession', kind: 'movie', year: '2026', poster: null, backdrop: null, bytes: 2.8 * GB, last_used: 1, in_use: false,
  ihs: ['c'.repeat(40), 'd'.repeat(40)], meta_line: '2026 · Movie · 2 releases · 2.8 GB here', summary: 'Movie · 2 releases · 2.8 GB', sub: null,
  seasons: [], episodes: [], releases: [
    { ih: 'c'.repeat(40), idx: 0, name: 'Obsession...-BEN.THE.MEN', group: 'BEN.THE.MEN', chips: ['4K', 'WEB', 'MP4'],
      verdict: { symbol: '✓✓', reason: null, summary: ['MP4', 'E-AC3', 'DV P8'] }, length: 21.7 * GB, done: 0.11, bytes: 2.4 * GB,
      last_used: 1e9 - 172800, in_use: false, busy: false, job: null },
    { ih: 'd'.repeat(40), idx: 4, name: 'Obsession (2026) (... r00t)', group: 'r00t', chips: ['MKV'],
      verdict: { symbol: '✗', reason: "TrueHD: TV can't decode", summary: ['MKV', 'TrueHD'] }, length: 9 * GB, done: 0.02, bytes: 448 * 1024 * 1024,
      last_used: 1e9 - 259200, in_use: false, busy: false, job: null }],
};
const UP = { key: 'tt7366338', ih: IH, idx: 4, title: 'Chernobyl', season: 1, episode: 4, label: 'S01E04', name: 'The Happiness of All Mankind',
  length: 6.4 * GB, done: 0, job: null, state: 'not_here', poster: 'P', backdrop: 'BG' };

test('Up next offers the episode, then shows its download, then that it is ready', () => {
  const { ctx } = page();
  let h = ctx.upNextHTML(UP);
  assert.ok(h.includes('Chernobyl · Season 1') && h.includes('E4 · The Happiness of All Mankind') && h.includes('Download for later'));
  assert.match(h, /data-get="b{40}" data-idx="4"/);
  h = ctx.upNextHTML({ ...UP, state: 'downloading', job: JOB, done: 0.34 });
  assert.ok(h.includes('Cancel') && h.includes('34%') && h.includes('about 25 min left'));
  assert.ok(ctx.upNextHTML({ ...UP, state: 'ready', done: 1 }).includes('ready to watch'));
  assert.equal(ctx.upNextHTML(null), '');
});

const SHELF = { used: 4.4 * GB, budget: 15 * GB, free: 22 * GB, idle_bytes: 3.8 * GB, idle_count: 1,
  items: [{ key: 'tt7366338', title: 'Chernobyl', kind: 'series', poster: 'P', summary: 'S1 · 1 episode here', sub: 'E4 downloading 34%', last_used: 1, bytes: 1 }] };

test('the shelf: one tile per title, the space, and clearing what is not in use', () => {
  const { ctx } = page();
  assert.ok(ctx.shelfHead(SHELF).includes('4.4 GB of 15 GB'));
  assert.ok(ctx.shelfTiles(SHELF).includes('data-open="tt7366338"'));
  assert.ok(ctx.shelfFoot(SHELF).includes('Delete everything not in use (3.8 GB)'));
  assert.ok(ctx.tileText(SHELF.items[0]).includes('E4 downloading 34%'));
  assert.ok(ctx.shelfTiles({ ...SHELF, items: [] }).includes('Nothing is stored'));
  assert.ok(ctx.shelfHead(null).includes('Reading the cache'));
});

test('the shelf\'s posters are not rewritten when only the cache figures change', () => {
  const { ctx, els } = page();
  ctx.render({ ...STATE, shelf: SHELF });
  assert.ok(els['shelf-tiles'].innerHTML.includes('src="P"'));
  els['shelf-tiles'].innerHTML = 'untouched';
  ctx.render({ ...STATE, shelf: { ...SHELF, used: 4.6 * GB, free: 21.8 * GB, idle_bytes: 4 * GB } });
  assert.equal(els['shelf-tiles'].innerHTML, 'untouched', 'posters stay, and the row keeps its scroll');
  assert.ok(els['shelf-head'].innerHTML.includes('4.6 GB of 15 GB') && els['shelf-foot'].innerHTML.includes('(4 GB)'));
});

test('a series sheet: every episode with its one action, the next one filled', () => {
  const { ctx } = page();
  const h = ctx.sheetBody(SERIES, 1);
  assert.equal((h.match(/class="ep[ "]/g) || []).length, 5);
  assert.match(h, /class="solid" type="button" data-get="b{40}" data-idx="5"/);
  assert.match(h, /data-delfile="b{40}" data-idx="3"/);
  assert.match(h, /data-cancel="b{40}" data-idx="4"/);
  assert.ok(h.includes('Not on the Mac · 6.4 GB') && h.includes('Open Wide, O Earth'));
  assert.ok(ctx.sheetHead(SERIES).includes('2019 · Accid · 3.8 GB here'));
});

test('the season picker appears only with more than one season', () => {
  const { ctx } = page();
  assert.ok(!ctx.seasonsHTML(SERIES, 1).includes('<select'));
  const two = { ...SERIES, seasons: [1, 2] };
  const h = ctx.seasonsHTML(two, 2);
  assert.match(h, /<select id="season-pick"/);
  assert.match(h, /<option value="2" selected>Season 2<\/option>/);
});

test('a movie sheet: one card per release with its verdict and how much is here', () => {
  const { ctx } = page();
  ctx.render(STATE);   // the cache budget (15 GB) comes from the state
  const h = ctx.sheetBody(MOVIE, null);
  assert.ok(h.includes('BEN.THE.MEN') && h.includes('Plays here — checked in the file: MP4, E-AC3, DV P8'));
  assert.ok(h.includes('Won&#39;t play here — TrueHD: TV can&#39;t decode'));
  assert.ok(h.includes('2.4 GB of 21.7 GB here (11%)'));
  assert.ok(h.includes('Too big to download fully'), 'the 21.7 GB release does not fit a 15 GB cache');
  assert.ok(h.includes('Download the rest'), 'the 9 GB one does');
  assert.ok(h.includes('Delete all 2 releases'));
  assert.match(h, /data-del="c{40}" data-key="c{40}:0" data-label="BEN.THE.MEN" data-size="2.4 GB"/, 'Delete removes the release from the Mac');
});

test('a sheet for a title that is gone says so', () => {
  const { ctx, els } = page();
  ctx.render({ ...STATE, titles: { tt7366338: SERIES } });
  ctx.location.hash = '#title=tt0000000';
  ctx.renderSheet();
  assert.equal(els.sheet.hidden, false);
  assert.ok(els['sheet-head'].innerHTML.includes('no longer on this Mac'));
  ctx.location.hash = '#title=tt7366338';
  ctx.renderSheet();
  assert.ok(els['sheet-head'].innerHTML.includes('Chernobyl'));
  assert.ok(els['sheet-body'].innerHTML.includes('Vichnaya Pamyat'));
  ctx.location.hash = '';
  ctx.renderSheet();
  assert.equal(els.sheet.hidden, true);
});

const CHAIN = [
  { id: 'swarm', name: 'Swarm', level: 'good', line: '31 Mbps in, the file needs 15 · 38 peers, 12 sending', reason: null, advice: null, details: [['Download', '31.0 Mbps']] },
  { id: 'mac', name: 'This Mac', level: 'good', line: 'up 16 h 6 min · cache 4.4 GB of 15.0 GB · 22.0 GB free', reason: null, advice: null, details: [],
    services: [{ name: 'Torrent server', level: 'good', text: '3% CPU, 80 MB' }, { name: 'Library', level: 'good', text: '0 movies, 0 shows' },
      { name: 'Stream Advisor', level: 'good', text: 'Rating streams for TCL' }, { name: 'Stremio account', level: 'warn', text: 'Signed out' }] },
  { id: 'wifi', name: 'Wi-Fi', level: 'warn', line: 'ping to the TV 120 ms', reason: "The TV's Wi-Fi is slow to answer", advice: 'Restart the TV', details: [['Ping to the TV', '120 ms (usually 4)']] },
  { id: 'tv', name: 'The TV', level: 'idle', line: 'Asleep — not answering', reason: null, advice: null, details: [] },
];

test('the chain: four steps, the weak one marked with its reason, services inside This Mac', () => {
  const { ctx } = page();
  const h = ctx.chainHTML(CHAIN);
  assert.equal((h.match(/class="step /g) || []).length, 4);
  assert.match(h, /step-warn[^]*The TV&#39;s Wi-Fi is slow to answer/);
  for (const s of ['Torrent server', 'Library', 'Stream Advisor', 'Stremio account', 'Asleep — not answering']) assert.ok(h.includes(s), s);
  assert.ok(!h.includes('120 ms (usually 4)'), 'details stay folded');
  ctx.openSteps.add('wifi');
  assert.ok(ctx.chainHTML(CHAIN).includes('120 ms (usually 4)'));
  assert.equal(ctx.chainHTML(null), '');
});

test('Recent opens by itself while something plays, with the buffer and the events', () => {
  const { ctx } = page();
  const r = { live: true, unit: 's', history: [[1, 250, 30, 480], [3, 255, 30, 485], [5, 260, 31, 490]],
    events: [{ kind: 'stall', text: 'Stalled for 6.0 s at 31:02', cause: 'swarm', at: 1e9 - 120, pos: 0.5, recent: false }] };
  const h = ctx.recentHTML(r, 1e9);
  assert.match(h, /<details class="recent" id="recent-d" open>/);
  assert.ok(h.includes('Stalled for 6.0 s at 31:02') && h.includes('The torrent sent pieces too slowly') && h.includes('2 min ago'));
  assert.ok(h.includes('<svg') && h.includes('id="spark-tip"'), 'the buffer sparkline, with its tooltip');
  assert.ok(!ctx.recentHTML({ ...r, live: false }, 1e9).includes(' open>'));
  assert.equal(ctx.recentHTML(null, 1e9), '');
});

test('the log lines, newest first', () => {
  const { ctx } = page();
  const h = ctx.logHTML([{ time: '18:04:56', text: 'first' }, { time: '18:05:00', text: '<b>second</b>' }]);
  assert.ok(h.indexOf('second') < h.indexOf('first'));
  assert.ok(h.includes('&lt;b&gt;second'));
});

test('the chain\'s steps and the folded sections are big enough to tap', () => {
  const css = /<style>([\s\S]*)<\/style>/.exec(html)[1];
  const minHeight = (selector) => {
    const rule = new RegExp(`(?:^|\\n)${selector.replace(/[.[\]]/g, '\\$&')} \\{([^}]*)\\}`).exec(css);
    assert.ok(rule, selector);
    return Number((/min-height: (\d+)px/.exec(rule[1]) || [])[1] || 0);
  };
  assert.ok(minHeight('.step-head') >= 40, '.step-head');
  assert.ok(minHeight('details summary') >= 40, 'details summary');
});
