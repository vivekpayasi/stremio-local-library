"""Where the torrent cache lives: this Mac, and a folder on an external drive while that drive is connected.

Each place keeps up to its own budget. What the TV streams is always cached on this Mac; downloads for later go to
the place chosen on the dashboard — the drive while it's connected, else this Mac — and a torrent cached elsewhere
moves there first (move_folder).

Most external drives come formatted exFAT, which can't skip empty space in a file: a write far past what a file holds
first fills the gap with zeros, at the drive's speed, and reads of that file wait meanwhile — about 25 s for the end of
a 20 GB movie. The TV reads the end of a file as it starts playing it, which is why streams stay on the Mac; and a file
downloaded for later onto such a drive is written out to its full length before it downloads (allocate), so no write
into it has a gap to fill.

Every look at a disk — whether the drive is there and usable, what its disk can do, which torrents it holds — happens in
Places.refresh(), which the server runs on a thread of its own: a drive can block for a while (macOS asking whether the
server may use it, a drive pulled out without ejecting), and the streaming path must never wait on it.

The settings the dashboard changes are applied at once and saved to torrentd.json (Places.configure).
"""
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

EXTERNAL_DIR = 'Stremio Sidecar cache'    # the cache's folder on the drive
CHECK_S = 10          # a drive that couldn't be used is looked at again this often
MB = 1024 * 1024
GB = 1024 * MB
IH_DIR = re.compile(r'[0-9a-f]{40}')
DENIED = 'macOS isn\'t letting the torrent server use it'    # why a drive is there but can't be used


# ------------------------------------------------------------------------------------------ resume data

def bdecode(data):
    """Bencoded bytes (a .torrent, libtorrent resume data) -> Python values; strings stay bytes."""
    def dec(i):
        c = data[i:i + 1]
        if c == b'i':
            j = data.index(b'e', i)
            return int(data[i + 1:j]), j + 1
        if c in (b'l', b'd'):
            i += 1
            items = []
            while data[i:i + 1] != b'e':
                v, i = dec(i)
                items.append(v)
            return (items if c == b'l' else dict(zip(items[::2], items[1::2]))), i + 1
        j = data.index(b':', i)
        n = int(data[i:j])
        return data[j + 1:j + 1 + n], j + 1 + n
    return dec(0)[0]


def bencode(v):
    """The reverse of bdecode (dictionary keys sorted, as the format requires)."""
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int):
        return b'i%de' % v
    if isinstance(v, str):
        v = v.encode()
    if isinstance(v, bytes):
        return b'%d:%s' % (len(v), v)
    if isinstance(v, list):
        return b'l' + b''.join(bencode(x) for x in v) + b'e'
    if isinstance(v, dict):
        items = sorted((k.encode() if isinstance(k, str) else k, x) for k, x in v.items())
        return b'd' + b''.join(bencode(k) + bencode(x) for k, x in items) + b'e'
    raise TypeError(f'cannot bencode {type(v).__name__}')


def resume_progress(path):
    """[{name, length, done}] for each file of a cached torrent, from libtorrent's resume data: the torrent's
    file list and its bitmap of downloaded pieces (one bit per piece, first piece in the high bit)."""
    try:
        d = bdecode(Path(path).read_bytes())
        info, bits, plen = d[b'info'], d.get(b'pieces', b''), d[b'info'][b'piece length']
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return []
    have = lambda p: p >> 3 < len(bits) and bits[p >> 3] & (0x80 >> (p & 7))  # noqa: E731
    files = info.get(b'files') or [{b'length': info.get(b'length', 0), b'path': [info.get(b'name', b'')]}]
    out, offset = [], 0
    for f in files:
        length = f[b'length']
        done = 0
        if length:
            for p in range(offset // plen, (offset + length - 1) // plen + 1):
                if have(p):
                    done += min((p + 1) * plen, offset + length) - max(p * plen, offset)
        out.append({'idx': len(out), 'name': f[b'path'][-1].decode(errors='replace'), 'length': length,
                    'done': round(done / length, 4) if length else 0})
        offset += length
    return out


def downloaded(d):
    """How many bytes of a cached torrent's files are downloaded, by its resume data."""
    return sum(f['length'] * f['done'] for f in resume_progress(Path(d) / '.resume'))


def _pieces(d):
    try:
        return bdecode((Path(d) / '.resume').read_bytes()).get(b'pieces', b'')
    except (OSError, ValueError, IndexError, AttributeError):
        return b''


def covers(a, b):
    """Whether copy `a` of a torrent holds every piece copy `b` holds — so `b` can go without losing anything."""
    pa, pb = _pieces(a), _pieces(b)
    n = max(len(pa), len(pb))
    return int.from_bytes(pb.ljust(n, b'\0'), 'big') & ~int.from_bytes(pa.ljust(n, b'\0'), 'big') == 0


def file_paths(d):
    """The path of each file of a cached torrent, by file index, from its resume data: None for a padding file (never
    written) and for a path that would leave the folder; [] without resume data."""
    d = Path(d)
    try:
        info = bdecode((d / '.resume').read_bytes())[b'info']
        name = info[b'name'].decode(errors='replace')
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return []
    base = d.resolve()
    out = []
    for f in info.get(b'files') or [{}]:          # a single-file torrent: its name is the file
        path = d.joinpath(name, *(p.decode(errors='replace') for p in f.get(b'path', [])))
        real = path.resolve()
        inside = real != base and real.is_relative_to(base)
        out.append(path if inside and b'p' not in f.get(b'attr', b'') else None)
    return out


def footprint(d, coming, sparse):
    """The bytes a cached torrent's folder takes once the files in `coming` ({file index: length}) are complete. Each
    other file counts what it has written on a disk that can skip empty space, its whole length on disk on one that
    can't (which is what it takes there once copied)."""
    skip = {p for i, p in enumerate(file_paths(d)) if i in coming and p}
    total = sum(coming.values())
    for root, _, files in os.walk(d):
        for f in files:
            p = Path(root) / f
            if p in skip:
                continue
            try:
                st = os.lstat(p)
            except OSError:
                continue
            total += st.st_blocks * 512 if sparse else max(st.st_blocks * 512, st.st_size)
    return total


# ------------------------------------------------------------------------------------------ moving and writing out

class Stopped(Exception):
    pass


def _copy(src, dest, stop, chunk):
    """Copy a file's data in chunks, asking stop() between them, and make sure it is on the disk before the original
    goes. Only the data: exFAT keeps no owners or flags."""
    with open(src, 'rb') as i, open(dest, 'wb') as o:
        while True:
            if stop():
                raise Stopped
            b = i.read(chunk)
            if not b:
                break
            o.write(b)
        o.flush()
        os.fsync(o.fileno())


def move_folder(src, dest_root, stop=lambda: False, chunk=8 * MB):
    """Move a torrent's folder to another disk: its regular files copied beside where it goes, put in place (replacing
    an older copy there — the caller decides which copy is worth keeping), then removed here. (moved?, message); a
    copy that fails, or that stop() cuts short, is cleaned up and the original kept."""
    src, dest_root = Path(src), Path(dest_root)
    dest, tmp = dest_root / src.name, dest_root / (src.name + '.moving')
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        for root, _, files in os.walk(src):
            out = tmp / Path(root).relative_to(src)
            out.mkdir(parents=True, exist_ok=True)
            for f in files:
                p = Path(root) / f
                if p.is_symlink() or not p.is_file():
                    continue
                _copy(p, out / f, stop, chunk)
        if dest.exists():
            shutil.rmtree(dest)
        os.rename(tmp, dest)
    except Stopped:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, 'Stopped'
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, f'Could not copy it there: {e.strerror or e}'
    shutil.rmtree(src, ignore_errors=True)
    return True, 'Moved'


def allocate(path, length, stop=lambda: False, step=256 * MB):
    """Write a file out to `length` bytes, keeping what it holds: whether it got there before stop() said to stop.
    A byte every `step` is read and written back (a zero past the file's end), which makes a disk that can't skip
    empty space write out everything before it — now, a step at a time, rather than whenever a piece far into the file
    arrives. Where the file is already written out, each step costs nothing."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        for end in [*range(step, length, step), length] if length > 0 else []:
            if stop():
                return False
            os.pwrite(fd, os.pread(fd, 1, end - 1) or b'\0', end - 1)
        return True
    finally:
        os.close(fd)


def can_skip_empty_space(d):
    """Whether a file written only far into itself takes up just that write on this disk (APFS) rather than everything
    up to it (exFAT). Also a test that the folder can be written to at all. Measured once the write is on the disk:
    macOS's exFAT driver counts a file's space only then."""
    p = Path(d) / '.sparse-check'
    fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.pwrite(fd, b'x', 16 * MB)
        os.fsync(fd)
        return os.fstat(fd).st_blocks * 512 < 8 * MB
    finally:
        os.close(fd)
        p.unlink(missing_ok=True)


# ------------------------------------------------------------------------------------------ the places

class Places:
    """This Mac's cache folder, and the cache's folder on the external drive chosen on the dashboard. A place is a dict:
    id ('mac' or 'external'), name, dir, budget (bytes), available, why (it isn't), sparse (whether its disk can skip
    empty space). This Mac is always available. `mounted(path)` says whether a drive is connected at that path.

    What the places are and where they stand is read from the last refresh(), which does every look at a disk;
    `kick()` asks the thread that runs it for one now (after a setting changed)."""

    def __init__(self, config, config_file=None, mounted=os.path.ismount, volumes_root='/Volumes', clock=time.monotonic):
        self.config, self.mounted, self.clock = config, mounted, clock
        self.file = Path(config_file) if config_file else None
        self.volumes_root = Path(volumes_root)
        self.checks = {}      # folder -> (device, when, why, sparse): the last look at it
        self.held = {}        # a drive's folder -> the info hashes in it, at the last look and since
        self.since = {}       # ... the ones moved there since that look began
        self.volume_list = []
        self.kick = lambda: None
        self.checking = threading.Lock()   # one look at a time: two probes of one folder would spoil each other's
        self.lock = threading.Lock()

    # ---- looking (refresh() only)

    def refresh(self):
        """Look at each place and at the drives connected: the only disk access of Places, on a thread of its own."""
        with self.checking:
            self._look(Path(self.config['cache_dir']))
            vol = self.config.get('external_volume')
            if vol:
                self._look(Path(vol) / EXTERNAL_DIR, Path(vol))
            held = {}
            for p in self.available():
                if p['id'] != 'mac':          # this Mac's own folder is local, and looked at directly
                    try:
                        held[str(p['dir'])] = {d.name for d in p['dir'].iterdir() if IH_DIR.fullmatch(d.name)}
                    except OSError:          # keep what it was known to hold rather than lose sight of it
                        held[str(p['dir'])] = set(self.held.get(str(p['dir']), ()))
            volumes = self._volumes()
        with self.lock:
            for k, ihs in self.since.items():
                held.setdefault(k, set()).update(ihs)
            self.since, self.held, self.volume_list = {}, held, volumes

    def _look(self, d, volume=None):
        """Whether a place's folder can be used, and what its disk can do. On a drive only while it's mounted — macOS
        can leave an empty folder under /Volumes once a drive has gone, and writing there would fill this Mac's disk.
        The folder is created and written to once per mount (macOS asks before a background process may use a
        removable drive); a look that failed is taken again after CHECK_S."""
        if volume is not None and not self.mounted(volume):
            self.checks[str(d)] = (None, self.clock(), 'Not connected', None)
            return
        try:
            dev = os.stat(volume if volume is not None else d.parent).st_dev
        except OSError as e:
            self.checks[str(d)] = (None, self.clock(), e.strerror or str(e), None)
            return
        last = self.checks.get(str(d))
        if last and last[0] == dev and (last[2] is None or self.clock() - last[1] < CHECK_S):
            return
        why = sparse = None
        try:
            d.mkdir(parents=volume is None, exist_ok=True)
            sparse = can_skip_empty_space(d)
        except PermissionError:
            why = DENIED
        except OSError as e:
            why = e.strerror or str(e)
        self.checks[str(d)] = (dev, self.clock(), why, sparse)

    def _volumes(self):
        out = []
        try:
            entries = sorted(self.volumes_root.iterdir())
        except OSError:
            return out
        for v in entries:
            try:
                if (v.name.startswith('.') or os.path.realpath(v) == '/' or not self.mounted(v)
                        or not os.access(v, os.W_OK) or os.statvfs(v).f_flag & os.ST_RDONLY):
                    continue
                du = shutil.disk_usage(v)
            except OSError:
                continue
            out.append({'path': str(v), 'name': v.name, 'free': du.free, 'total': du.total})
        return out

    # ---- what the last look found

    def _seen(self, d):
        last = self.checks.get(str(d))
        return (last[2], last[3]) if last else ('Checking…', None)

    def mac(self):
        d = Path(self.config['cache_dir'])
        why, sparse = self._seen(d)
        return {'id': 'mac', 'name': 'This Mac', 'dir': d, 'budget': int(self.config.get('cache_budget_gb', 15) * GB),
                'available': True, 'why': None if why == 'Checking…' else why, 'sparse': sparse is not False}

    def external(self):
        """The chosen drive's place, connected or not; None if no drive was chosen."""
        vol = self.config.get('external_volume')
        if not vol:
            return None
        vol = Path(vol)
        why, sparse = self._seen(vol / EXTERNAL_DIR)
        return {'id': 'external', 'name': vol.name, 'dir': vol / EXTERNAL_DIR,
                'budget': int(self.config.get('external_budget_gb', 50) * GB), 'available': why is None, 'why': why,
                'sparse': sparse}

    def all(self):
        ext = self.external()
        return [self.mac()] + ([ext] if ext else [])

    def available(self):
        return [p for p in self.all() if p['available']]

    def get(self, place_id):
        return next((p for p in self.all() if p['id'] == place_id), None)

    def at(self, d):
        """The place whose folder is `d`, connected or not; None if it isn't one of the places any more."""
        return next((p for p in self.all() if str(p['dir']) == str(d)), None)

    def destination(self):
        """Where downloads for later go: the drive while it's connected (unless this Mac was chosen), else this Mac."""
        ext = self.external()
        if ext and ext['available'] and self.config.get('downloads_to', 'external') == 'external':
            return ext
        return self.mac()

    def volumes(self):
        """The drives that could hold the cache, at the last look: mounted under /Volumes, writable, and not this
        Mac's own disk."""
        return self.volume_list

    def note(self, ih, place):
        """A torrent's folder has just arrived in a place (a move): known there before the next look."""
        if place['id'] == 'mac':
            return
        with self.lock:
            self.held.setdefault(str(place['dir']), set()).add(ih)
            self.since.setdefault(str(place['dir']), set()).add(ih)

    def copies(self, ih):
        """[(place, folder)] of a torrent in the places available now. A drive is looked at only for a torrent it held
        at the last look, or that moved there since."""
        if not isinstance(ih, str) or not IH_DIR.fullmatch(ih.lower()):
            return []
        ih = ih.lower()
        return [(p, p['dir'] / ih) for p in self.available()
                if (p['id'] == 'mac' or ih in self.held.get(str(p['dir']), ())) and (p['dir'] / ih).is_dir()]

    def locate(self, ih):
        """The folder of a cached torrent, or None. With a copy in each place (the drive was away, and the TV played the
        torrent again from this Mac), the one with more downloaded — the drive's, if they have as much."""
        found = self.copies(ih)
        if len(found) < 2:
            return found[0][1] if found else None
        return max(found, key=lambda pd: (downloaded(pd[1]), pd[0]['id'] == 'external'))[1]

    def place_of(self, path):
        """The available place a torrent's folder is in, or None."""
        path = Path(path)
        return next((p for p in self.available() if path == p['dir'] or p['dir'] in path.parents), None)

    # ---- changing them

    def configure(self, change):
        """A change from the dashboard's Storage panel — {'place': 'mac'|'external', 'budget_gb': n}, {'downloads_to':
        'mac'} or {'downloads_to': 'external', 'volume': path} — applied and saved. (ok, message)."""
        if not isinstance(change, dict):
            return False, 'Nothing to change'
        if 'budget_gb' in change:
            place, n = change.get('place'), change['budget_gb']
            key = {'mac': 'cache_budget_gb', 'external': 'external_budget_gb'}.get(place)
            if key is None or (place == 'external' and not self.config.get('external_volume')):
                return False, 'No such place'
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not 1 <= n <= 100_000:
                return False, 'The limit must be a number of GB, at least 1'
            n = int(n) if n == int(n) else round(n, 1)
            ok, msg = self._save({key: n})
            return (True, f'{self.get(place)["name"]} keeps up to {n} GB') if ok else (False, msg)
        to = change.get('downloads_to')
        if to == 'mac':
            ok, msg = self._save({'downloads_to': 'mac'})
            return (True, 'Downloads for later stay on this Mac') if ok else (False, msg)
        if to == 'external':
            vol = change.get('volume') or self.config.get('external_volume')     # none: the drive chosen before
            if not isinstance(vol, str) or (vol != self.config.get('external_volume')
                                            and vol not in [v['path'] for v in self.volumes()]):
                return False, 'That drive isn\'t connected'
            ok, msg = self._save({'external_volume': vol, 'downloads_to': 'external'})
            if ok:
                self.kick()           # a drive just chosen is looked at now rather than in a few seconds
            return (True, f'Downloads for later go to {Path(vol).name}') if ok else (False, msg)
        return False, 'Nothing to change'
    def _save(self, changes):
        """Apply settings, and write them to torrentd.json beside the ones already there. (ok, message)."""
        with self.lock:
            if self.file is not None:
                try:
                    saved = json.loads(self.file.read_text()) if self.file.exists() else {}
                    if not isinstance(saved, dict):
                        raise ValueError
                except (OSError, ValueError):
                    return False, f'{self.file.name} couldn\'t be read, so nothing was changed. Fix or delete it, then try again.'
                saved.update(changes)
                tmp = self.file.with_name(self.file.name + '.tmp')
                try:
                    tmp.write_text(json.dumps(saved, indent=2) + '\n')
                    os.replace(tmp, self.file)
                except OSError as e:
                    return False, f'Could not save the settings: {e.strerror or e}'
            self.config.update(changes)
        return True, None
