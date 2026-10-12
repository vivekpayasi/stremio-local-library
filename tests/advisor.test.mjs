// Run with:  node --test   (from the repo root)

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { createAdvisor, parseRuntime } from '../src/advisor.mjs';

const IH_GOOD = '1'.repeat(40);
const IH_BAD = '2'.repeat(40);
const TORRENTIO = 'https://torrentio.strem.fun/manifest.json';

const STREAMS = [
  {
    name: 'Torrentio\n4k DV | HDR',
    title: 'Movie.2026.2160p.UHD.BluRay.x265.10bit.DV.HDR.TrueHD.Atmos.7.1-XYZ\n👤 266 💾 20.03 GB ⚙️ 1337x',
    infoHash: IH_BAD, fileIdx: 4,
    behaviorHints: { bingeGroup: 'torrentio|4k', filename: 'Movie.2026.2160p.UHD.BluRay.x265.10bit.DV.HDR.TrueHD.Atmos.7.1-XYZ.mkv' },
  },
  {
    name: 'Torrentio\n4k DV | HDR10',
    title: 'Movie 2026 WEB-DL 2160p x265 DV HDR10 DUAL 5.1\n👤 173 💾 19.86 GB ⚙️ BluDV',
    infoHash: IH_GOOD, fileIdx: 0,
    behaviorHints: { bingeGroup: 'torrentio|4k', filename: 'Movie 2026 WEB-DL 2160p x265 DV HDR10 DUAL 5.1.mkv' },
  },
];

const FACT_GOOD = {
  source: 'ffprobe', container: 'mkv', bitrateMbps: 26.1,
  video: { codec: 'hevc', width: 3840, height: 2160, bitDepth: 10, transfer: 'smpte2084', dv: { profile: 8, level: 6, el: false } },
  audio: [{ codec: 'eac3', channels: 6, lang: 'eng', default: true }],
  subtitles: [], mp4SubsHidden: 0,
};

function setup({ config, facts = {}, upstream = () => ({ streams: STREAMS }), collection } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'advisor-test-'));
  const files = {
    config: path.join(dir, 'advisor.json'),
    profile: path.join(dir, 'device-profile.json'),
    facts: path.join(dir, 'stream-facts.json'),
    titles: path.join(dir, 'stream-titles.json'),
  };
  if (config) fs.writeFileSync(files.config, JSON.stringify(config));
  fs.writeFileSync(files.facts, JSON.stringify({ version: 1, facts }));
  const requests = [];
  const fetch = async (url, opts = {}) => {
    requests.push({ url: String(url), opts });
    const u = String(url);
    const reply = (status, body) => ({ ok: status === 200, status, json: async () => body });
    if (u.startsWith('https://v3-cinemeta.strem.io/meta/')) {
      return reply(200, { meta: { runtime: '109 min', name: 'Chernobyl', releaseInfo: '2019', poster: 'https://images.metahub.space/poster/medium/tt7366338/img' } });
    }
    if (u.startsWith('https://torrentio.strem.fun/') && u.includes('/stream/')) {
      const out = upstream(u);
      if (out instanceof Error) throw out;
      return typeof out === 'number' ? reply(out, {}) : reply(200, out);
    }
    if (u.includes('/torrentd/probe/')) return reply(200, { queued: true });
    throw new Error(`unexpected fetch ${u}`);
  };
  const logs = [];
  const advisor = createAdvisor({
    files, fetch, log: (...a) => logs.push(a.join(' ')),
    getCollection: collection ?? (async () => [{ manifest: { id: 'com.stremio.torrentio.addon', name: 'Torrentio' }, transportUrl: TORRENTIO }]),
  });
  return { advisor, files, requests, logs, cleanup: () => fs.rmSync(dir, { recursive: true, force: true }) };
}

const CONFIGURED = { upstreams: [{ name: 'Torrentio', url: TORRENTIO }] };

test('parseRuntime reads the forms Cinemeta uses', () => {
  assert.equal(parseRuntime('109 min'), 109);
  assert.equal(parseRuntime('2h 28min'), 148);
  assert.equal(parseRuntime('1h'), 60);
  assert.equal(parseRuntime('45m'), 45);
  assert.equal(parseRuntime(''), null);
  assert.equal(parseRuntime(undefined), null);
});

test('the manifest is a stream-only addon for IMDb ids', async () => {
  const { advisor, cleanup } = setup({ config: CONFIGURED });
  const m = await advisor.route('/advisor/manifest.json');
  assert.equal(m.id, 'org.local.advisor');
  assert.deepEqual(m.resources, ['stream']);
  assert.deepEqual(m.idPrefixes, ['tt']);
  assert.deepEqual(m.catalogs, []);
  cleanup();
});

test('streams come back rated, unchanged where it matters, with verified facts used', async () => {
  const { advisor, requests, cleanup } = setup({ config: CONFIGURED, facts: { [`${IH_GOOD}:0`]: FACT_GOOD } });
  const { streams } = await advisor.route('/advisor/stream/movie/tt37287335.json');
  assert.equal(streams.length, 2);
  assert.match(streams[0].name, /^⭐\n/u);
  assert.match(streams[1].name, /^⭐{5}\n/u);
  assert.match(streams[1].name, /^⭐{5}\n✓✓ 4k DV \| HDR10$/u);
  assert.match(streams[1].title, /^👤 173 💾 19\.86 GB/u);
  for (const [i, s] of streams.entries()) {
    assert.equal(s.infoHash, STREAMS[i].infoHash);
    assert.equal(s.fileIdx, STREAMS[i].fileIdx);
    assert.deepEqual(s.behaviorHints, STREAMS[i].behaviorHints);
  }
  const tio = requests.find((r) => r.url.startsWith('https://torrentio'));
  assert.equal(tio.url, 'https://torrentio.strem.fun/stream/movie/tt37287335.json');
  assert.match(tio.opts.headers['User-Agent'], /Mozilla/);
  cleanup();
});

test('series ids are passed through and use the show runtime', async () => {
  const { advisor, requests, cleanup } = setup({ config: CONFIGURED });
  await advisor.route('/advisor/stream/series/tt7366338:1:1.json');
  assert.ok(requests.some((r) => r.url === 'https://torrentio.strem.fun/stream/series/tt7366338:1:1.json'));
  assert.ok(requests.some((r) => r.url === 'https://v3-cinemeta.strem.io/meta/series/tt7366338.json'));
  cleanup();
});

test('upstream answers are cached for a while', async () => {
  let calls = 0;
  const { advisor, cleanup } = setup({ config: CONFIGURED, upstream: () => { calls += 1; return { streams: STREAMS }; } });
  await advisor.route('/advisor/stream/movie/tt1.json');
  await advisor.route('/advisor/stream/movie/tt1.json');
  assert.equal(calls, 1);
  cleanup();
});

test('an upstream failure shows one explanatory entry instead of an empty list', async () => {
  const { advisor, cleanup } = setup({ config: CONFIGURED, upstream: () => 503 });
  const { streams } = await advisor.route('/advisor/stream/movie/tt1.json');
  assert.equal(streams.length, 1);
  assert.match(streams[0].title, /Torrentio.*503/);
  assert.ok(streams[0].externalUrl);
  cleanup();
});

test('the Torrentio URL is discovered from the account once and saved', async () => {
  const { advisor, files, cleanup } = setup();
  let ready = 0;
  assert.equal(await advisor.start({ onReady: () => { ready += 1; } }), true);
  assert.equal(ready, 1);
  const saved = JSON.parse(fs.readFileSync(files.config, 'utf8'));
  assert.deepEqual(saved.upstreams, [{ name: 'Torrentio', url: TORRENTIO }]);
  assert.equal(fs.statSync(files.config).mode & 0o777, 0o600, 'may hold a debrid key: owner-only');
  cleanup();
});

test('with no Torrentio in the account the advisor stays off', async () => {
  const { advisor, logs, cleanup } = setup({ collection: async () => [] });
  let ready = 0;
  assert.equal(await advisor.start({ onReady: () => { ready += 1; } }), false);
  assert.equal(ready, 0);
  assert.ok(logs.some((l) => /no Torrentio/.test(l)));
  cleanup();
});

test('a configured Torrentio URL with a debrid key never reaches the log or the TV', async () => {
  const secret = 'https://torrentio.strem.fun/realdebrid=SECRETKEY123/manifest.json';
  for (const upstream of [() => 500, (u) => new TypeError(`fetch failed for ${u}`)]) {
    const { advisor, logs, cleanup } = setup({ config: { upstreams: [{ name: 'Torrentio', url: secret }] }, upstream });
    await advisor.start({ onReady: () => {} });
    const { streams } = await advisor.route('/advisor/stream/movie/tt1.json');
    assert.ok(logs.length > 1);
    assert.ok(logs.every((l) => !l.includes('SECRETKEY123')), logs.join('\n'));
    assert.ok(!JSON.stringify(streams).includes('SECRETKEY123'), JSON.stringify(streams));
    cleanup();
  }
});

test('a device profile override file changes ratings', async () => {
  const { advisor, files, cleanup } = setup({ config: CONFIGURED });
  fs.writeFileSync(files.profile, JSON.stringify({ audio: { truehd: null } }));
  const { streams } = await advisor.route('/advisor/stream/movie/tt1.json');
  assert.match(streams[0].name, /^⭐{5}\n/u);
  cleanup();
});

test('sort: rating puts the best stream first', async () => {
  const { advisor, cleanup } = setup({ config: { ...CONFIGURED, sort: 'rating' } });
  const { streams } = await advisor.route('/advisor/stream/movie/tt1.json');
  assert.equal(streams[0].infoHash, IH_GOOD);
  cleanup();
});

test('background probing asks the torrent server about the top unknown streams only when enabled', async () => {
  const off = setup({ config: CONFIGURED });
  await off.advisor.route('/advisor/stream/movie/tt1.json');
  assert.ok(!off.requests.some((r) => r.url.includes('/torrentd/probe/')));
  off.cleanup();

  const on = setup({ config: { ...CONFIGURED, probe: { enabled: true, top: 5 } }, facts: { [`${IH_GOOD}:0`]: FACT_GOOD } });
  await on.advisor.route('/advisor/stream/movie/tt1.json');
  await new Promise((r) => setTimeout(r, 20));
  const probes = on.requests.filter((r) => r.url.includes('/torrentd/probe/')).map((r) => r.url);
  assert.equal(probes.length, 1, 'the stream with known facts is skipped');
  assert.match(probes[0], new RegExp(`^http://127\\.0\\.0\\.1:11472/torrentd/probe/${IH_BAD}/4\\?name=`));
  on.cleanup();
});

test('a stream without fileIdx uses the facts of its torrent when only one file was probed', async () => {
  const noIdx = STREAMS.map((s) => ({ ...s, fileIdx: undefined }));
  const one = setup({ config: CONFIGURED, upstream: () => ({ streams: noIdx }), facts: { [`${IH_GOOD}:0`]: FACT_GOOD } });
  const { streams } = await one.advisor.route('/advisor/stream/movie/tt1.json');
  assert.match(streams[1].name, /\n✓✓ /);
  one.cleanup();

  const two = setup({ config: CONFIGURED, upstream: () => ({ streams: noIdx }),
    facts: { [`${IH_GOOD}:0`]: FACT_GOOD, [`${IH_GOOD}:3`]: FACT_GOOD } });
  const r2 = await two.advisor.route('/advisor/stream/movie/tt1.json');
  assert.doesNotMatch(r2.streams[1].name, /✓✓/, 'ambiguous: several files of that torrent are known');
  two.cleanup();
});

test('background probing skips streams without a file index', async () => {
  const noIdx = STREAMS.map((s) => ({ ...s, fileIdx: undefined }));
  const { advisor, requests, cleanup } = setup({ config: { ...CONFIGURED, probe: { enabled: true, top: 5 } }, upstream: () => ({ streams: noIdx }) });
  await advisor.route('/advisor/stream/movie/tt1.json');
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(requests.filter((r) => r.url.includes('/torrentd/probe/')).length, 0);
  cleanup();
});

test('an upstream answer without a streams list counts as a failure', async () => {
  const { advisor, cleanup } = setup({ config: CONFIGURED, upstream: () => ({ streams: 'nope' }) });
  const { streams } = await advisor.route('/advisor/stream/movie/tt1.json');
  assert.equal(streams.length, 1);
  assert.match(streams[0].name, /Advisor/);
  cleanup();
});

test('enabled: false serves no streams and reports itself inactive', async () => {
  const { advisor, cleanup } = setup({ config: { ...CONFIGURED, enabled: false } });
  assert.deepEqual(await advisor.route('/advisor/stream/movie/tt1.json'), { streams: [] });
  assert.equal(advisor.active(), false);
  cleanup();
});

test('which title each torrent belongs to is recorded for the dashboard', async () => {
  const { advisor, files, cleanup } = setup({ config: CONFIGURED });
  await advisor.route('/advisor/stream/series/tt7366338:1:2.json');
  const titles = JSON.parse(fs.readFileSync(files.titles, 'utf8'));
  assert.deepEqual(titles[IH_GOOD], {
    imdb: 'tt7366338', type: 'series', name: 'Chernobyl', year: '2019', season: 1, episode: 2,
    poster: 'https://images.metahub.space/poster/medium/tt7366338/img', at: titles[IH_GOOD].at,
  });
  assert.equal(typeof titles[IH_GOOD].at, 'number');
  assert.equal(titles[`${IH_GOOD}:0`].episode, 2, 'per file too: a season pack shares its hash across episodes');
  assert.equal(titles[`${IH_BAD}:4`].episode, 2);
  cleanup();
});

test('unknown paths are not ours', async () => {
  const { advisor, cleanup } = setup({ config: CONFIGURED });
  assert.equal(await advisor.route('/advisor/catalog/movie/x.json'), null);
  cleanup();
});
