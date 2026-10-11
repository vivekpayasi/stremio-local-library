// Keeps this addon's entry correct in the Stremio account: its install URL as the Mac moves
// networks, the manifest copy clients render from, and its place in the addon order.
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

const urlFor = (ip, hash, port, manifestPath) =>
  `https://${ip.replaceAll('.', '-')}.${hash}:${port}${manifestPath}`;

export async function api(method, authKey, extra = {}) {
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
  // Configured addon URLs (Torrentio's, for one) can carry API keys: owner-only.
  await fsp.mkdir(BACKUP_DIR, { recursive: true, mode: 0o700 });
  const f = path.join(BACKUP_DIR, `${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
  await fsp.writeFile(f, JSON.stringify({ savedAt: new Date().toISOString(), addons }, null, 2), { mode: 0o600 });
  // Keep the last 10 so this cannot grow without bound.
  const kept = (await fsp.readdir(BACKUP_DIR)).filter((n) => n.endsWith('.json')).sort();
  for (const old of kept.slice(0, -10)) await fsp.unlink(path.join(BACKUP_DIR, old)).catch(() => {});
  return f;
}

// Board rows follow addon order and new addons land last, below every other addon's rows.
// Sitting right after Cinemeta puts the local rows near the top. Pass pinAfter: null to opt out.
const CINEMETA = 'com.linvo.cinemeta';

// One addon's get-modify-set must not interleave with another's: addonCollectionSet replaces the whole
// collection, so the later write would silently undo the earlier one.
let queue = Promise.resolve();

function placeAfter(others, entry, anchorId) {
  const at = anchorId ? others.findIndex((a) => a?.manifest?.id === anchorId) : -1;
  if (at === -1) return null;
  return [...others.slice(0, at + 1), entry, ...others.slice(at + 1)];
}

export function startAutoHeal({
  addonId, port, certFile, manifest, manifestPath = '/manifest.json', intervalMs = 60_000, log, pinAfter = CINEMETA,
  active = () => true,
}) {
  let lastUrl = null;      // last URL we know is correct in the account
  let warnedSignedOut = false;
  let pin = pinAfter;      // cleared if the API turns out not to keep our ordering

  async function reconcile(reason) {
    if (!active()) return;
    const ip = currentIp();
    if (!ip) return;

    let hash;
    try { hash = certHash(certFile); } catch { return; }
    if (!hash) return;

    const want = urlFor(ip, hash, port, manifestPath);
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
    const others = addons.filter((a) => a?.manifest?.id !== addonId);
    const current = manifest();
    const isPlaced = (list) => {
      const anchor = pin ? list.findIndex((a) => a?.manifest?.id === pin) : -1;
      return anchor === -1 || list[anchor + 1]?.manifest?.id === addonId;
    };
    const urlOk = mine.length === 1 && mine[0].transportUrl === want;
    // Clients render catalog names from the account's copy of the manifest, not from the server.
    const manifestOk = mine[0]?.manifest?.version === current.version;
    const correct = urlOk && manifestOk && isPlaced(addons);

    if (correct) {
      if (lastUrl !== want) log(`autoheal: account URL already correct -> ${want}`);
      lastUrl = want;
      return;
    }

    const entry = {
      ...(mine[0] ?? { transportName: '', flags: { official: false, protected: false } }),
      transportUrl: want,
      manifest: current,
    };
    // Without a pin, keep whatever slot we already hold; a new install goes last.
    const held = addons.findIndex((a) => a?.manifest?.id === addonId);
    const next = placeAfter(others, entry, pin)
      ?? (held === -1 ? [...others, entry] : [...others.slice(0, held), entry, ...others.slice(held)]);

    const bk = await backup(addons, log);
    await api('addonCollectionSet', authKey, { addons: next });

    const verify = await api('addonCollectionGet', authKey, { update: true });
    const ok = verify.addons.some((a) => a?.manifest?.id === addonId && a.transportUrl === want);
    if (!ok) throw new Error('verification failed — collection did not take the new URL');
    if (verify.addons.length !== next.length) {
      throw new Error(`verification failed — expected ${next.length} addons, account has ${verify.addons.length}`);
    }
    if (!isPlaced(verify.addons)) {
      // Never fight the API over ordering: that would rewrite the collection every reconcile.
      log(`autoheal: account did not keep the order of ${addonId} — leaving its position alone`);
      pin = null;
    }

    const changes = [];
    if (!manifestOk && mine.length) changes.push(`manifest v${mine[0].manifest?.version} -> v${current.version}`);
    if (!isPlaced(addons) && pin) {
      changes.push(`moved to position ${verify.addons.findIndex((a) => a?.manifest?.id === addonId) + 1} of ${verify.addons.length}`);
    }
    const extra = changes.length ? `; ${changes.join(', ')}` : '';
    const bkName = path.basename(bk);
    if (mine.length === 0) {
      log(`autoheal: INSTALLED addon at ${want} (${addons.length} -> ${verify.addons.length} addons, backup ${bkName})`);
    } else if (!urlOk) {
      log(`autoheal: URL updated ${mine[0].transportUrl} -> ${want}${extra} (backup ${bkName})`);
    } else {
      log(`autoheal: account entry for ${addonId} updated — ${changes.join(', ')} (backup ${bkName})`);
    }
    lastUrl = want;
  }

  const run = (reason) => {
    queue = queue.then(() => reconcile(reason)).catch((e) => log(`autoheal: ${addonId}: ${e.message}`));
    return queue;
  };

  run('startup');
  setInterval(() => run('tick'), intervalMs);
  // A full reconcile every 30 min catches drift made from another device.
  setInterval(() => { lastUrl = null; run('reconcile'); }, 30 * 60_000);
}
