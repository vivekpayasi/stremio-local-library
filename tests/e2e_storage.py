"""End-to-end check of the cache on an external drive, with real libtorrent and a real exFAT disk: a disk image made
and mounted here (not under /Volumes), a local seeder serving two dummy movies, and the real Engine, Handler, Dashboard
and Prefetcher on their own ports with a temporary cache. Nothing touches the running server, ~/.stremio-local-addon
or any drive you have connected. Takes about a minute; not part of `unittest discover`. Run with:

    ~/.stremio-local-addon/venv/bin/python -u tests/e2e_storage.py
"""
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SCRATCH = Path(tempfile.mkdtemp(prefix='e2e-storage-'))
DRIVE = SCRATCH / 'drive'             # the exFAT image's mount point
(SCRATCH / 'cache').mkdir(parents=True)
(SCRATCH / 'install').mkdir()
DRIVE.mkdir()
IMAGE = SCRATCH / 'drive.dmg'
subprocess.run(['hdiutil', 'create', '-size', '600m', '-fs', 'ExFAT', '-volname', 'SidecarE2E', str(IMAGE)],
               check=True, capture_output=True)


def plug():
    subprocess.run(['hdiutil', 'attach', '-nobrowse', '-noautoopen', '-mountpoint', str(DRIVE), str(IMAGE)],
                   check=True, capture_output=True)


def unplug():
    subprocess.run(['hdiutil', 'detach', str(DRIVE), '-force'], check=True, capture_output=True)


plug()
(SCRATCH / 'torrentd.json').write_text(json.dumps({
    'cache_dir': str(SCRATCH / 'cache'), 'listen_port': 46892, 'http_port': 41482, 'https_port': 41483,
    'learn_from_plays': False, 'dashboard_tv_adb': False, 'download_limit_mbps': 0, 'cache_budget_gb': 15,
    'external_volume': str(DRIVE), 'external_budget_gb': 0.4, 'downloads_to': 'external', 'min_free_gb': 0}))
os.environ['TORRENTD_CONFIG'] = str(SCRATCH / 'torrentd.json')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import warnings  # noqa: E402
warnings.simplefilter('ignore', DeprecationWarning)
import libtorrent as lt  # noqa: E402
import torrent_server as ts  # noqa: E402
from dashboard import Dashboard  # noqa: E402
from prefetch import Prefetcher  # noqa: E402
from storage import EXTERNAL_DIR  # noqa: E402

MB = 1024 * 1024
PORT = 41482

# ---- two movies, seeded locally at 16 MB/s
src = SCRATCH / 'seed'
src.mkdir()
infos = {}
for name, size in (('Movie.A.2026.2160p.mkv', 160), ('Movie.B.2026.1080p.mkv', 48)):
    (src / name).write_bytes(os.urandom(size * MB))
    fs = lt.file_storage()
    lt.add_files(fs, str(src / name))
    ct = lt.create_torrent(fs, 4 * MB, flags=lt.create_torrent.v1_only)
    lt.set_piece_hashes(ct, str(src))
    ti = lt.torrent_info(lt.bdecode(lt.bencode(ct.generate())))
    infos[name] = ti
A, B = (str(infos[n].info_hash()) for n in infos)
TI_A, TI_B = infos['Movie.A.2026.2160p.mkv'], infos['Movie.B.2026.1080p.mkv']
SRC_A, SRC_B = src / 'Movie.A.2026.2160p.mkv', src / 'Movie.B.2026.1080p.mkv'
print('movies', A[:8], B[:8])

seeder = lt.session({'listen_interfaces': '127.0.0.1:46891', 'enable_dht': False, 'enable_lsd': False,
                     'enable_upnp': False, 'enable_natpmp': False, 'upload_rate_limit': 16 * MB,
                     'allow_multiple_connections_per_ip': True, 'ignore_limits_on_local_network': False})
for ti in infos.values():
    sp = lt.add_torrent_params()
    sp.ti, sp.save_path = ti, str(src)
    h = seeder.add_torrent(sp)
    h.force_recheck()
    while not h.status().is_seeding:
        time.sleep(0.1)
print('seeder ready')

# ---- the server under test, wired as main() wires it
ts.MOVE_IDLE_S = 3        # (60 in the server: a torrent the TV read in the last minute isn't moved)
engine = ts.Engine()
engine.ses.apply_settings({'enable_dht': False, 'enable_lsd': False, 'enable_upnp': False, 'enable_natpmp': False,
                           'allow_multiple_connections_per_ip': True})
lines = []
log = lambda *a: (lines.append(' '.join(map(str, a))), print('   log:', *a))  # noqa: E731
ts.log = log
prefetcher = Prefetcher(engine, log)
engine.pinned = prefetcher.pinned
ts.Handler.engine = engine
ts.Handler.dashboard = Dashboard(sessions=engine.dashboard_sessions, facts=lambda k: None, config=ts.CONFIG,
                                 install_dir=SCRATCH / 'install', log=log, in_session=lambda: set(engine.torrents),
                                 live_progress=engine.file_progress, busy_files=engine.busy_files, offline=engine.offline,
                                 prefetch=prefetcher, places=engine.places,
                                 held=lambda: engine.moving | prefetcher.pinned())
server = ts.PlainServer(('127.0.0.1', PORT), ts.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


def peers():   # stands in for peer discovery: every torrent in the session knows the seeder
    while True:
        for t in list(engine.torrents.values()):
            try:
                if t.handle.status().num_peers == 0:
                    t.handle.connect_peer(('127.0.0.1', 46891))
            except Exception:
                pass
        time.sleep(0.5)


threading.Thread(target=peers, daemon=True).start()


def post(path, body):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.request('POST', path, json.dumps(body), {'Content-Type': 'application/json', 'X-Dashboard': '1',
                                               'Sec-Fetch-Site': 'same-origin', 'Host': f'localhost:{PORT}'})
    r = c.getresponse()
    return r.status, json.loads(r.read())


def state():
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.request('GET', '/ui/api/state', headers={'Host': f'localhost:{PORT}'})
    return json.loads(c.getresponse().read())


def read(ih, start, end):
    """What the TV gets for a range of the movie's file."""
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=120)
    c.request('GET', f'/{ih}/0', headers={'Range': f'bytes={start}-{end}'})
    r = c.getresponse()
    data = r.read()
    c.close()
    return data


def job(ih):
    return next((j for j in prefetcher.jobs() if j['ih'] == ih), None)


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


def idle(ih):
    return wait(lambda: not engine.busy_files(ih) and not (engine.find(ih) and engine.find(ih).live_readers()), 15,
                f'{ih[:8]} not read')


EXT = DRIVE / EXTERNAL_DIR
size_a, size_b = SRC_A.stat().st_size, SRC_B.stat().st_size
want_a = SRC_A.read_bytes()

try:
    # 1. the drive is seen for what it is, once the places thread has looked at it
    ext = wait(lambda: (lambda e: e if e['why'] != 'Checking…' else None)(engine.places.external()), 10, 'first look')
    check('the drive is connected and usable', ext['available'], json.dumps({k: str(v) for k, v in ext.items()}))
    check('exFAT is found to be a disk that can\'t skip empty space', ext['sparse'] is False)

    # 2. the TV streams movie A, reading its end first, as players do: it is cached on this Mac
    engine.get(A, ti=TI_A)
    tail = read(A, size_a - 2 * MB, size_a - 1)
    head = read(A, 0, 8 * MB - 1)
    check('the stream gets the right bytes', tail == want_a[-2 * MB:] and head == want_a[:8 * MB])
    check('what the TV streams is cached on this Mac', (SCRATCH / 'cache' / A).is_dir() and not (EXT / A).exists())
    idle(A)

    # 3. download it for later: the torrent moves to the drive, then finishes there
    status, out = post('/ui/api/prefetch', {'ih': A, 'idx': 0})
    check('the download for later is accepted', status == 200 and out['ok'], json.dumps(out))
    j = wait(lambda: (lambda j: j if j and j['state'] in ('waiting', 'moving', 'downloading', 'done') else None)(job(A)), 10, 'A under way')
    check('it waits until the TV has left the torrent alone a while', j['state'] == 'waiting' and 'TV' in (j['message'] or ''),
          json.dumps(j))
    j = wait(lambda: (lambda j: j if j and j['state'] in ('done', 'failed') else None)(job(A)), 90, 'A done')
    check('it finishes', j['state'] == 'done', json.dumps(j))
    check('the torrent was moved to the drive first', any(f'torrent {A[:8]}: moving it to' in l for l in lines))
    check('it left this Mac', not (SCRATCH / 'cache' / A).exists())
    time.sleep(1.5)
    check('on the drive the file is byte-for-byte the movie', sha(EXT / A / SRC_A.name) == sha(SRC_A))
    rows = {r['ih']: r for r in (ts.Handler.dashboard._cache() or ts.Handler.dashboard.slow['cache_list'])}
    check('the shelf lists it on the drive', rows.get(A, {}).get('place') == 'external', json.dumps(rows.get(A, {}).get('place')))

    # 4. movie B was never played: on the drive its file is written out to its full length before it downloads
    engine.get(B, ti=TI_B)
    wait(lambda: engine.find(B) and engine.find(B).ti is not None, 10, 'B metadata')
    status, out = post('/ui/api/prefetch', {'ih': B, 'idx': 0})
    check('B is accepted', out['ok'], json.dumps(out))
    j = wait(lambda: (lambda j: j if j and j['state'] in ('done', 'failed') else None)(job(B)), 90, 'B done')
    check('B finishes', j['state'] == 'done', json.dumps(j))
    check('B\'s file was written out on the drive first', any(f'torrent {B[:8]}: writing out' in l for l in lines))
    time.sleep(1.5)
    check('B is byte-for-byte on the drive', sha(EXT / B / SRC_B.name) == sha(SRC_B))

    # 5. the TV plays A from the drive: a jump to the middle is served at once
    t0 = time.time()
    mid = read(A, size_a // 2, size_a // 2 + 4 * MB - 1)
    check('a jump into the movie on the drive', mid == want_a[size_a // 2:size_a // 2 + 4 * MB], f'{time.time() - t0:.2f} s')
    idle(A)

    # 6. the storage settings, from the dashboard
    status, out = post('/ui/api/storage', {'place': 'mac', 'budget_gb': 25})
    saved = json.loads((SCRATCH / 'torrentd.json').read_text())
    check('this Mac\'s limit is raised and saved', out['ok'] and ts.CONFIG['cache_budget_gb'] == 25 and saved['cache_budget_gb'] == 25,
          json.dumps(out))
    post('/ui/api/storage', {'downloads_to': 'mac'})
    check('downloads for later can stay on this Mac', state()['storage']['dest']['id'] == 'mac')
    post('/ui/api/storage', {'downloads_to': 'external'})
    check('... and go back to the drive', state()['storage']['dest']['id'] == 'external')

    # 7. the drive is unplugged while A is in the session: it is let go, and played again it starts over on this Mac
    read(A, 0, MB - 1)
    unplug()
    wait(lambda: any(f'torrent {A[:8]}: leaving the session' in l and 'disconnected' in l for l in lines), 20, 'A let go')
    check('a torrent on a drive that went is let go', True)
    wait(lambda: A not in engine.torrents, 15, 'A out of the session')
    ts.Handler.dashboard._cache()
    places = {p['id']: p for p in state()['storage']['places']}
    check('the page shows the drive as not connected', places['external']['available'] is False and places['external']['why'] == 'Not connected',
          json.dumps(places['external']))
    check('nothing was written where the drive was', not EXT.exists())
    again = read(A, 0, 4 * MB - 1)
    check('played again, it streams from this Mac', again == want_a[:4 * MB] and (SCRATCH / 'cache' / A).is_dir())
    idle(A)

    # 8. the drive comes back: of the two copies, the one with less downloaded (this Mac's) goes
    plug()
    engine.drop(engine.find(A), 'test: done playing')
    wait(lambda: A not in engine.torrents, 15, 'A out of the session again')
    wait(lambda: not (SCRATCH / 'cache' / A).exists(), 20, 'the Mac copy deleted')
    check('the Mac\'s partial copy is deleted, the full one on the drive kept', (EXT / A / SRC_A.name).exists())
    check('which plays', read(A, size_a - MB, size_a - 1) == want_a[-MB:])
finally:
    print()
    print(f'{sum(ok for _, ok in results)}/{len(results)} checks passed')
    server.shutdown()
    prefetcher.stop()
    subprocess.run(['hdiutil', 'detach', str(DRIVE), '-force'], capture_output=True)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    sys.stdout.flush()
    os._exit(0 if results and all(ok for _, ok in results) else 1)
