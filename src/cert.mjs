// Renews Stremio's HTTPS certificate, so it never lapses while Stremio desktop stays closed.
//
// Stremio's own server asks for a certificate only once the saved one has expired, or when an app calls its
// /get-https (Stremio desktop does on every launch). Either way it is one request to Stremio's API — the account's
// authKey and the LAN IP in, the wildcard certificate for this install out — saved to httpsCert.json. The API hands
// out the same certificate until Stremio's side has renewed it, so from 30 days before the expiry this asks once a
// day and saves the answer only when it lasts longer. The addon, the torrent server and Stremio itself all reload
// the file when it changes.

import fs from 'node:fs';
import tls from 'node:tls';
import { readAuthKey } from './auth.mjs';
import { currentIp } from './autoheal.mjs';

const API = 'https://api.strem.io/api/certificateGet';
const DAY = 86_400_000;
const WINDOW = 30 * DAY;

// "192-168-1-50.a1b2c3d4e5f6.stremio.rocks" -> "a1b2c3d4e5f6.stremio.rocks"
const installOf = (domain) => String(domain).split('.').slice(1).join('.');

async function fetchCertificate(authKey, ip, fetchFn) {
  const res = await fetchFn(API, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ authKey, ipAddress: ip }),
    signal: AbortSignal.timeout(25000),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  if (json.error) throw new Error(json.error.message ?? JSON.stringify(json.error));
  const c = JSON.parse(json.result.certificate);
  // As Stremio's server builds it, so Stremio reads the file back unchanged.
  return {
    domain: ip.replaceAll('.', '-') + c.commonName.replace('*', ''),
    key: Buffer.from(c.contents.PrivateKey, 'base64').toString('ascii'),
    cert: Buffer.from(c.contents.Certificate, 'base64').toString('ascii'),
    notBefore: c.contents.NotBefore,
    notAfter: c.contents.NotAfter,
  };
}

// Why a certificate from the API mustn't replace the saved one, or null. A bad file would take HTTPS down for the TV.
function unusable(fresh, saved, now) {
  if (installOf(fresh.domain) !== installOf(saved.domain)) return 'Stremio\'s API sent a certificate for another install';
  if (!(Date.parse(fresh.notBefore) <= now && now <= Date.parse(fresh.notAfter))) {
    return 'Stremio\'s API sent a certificate that isn\'t valid now';
  }
  try {
    tls.createSecureContext({ key: fresh.key, cert: fresh.cert });
  } catch {
    return 'Stremio\'s API sent a certificate whose key doesn\'t match';
  }
  return null;
}

/** One check: { result: 'not-due' | 'renewed' | 'same' | 'signed-out' | 'failed', message?, notAfter? }. */
export async function renewCertificate({ certFile, now = Date.now(), authKey = readAuthKey, ip = currentIp, fetchFn = fetch }) {
  const saved = JSON.parse(fs.readFileSync(certFile, 'utf8'));
  if (Date.parse(saved.notAfter) - now > WINDOW) return { result: 'not-due' };
  const key = authKey();
  if (!key) return { result: 'signed-out', message: 'Stremio desktop is signed out' };
  const addr = ip();
  if (!addr) return { result: 'failed', message: 'this Mac has no network address' };
  let fresh;
  try {
    fresh = await fetchCertificate(key, addr, fetchFn);
  } catch (e) {
    return { result: 'failed', message: `Stremio's API: ${e.message}` };
  }
  const why = unusable(fresh, saved, now);
  if (why) return { result: 'failed', message: why };
  if (Date.parse(fresh.notAfter) <= Date.parse(saved.notAfter)) return { result: 'same', notAfter: saved.notAfter };
  // Written whole, then moved into place: nothing ever reads half a certificate.
  const tmp = `${certFile}.sidecar-tmp`;
  fs.writeFileSync(tmp, JSON.stringify(fresh), { mode: 0o600 });
  fs.renameSync(tmp, certFile);
  return { result: 'renewed', notAfter: fresh.notAfter };
}

const LINE = {
  renewed: (r) => `renewed, valid until ${r.notAfter}`,
  same: (r) => `Stremio's API still has the one valid until ${r.notAfter}`,
  'signed-out': (r) => `failed — ${r.message}`,
  failed: (r) => `failed — ${r.message}`,
};

/** Checks now and every `everyMs`, asking the API at most once a day; each answer goes to the log and `statusFile`. */
export function startCertRenewal({ certFile, statusFile, log, everyMs = 3_600_000, ...deps }) {
  let last = -Infinity;    // wall-clock: timers stop while the Mac sleeps
  const check = async () => {
    if (Date.now() - last < DAY) return;
    let r;
    try {
      r = await renewCertificate({ certFile, ...deps });
    } catch (e) {
      r = { result: 'failed', message: e.message };     // an unreadable httpsCert.json
    }
    last = Date.now();
    if (r.result === 'not-due') return;
    log(`cert renewal: ${LINE[r.result](r)}`);
    try {
      fs.writeFileSync(statusFile, JSON.stringify({ at: new Date().toISOString(), ...r }));
    } catch (e) {
      log(`cert renewal: cannot write ${statusFile}: ${e.message}`);
    }
  };
  const timer = setInterval(check, everyMs);
  return { ready: check(), check, stop: () => clearInterval(timer) };
}
