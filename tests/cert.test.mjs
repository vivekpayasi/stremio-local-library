// Run with:  node --test   (from the repo root)

import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { renewCertificate, startCertRenewal } from '../src/cert.mjs';

const DAY = 86_400_000;
const NOW = Date.parse('2026-12-01T12:00:00Z');

// A key and a self-signed certificate for it: what the API sends is checked to be a pair before it is saved.
function pair(dir, name) {
  const key = crypto.generateKeyPairSync('ec', { namedCurve: 'prime256v1' }).privateKey.export({ type: 'pkcs8', format: 'pem' });
  const keyFile = path.join(dir, `${name}.key`);
  fs.writeFileSync(keyFile, key);
  const cert = execFileSync('openssl', ['req', '-x509', '-key', keyFile, '-subj', '/CN=test', '-days', '1'], { encoding: 'utf8' });
  return { key, cert };
}

function setup({ notAfter = '2026-12-24T12:32:50Z' } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cert-test-'));
  const old = pair(dir, 'old');
  const fresh = pair(dir, 'fresh');
  const certFile = path.join(dir, 'httpsCert.json');
  const saved = { domain: '192-168-0-101.abc123def456.stremio.rocks', key: old.key, cert: old.cert,
    notBefore: '2026-09-25T12:32:51Z', notAfter };
  fs.writeFileSync(certFile, JSON.stringify(saved));
  return { dir, certFile, saved, fresh, other: pair(dir, 'other') };
}

// Stremio's API answer: the certificate is itself a JSON string, its PEMs base64-encoded.
const answer = ({ key, cert }, { commonName = '*.abc123def456.stremio.rocks', notBefore = '2026-11-25T08:00:00Z',
  notAfter = '2027-02-23T08:00:00Z' } = {}) => ({
  result: { certificate: JSON.stringify({ commonName, contents: {
    PrivateKey: Buffer.from(key).toString('base64'), Certificate: Buffer.from(cert).toString('base64'),
    NotBefore: notBefore, NotAfter: notAfter } }) },
});

function api(body) {
  const calls = [];
  const fetchFn = async (url, opts) => {
    calls.push({ url, body: JSON.parse(opts.body) });
    return { ok: true, status: 200, json: async () => (typeof body === 'function' ? body() : body) };
  };
  return { calls, fetchFn };
}

const ARGS = { now: NOW, authKey: () => 'the-key', ip: () => '192.168.0.101' };

test('more than 30 days before the expiry, Stremio\'s API is not asked', async () => {
  const s = setup();
  const { calls, fetchFn } = api(answer(s.fresh));
  const r = await renewCertificate({ ...ARGS, certFile: s.certFile, fetchFn, now: Date.parse('2026-11-20T12:00:00Z') });
  assert.equal(r.result, 'not-due');
  assert.equal(calls.length, 0);
});

test('a certificate that lasts longer is saved in Stremio\'s format, for this Mac\'s address', async () => {
  const s = setup();
  const { calls, fetchFn } = api(answer(s.fresh));
  const r = await renewCertificate({ ...ARGS, certFile: s.certFile, fetchFn });
  assert.equal(r.result, 'renewed');
  assert.equal(r.notAfter, '2027-02-23T08:00:00Z');
  assert.deepEqual(calls, [{ url: 'https://api.strem.io/api/certificateGet', body: { authKey: 'the-key', ipAddress: '192.168.0.101' } }]);
  assert.deepEqual(JSON.parse(fs.readFileSync(s.certFile, 'utf8')), {
    domain: '192-168-0-101.abc123def456.stremio.rocks', key: s.fresh.key, cert: s.fresh.cert,
    notBefore: '2026-11-25T08:00:00Z', notAfter: '2027-02-23T08:00:00Z' });
});

test('the same certificate again leaves the file alone', async () => {
  const s = setup();
  const before = fs.readFileSync(s.certFile, 'utf8');
  const { fetchFn } = api(answer(s.fresh, { notBefore: '2026-09-25T12:32:51Z', notAfter: '2026-12-24T12:32:50Z' }));
  const r = await renewCertificate({ ...ARGS, certFile: s.certFile, fetchFn });
  assert.equal(r.result, 'same');
  assert.equal(r.notAfter, '2026-12-24T12:32:50Z');
  assert.equal(fs.readFileSync(s.certFile, 'utf8'), before);
});

test('signed out of Stremio: nothing is asked', async () => {
  const s = setup();
  const { calls, fetchFn } = api(answer(s.fresh));
  const r = await renewCertificate({ ...ARGS, certFile: s.certFile, fetchFn, authKey: () => null });
  assert.equal(r.result, 'signed-out');
  assert.equal(calls.length, 0);
});

test('an expired certificate is renewed too', async () => {
  const s = setup({ notAfter: '2026-11-30T12:00:00Z' });
  const { fetchFn } = api(answer(s.fresh));
  assert.equal((await renewCertificate({ ...ARGS, certFile: s.certFile, fetchFn })).result, 'renewed');
});

for (const [why, body, message] of [
  ['for another install', (s) => answer(s.fresh, { commonName: '*.999999999999.stremio.rocks' }), /another install/],
  ['not valid yet', (s) => answer(s.fresh, { notBefore: '2026-12-02T00:00:00Z' }), /isn't valid now/],
  ['with a key that isn\'t the certificate\'s', (s) => answer({ key: s.other.key, cert: s.fresh.cert }), /key doesn't match/],
  ['an error from the API', () => ({ error: { message: 'Session does not exist' } }), /Session does not exist/],
]) {
  test(`a certificate ${why} is not saved`, async () => {
    const s = setup();
    const before = fs.readFileSync(s.certFile, 'utf8');
    const { fetchFn } = api(body(s));
    const r = await renewCertificate({ ...ARGS, certFile: s.certFile, fetchFn });
    assert.equal(r.result, 'failed');
    assert.match(r.message, message);
    assert.equal(fs.readFileSync(s.certFile, 'utf8'), before);
  });
}

test('each check that asked the API is logged and recorded for the dashboard, at most once a day', async () => {
  const notAfter = new Date(Date.now() + 10 * DAY).toISOString();
  const s = setup({ notAfter });
  const statusFile = path.join(s.dir, 'cert-renewal.json');
  const lines = [];
  // The API still has the same certificate, so every check is due: only the once-a-day rule keeps the second one quiet.
  const { calls, fetchFn } = api(answer(s.fresh, { notBefore: '2026-01-01T00:00:00Z', notAfter }));
  const r = startCertRenewal({ certFile: s.certFile, statusFile, log: (l) => lines.push(l), authKey: ARGS.authKey, ip: ARGS.ip,
    fetchFn, everyMs: 1e9 });
  await r.ready;
  await r.check();
  r.stop();
  assert.equal(calls.length, 1, 'checked again within the day');
  assert.deepEqual(lines, [`cert renewal: Stremio's API still has the one valid until ${notAfter}`]);
  const status = JSON.parse(fs.readFileSync(statusFile, 'utf8'));
  assert.equal(status.result, 'same');
  assert.ok(Math.abs(Date.parse(status.at) - Date.now()) < 60_000);
});
