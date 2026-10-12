"""End-to-end check of "download for later" with real libtorrent: a local seeder serving a dummy three-episode pack,
and the real Engine, Handler, Dashboard and Prefetcher on their own ports with a temporary cache. Nothing touches
the running server or ~/.stremio-local-addon. Takes about a minute; not part of `unittest discover`. Run with:

    ~/.stremio-local-addon/venv/bin/python -u tests/e2e_prefetch.py
"""
import hashlib
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

SCRATCH = Path(tempfile.mkdtemp(prefix='e2e-prefetch-'))
(SCRATCH / 'cache').mkdir(parents=True)
(SCRATCH / 'install').mkdir()
(SCRATCH / 'torrentd.json').write_text(json.dumps({
    'cache_dir': str(SCRATCH / 'cache'), 'listen_port': 46882, 'http_port': 41472, 'https_port': 41473,
    'learn_from_plays': False, 'dashboard_tv_adb': False, 'download_limit_mbps': 0, 'cache_budget_gb': 15}))
os.environ['TORRENTD_CONFIG'] = str(SCRATCH / 'torrentd.json')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import warnings  # noqa: E402
warnings.simplefilter('ignore', DeprecationWarning)
import libtorrent as lt  # noqa: E402
import torrent_server as ts  # noqa: E402
from dashboard import Dashboard  # noqa: E402
from prefetch import Prefetcher  # noqa: E402

MB = 1024 * 1024
PORT = 41472

# ---- a 3-episode pack, seeded locally at 4 MB/s
src = SCRATCH / 'seed'
(src / 'Show S01').mkdir(parents=True)
for name, size in (('Show.S01E01.mkv', 32), ('Show.S01E02.mkv', 32), ('Show.S01E03.mkv', 24)):
    (src / 'Show S01' / name).write_bytes(os.urandom(size * MB))
fs = lt.file_storage()
lt.add_files(fs, str(src / 'Show S01'))
ct = lt.create_torrent(fs, 1 * MB, flags=lt.create_torrent.v1_only)
lt.set_piece_hashes(ct, str(src))
ti = lt.torrent_info(lt.bdecode(lt.bencode(ct.generate())))
IH = str(ti.info_hash())
names = [ti.files().file_name(i) for i in range(ti.num_files())]
print('pack', IH[:8], names)

seeder = lt.session({'listen_interfaces': '127.0.0.1:46881', 'enable_dht': False, 'enable_lsd': False,
                     'enable_upnp': False, 'enable_natpmp': False, 'upload_rate_limit': 4 * MB,
                     'allow_multiple_connections_per_ip': True, 'ignore_limits_on_local_network': False})
sp = lt.add_torrent_params()
sp.ti, sp.save_path = ti, str(src)
seed_h = seeder.add_torrent(sp)
seed_h.force_recheck()
while not seed_h.status().is_seeding:
    time.sleep(0.1)
print('seeder ready')

# ---- the server under test
engine = ts.Engine()
engine.ses.apply_settings({'enable_dht': False, 'enable_lsd': False, 'enable_upnp': False, 'enable_natpmp': False,
                           'allow_multiple_connections_per_ip': True})
lines = []
log = lambda *a: (lines.append(' '.join(map(str, a))), print('   log:', *a))  # noqa: E731
ts.log = log
prefetcher = Prefetcher(engine, log)
engine.pinned = prefetcher.pinned        # as main() wires it
ts.Handler.engine = engine
ts.Handler.dashboard = Dashboard(sessions=engine.dashboard_sessions, facts=lambda k: None, config=ts.CONFIG,
                                 install_dir=SCRATCH / 'install', log=log, in_session=lambda: set(engine.torrents),
                                 live_progress=engine.file_progress, busy_files=engine.busy_files, offline=engine.offline,
                                 prefetch=prefetcher)
server = ts.PlainServer(('127.0.0.1', PORT), ts.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


def peers():   # stands in for peer discovery: whenever the torrent is in the session, it knows the seeder
    while True:
        t = engine.torrents.get(IH)
        if t:
            try:
                if t.handle.status().num_peers == 0:
                    t.handle.connect_peer(('127.0.0.1', 46881))
            except Exception:
                pass
        time.sleep(0.5)


threading.Thread(target=peers, daemon=True).start()
engine.get(IH, ti=ti)
assert engine.torrents[IH].wait_metadata(5)


def post(path, body, headers=None):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    h = {'Content-Type': 'application/json', 'X-Dashboard': '1', 'Sec-Fetch-Site': 'same-origin', 'Host': f'localhost:{PORT}'}
    h.update(headers or {})
    c.request('POST', path, json.dumps(body), h)
    r = c.getresponse()
    return r.status, json.loads(r.read())


def state():
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.request('GET', '/ui/api/state', headers={'Host': f'localhost:{PORT}'})
    return json.loads(c.getresponse().read())


def job(idx):
    return next((j for j in state()['prefetch'] if j['idx'] == idx), None)


def done(idx):
    return engine.file_progress(IH)[idx]['done']


def wait(cond, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.25)
    raise AssertionError(f'timed out: {what}')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print(('PASS ' if ok else 'FAIL ') + name + (f' — {detail}' if detail else ''))


E01, E02, E03 = (names.index(f'Show.S01E0{i}.mkv') for i in (1, 2, 3))

# 1. the write guard: no X-Dashboard header -> refused
status, out = post('/ui/api/prefetch', {'ih': IH, 'idx': E03}, {'X-Dashboard': ''})
check('a request without the dashboard header is refused', status == 403, f'{status} {out}')

# 2. start E03; it downloads in the background
status, out = post('/ui/api/prefetch', {'ih': IH, 'idx': E03})
check('prefetch accepted', status == 200 and out['ok'], json.dumps(out))
j = wait(lambda: (lambda j: j if j and j['state'] == 'downloading' and j['done'] > 0.1 else None)(job(E03)), 30, 'E03 downloading')
check('job shows progress and a rate', j['mbps'] is not None and j['mbps'] > 0, json.dumps(j))
t = engine.torrents[IH]
dbg = t.debug()
check('its reader is a background one with no deadlines', any(r['background'] for r in dbg['readers']) and dbg['deadlines'] == 0,
      json.dumps(dbg['readers']))
check('the file counts as being read', engine.busy_files(IH) == {E03})
check('eviction keeps the torrent while the job runs', engine.pinned() == {IH})
out = ts.Handler.dashboard.delete_file(IH, E03)
check('deleting it is refused while it downloads', not out['ok'], out['message'])

# 3. a stream starts on E01: the download for later waits until the stream has its buffer
stream_bytes = bytearray()
stop_stream = threading.Event()


def stream():
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=60)
    c.request('GET', f'/{IH}/{E01}', headers={'Range': 'bytes=0-'})
    r = c.getresponse()
    stream_bytes.extend(r.read(256 * 1024))       # a player that reads a little, then sits on a full buffer
    stop_stream.wait()
    c.close()


before = done(E03)
th = threading.Thread(target=stream)
th.start()
j = wait(lambda: (lambda j: j if j and j['state'] == 'waiting' else None)(job(E03)), 10, 'E03 waiting')
check('the download waits while the stream is short of buffer', True, j['message'])
mid = done(E03)
e01_start = done(E01)
time.sleep(3)
check('E03 hardly moves while it waits', done(E03) - mid < 0.1, f'E03 {mid:.2f} -> {done(E03):.2f}')
check('E01 (the stream) gets the bandwidth', done(E01) > e01_start, f'E01 {e01_start:.2f} -> {done(E01):.2f}')
wait(lambda: done(E01) >= 1.0, 30, 'E01 complete')
j = wait(lambda: (lambda j: j if j and j['state'] in ('downloading', 'done') else None)(job(E03)), 10, 'E03 resumes')
check('it resumes once the stream has its buffer', True, j['state'])
stop_stream.set()
th.join()

# 4. it finishes; the bytes match the source
j = wait(lambda: (lambda j: j if j and j['state'] == 'done' else None)(job(E03)), 60, 'E03 done')
check('job done at 100%', j['done'] == 1.0, json.dumps(j))
time.sleep(1.5)
got = SCRATCH / 'cache' / IH / 'Show S01' / 'Show.S01E03.mkv'
check('the downloaded file is byte-for-byte the source', got.exists() and sha(got) == sha(src / 'Show S01' / 'Show.S01E03.mkv'))
wait(lambda: not engine.busy_files(IH), 5, 'reader let go')
check('the file is no longer busy', engine.busy_files(IH) == set())
status, out = post('/ui/api/prefetch', {'ih': IH, 'idx': E03})
check('asking again for a complete file is refused', not out['ok'], out['message'])
ts.CONFIG['cache_budget_gb'] = 0.07     # 72 MB: the pack (E01 + E03 = 56 MB here) plus E02's 32 MB won't fit
status, out = post('/ui/api/prefetch', {'ih': IH, 'idx': E02})
check('refused when the pack would outgrow the cache budget', not out['ok'] and 'watched' in out['message'], out['message'])
ts.CONFIG['cache_budget_gb'] = 15

# 5. cancel E02 halfway
post('/ui/api/prefetch', {'ih': IH, 'idx': E02})
wait(lambda: done(E02) > 0.2, 30, 'E02 under way')
status, out = post('/ui/api/prefetch/cancel', {'ih': IH, 'idx': E02})
check('cancel accepted', out['ok'], out['message'])
wait(lambda: not engine.busy_files(IH), 5, 'reader let go after cancel')
time.sleep(4)          # the window clears within a second; anything in flight lands
a = done(E02)
time.sleep(3)
check('after cancel E02 stops growing and is kept', 0 < a < 1 and done(E02) == a, f'E02 at {a:.2f}, then {done(E02):.2f}')
check('the job left the list', job(E02) is None)

# 6. Stremio's /remove mid-download: the job adds the torrent back and finishes
post('/ui/api/prefetch', {'ih': IH, 'idx': E02})
wait(lambda: done(E02) > a + 0.1, 30, 'E02 under way again')
c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
c.request('GET', f'/{IH}/remove')
c.getresponse().read()
old = t
j = wait(lambda: (lambda j: j if j and j['state'] == 'done' else None)(job(E02)), 90, 'E02 done after /remove')
time.sleep(1.5)
got = SCRATCH / 'cache' / IH / 'Show S01' / 'Show.S01E02.mkv'
check('after /remove it carried on to the end', sha(got) == sha(src / 'Show S01' / 'Show.S01E02.mkv'))
check('the torrent left the session and came back',
      any('removed by client' in l for l in lines) and any('resumed from cache' in l or 'wanted again' in l for l in lines))

# 7. what the page gets
s = state()
row = next(r for r in s['cache'] or [] if r['ih'] == IH) if s.get('cache') else None
print('cache row next:', row and row['next'])
print('prefetch jobs:', json.dumps(s['prefetch']))

print()
print(f'{sum(ok for _, ok in results)}/{len(results)} checks passed')
server.shutdown()
prefetcher.stop()
shutil.rmtree(SCRATCH, ignore_errors=True)
sys.stdout.flush()
os._exit(0 if all(ok for _, ok in results) else 1)
