#!/usr/bin/env node
// Local Library — a Stremio addon that serves this Mac's media files over HTTPS
// with HTTP Range support, so remote Stremio clients (Android TV) get full seeking.
//
// It reuses the Let's Encrypt cert that Stremio's own streaming server maintains for
// <dashed-lan-ip>.<install-hash>.stremio.rocks, so the TV trusts it with no extra setup.

import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import https from 'node:https';
import crypto from 'node:crypto';
import os from 'node:os';

import { startAutoHeal } from './autoheal.mjs';
import { startCertRenewal } from './cert.mjs';
import { createAdvisor } from './advisor.mjs';

const HOME = os.homedir();

const CONFIG = {
  port: Number(process.env.LOCALLIB_PORT ?? 12471),
  roots: [path.join(HOME, 'Media'), path.join(HOME, 'Movies')],
  // Anything under here is listed exactly as named, with no IMDb/Cinemeta matching.
  otherRoots: [path.join(HOME, 'Media', 'Other')],
  certFile: path.join(HOME, 'Library/Application Support/stremio-server/httpsCert.json'),
  certStatusFile: path.join(HOME, '.stremio-local-addon/cert-renewal.json'),   // the last renewal check, for the dashboard
  stremioIndex: path.join(HOME, 'Library/Application Support/stremio-server/localFiles'),
  stremioMeta: path.join(HOME, 'Library/Application Support/stremio-server/localFilesMeta'),
  cacheFile: path.join(HOME, '.stremio-local-addon/cache.json'),
  ignoreFile: path.join(HOME, '.stremio-local-addon/ignore.txt'),
  cinemeta: 'https://v3-cinemeta.strem.io',
  rescanMs: 10 * 60 * 1000,
  videoExt: new Set(['.mkv', '.mp4', '.avi', '.m4v', '.mov', '.webm']),
  subExt: new Set(['.srt', '.vtt', '.ass', '.sub']),
  minBytes: 50 * 1024 * 1024, // ignore clips/samples smaller than this
};

const MIME = {
  '.mkv': 'video/x-matroska', '.mp4': 'video/mp4', '.m4v': 'video/mp4',
  '.avi': 'video/x-msvideo', '.mov': 'video/quicktime', '.webm': 'video/webm',
  '.srt': 'application/x-subrip', '.vtt': 'text/vtt', '.ass': 'text/plain', '.sub': 'text/plain',
};

const log = (...a) => console.log(new Date().toISOString(), ...a);

// A second addon in this process: Torrentio's streams, rated for the TV. See advisor.mjs.
const advisor = createAdvisor({ log });

// ---------------------------------------------------------------- library index

let LIBRARY = new Map(); // fileId -> item (movies and episodes alike)
let SERIES = new Map();  // showId  -> { id, title, year, meta, episodes: Map<"s:e", item[]> }
let CACHE = {};          // lookup key -> cinemeta result (persisted, so restarts stay fast)

const fileId = (p) => crypto.createHash('sha1').update(p).digest('hex').slice(0, 16);

// Episode numbering, in rough order of how common each form is.
const EP_PATTERNS = [
  /\bS(\d{1,2})[\s._-]*E(\d{1,3})\b/i,
  /\bSeason[\s._-]*(\d{1,2})[\s._-]*Episode[\s._-]*(\d{1,3})\b/i,
  /\b(\d{1,2})x(\d{1,3})\b/,
];

function parseEpisode(filename) {
  for (const re of EP_PATTERNS) {
    const m = re.exec(filename);
    if (m) return { season: Number(m[1]), episode: Number(m[2]), at: m.index };
  }
  return null;
}

function cleanTitle(filename) {
  let n = filename.replace(/\.[^.]+$/, '').replace(/[._]+/g, ' ');
  const ym = n.match(/\b(19\d{2}|20\d{2})\b/);
  const year = ym ? ym[1] : null;
  if (ym) n = n.slice(0, ym.index);
  n = n.replace(
    /\b(1080p|720p|2160p|4k|uhd|bluray|blu ray|brrip|bdrip|web ?dl|webrip|hdrip|dvdrip|x264|x265|h ?264|h ?265|hevc|10bit|hdr|dv|aac|ac3|eac3|ddp?5 1|dts|truehd|atmos|remux|proper|repack|extended|unrated|nf|amzn|yts|rarbg)\b.*$/i,
    '',
  );
  return { title: n.replace(/[[\](){}]/g, ' ').replace(/\s+-\s*$/, '').replace(/\s+/g, ' ').trim(), year };
}

// "Other" files keep their own name — no year stripping, no release-tag trimming.
const lightTitle = (filename) =>
  filename.replace(/\.[^.]+$/, '').replace(/[._]+/g, ' ').replace(/\s+/g, ' ').trim();

const isOther = (p) =>
  CONFIG.otherRoots.some((r) => p === r || p.startsWith(r + path.sep));

// Stremio's own index has already IMDb-matched anything Spotlight found — reuse that work.
async function readStremioMatches() {
  const byPath = new Map();
  try {
    const raw = await fsp.readFile(CONFIG.stremioIndex, 'utf8');
    for (const line of raw.split('\n')) {
      if (!line.trim()) continue;
      let rec;
      try { rec = JSON.parse(line); } catch { continue; }
      for (const f of rec?.entry?.files ?? []) {
        // Only real files on disk — this index also contains .torrent contents.
        if (f.imdb_id && f.path && path.isAbsolute(f.path)) byPath.set(f.path, f.imdb_id);
      }
    }
  } catch { /* index may not exist yet */ }
  return byPath;
}

async function readStremioMeta() {
  const byImdb = new Map();
  try {
    const raw = await fsp.readFile(CONFIG.stremioMeta, 'utf8');
    for (const line of raw.split('\n')) {
      if (!line.trim()) continue;
      let rec;
      try { rec = JSON.parse(line); } catch { continue; }
      const e = rec?.entry;
      if (e?.imdb_id) byImdb.set(e.imdb_id, e);
    }
  } catch { /* fine */ }
  return byImdb;
}

// One line per rule. A bare string matches anywhere in the path, /.../ is a regex, # is a comment.
async function readIgnorePatterns() {
  try {
    const raw = await fsp.readFile(CONFIG.ignoreFile, 'utf8');
    return raw
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('#'))
      .map((l) =>
        l.length > 2 && l.startsWith('/') && l.endsWith('/')
          ? new RegExp(l.slice(1, -1), 'i')
          : new RegExp(l.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'),
      );
  } catch { return []; }
}

async function cinemetaSearch(type, title, year) {
  const key = `${type}|${title}|${year ?? ''}`;
  if (CACHE[key] !== undefined) return CACHE[key];
  let found = null;
  try {
    const url = `${CONFIG.cinemeta}/catalog/${type}/top/search=${encodeURIComponent(title)}.json`;
    const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
    if (res.ok) {
      const { metas = [] } = await res.json();
      found = (year && metas.find((m) => String(m.releaseInfo ?? '').startsWith(year))) || metas[0] || null;
    }
  } catch { /* offline — degrade to the filename title */ }
  CACHE[key] = found;
  saveCache();
  return found;
}

// Full series metadata, including per-episode titles, synopses and stills.
async function cinemetaSeries(imdbId) {
  const key = `seriesmeta|${imdbId}`;
  if (CACHE[key] !== undefined) return CACHE[key];
  let found = null;
  try {
    const res = await fetch(`${CONFIG.cinemeta}/meta/series/${imdbId}.json`, {
      signal: AbortSignal.timeout(10000),
    });
    if (res.ok) found = (await res.json())?.meta ?? null;
  } catch { /* offline */ }
  CACHE[key] = found;
  saveCache();
  return found;
}

let saveTimer = null;
function saveCache() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    fsp.writeFile(CONFIG.cacheFile, JSON.stringify(CACHE)).catch(() => {});
  }, 2000);
}

async function walk(dir, out = []) {
  let entries;
  try { entries = await fsp.readdir(dir, { withFileTypes: true }); } catch { return out; }
  for (const e of entries) {
    if (e.name.startsWith('.')) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) await walk(full, out);
    else out.push(full);
  }
  return out;
}

async function buildLibrary() {
  const [matches, stremioMeta, ignore] = await Promise.all([
    readStremioMatches(), readStremioMeta(), readIgnorePatterns(),
  ]);
  const next = new Map();
  let skipped = 0;

  for (const root of CONFIG.roots) {
    const all = await walk(root);
    const videos = all.filter((f) => CONFIG.videoExt.has(path.extname(f).toLowerCase()));
    const subs = all.filter((f) => CONFIG.subExt.has(path.extname(f).toLowerCase()));

    for (const filePath of videos) {
      if (ignore.some((re) => re.test(filePath))) { skipped += 1; continue; }

      let st;
      try { st = await fsp.stat(filePath); } catch { continue; }

      const base = path.basename(filePath);
      const other = isOther(filePath);

      // The size floor keeps samples and clips out of the matched catalogs; anything
      // deliberately filed under Other is listed whatever its size.
      if (!other && st.size < CONFIG.minBytes) { skipped += 1; continue; }

      const ep = other ? null : parseEpisode(base);
      const kind = other ? 'other' : ep ? 'series' : 'movie';

      // For an episode the show name is whatever precedes the SxxEyy marker.
      const nameSource = ep ? base.slice(0, ep.at) : base;
      const { title, year } = other
        ? { title: lightTitle(base), year: null }
        : cleanTitle(ep ? `${nameSource}.x` : base);

      let imdbId = other ? null : (matches.get(filePath) ?? null);
      let meta = imdbId ? stremioMeta.get(imdbId) : null;

      // Other files are shown exactly as named, so nothing can mis-match them.
      if (!other && !meta && title) {
        const hit = await cinemetaSearch(kind, title, year);
        if (hit) { meta = hit; imdbId = imdbId ?? hit.imdb_id ?? hit.id; }
      }

      // Subtitles sitting next to the video, or sharing its basename.
      const stem = base.replace(/\.[^.]+$/, '');
      const near = subs.filter(
        (s) => path.dirname(s) === path.dirname(filePath) || path.basename(s).startsWith(stem),
      );

      const id = fileId(filePath);
      next.set(id, {
        id, filePath, size: st.size, kind,
        season: ep?.season ?? null,
        episode: ep?.episode ?? null,
        title: meta?.name ?? title ?? base,
        year: meta?.releaseInfo ?? year ?? '',
        imdbId, meta, subs: near,
        filename: base,
      });
    }
  }

  LIBRARY = next;
  await buildSeries();

  const movies = [...LIBRARY.values()].filter((i) => i.kind === 'movie');
  const others = [...LIBRARY.values()].filter((i) => i.kind === 'other');
  const episodes = LIBRARY.size - movies.length - others.length;
  log(`library: ${LIBRARY.size} file(s) — ${movies.length} movie(s), ` +
      `${SERIES.size} show(s) / ${episodes} episode(s), ${others.length} other` +
      (skipped ? `, ${skipped} skipped` : ''));
  for (const it of movies) log(`   movie  ${it.imdbId ?? '(unmatched)'}  ${it.title}`);
  for (const it of others) log(`   other  ${it.title}`);
  for (const s of SERIES.values()) {
    const eps = [...s.episodes.values()].flat().length;
    log(`   series ${s.id}  ${s.title}  (${eps} episode file(s))`);
  }
}

// Group episode files into shows, and pull per-episode metadata once per show.
async function buildSeries() {
  const shows = new Map();
  for (const it of LIBRARY.values()) {
    if (it.kind !== 'series') continue;
    const showId = it.imdbId ?? `locallib_${crypto.createHash('sha1').update(it.title).digest('hex').slice(0, 16)}`;
    it.showId = showId;
    if (!shows.has(showId)) {
      shows.set(showId, { id: showId, title: it.title, year: it.year, meta: it.meta, episodes: new Map() });
    }
    const show = shows.get(showId);
    const key = `${it.season}:${it.episode}`;
    if (!show.episodes.has(key)) show.episodes.set(key, []);
    show.episodes.get(key).push(it);
  }

  for (const show of shows.values()) {
    if (!show.id.startsWith('tt')) continue;
    const full = await cinemetaSeries(show.id);
    if (full) {
      show.meta = full;
      show.title = full.name ?? show.title;
      // id -> episode record, so we can attach real titles and stills below.
      show.videoIndex = new Map((full.videos ?? []).map((v) => [`${v.season}:${v.episode}`, v]));
    }
  }

  // Best-quality file first within each episode.
  for (const show of shows.values()) {
    for (const list of show.episodes.values()) list.sort((a, b) => b.size - a.size);
  }
  SERIES = shows;
}

// ---------------------------------------------------------------- stremio shapes

const MOVIE_CATALOG = 'local-library';
const SERIES_CATALOG = 'local-series';
const OTHER_CATALOG = 'local-other';

const manifest = () => ({
  id: 'org.local.library',
  version: '1.3.0',
  name: 'Local Library',
  description: `Plays media stored on this Mac (${CONFIG.roots.join(', ')}) with full seek support.`,
  logo: 'https://images.metahub.space/logo/medium/tt0405508/img',
  resources: ['catalog', 'meta', 'stream'],
  types: ['movie', 'series'],
  // Clients title a Board row "<catalog name> - <Type>", so the name has to say whose row it is:
  // these read "Local Library - Movie" / "Local Library - Series", not "Movies - Movie".
  catalogs: [
    {
      type: 'movie', id: MOVIE_CATALOG, name: 'Local Library',
      extra: [{ name: 'search', isRequired: false }, { name: 'skip', isRequired: false }],
    },
    {
      type: 'series', id: SERIES_CATALOG, name: 'Local Library',
      extra: [{ name: 'search', isRequired: false }, { name: 'skip', isRequired: false }],
    },
    // Home videos and anything else that should be listed verbatim. Typed 'movie' rather than
    // Stremio's 'other' type so that every client renders it reliably.
    {
      type: 'movie', id: OTHER_CATALOG, name: 'Local Library · Other',
      extra: [{ name: 'search', isRequired: false }, { name: 'skip', isRequired: false }],
    },
  ],
  // Respond to normal IMDb pages so local copies appear as a source on the title's own page,
  // plus our internal ids for anything with no IMDb match.
  idPrefixes: ['tt', 'locallib_'],
  behaviorHints: { configurable: false, adult: false },
});

const publicId = (it) => it.imdbId ?? `locallib_${it.id}`;

// "tt123:1:3" -> { showId: "tt123", season: 1, episode: 3 }
function parseSeriesId(raw) {
  const parts = raw.split(':');
  if (parts.length >= 3) {
    const episode = Number(parts.pop());
    const season = Number(parts.pop());
    if (Number.isFinite(season) && Number.isFinite(episode)) {
      return { showId: parts.join(':'), season, episode };
    }
  }
  return { showId: raw, season: null, episode: null };
}

function baseMeta(m, id, type, name, year) {
  return {
    id, type, name,
    poster: m?.poster ?? undefined,
    background: m?.background ?? undefined,
    logo: m?.logo ?? undefined,
    description: m?.description ?? undefined,
    releaseInfo: m?.releaseInfo ?? year ?? undefined,
    imdbRating: m?.imdbRating ?? undefined,
    runtime: m?.runtime ?? undefined,
    genres: m?.genres ?? m?.genre ?? undefined,
    cast: m?.cast ?? undefined,
    director: m?.director ?? undefined,
  };
}

function movieMeta(it) {
  const meta = baseMeta(it.meta, publicId(it), 'movie', it.title, it.year);
  meta.description ??= `Local file: ${it.filename}`;
  return meta;
}

// Only the episodes actually present on disk — this is a *local* library, so listing
// episodes that cannot play would be misleading.
function seriesMeta(show) {
  const meta = baseMeta(show.meta, show.id, 'series', show.title, show.year);
  meta.description ??= `${[...show.episodes.values()].flat().length} local episode file(s)`;

  const videos = [];
  for (const [key, files] of show.episodes) {
    const [season, episode] = key.split(':').map(Number);
    const info = show.videoIndex?.get(key);
    videos.push({
      id: `${show.id}:${season}:${episode}`,
      title: info?.name ?? `Episode ${episode}`,
      name: info?.name ?? `Episode ${episode}`,
      season, episode,
      released: info?.released ?? info?.firstAired ?? undefined,
      thumbnail: info?.thumbnail ?? undefined,
      overview: info?.overview ?? files[0].filename,
    });
  }
  videos.sort((a, b) => a.season - b.season || a.episode - b.episode);
  meta.videos = videos;
  return meta;
}

function toStream(it, base) {
  const gb = (it.size / 1024 ** 3).toFixed(2);
  const ext = path.extname(it.filePath).toLowerCase();
  const label = it.kind === 'series' && it.season != null
    ? `S${String(it.season).padStart(2, '0')}E${String(it.episode).padStart(2, '0')}`
    : null;
  return {
    url: `${base}/file/${it.id}`,
    name: 'Local Library',
    title: `${label ? `${label} · ` : ''}${it.filename}\n${gb} GB · direct play from Mac`,
    behaviorHints: {
      bingeGroup: it.kind === 'series' ? `locallib-${it.showId}` : `locallib-${it.id}`,
      // Browsers can't play mkv/avi; the Android TV player can. Flagging it lets web
      // clients fall back to transcoding instead of failing silently.
      notWebReady: ext === '.mkv' || ext === '.avi',
    },
    subtitles: it.subs.map((s, i) => ({
      id: `sub-${i}`,
      url: `${base}/sub/${it.id}/${i}`,
      lang: /\.en\./i.test(path.basename(s)) ? 'eng' : 'und',
    })),
  };
}

// Several files can legitimately map to one movie (different rips) — return every match,
// so they become alternative sources on the title's page. 'Other' files live in the same
// namespace: they are addressed by their own locallib_ id and never by an IMDb id.
const findMovies = (wanted) =>
  [...LIBRARY.values()].filter(
    (it) => (it.kind === 'movie' || it.kind === 'other')
      && (it.imdbId === wanted || `locallib_${it.id}` === wanted),
  );

// ---------------------------------------------------------------- http plumbing

function baseUrlFor(req) {
  // Build absolute URLs from the Host the client actually used, so a changed LAN IP
  // (and therefore a different stremio.rocks hostname) still produces working stream URLs.
  const host = req.headers.host ?? `localhost:${CONFIG.port}`;
  return `https://${host}`;
}

function sendJson(res, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(200, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
    'Cache-Control': 'no-store',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

// Range support is the whole point — without 206 responses the TV cannot seek.
function serveFile(req, res, filePath, size) {
  const ext = path.extname(filePath).toLowerCase();
  const type = MIME[ext] ?? 'application/octet-stream';
  const range = req.headers.range;

  const common = {
    'Content-Type': type,
    'Accept-Ranges': 'bytes',
    'Access-Control-Allow-Origin': '*',
    'Cache-Control': 'no-store',
  };

  if (!range) {
    res.writeHead(200, { ...common, 'Content-Length': size });
    if (req.method === 'HEAD') return res.end();
    return fs.createReadStream(filePath).on('error', () => res.destroy()).pipe(res);
  }

  const m = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
  if (!m) {
    res.writeHead(416, { ...common, 'Content-Range': `bytes */${size}` });
    return res.end();
  }

  let start = m[1] === '' ? null : parseInt(m[1], 10);
  let end = m[2] === '' ? null : parseInt(m[2], 10);
  if (start === null) {                    // suffix form: bytes=-N
    const n = end ?? 0;
    start = Math.max(0, size - n);
    end = size - 1;
  } else if (end === null || end >= size) {
    end = size - 1;
  }
  if (Number.isNaN(start) || Number.isNaN(end) || start > end || start >= size) {
    res.writeHead(416, { ...common, 'Content-Range': `bytes */${size}` });
    return res.end();
  }

  res.writeHead(206, {
    ...common,
    'Content-Range': `bytes ${start}-${end}/${size}`,
    'Content-Length': end - start + 1,
  });
  if (req.method === 'HEAD') return res.end();
  fs.createReadStream(filePath, { start, end }).on('error', () => res.destroy()).pipe(res);
}

async function handle(req, res) {
  const url = new URL(req.url, 'https://x');
  const p = decodeURIComponent(url.pathname);
  const base = baseUrlFor(req);

  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
      'Access-Control-Allow-Headers': '*',
    });
    return res.end();
  }

  if (p === '/manifest.json') return sendJson(res, manifest());

  if (p.startsWith('/advisor/')) {
    const body = await advisor.route(p);
    if (body) return sendJson(res, body);
  }

  if (p === '/health') {
    return sendJson(res, {
      ok: true, files: LIBRARY.size, shows: SERIES.size, roots: CONFIG.roots, base,
      movies: [...LIBRARY.values()].filter((i) => i.kind === 'movie')
        .map((i) => ({ id: publicId(i), title: i.title, file: i.filename })),
      other: [...LIBRARY.values()].filter((i) => i.kind === 'other')
        .map((i) => ({ id: publicId(i), title: i.title, file: i.filename })),
      series: [...SERIES.values()].map((s) => ({
        id: s.id, title: s.title,
        episodes: [...s.episodes.keys()].sort((a, b) => {
          const [as, ae] = a.split(':').map(Number); const [bs, be] = b.split(':').map(Number);
          return as - bs || ae - be;
        }),
      })),
    });
  }

  // /catalog/<type>/<id>.json  (optionally .../search=foo.json)
  let m = p.match(/^\/catalog\/(movie|series)\/([^/]+?)(?:\/(.+))?\.json$/);
  if (m) {
    const [, type, id, extraRaw] = m;
    const q = new URLSearchParams(extraRaw ?? '').get('search')?.toLowerCase();

    if (type === 'movie' && (id === MOVIE_CATALOG || id === OTHER_CATALOG)) {
      const kind = id === OTHER_CATALOG ? 'other' : 'movie';
      let items = [...LIBRARY.values()].filter((i) => i.kind === kind);
      if (q) items = items.filter((i) => i.title.toLowerCase().includes(q));
      // One card per title even when several files back it; prefer the entry with real metadata.
      const byId = new Map();
      for (const it of items) {
        const key = publicId(it);
        if (!byId.has(key) || (!byId.get(key).meta && it.meta)) byId.set(key, it);
      }
      const metas = [...byId.values()].sort((a, b) => a.title.localeCompare(b.title)).map(movieMeta);
      return sendJson(res, { metas });
    }

    if (type === 'series' && id === SERIES_CATALOG) {
      let shows = [...SERIES.values()];
      if (q) shows = shows.filter((s) => s.title.toLowerCase().includes(q));
      shows.sort((a, b) => a.title.localeCompare(b.title));
      // Catalog cards do not need the full episode list.
      const metas = shows.map((s) => { const { videos, ...card } = seriesMeta(s); return card; });
      return sendJson(res, { metas });
    }
    return sendJson(res, { metas: [] });
  }

  m = p.match(/^\/meta\/(movie|series)\/(.+)\.json$/);
  if (m) {
    const [, type, id] = m;
    if (type === 'series') {
      const show = SERIES.get(parseSeriesId(id).showId);
      return sendJson(res, show ? { meta: seriesMeta(show) } : { meta: null });
    }
    const hits = findMovies(id);
    const best = hits.find((h) => h.meta) ?? hits[0];
    return sendJson(res, best ? { meta: movieMeta(best) } : { meta: null });
  }

  m = p.match(/^\/stream\/(movie|series)\/(.+)\.json$/);
  if (m) {
    const [, type, id] = m;
    if (type === 'series') {
      const { showId, season, episode } = parseSeriesId(id);
      const show = SERIES.get(showId);
      const files = show?.episodes.get(`${season}:${episode}`) ?? [];
      return sendJson(res, { streams: files.map((it) => toStream(it, base)) });
    }
    const hits = findMovies(id);
    hits.sort((a, b) => b.size - a.size); // best-quality rip first
    return sendJson(res, { streams: hits.map((it) => toStream(it, base)) });
  }

  m = p.match(/^\/file\/([a-f0-9]{16})$/);
  if (m) {
    const it = LIBRARY.get(m[1]);
    if (!it) { res.writeHead(404); return res.end('not found'); }
    let st;
    try { st = await fsp.stat(it.filePath); } catch { res.writeHead(410); return res.end('file gone'); }
    return serveFile(req, res, it.filePath, st.size);
  }

  m = p.match(/^\/sub\/([a-f0-9]{16})\/(\d+)$/);
  if (m) {
    const it = LIBRARY.get(m[1]);
    const sub = it?.subs[Number(m[2])];
    if (!sub) { res.writeHead(404); return res.end('not found'); }
    let st;
    try { st = await fsp.stat(sub); } catch { res.writeHead(410); return res.end('gone'); }
    return serveFile(req, res, sub, st.size);
  }

  res.writeHead(404, { 'Access-Control-Allow-Origin': '*' });
  res.end('not found');
}

// ---------------------------------------------------------------- tls + startup

const readCert = () => {
  const c = JSON.parse(fs.readFileSync(CONFIG.certFile, 'utf8'));
  return { key: c.key, cert: c.cert, domain: c.domain, notAfter: c.notAfter };
};

async function main() {
  await fsp.mkdir(path.dirname(CONFIG.cacheFile), { recursive: true });
  try { CACHE = JSON.parse(await fsp.readFile(CONFIG.cacheFile, 'utf8')); } catch { CACHE = {}; }

  const c0 = readCert();
  log(`cert ${c0.domain} (expires ${c0.notAfter})`);

  await buildLibrary();
  setInterval(() => buildLibrary().catch((e) => log('rescan failed:', e.message)), CONFIG.rescanMs);

  const server = https.createServer({ key: c0.key, cert: c0.cert }, (req, res) => {
    handle(req, res).catch((e) => {
      log('handler error:', e.message);
      if (!res.headersSent) res.writeHead(500);
      res.end('error');
    });
  });

  // A renewed cert (by Stremio, or by cert.mjs) is picked up without a restart.
  fs.watchFile(CONFIG.certFile, { interval: 60_000 }, () => {
    try {
      const c = readCert();
      server.setSecureContext({ key: c.key, cert: c.cert });
      log(`cert reloaded: ${c.domain} (expires ${c.notAfter})`);
    } catch (e) { log('cert reload failed:', e.message); }
  });

  server.listen(CONFIG.port, '0.0.0.0', () => {
    log(`listening on 0.0.0.0:${CONFIG.port}`);
    log(`install URL: https://${c0.domain}:${CONFIG.port}/manifest.json`);

    // Keep the account's stored install URL pointing at whatever LAN IP we are on now.
    // Disabled when running a throwaway instance for testing.
    if (!process.env.LOCALLIB_NO_AUTOHEAL) {
      startAutoHeal({
        addonId: manifest().id,
        port: CONFIG.port,
        certFile: CONFIG.certFile,
        manifest,
        log,
      });

      // Keeps the certificate current while Stremio desktop stays closed.
      startCertRenewal({ certFile: CONFIG.certFile, statusFile: CONFIG.certStatusFile, log });

      // The advisor registers itself once it knows whose streams to rate; it sits right after this addon.
      const startAdvisor = () => advisor.start({
        onReady: () => startAutoHeal({
          addonId: advisor.manifest().id,
          manifestPath: '/advisor/manifest.json',
          port: CONFIG.port,
          certFile: CONFIG.certFile,
          manifest: advisor.manifest,
          pinAfter: manifest().id,
          active: advisor.active,
          log,
        }),
      }).then((ok) => ok, (e) => { log(`advisor: ${e.message}`); return false; })
        .then((ok) => { if (!ok) setTimeout(startAdvisor, 10 * 60_000); });
      startAdvisor();
    }
  });
}

main().catch((e) => { console.error(e); process.exit(1); });
