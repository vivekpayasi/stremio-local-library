// Keeps this addon's install URL correct in the Stremio account as the Mac moves networks.
//
// The stremio.rocks hostname encodes the LAN IP, so the transportUrl stored in the account goes
// stale on every IP change. The certificate does NOT — it is a wildcard for the install hash and
// covers every IP-encoded subdomain — so the only thing that ever needs fixing is this one string.

import os from 'node:os';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { readAuthKey } from './auth.mjs';

const HOME = os.homedir();

const API = 'https://api.strem.io/api';
const BACKUP_DIR = path.join(HOME, '.stremio-local-addon/collection-backups');

// Prefer the interface actually carrying the default route; fall back to any private IPv4.
function currentIp() {
  const ifaces = os.networkInterfaces();
  for (const name of ['en0', 'en1', ...Object.keys(ifaces)]) {
    for (const a of ifaces[name] ?? []) {
      if (a.family === 'IPv4' && !a.internal) return a.address;
    }
  }
  return null;
}

// "192-168-1-50.a1b2c3d4e5f6.stremio.rocks" -> "a1b2c3d4e5f6.stremio.rocks"
function certHash(certFile) {
  const { domain } = JSON.parse(fs.readFileSync(certFile, 'utf8'));
  const parts = String(domain).split('.');
  return parts.slice(1).join('.') || null;
}

const urlFor = (ip, hash, port) =>
  `https://${ip.replaceAll('.', '-')}.${hash}:${port}/manifest.json`;

async function api(method, authKey, extra = {}) {
  const res = await fetch(`${API}/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: method[0].toUpperCase() + method.slice(1), authKey, ...extra }),
    signal: AbortSignal.timeout(25000),
  });
  if (!res.ok) throw new Error(`${method} HTTP ${res.status}`);
  const json = await res.json();
  if (json.error) throw new Error(`${method}: ${json.error.message ?? JSON.stringify(json.error)}`);
  return json.result;
}

async function backup(addons, log) {
  await fsp.mkdir(BACKUP_DIR, { recursive: true });
  const f = path.join(BACKUP_DIR, `${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
  await fsp.writeFile(f, JSON.stringify({ savedAt: new Date().toISOString(), addons }, null, 2));
  // Keep the last 10 so this cannot grow without bound.
  const kept = (await fsp.readdir(BACKUP_DIR)).filter((n) => n.endsWith('.json')).sort();
  for (const old of kept.slice(0, -10)) await fsp.unlink(path.join(BACKUP_DIR, old)).catch(() => {});
  return f;
}

export function startAutoHeal({ addonId, port, certFile, manifest, intervalMs = 60_000, log }) {
  let lastUrl = null;      // last URL we know is correct in the account
  let warnedSignedOut = false;

  async function reconcile(reason) {
    const ip = currentIp();
    if (!ip) return;

    let hash;
    try { hash = certHash(certFile); } catch { return; }
    if (!hash) return;

    const want = urlFor(ip, hash, port);
    if (want === lastUrl && reason === 'tick') return; // nothing changed since last success

    const authKey = readAuthKey();
    if (!authKey) {
      if (!warnedSignedOut) {
        log('autoheal: cannot read authKey (Stremio signed out?) — will retry quietly');
        warnedSignedOut = true;
      }
      return;
    }
    warnedSignedOut = false;

    const { addons } = await api('addonCollectionGet', authKey, { update: true });
    const mine = addons.filter((a) => a?.manifest?.id === addonId);
    const correct = mine.length === 1 && mine[0].transportUrl === want;

    if (correct) {
      if (lastUrl !== want) log(`autoheal: account URL already correct -> ${want}`);
      lastUrl = want;
      return;
    }

    const others = addons.filter((a) => a?.manifest?.id !== addonId);
    const entry = {
      ...(mine[0] ?? { transportName: '', flags: { official: false, protected: false } }),
      transportUrl: want,
      manifest: manifest(),
    };
    const next = [...others, entry];

    const bk = await backup(addons, log);
    await api('addonCollectionSet', authKey, { addons: next });

    const verify = await api('addonCollectionGet', authKey, { update: true });
    const ok = verify.addons.some((a) => a?.manifest?.id === addonId && a.transportUrl === want);
    if (!ok) throw new Error('verification failed — collection did not take the new URL');

    log(
      mine.length === 0
        ? `autoheal: INSTALLED addon at ${want} (${addons.length} -> ${verify.addons.length} addons, backup ${path.basename(bk)})`
        : `autoheal: URL updated ${mine[0].transportUrl} -> ${want} (backup ${path.basename(bk)})`,
    );
    lastUrl = want;
  }

  const run = (reason) =>
    reconcile(reason).catch((e) => log(`autoheal: ${e.message}`));

  run('startup');
  setInterval(() => run('tick'), intervalMs);
  // A full reconcile every 30 min catches drift made from another device.
  setInterval(() => { lastUrl = null; run('reconcile'); }, 30 * 60_000);
}
