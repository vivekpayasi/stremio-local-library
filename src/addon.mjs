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

const HOME = os.homedir();

const CONFIG = {
  port: 12471,
  roots: [path.join(HOME, 'Media'), path.join(HOME, 'Movies')],
  certFile: path.join(HOME, 'Library/Application Support/stremio-server/httpsCert.json'),
  stremioIndex: path.join(HOME, 'Library/Application Support/stremio-server/localFiles'),
  stremioMeta: path.join(HOME, 'Library/Application Support/stremio-server/localFilesMeta'),
  cacheFile: path.join(HOME, '.stremio-local-addon/cache.json'),
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

// ---------------------------------------------------------------- library index

let LIBRARY = new Map(); // fileId -> { id, filePath, size, title, year, imdbId, meta, subs[] }
let CACHE = {};          // "title|year" -> cinemeta meta (persisted, so restarts stay fast)

const fileId = (p) => crypto.createHash('sha1').update(p).digest('hex').slice(0, 16);

function cleanTitle(filename) {
  let n = filename.replace(/\.[^.]+$/, '').replace(/[._]+/g, ' ');
  const ym = n.match(/\b(19\d{2}|20\d{2})\b/);
  const year = ym ? ym[1] : null;
  if (ym) n = n.slice(0, ym.index);
  n = n.replace(
    /\b(1080p|720p|2160p|4k|uhd|bluray|blu ray|brrip|bdrip|web ?dl|webrip|hdrip|dvdrip|x264|x265|h ?264|h ?265|hevc|10bit|aac|ac3|eac3|dts|truehd|atmos|remux|proper|repack|extended|unrated|yts|rarbg)\b.*$/i,
    '',
  );
  return { title: n.replace(/[[\](){}]/g, ' ').replace(/\s+/g, ' ').trim(), year };
}

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

async function cinemetaLookup(title, year) {
  const key = `${title}|${year ?? ''}`;
  if (CACHE[key] !== undefined) return CACHE[key];
  let found = null;
  try {
    const url = `${CONFIG.cinemeta}/catalog/movie/top/search=${encodeURIComponent(title)}.json`;
    const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
    if (res.ok) {
      const { metas = [] } = await res.json();
      found = (year && metas.find((m) => String(m.releaseInfo ?? '').startsWith(year))) || metas[0] || null;
    }
  } catch { /* offline — degrade to filename title */ }
  CACHE[key] = found;
  fsp.writeFile(CONFIG.cacheFile, JSON.stringify(CACHE)).catch(() => {});
  return found;
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
  const [matches, stremioMeta] = await Promise.all([readStremioMatches(), readStremioMeta()]);
  const next = new Map();

  for (const root of CONFIG.roots) {
    const all = await walk(root);
    const videos = all.filter((f) => CONFIG.videoExt.has(path.extname(f).toLowerCase()));
    const subs = all.filter((f) => CONFIG.subExt.has(path.extname(f).toLowerCase()));

    for (const filePath of videos) {
      let st;
      try { st = await fsp.stat(filePath); } catch { continue; }
      if (st.size < CONFIG.minBytes) continue;

      const base = path.basename(filePath);
      const { title, year } = cleanTitle(base);
      let imdbId = matches.get(filePath) ?? null;
      let meta = imdbId ? stremioMeta.get(imdbId) : null;

      if (!meta && title) {
        const hit = await cinemetaLookup(title, year);
        if (hit) { meta = hit; imdbId = imdbId ?? hit.imdb_id ?? hit.id; }
      }

      // Subtitles sitting next to the video, or sharing its basename.
      const stem = base.replace(/\.[^.]+$/, '');
      const near = subs.filter(
        (s) => path.dirname(s) === path.dirname(filePath) || path.basename(s).startsWith(stem),
      );

      const id = fileId(filePath);
      next.set(id, {
        id, filePath, size: st.size,
        title: meta?.name ?? title ?? base,
        year: meta?.releaseInfo ?? year ?? '',
        imdbId, meta, subs: near,
        filename: base,
      });
    }
  }

  LIBRARY = next;
  log(`library: ${LIBRARY.size} file(s)`);
  for (const it of LIBRARY.values()) log(`   ${it.imdbId ?? '(unmatched)'}  ${it.title}  <- ${it.filename}`);
}

// ---------------------------------------------------------------- stremio shapes

const catalogId = 'local-library';

const manifest = () => ({
  id: 'org.local.library',
  version: '1.0.0',
  name: 'Local Library',
  description: `Plays media stored on this Mac (${CONFIG.roots.join(', ')}) with full seek support.`,
  logo: 'https://images.metahub.space/logo/medium/tt0405508/img',
  resources: ['catalog', 'meta', 'stream'],
  types: ['movie'],
  catalogs: [{
    type: 'movie',
    id: catalogId,
    name: 'Local Library',
    extra: [{ name: 'search', isRequired: false }, { name: 'skip', isRequired: false }],
  }],
  // Respond to normal IMDb pages so local copies appear as a source on the movie's own page,
  // plus our internal ids for anything that has no IMDb match.
  idPrefixes: ['tt', 'locallib:'],
  behaviorHints: { configurable: false, adult: false },
});

const publicId = (it) => it.imdbId ?? `locallib:${it.id}`;

function toMeta(it) {
  const m = it.meta ?? {};
  return {
    id: publicId(it),
    type: 'movie',
    name: it.title,
    poster: m.poster ?? undefined,
    background: m.background ?? undefined,
    logo: m.logo ?? undefined,
    description: m.description ?? `Local file: ${it.filename}`,
    releaseInfo: it.year || undefined,
    imdbRating: m.imdbRating ?? undefined,
    runtime: m.runtime ?? undefined,
    genres: m.genres ?? m.genre ?? undefined,
    cast: m.cast ?? undefined,
    director: m.director ?? undefined,
  };
}

function toStream(it, base) {
  const gb = (it.size / 1024 ** 3).toFixed(2);
  const ext = path.extname(it.filePath).toLowerCase();
  return {
    url: `${base}/file/${it.id}`,
    name: 'Local Library',
    title: `${it.filename}\n${gb} GB · direct play from Mac`,
    behaviorHints: {
      bingeGroup: `locallib-${it.id}`,
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

// Several files can legitimately map to the same title (different rips of one movie),
// so this returns every match — they become alternative sources on the movie's page.
const findAllById = (wanted) =>
  [...LIBRARY.values()].filter((it) => it.imdbId === wanted || `locallib:${it.id}` === wanted);

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

  if (p === '/health') {
    return sendJson(res, {
      ok: true, files: LIBRARY.size, roots: CONFIG.roots, base,
      items: [...LIBRARY.values()].map((i) => ({ id: publicId(i), title: i.title, file: i.filename })),
    });
  }

  // /catalog/movie/local-library.json  (optionally .../search=foo.json)
  let m = p.match(/^\/catalog\/movie\/([^/]+?)(?:\/(.+))?\.json$/);
  if (m) {
    if (m[1] !== catalogId) return sendJson(res, { metas: [] });
    let items = [...LIBRARY.values()];
    const extra = new URLSearchParams(m[2] ?? '');
    const q = extra.get('search');
    if (q) items = items.filter((i) => i.title.toLowerCase().includes(q.toLowerCase()));
    // One card per title even when several files back it; prefer the entry with real metadata.
    const byId = new Map();
    for (const it of items) {
      const key = publicId(it);
      if (!byId.has(key) || (!byId.get(key).meta && it.meta)) byId.set(key, it);
    }
    const metas = [...byId.values()].sort((a, b) => a.title.localeCompare(b.title)).map(toMeta);
    return sendJson(res, { metas });
  }

  m = p.match(/^\/meta\/movie\/(.+)\.json$/);
  if (m) {
    const hits = findAllById(m[1]);
    const best = hits.find((h) => h.meta) ?? hits[0];
    return sendJson(res, best ? { meta: toMeta(best) } : { meta: null });
  }

  m = p.match(/^\/stream\/movie\/(.+)\.json$/);
  if (m) {
    const hits = findAllById(m[1]);
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

  // Stremio renews the cert roughly every 90 days; pick it up without a restart.
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
    startAutoHeal({
      addonId: manifest().id,
      port: CONFIG.port,
      certFile: CONFIG.certFile,
      manifest,
      log,
    });
  });
}

main().catch((e) => { console.error(e); process.exit(1); });
