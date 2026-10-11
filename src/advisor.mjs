// Stream Advisor — rates every Torrentio stream 1 to 5 for how well it will play on this TV, right in
// Stremio's stream list.
//
// Stremio shows each addon's streams as the addon sends them, and one addon cannot annotate another's.
// So this is an addon of its own, served by the Local Library process at /advisor/manifest.json: the TV
// asks it for streams, it fetches the user's Torrentio list, rates each stream (rating.mjs) and returns
// the very same streams — same infoHash, fileIdx, sources and hints, so playback is untouched — with
// stars in the name and the reasons above the description.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { readAuthKey } from './auth.mjs';
import { api } from './autoheal.mjs';
import { adviseStreams, defaultProfile, mergeProfile, starsIn } from './rating.mjs';

const DIR = path.join(os.homedir(), '.stremio-local-addon');
export const ADVISOR_FILES = {
  config: path.join(DIR, 'advisor.json'),             // upstreams, sort order, background probing
  profile: path.join(DIR, 'device-profile.json'),     // optional overrides of the shipped TV profile
  facts: path.join(DIR, 'stream-facts.json'),         // written by the torrent server after it probes a file
  titles: path.join(DIR, 'stream-titles.json'),       // info hash -> title, for the dashboard's "now playing"
};

const ADDON_ID = 'org.local.advisor';
const TORRENTIO_ID = 'com.stremio.torrentio.addon';
const CINEMETA = 'https://v3-cinemeta.strem.io';
// Torrentio sits behind Cloudflare, which turns away clients that don't look like a browser.
const BROWSER_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
  + 'Chrome/129.0 Safari/537.36';

const DEFAULTS = {
  enabled: true,
  upstreams: [],              // [{ name, url }]: stream addons whose lists are rated, merged in this order
  sort: 'original',           // 'rating': best-rated first (stable within a rating)
  // Probe the top few streams nobody has played yet, through the torrent server (a few MB each), so
  // their ratings are verified next time the list is opened. Off by default: it joins those swarms.
  probe: { enabled: false, top: 3, server: 'http://127.0.0.1:11472' },
  cacheMinutes: 10,
};

export const advisorManifest = () => ({
  id: ADDON_ID,
  version: '1.0.0',
  name: 'Stream Advisor',
  description: 'Torrentio streams rated 1–5 ⭐ for how well they play on this TV (codecs, Dolby Vision, '
    + 'bitrate, peers), with the reasons in each stream\'s description.',
  resources: ['stream'],
  types: ['movie', 'series'],
  idPrefixes: ['tt'],
  catalogs: [],
  behaviorHints: { configurable: false, adult: false },
});

/** Minutes from Cinemeta's runtime text: "109 min", "2h 28min", "1h", "45m". */
export function parseRuntime(s) {
  const t = String(s ?? '');
  const h = /(\d+)\s*h/i.exec(t);
  const m = /(\d+)\s*m/i.exec(t);
  const min = (h ? Number(h[1]) * 60 : 0) + (m ? Number(m[1]) : 0);
  return min > 0 ? min : null;
}

// A configured Torrentio URL carries the user's options, which can include a debrid API key.
const safeUrl = (u) => { try { return new URL(u).origin; } catch { return '(invalid url)'; } };

// A JSON file, re-read only when it changes.
function watchedJson(file) {
  let mtime = null, value = null;
  return () => {
    let st;
    try { st = fs.statSync(file); } catch { mtime = null; value = null; return null; }
    if (st.mtimeMs !== mtime) {
      try { value = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { value = null; }
      mtime = st.mtimeMs;
    }
    return value;
  };
}

async function accountCollection() {
  const authKey = readAuthKey();
  if (!authKey) throw new Error('cannot read the Stremio authKey (signed out?)');
  return (await api('addonCollectionGet', authKey, { update: true })).addons;
}

export function createAdvisor({
  files = ADVISOR_FILES, fetch = globalThis.fetch, log = console.log, getCollection = accountCollection,
} = {}) {
  const readConfig = watchedJson(files.config);
  const readProfile = watchedJson(files.profile);
  const readFacts = watchedJson(files.facts);
  const upstreamCache = new Map();   // url -> { at, streams }
  const metas = new Map();           // imdb id -> { runtime, name, year, poster }
  const warned = new Set();   // things said once, not on every retry
  const warnOnce = (msg) => { if (!warned.has(msg)) { warned.add(msg); log(msg); } };

  const config = () => {
    const c = readConfig() ?? {};
    return { ...DEFAULTS, ...c, probe: { ...DEFAULTS.probe, ...c.probe } };
  };

  let base = null;
  const profile = () => {
    base ??= defaultProfile();
    const over = readProfile();
    return over ? mergeProfile(base, over) : base;
  };

  /** Find the Torrentio install in the account and remember it, so it keeps working once removed there. */
  async function start({ onReady }) {
    const cfg = config();
    if (!cfg.enabled) {
      warnOnce('advisor: disabled in advisor.json');
      return false;
    }
    if (!cfg.upstreams.length) {
      const addons = await getCollection();
      const tio = addons.find((a) => a?.manifest?.id === TORRENTIO_ID);
      if (!tio) {
        warnOnce('advisor: no Torrentio in the Stremio account and none in advisor.json — Stream Advisor stays off');
        return false;
      }
      const saved = { ...(readConfig() ?? {}), upstreams: [{ name: tio.manifest.name ?? 'Torrentio', url: tio.transportUrl }] };
      fs.mkdirSync(path.dirname(files.config), { recursive: true });
      fs.writeFileSync(files.config, `${JSON.stringify({ ...DEFAULTS, ...saved }, null, 2)}\n`, { mode: 0o600 });
      fs.chmodSync(files.config, 0o600);
      log(`advisor: found ${tio.manifest.name ?? 'Torrentio'} in the account (${safeUrl(tio.transportUrl)}); saved to advisor.json`);
    }
    const ups = config().upstreams.map((u) => `${u.name} (${safeUrl(u.url)})`).join(', ');
    log(`advisor: rating streams from ${ups} for "${profile().name}"`);
    onReady();
    return true;
  }

  async function upstreamStreams(u, type, id, maxAgeMs) {
    const url = `${u.url.replace(/\/manifest\.json$/, '')}/stream/${type}/${id}.json`;
    const hit = upstreamCache.get(url);
    if (hit && Date.now() - hit.at < maxAgeMs) return hit.streams;
    const res = await fetch(url, {
      headers: { 'User-Agent': BROWSER_UA, Accept: 'application/json' },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const streams = (await res.json())?.streams;
    if (!Array.isArray(streams)) throw new Error('no stream list in the answer');
    upstreamCache.set(url, { at: Date.now(), streams });
    if (upstreamCache.size > 300) upstreamCache.delete(upstreamCache.keys().next().value);
    return streams;
  }

  async function metaFor(type, imdb) {
    if (metas.has(imdb)) return metas.get(imdb);
    let meta = null;
    try {
      const res = await fetch(`${CINEMETA}/meta/${type}/${imdb}.json`, { signal: AbortSignal.timeout(6000) });
      const m = res.ok ? (await res.json())?.meta : null;
      if (m) meta = { runtime: parseRuntime(m.runtime), name: m.name ?? null, year: m.releaseInfo ?? m.year ?? null, poster: m.poster ?? null };
    } catch { /* offline: no bitrate estimate or title this time */ }
    if (meta) metas.set(imdb, meta);
    return meta;
  }

  // The torrent server only sees info hashes; this lets the dashboard name what is playing. Kept to the
  // most recent few thousand torrents.
  function recordTitles(streams, type, id, meta) {
    if (!files.titles) return;
    const [imdb, season, episode] = id.split(':');
    let titles = {};
    try { titles = JSON.parse(fs.readFileSync(files.titles, 'utf8')); } catch { /* first time */ }
    const entry = {
      imdb, type, name: meta?.name ?? null, year: meta?.year ?? null,
      season: season ? Number(season) : null, episode: episode ? Number(episode) : null,
      poster: meta?.poster ?? null, at: Date.now(),
    };
    for (const s of streams) {
      if (!s?.infoHash) continue;
      const ih = String(s.infoHash).toLowerCase();
      titles[ih] = entry;
      // A season pack's hash comes up for every episode; the file index says which one this stream is.
      if (Number.isInteger(s.fileIdx)) titles[`${ih}:${s.fileIdx}`] = entry;
    }
    const keep = Object.entries(titles).sort((a, b) => b[1].at - a[1].at).slice(0, 6000);
    try {
      fs.writeFileSync(`${files.titles}.tmp`, JSON.stringify(Object.fromEntries(keep)));
      fs.renameSync(`${files.titles}.tmp`, files.titles);
    } catch (e) { log(`advisor: could not save stream titles: ${e.message}`); }
  }

  const factKey = (s) => `${String(s.infoHash).toLowerCase()}:${s.fileIdx ?? -1}`;

  // Facts for each stream. Without a fileIdx the TV plays whichever file the server picks, which is the
  // one probed — so when exactly one file of that torrent is known, it is that one.
  function factsLookup(facts) {
    const byHash = new Map();
    for (const k of Object.keys(facts)) {
      const [ih, idx] = k.split(':');
      if (idx !== '-1') byHash.set(ih, [...(byHash.get(ih) ?? []), k]);
    }
    return (s) => {
      if (!s?.infoHash) return null;
      const exact = facts[factKey(s)];
      if (exact || Number.isInteger(s.fileIdx)) return exact ?? null;
      const keys = byHash.get(String(s.infoHash).toLowerCase()) ?? [];
      return keys.length === 1 ? facts[keys[0]] : null;
    };
  }

  // Fire and forget: the torrent server queues these and probes one at a time.
  function probeTop(streams, factsFor, cfg) {
    const unknown = streams.filter((s) => s?.infoHash && Number.isInteger(s.fileIdx) && !factsFor(s)).slice(0, cfg.probe.top);
    for (const s of unknown) {
      const name = encodeURIComponent(s.behaviorHints?.filename ?? '');
      fetch(`${cfg.probe.server}/torrentd/probe/${s.infoHash.toLowerCase()}/${s.fileIdx}?name=${name}`,
        { method: 'POST', signal: AbortSignal.timeout(5000) }).catch(() => {});
    }
  }

  async function streamsFor(type, id) {
    const cfg = config();
    const t0 = Date.now();
    const imdb = id.split(':')[0];
    const [lists, meta] = await Promise.all([
      Promise.all(cfg.upstreams.map((u) => upstreamStreams(u, type, id, cfg.cacheMinutes * 60_000)
        .then((streams) => ({ u, streams }), (error) => ({ u, error })))),
      metaFor(type, imdb),
    ]);
    const runtime = meta?.runtime ?? null;
    const all = lists.flatMap((l) => l.streams ?? []);
    // An error can quote the request URL, and with it the user's options.
    const why = (l) => String(l.error?.message).replaceAll(l.u.url.replace(/\/manifest\.json$/, ''), safeUrl(l.u.url));
    for (const l of lists) if (l.error) log(`advisor: ${l.u.name} (${safeUrl(l.u.url)}) failed for ${type} ${id}: ${why(l)}`);

    if (!all.length && lists.some((l) => l.error)) {
      const l = lists.find((x) => x.error);
      return [{
        name: '⚠ Advisor',
        title: `${l.u.name} didn't answer (${why(l)}) — try again in a minute`,
        externalUrl: safeUrl(l.u.url),
      }];
    }

    const factsFor = factsLookup(readFacts()?.facts ?? {});
    const out = adviseStreams(all, { profile: profile(), runtimeMin: runtime, factsFor, sort: cfg.sort });
    if (cfg.probe.enabled) probeTop(all, factsFor, cfg);
    recordTitles(all, type, id, meta);

    const hist = [1, 2, 3, 4, 5].map((n) => out.filter((s) => starsIn(s) === n).length);
    const verified = all.filter((s) => factsFor(s)).length;
    log(`advisor: ${type} ${id} — ${all.length} stream(s) in ${Date.now() - t0} ms; ⭐1–5: ${hist.join('/')}`
      + `${verified ? `; ${verified} verified` : ''}${runtime ? '' : '; no runtime, no bitrate estimates'}`);
    return out;
  }

  /** The response body for an /advisor/... path, or null when the path is not one of ours. */
  async function route(p) {
    if (p === '/advisor/manifest.json') return advisorManifest();
    const m = /^\/advisor\/stream\/(movie|series)\/([^/]+)\.json$/.exec(p);
    if (m) return { streams: config().enabled ? await streamsFor(m[1], m[2]) : [] };
    if (p === '/advisor/health') {
      const cfg = config();
      return {
        ok: true, profile: profile().name, sort: cfg.sort, probe: cfg.probe,
        upstreams: cfg.upstreams.map((u) => ({ name: u.name, origin: safeUrl(u.url) })),
        facts: Object.keys(readFacts()?.facts ?? {}).length,
      };
    }
    return null;
  }

  // Read by autoheal before each reconcile, so turning the advisor off also stops it re-adding itself.
  const active = () => config().enabled !== false && config().upstreams.length > 0;

  return { start, route, active, manifest: advisorManifest };
}
