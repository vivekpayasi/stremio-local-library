"""The dashboard behind /ui: what is playing, how healthy the stream is, and the state of the Mac, the network
and the TV — gathered here and served as one JSON document the page polls.

Cheap things (the torrent sessions) are read on every poll. Slow things — the Mac's Wi-Fi report (~4 s), a
ping to the TV, the TV itself over ADB, the Local Library addon — refresh on a background thread, and only
while someone has the dashboard open.
"""
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

from media_text import EPISODE, SAMPLE, VIDEO, clock, episode_key, episode_label, next_episode  # noqa: F401 (re-exported for tests)
from prefetch import ACTIVE as JOB_ACTIVE

MB = 1024 * 1024
HERE = Path(__file__).resolve().parent
PAGE = HERE / 'dashboard.html'


# ------------------------------------------------------------------------------------------ parsing

def parse_ping(out):
    """macOS `ping` summary -> {'ms': average or None, 'loss': percent}."""
    loss = re.search(r'([\d.]+)% packet loss', out or '')
    avg = re.search(r'= [\d.]+/([\d.]+)/', out or '')
    return {'ms': round(float(avg.group(1)), 1) if avg else None, 'loss': int(float(loss.group(1))) if loss else 100}


def parse_mac_wifi(out):
    """The connected network from `system_profiler SPAirPortDataType`, or None. The network's name is left out."""
    lines = (out or '').splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == 'Current Network Information:')
    except StopIteration:
        return None
    base = len(lines[start]) - len(lines[start].lstrip())
    fields = {}
    for line in lines[start + 1:]:
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent <= base:
            break
        if ':' in line:
            k, v = line.strip().split(':', 1)
            fields.setdefault(k.strip(), v.strip())
    if not fields:
        return None
    out = {}
    if 'PHY Mode' in fields:
        out['phy'] = fields['PHY Mode']
    m = re.match(r'(\d+)\s*\(([^,]+),\s*([^)]+)\)', fields.get('Channel', ''))
    if m:
        out.update(channel=int(m.group(1)), band=m.group(2).strip(), width=m.group(3).strip())
    m = re.match(r'(-?\d+) dBm / (-?\d+) dBm', fields.get('Signal / Noise', ''))
    if m:
        out.update(rssi=int(m.group(1)), noise=int(m.group(2)))
    if re.fullmatch(r'\d+', fields.get('Transmit Rate', '')):
        out['rate'] = int(fields['Transmit Rate'])
    return out or None


TV_COMMAND = ('getprop ro.product.model; getprop ro.build.version.release; echo ===; '
              'dumpsys wifi | grep -m1 mWifiInfo; echo ===; grep -E "^Mem(Total|Available)" /proc/meminfo; echo ===; '
              'top -b -n 1 -o PID,%CPU,RES,ARGS | grep com.stremio | grep -v grep; echo ===; '
              'logcat -d -v time -t 1500 | grep -E "I/ExoPlayerImpl|E/ExoPlayerImplInternal|ExoPlaybackException|'
              'D/MediaCodec.*create \\+|V/mpv|lowmemorykiller.*Kill|aml_audio_dts_dec" | tail -60')

AUDIO_CODEC_NAME = re.compile(r'\.audio\.|aac|mp3|opus|vorbis|flac|raw|amr|g711|alac|ape', re.I)


def _band(mhz):
    return '2.4GHz' if mhz < 3000 else '5GHz' if mhz < 5925 else '6GHz'


def parse_tv_snapshot(out):
    """The output of TV_COMMAND -> device, Wi-Fi, memory, Stremio's process and what its player is doing.
    Network names and hardware addresses are dropped: the page can be open on any device on the LAN."""
    parts = (out or '').replace('\r', '').split('===\n')
    parts += [''] * (5 - len(parts))
    head = [l.strip() for l in parts[0].splitlines() if l.strip()]
    tv = {'model': head[0] if head else None, 'android': head[1] if len(head) > 1 else None,
          'wifi': None, 'memory': None, 'stremio': None}

    w = parts[1]
    if 'RSSI' in w:
        num = lambda k: (lambda m: int(m.group(1)) if m else None)(re.search(rf'{k}: (-?\d+)', w))  # noqa: E731
        freq, std = num('Frequency'), num('Wi-Fi standard')
        tv['wifi'] = {'rssi': num('RSSI'), 'link': num('Link speed'), 'band': _band(freq) if freq else None,
                      'standard': f'Wi-Fi {std}' if std else None}

    mem = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+) kB', parts[2], re.M))
    if mem:
        tv['memory'] = {'total_mb': round(int(mem.get('MemTotal', 0)) / 1024),
                        'available_mb': round(int(mem.get('MemAvailable', 0)) / 1024)}

    for line in parts[3].splitlines():
        cols = line.split()
        if len(cols) >= 4 and cols[3] == 'com.stremio.one':
            tv['stremio'] = {'cpu': float(cols[1]), 'memory': cols[2]}

    player = {'name': None, 'video_decoder': None, 'error': None, 'error_at': None, 'dts_on_tv': False,
              'low_memory_kills': 0}
    lines = parts[4].splitlines()
    for i, line in enumerate(lines):
        clock = re.match(r'\d\d-\d\d (\d\d:\d\d:\d\d)', line)
        if 'I/ExoPlayerImpl' in line and ': Init ' in line:
            player['name'] = 'ExoPlayer'
        elif 'I/ExoPlayerImpl' in line and ': Release ' in line:
            player['name'] = None
        elif 'V/mpv' in line:
            player['name'] = 'mpv'
        elif 'create +' in line:
            name = line.split('create +', 1)[1].strip()
            if not AUDIO_CODEC_NAME.search(name):
                player['video_decoder'] = name
        elif 'Playback error' in line:
            detail = lines[i + 1] if i + 1 < len(lines) and 'Exception' in lines[i + 1] else line
            msg = detail.split('Exception: ', 1)[-1].strip()
            player['error'] = msg[:300]
            player['error_at'] = clock.group(1) if clock else None
        elif 'aml_audio_dts_dec' in line:
            player['dts_on_tv'] = True
        elif 'lowmemorykiller' in line and 'Kill' in line:
            player['low_memory_kills'] += 1
    tv['player'] = player
    return tv


NOISE = re.compile(r' (?:GET|HEAD|POST) /(?:settings|network-info|device-info|casting|torrentd/)|/stats\.json')


def filter_log(text, limit=40):
    """The interesting tail of torrent.log: the TV's constant status polling and the dashboard's own reads
    are left out."""
    out = []
    for line in (text or '').splitlines():
        if not line.strip() or NOISE.search(line):
            continue
        m = re.match(r'\d{4}-\d\d-\d\dT(\d\d:\d\d:\d\d)\.\d+Z (.*)', line)
        time_, text = (m.group(1), m.group(2)) if m else ('', line.rstrip())
        # Query strings can carry another addon's URL, debrid token and all: the page may be open on any device.
        out.append({'time': time_, 'text': re.sub(r'\?\S+', '?…', text)})
    return out[-limit:]


# ------------------------------------------------------------------------------------------ sessions

def ui_allowed(client, host, fetch_site, lan, own, cert_suffix):
    """Whether a request may read the dashboard. Only names of this Mac are accepted as Host (a site that
    points its own name at 127.0.0.1 — DNS rebinding — gets nothing), browsers' cross-site fetches are refused
    (any web page could otherwise read localhost), and other devices need the LAN switch."""
    if fetch_site == 'cross-site':
        return False
    name = (host or '').rsplit(':', 1)[0].strip('[]').lower()
    if not (name in ('localhost', '127.0.0.1', '::1') or name in own or (cert_suffix and name.endswith(cert_suffix))):
        return False
    return client.startswith('127.') or bool(lan)


def ui_write_allowed(client, host, fetch_site, marker, lan, own, cert_suffix):
    """Whether a request may change something (delete from the cache). On top of ui_allowed: it must come from
    the dashboard page itself — a same-origin fetch carrying the X-Dashboard header, which a form on another
    site can't send and a cross-site script can't add without a preflight this server never answers."""
    if marker != '1' or fetch_site not in (None, 'same-origin'):
        return False
    return ui_allowed(client, host, fetch_site, lan, own, cert_suffix)


IH_DIR = re.compile(r'[0-9a-f]{40}')
RECENT_S = 120   # used this recently = in use: after a restart, a reader takes a moment to reconnect


def _file_ref(ih, idx):
    """Whether (ih, idx) from a request names a file of a torrent: a cache folder's hash and a file index."""
    return isinstance(ih, str) and bool(IH_DIR.fullmatch(ih)) and isinstance(idx, int) and not isinstance(idx, bool)


def _last_used(d):
    try:
        return float((d / '.last-used').read_text())
    except (OSError, ValueError):
        return d.stat().st_mtime


def _in_use(d, in_session):
    return d.name in in_session or time.time() - _last_used(d) < RECENT_S


def _usage(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_blocks * 512
            except OSError:
                pass
    return total


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


def cache_entries(cache_dir, in_session, titles, live_progress=None, busy_files=None):
    """Every torrent in the cache, most recently used first: its name, title, size on disk, whether it's in use,
    and how much of each file that has any data is downloaded (live for a torrent in the session)."""
    rows = []
    try:
        dirs = [d for d in Path(cache_dir).iterdir() if d.is_dir() and IH_DIR.fullmatch(d.name)]
    except OSError:
        return rows
    for d in dirs:
        used = _last_used(d)
        names = sorted(p.name for p in d.iterdir() if not p.name.startswith('.'))
        t = titles.get(d.name) or {}
        files = (live_progress(d.name) if live_progress and d.name in in_session else None) or resume_progress(d / '.resume')
        busy = busy_files(d.name) if busy_files and d.name in in_session else set()
        files = [dict(f, idx=f.get('idx', i), busy=f.get('idx', i) in busy) for i, f in enumerate(files)]
        rows.append({'ih': d.name, 'name': names[0] if names else d.name[:8], 'title': t.get('name'),
                     'poster': t.get('poster'), 'bytes': _usage(d), 'last_used': used, 'in_use': _in_use(d, in_session),
                     # Videos only (not the .nfo or poster that come along), at least 1% in (not a piece shared
                     # with the neighbouring episode), most complete first.
                     'videos': [f for f in sorted(files, key=lambda f: f['idx']) if VIDEO.search(f['name'])],
                     'files': sorted((f for f in files if f['done'] >= 0.01 and VIDEO.search(f['name'])),
                                     key=lambda f: -f['done'])[:6],
                     'next': next_episode(files)})
    rows.sort(key=lambda r: r['last_used'], reverse=True)
    return rows


def cached_progress(cache_dir, in_session, live_progress=None):
    """{info hash: {file index: fraction downloaded}} for every file at least 1% on this Mac — what the advisor
    shows on the TV's stream cards. Live for torrents in the session, from resume data for the rest."""
    out = {}
    try:
        dirs = [d for d in Path(cache_dir).iterdir() if d.is_dir() and IH_DIR.fullmatch(d.name)]
    except OSError:
        return out
    for d in dirs:
        files = (live_progress(d.name) if live_progress and d.name in in_session else None) or resume_progress(d / '.resume')
        got = {str(i): round(f['done'], 4) for i, f in enumerate(files) if f['done'] >= 0.01}
        if got:
            out[d.name] = got
    return out


def delete_file_cached(cache_dir, ih, idx):
    """Delete one file of a cached torrent — an episode of a season pack — that is not in the session:
    (deleted?, message, bytes freed). Its pieces are marked missing in the resume data first, including the
    ones it shares with its neighbours, so libtorrent fetches them again rather than reading a file that is
    gone; then the file is removed."""
    if not isinstance(ih, str) or not IH_DIR.fullmatch(ih) or not isinstance(idx, int) or isinstance(idx, bool):
        return False, 'Not a file in the cache', 0
    d = Path(cache_dir) / ih
    try:
        resume = bdecode((d / '.resume').read_bytes())
        info, plen = resume[b'info'], resume[b'info'][b'piece length']
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return False, 'Not a file in the cache', 0
    files = info.get(b'files')
    entries = files or [{b'length': info.get(b'length', 0), b'path': []}]
    if not 0 <= idx < len(entries):
        return False, 'Not a file in the cache', 0
    offset = sum(f[b'length'] for f in entries[:idx])
    length = entries[idx][b'length']
    path = d.joinpath(info[b'name'].decode(errors='replace'), *(p.decode(errors='replace') for p in entries[idx][b'path']))
    if not path.resolve().is_relative_to(d.resolve()):
        return False, 'Not a file in the cache', 0
    first, last = offset // plen, (offset + max(length, 1) - 1) // plen

    bits = bytearray(resume.get(b'pieces', b''))
    for p in range(first, last + 1):
        if p >> 3 < len(bits):
            bits[p >> 3] &= ~(0x80 >> (p & 7)) & 0xFF
    resume[b'pieces'] = bytes(bits)
    resume[b'unfinished'] = [u for u in resume.get(b'unfinished', []) if not first <= u.get(b'piece', -1) <= last]
    if isinstance(resume.get(b'file_priority'), list) and idx < len(resume[b'file_priority']):
        resume[b'file_priority'][idx] = 0
    tmp = d / '.resume.tmp'
    tmp.write_bytes(bencode(resume))
    os.replace(tmp, d / '.resume')

    freed = 0
    try:
        freed = path.lstat().st_blocks * 512
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        return False, f'Could not delete the file: {e.strerror}', 0
    return True, 'Deleted', freed


def delete_cached(cache_dir, ih, in_session):
    """(deleted?, message, bytes freed). Only a torrent folder of the cache, and never one in use."""
    if not isinstance(ih, str) or not IH_DIR.fullmatch(ih):
        return False, 'Not a torrent in the cache', 0
    d = Path(cache_dir) / ih
    if not d.is_dir():
        return False, 'Already gone', 0
    if _in_use(d, in_session):
        return False, 'It\'s in use. It can be deleted a few minutes after it was last read.', 0
    freed = _usage(d)
    shutil.rmtree(d, ignore_errors=True)
    return not d.exists(), 'Deleted', freed


def clear_cache(cache_dir, in_session):
    """Delete every torrent not in use: ([deleted info hashes], bytes freed)."""
    deleted, freed = [], 0
    for row in cache_entries(cache_dir, in_session, {}):
        if row['in_use']:
            continue
        ok, _, n = delete_cached(cache_dir, row['ih'], in_session)
        if ok:
            deleted.append(row['ih'])
            freed += n
    return deleted, freed


def title_for(titles, ih, idx, file_name):
    """What the advisor recorded for this file. Season packs share one hash across episodes, so a match on
    the hash alone takes the episode from the file name rather than from whichever episode was browsed last."""
    exact = titles.get(f'{ih}:{idx}')
    if exact:
        return exact
    t = titles.get(ih)
    if not t or t.get('type') != 'series':
        return t
    m = EPISODE.search(file_name or '')
    season, episode = (m.group(1) or m.group(3), m.group(2) or m.group(4)) if m else (None, None)
    return dict(t, season=int(season) if season else None, episode=int(episode) if episode else None)


def account_status(line):
    """The last self-heal line from addon.log, in plain words."""
    if not line:
        return None
    m = re.match(r'(\S+Z)\s+autoheal:\s*(.*)', line)
    at, msg = (m.group(1), m.group(2)) if m else (None, line)
    if re.search(r'already correct|INSTALLED|URL updated|entry .*updated', msg):
        return {'text': 'Your Stremio account is up to date', 'ok': True, 'at': at}
    if 'authKey' in msg:
        return {'text': 'Stremio desktop is signed out, so the account can\'t be updated', 'ok': False, 'at': at}
    why = re.sub(r'https://\S+', '', msg).strip()[:160]
    return {'text': f'The last account update failed: {why}', 'ok': False, 'at': at}


def last_tv_ip(log_text, own):
    """The last device in torrent.log that polled the server the way a Stremio client does (its status checks
    or a stream's stats), other than this Mac — the TV, even before it has talked to a freshly started server."""
    ip = None
    for m in re.finditer(r'^\S+Z (\d+\.\d+\.\d+\.\d+) GET (?:/settings|/network-info|/device-info|/casting|/\S*/stats\.json) ',
                         log_text or '', re.M):
        if m.group(1) not in own:
            ip = m.group(1)
    return ip


def piece_strip(have, buckets=120):
    """Downloaded fraction per bucket across a file's pieces — the strip drawn under what's playing."""
    n = len(have)
    if not n:
        return []
    if n < buckets:
        return [1.0 if have[i * n // buckets] else 0.0 for i in range(buckets)]
    out = []
    for i in range(buckets):
        a, b = i * n // buckets, (i + 1) * n // buckets
        out.append(round(sum(1 for x in have[a:b] if x) / (b - a), 3))
    return out


def classify_requests(reqs, size, duration_s=None, now=None, stalls=()):
    """What the player's requests say happened, newest first: seeks, stalls waiting for pieces (the swarm), and
    the player re-reading one spot over and over (the player, or a file laid out badly for it). Short reads near
    the end of a large file are the player reading the index, not seeks. Stalls are recorded by the server the
    moment a piece took long to arrive (`stalls`); a request's total wait would only say so once the request
    ends, which for a player streaming a whole film is the end of the film. Time spent sending says nothing: the TV
    reads only as fast as it plays, so a healthy request spends most of its life waiting to send. The network
    is judged by the ping to the TV instead (see health)."""
    events, prev_end, recent_starts, last_reread = [], None, [], None

    def where(byte):
        return clock(byte / size * duration_s) if duration_s and size else f'{byte / size:.0%}' if size else ''

    def add(r, kind, text, cause):
        events.append({'at': r['at'], 'kind': kind, 'text': text, 'cause': cause,
                       'pos': round(r['start'] / size, 4) if size else None, 'recent': bool(now and now - r['at'] < 30)})

    for r in reqs:
        if size > 256 * MB and r['start'] > size - 64 * MB and r['sent'] < 4 * MB:
            continue
        if prev_end is not None and abs(r['start'] - prev_end) > 32 * MB:
            add(r, 'seek', f'Jumped to {where(r["start"])}', None)
        recent_starts = [(t, s) for t, s in recent_starts if r['at'] - t <= 30] + [(r['at'], r['start'])]
        close = [s for _, s in recent_starts if abs(s - r['start']) <= MB]
        if len(close) >= 3 and (last_reread is None or abs(last_reread - r['start']) > MB):
            add(r, 'reread', f'The player re-read {where(r["start"])} {len(close)} times', 'player')
            last_reread = r['start']
        prev_end = r['start'] + r['sent']
    # A wait in the middle of data that was flowing is a stall. Waiting for the first pieces of the stream, or
    # of the spot just jumped to, is ordinary start-up and buffering — slow, perhaps, but not a failing torrent.
    first_at = min((r['at'] for r in reqs), default=None)
    for st in stalls:
        at, w, place = {'at': st['at'], 'start': st['pos']}, st['wait'], where(st['pos'])
        if st.get('mid', True):
            add(at, 'stall', f'Stalled for {w:.1f} s at {place}', 'swarm')
        elif first_at is not None and st['at'] - first_at < 30:
            add(at, 'startup', f'Took {w:.1f} s to get the first pieces', None)
        else:
            add(at, 'buffering', f'Buffered for {w:.1f} s after jumping to {place}', 'swarm')
    events.sort(key=lambda e: e['at'], reverse=True)
    return events


def _chips(facts, file_name):
    if not facts:
        ext = re.search(r'\.([a-z0-9]{2,4})$', file_name or '', re.I)
        return [ext.group(1).upper()] if ext else []
    v = facts.get('video') or {}
    chips = [c.upper() for c in [facts.get('container')] if c]
    codec = {'hevc': 'HEVC', 'h264': 'H.264', 'av1': 'AV1', 'vp9': 'VP9'}.get(v.get('codec'), (v.get('codec') or '').upper())
    if codec:
        chips.append(codec)
    w, h = v.get('width') or 0, v.get('height') or 0
    res = '4K' if w >= 3000 or h >= 1600 else '1080p' if w >= 1700 or h >= 900 else '720p' if w >= 1100 else ''
    if res:
        chips.append(res)
    if v.get('dv'):
        chips.append(f'Dolby Vision {v["dv"].get("profile")}')
    elif v.get('transfer') == 'smpte2084':
        chips.append('HDR10')
    elif v.get('transfer') == 'arib-std-b67':
        chips.append('HLG')
    audio = facts.get('audio') or []
    if audio:
        first = next((a for a in audio if a.get('default')), audio[0])
        name = {'eac3': 'E-AC3', 'ac3': 'AC3', 'truehd': 'TrueHD', 'aac': 'AAC', 'dts': first.get('profile') or 'DTS',
                'flac': 'FLAC', 'opus': 'Opus'}.get(first.get('codec'), (first.get('codec') or '').upper())
        chips.append(name + (f' + {len(audio) - 1} more' if len(audio) > 1 else ''))
    return chips


def summarize_session(snap, facts, title, now):
    """One stream as the page shows it. `snap` comes from the torrent server, `facts` from stream-facts.json,
    `title` from the advisor's stream-titles.json."""
    size, plen = snap['size'], snap['piece_len']
    pos = min(snap['pos_byte'], size)
    first = snap['first_piece']
    piece = (snap['file_offset'] + pos) // plen
    run = 0
    have = snap['have']
    while 0 <= piece - first + run < len(have) and have[piece - first + run]:
        run += 1
    end = min((piece + run) * plen, snap['file_offset'] + size)
    ahead = max(0, end - (snap['file_offset'] + pos)) if run else 0
    complete = pos + ahead >= size      # everything from the TV's read position to the end is on this Mac
    # Once the end has been sent, the TV plays the rest from its own buffer without asking for more.
    live = snap['live'] or (complete and now - snap['pos_at'] < 120)

    bitrate = (facts or {}).get('bitrateMbps')
    duration = (facts or {}).get('durationS')
    events = classify_requests(snap.get('requests') or [], size, duration, now, snap.get('stalls') or ())
    t = title or {}
    if t.get('season') is not None and t.get('episode') is not None:
        subtitle = f'Season {t["season"]}, episode {t["episode"]}'
    else:
        subtitle = str(t.get('year') or '') or None
    hidden = snap.get('mp4_hidden')
    mp4_fix = None if hidden is None else (
        f'Hiding {hidden} subtitle tracks stored away from the video, so the player doesn\'t jump around the file'
        if hidden else 'Subtitles are stored with the video — no fix needed')
    return {
        'ih': snap['ih'], 'idx': snap['idx'], 'live': live, 'complete': complete, 'pos_at': snap['pos_at'],
        'waiting_s': snap.get('waiting_s') or 0, 'waiting_mid': bool(snap.get('waiting_mid')),
        'starting': (lambda first: first is not None and now - first < 30)(
            min((r['at'] for r in snap.get('requests') or []), default=None)),
        'title': t.get('name') or snap['torrent'], 'subtitle': subtitle, 'poster': t.get('poster'),
        'file': snap['file'], 'size': size,
        'position': pos / size if size else 0, 'position_s': pos / size * duration if size and duration else None,
        'duration_s': duration, 'ahead_bytes': ahead, 'ahead_end': (pos + ahead) / size if size else 0,
        'ahead_s': ahead * 8 / (bitrate * 1e6) if bitrate else None,
        'bitrate_mbps': bitrate, 'download_mbps': round(snap['download_bps'] * 8 / 1e6, 1),
        'peers': snap['peers'], 'strip': piece_strip(have), 'chips': _chips(facts, snap['file']),
        'verified': bool(facts), 'mp4_fix': mp4_fix, 'events': events[:12],
        'ended_ago_s': None if live else round(now - snap['pos_at']),
    }


def health(s, tv_ping=None):
    """One sentence on how the stream is doing, and a level for its colour and icon."""
    if not s:
        return {'level': 'idle', 'text': 'Nothing is playing'}
    if not s.get('live'):
        return {'level': 'idle', 'text': 'Nothing is playing now'}
    recent = [e for e in s.get('events') or [] if e.get('recent')]
    waiting = s.get('waiting_s') or 0
    if waiting >= 3:
        if s.get('waiting_mid'):
            return {'level': 'bad', 'text': f'Stalled, waiting for pieces ({waiting:.0f} s) — the torrent can\'t keep up'}
        if s.get('starting'):
            return {'level': 'idle', 'text': f'Starting, getting the first pieces ({waiting:.0f} s)'}
        return {'level': 'warn', 'text': f'Buffering after a jump ({waiting:.0f} s)'}
    if any(e['kind'] == 'stall' for e in recent):
        return {'level': 'bad', 'text': 'The stream stalled waiting for pieces — the torrent can\'t keep up'}
    if any(e['kind'] == 'buffering' for e in recent):
        return {'level': 'warn', 'text': 'Buffering after a jump'}
    # A ping that gets no answer proves nothing (some TVs ignore ping); a slow answer does.
    if tv_ping and tv_ping.get('ms') is not None and tv_ping['ms'] >= 50:
        return {'level': 'warn', 'text': 'The TV\'s Wi-Fi is slow to answer — playback may stutter'}
    if s.get('complete'):
        return {'level': 'good', 'text': 'Playing, the rest of the file is on this Mac'}
    if any(e['kind'] == 'reread' for e in recent):
        return {'level': 'warn', 'text': 'The player keeps re-reading one spot'}
    ahead, down, need = s.get('ahead_s'), s.get('download_mbps'), s.get('bitrate_mbps')
    if ahead is not None and ahead < 15:
        if need and down is not None and down < need:
            return {'level': 'warn', 'text': f'Buffer is low: the swarm sends {down:.0f} Mbps, the file needs {need:.0f}'}
        return {'level': 'warn', 'text': 'Buffer is low'}
    if ahead is not None:
        return {'level': 'good', 'text': f'Playing smoothly, {clock(ahead)} buffered ahead'}
    return {'level': 'good', 'text': f'Playing, {s.get("ahead_bytes", 0) / MB:.0f} MB buffered ahead'}


# ------------------------------------------------------------------------------------------ collector

def _run(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def _tail(path, size=96 * 1024):
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - size))
            return f.read().decode(errors='replace')
    except OSError:
        return ''


def _local_json(url, timeout=4):
    """JSON from our own services on this Mac. They use Stremio's certificate, which names the LAN hostname,
    not 127.0.0.1 — so this loopback-only call skips the name check."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, context=ctx, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


class Dashboard:
    """state() is what /ui/api/state returns. `sessions()` returns raw stream snapshots from the torrent engine;
    `facts(key)` reads the probe store."""

    VIEWED_S = 60          # keep the slow collectors running this long after the last poll

    def __init__(self, sessions, facts, config, install_dir, log, tv_ip=lambda: None, started=None,
                 phone_url=lambda: None, in_session=lambda: set(), live_progress=None, busy_files=None, offline=None,
                 prefetch=None):
        self.sessions, self.facts, self.config, self.log = sessions, facts, config, log
        self.prefetcher = prefetch      # prefetch.Prefetcher: downloads for later
        self.in_session, self.live_progress = in_session, live_progress
        self.busy_files = busy_files    # ih -> {file index being read}, for torrents in the session
        self.offline = offline          # (ih, fn) -> fn()'s result with the torrent out of the session, or None if busy
        self.phone_url = phone_url
        self.dir = Path(install_dir)
        self.tv_ip = tv_ip
        self.started = started or time.time()
        self.last_poll = 0.0
        self.slow = {'mac_wifi': None, 'tv_ping': None, 'tv': None, 'library': None, 'advisor': None,
                     'process': None, 'cache': None, 'autoheal': None}
        self.history = {}      # ih:idx -> deque of (time, ahead_s, download_mbps, ahead_mb)
        self.seen = {}         # ih:idx -> (last summary, when) — what was played, after its torrent left the session
        self.titles = {'mtime': None, 'data': {}}
        self.adb = shutil.which('adb', path=os.environ.get('PATH', '') + ':/opt/homebrew/bin:/usr/local/bin')
        self.adb_retry_at = 0.0  # a TV that hasn't allowed debugging isn't asked again for a while
        self.lock = threading.Lock()
        threading.Thread(target=self._collect, name='dashboard', daemon=True).start()

    # ---- slow collectors

    def _collect(self):
        due = {}
        while True:
            time.sleep(1)
            if time.time() - self.last_poll > self.VIEWED_S:
                continue
            now = time.time()
            for name, every, fn in (('process', 5, self._process), ('cache', 20, self._cache),
                                    ('tv_ping', 5, self._ping), ('mac_wifi', 60, self._mac_wifi),
                                    ('tv', 30, self._tv), ('library', 30, self._library)):
                if now >= due.get(name, 0):
                    due[name] = now + every
                    try:
                        fn()
                    except Exception as e:
                        self.log(f'dashboard: {name} failed: {type(e).__name__}: {e}')

    def _process(self):
        out = _run(['ps', '-o', '%cpu=,rss=', '-p', str(os.getpid())]).split()
        if len(out) == 2:
            self.slow['process'] = {'cpu': float(out[0]), 'rss_mb': round(int(out[1]) / 1024)}

    def _cache(self):
        cache = Path(self.config['cache_dir'])
        used = 0
        for root, _, files in os.walk(cache):
            for f in files:
                try:
                    used += os.lstat(os.path.join(root, f)).st_blocks * 512
                except OSError:
                    pass
        self.slow['cache'] = {'used': used, 'budget': self.config['cache_budget_gb'] * 1024 * MB,
                              'free': shutil.disk_usage(cache).free}
        self.slow['cache_list'] = cache_entries(cache, self.in_session(), self._titles(), self.live_progress, self.busy_files)

    # ---- changes, from the dashboard's buttons

    def delete(self, ih):
        ok, msg, freed = delete_cached(self.config['cache_dir'], ih, self.in_session())
        if ok:
            self.log(f'cache: deleted {ih[:8]} ({freed / MB:.0f} MB) from the dashboard')
        self._cache()
        return {'ok': ok, 'message': msg, 'freed': freed}

    def delete_file(self, ih, idx):
        """Delete one episode of a cached season pack, unless something in that torrent is being read."""
        def no(message):
            return {'ok': False, 'message': message, 'freed': 0}
        if not _file_ref(ih, idx):
            return no('Not a file in the cache')
        busy = self.busy_files(ih) if self.busy_files and ih in self.in_session() else set()
        if idx in busy:
            return no('It\'s being read right now, by the TV or a download.')
        if busy:
            return no('Another file of this torrent is being read right now. You can delete this one once it stops.')
        run = self.offline or (lambda _ih, fn: fn())
        result = run(ih, lambda: delete_file_cached(self.config['cache_dir'], ih, idx))
        if not result:
            return no('The torrent is busy. Try again in a minute.')
        ok, msg, freed = result
        if ok:
            self.log(f'cache: deleted file {idx} of {ih[:8]} ({freed / MB:.0f} MB) from the dashboard')
        self._cache()
        return {'ok': ok, 'message': msg, 'freed': freed}

    def clear(self):
        deleted, freed = clear_cache(self.config['cache_dir'], self.in_session())
        if deleted:
            self.log(f'cache: cleared {len(deleted)} torrent(s), {freed / MB:.0f} MB, from the dashboard')
        self._cache()
        return {'ok': True, 'message': f'Deleted {len(deleted)} torrent(s)', 'freed': freed}

    def _files(self, ih):
        """[{idx, name, length, done}] for each file of a cached torrent: live while it's in the session."""
        files = self.live_progress(ih) if self.live_progress and ih in self.in_session() else None
        return files or resume_progress(Path(self.config['cache_dir']) / ih / '.resume')

    def prefetch(self, ih, idx):
        """Download one file of a cached torrent for later — the next episode of a season pack — if it fits on disk."""
        def no(message):
            return {'ok': False, 'message': message}
        if not self.prefetcher:
            return no('Downloads for later are off')
        f = next((f for f in self._files(ih) if f['idx'] == idx), None) if _file_ref(ih, idx) else None
        if not f:
            return no('Not a file in the cache')
        label = episode_label(f['name'])
        if f['done'] >= 1:
            return no(f'{label} is already on this Mac')
        def left(files, i):
            return next((g['length'] * (1 - g['done']) for g in files if g['idx'] == i), 0)
        gb = lambda n: f'{n / 1024 / MB:.1f} GB'  # noqa: E731
        others = [j for j in self.prefetcher.jobs() if j['state'] in JOB_ACTIVE and (j['ih'], j['idx']) != (ih, idx)]
        # Past the budget the cache deletes an idle torrent whole, the episode downloaded for later with it. So the
        # torrent must fit with the rest of this file, of the files being streamed from it and of its other downloads.
        files = self._files(ih)
        busy = self.busy_files(ih) if self.busy_files and ih in self.in_session() else set()
        coming = {idx} | busy | {j['idx'] for j in others if j['ih'] == ih}
        pack = _usage(Path(self.config['cache_dir']) / ih) + sum(left(files, i) for i in coming)
        budget = self.config['cache_budget_gb'] * 1024 * MB
        if pack > budget:
            return no(f'{label} would bring this torrent to {gb(pack)}, past the {gb(budget)} the cache keeps, and the cache '
                      f'would then delete all of it. Delete the episodes you\'ve watched first.')
        # The disk: the rest of this file and of every other download still to come.
        need = left(files, idx) + sum(left(self._files(j['ih']), j['idx']) for j in others)
        keep = self.config.get('min_free_gb', 3) * 1024 * MB
        free = shutil.disk_usage(self.config['cache_dir']).free
        if need > free - keep:
            return no(f'Not enough free disk space: {"this and the downloads queued before it need" if others else f"{label} needs"} '
                      f'{gb(need)}, and only {gb(max(0, free - keep))} can be used ({keep / 1024 / MB:.0f} GB is kept free)')
        ok, message = self.prefetcher.start(ih, idx, label)
        return {'ok': ok, 'message': message}

    def cancel_prefetch(self, ih, idx):
        if not self.prefetcher or not _file_ref(ih, idx):
            return {'ok': False, 'message': 'Nothing is downloading for that file'}
        ok, message = self.prefetcher.cancel(ih, idx)
        return {'ok': ok, 'message': message}

    def _ping(self):
        ip = self.tv_ip()
        self.slow['tv_ping'] = dict(parse_ping(_run(['ping', '-c', '3', '-i', '0.2', '-t', '3', ip], 6)), ip=ip) if ip else None

    def _mac_wifi(self):
        self.slow['mac_wifi'] = parse_mac_wifi(_run(['system_profiler', 'SPAirPortDataType'], 20))

    def _tv(self):
        ip = self.tv_ip()
        if not (self.config.get('dashboard_tv_adb', True) and self.adb and ip):
            self.slow['tv'] = {'state': 'off', 'why': 'adb is not installed' if not self.adb else 'TV not seen yet'}
            return
        ping = self.slow.get('tv_ping') or {}
        if ping.get('ms') is None:
            self.slow['tv'] = {'state': 'unreachable', 'ip': ip}
            return
        serial = f'{ip}:5555'
        devices = _run([self.adb, 'devices'])
        if not re.search(rf'^{re.escape(serial)}\s+device$', devices, re.M):
            if time.time() < self.adb_retry_at:
                return
            # Each connect can put "Allow debugging?" on screen, over whatever is playing: ask rarely.
            self.adb_retry_at = time.time() + 600
            _run([self.adb, 'connect', serial], 8)
            devices = _run([self.adb, 'devices'])
            if not re.search(rf'^{re.escape(serial)}\s+device$', devices, re.M):
                state = 'unauthorized' if re.search(rf'^{re.escape(serial)}\s+unauthorized', devices, re.M) else 'no-adb'
                self.slow['tv'] = {'state': state, 'ip': ip}
                return
        out = _run([self.adb, '-s', serial, 'shell', TV_COMMAND], 20)
        self.slow['tv'] = dict(parse_tv_snapshot(out), state='ok', ip=ip, at=time.time()) if out else {'state': 'no-adb', 'ip': ip}

    def _library(self):
        port = 12471
        self.slow['library'] = _local_json(f'https://127.0.0.1:{port}/health')
        self.slow['advisor'] = _local_json(f'https://127.0.0.1:{port}/advisor/health')
        heal = [l for l in _tail(self.dir / 'addon.log').splitlines() if 'autoheal:' in l]
        self.slow['autoheal'] = heal[-1][:300] if heal else None

    # ---- titles from the advisor

    def _titles(self):
        p = self.dir / 'stream-titles.json'
        try:
            m = p.stat().st_mtime
            if m != self.titles['mtime']:
                self.titles = {'mtime': m, 'data': json.loads(p.read_text())}
        except (OSError, ValueError):
            pass
        return self.titles['data']

    # ---- the document

    def state(self):
        with self.lock:          # the Mac and a phone can poll at the same moment
            return self._state()

    def _state(self):
        now = time.time()
        if now - self.last_poll > self.VIEWED_S:
            # Opened again after a while: readings from back then would mislead until they refresh.
            self.slow.update(tv_ping=None, tv=None)
        self.last_poll = now
        titles = self._titles()
        out = []
        for snap in self.sessions():
            key = f'{snap["ih"]}:{snap["idx"]}'
            s = summarize_session(snap, self.facts(key), title_for(titles, snap['ih'], snap['idx'], snap['file']), now)
            h = self.history.setdefault(key, deque(maxlen=150))
            if s['live']:
                h.append((round(now, 1), None if s['ahead_s'] is None else round(s['ahead_s'], 1), s['download_mbps'],
                          round(s['ahead_bytes'] / MB, 1)))
            s['history'] = list(h)
            s['health'] = health(s)
            out.append(s)
        current = {f'{s["ih"]}:{s["idx"]}' for s in out}
        for s in out:
            self.seen[f'{s["ih"]}:{s["idx"]}'] = (s, now)
        for key, (s, at) in list(self.seen.items()):
            if key in current:
                continue
            if now - at > 2 * 3600:
                self.seen.pop(key, None)
                continue
            gone = dict(s, live=False, ended_ago_s=round(now - at), events=[], history=[])
            gone['health'] = health(gone)
            out.append(gone)
        for key in list(self.history):
            if key not in self.seen:
                self.history.pop(key, None)
            else:
                h = self.history[key]
                while h and now - h[0][0] > 600:
                    h.popleft()
        out.sort(key=lambda s: (not s['live'], -s['pos_at'] if s['live'] else s['ended_ago_s'] or 0))
        out = out[:4]
        for s in out:
            s['next'] = next_episode(self._files(s['ih']), s['idx'])
        lan_ip = (self.slow.get('tv_ping') or {}).get('ip')
        return {
            'now': now,
            'phone_url': self.phone_url() if self.config.get('dashboard_lan') else None,
            'health': health(out[0] if out and out[0]['live'] else None, self.slow.get('tv_ping')),
            'sessions': out,
            'server': {**(self.slow.get('process') or {}), 'uptime_s': round(now - self.started),
                       'download_cap_mbps': self.config.get('download_limit_mbps'),
                       'cache': self.slow.get('cache'), 'learning': self.config.get('learn_from_plays')},
            'network': {'mac_wifi': self.slow.get('mac_wifi'), 'tv_ping': self.slow.get('tv_ping'), 'tv_ip': lan_ip},
            'tv': self.slow.get('tv'),
            'cache': self.slow.get('cache_list'),
            'prefetch': self.prefetcher.jobs() if self.prefetcher else [],
            'library': self._library_view(),
            'log': filter_log(_tail(self.dir / 'torrent.log')),
        }

    def _library_view(self):
        lib, adv = self.slow.get('library'), self.slow.get('advisor')
        if lib is None and adv is None:
            return None
        return {
            'files': (lib or {}).get('files'), 'shows': (lib or {}).get('shows'),
            'movies': len((lib or {}).get('movies') or []), 'other': len((lib or {}).get('other') or []),
            'advisor': adv and {'profile': adv.get('profile'), 'facts': adv.get('facts'),
                                'upstreams': [u.get('name') for u in adv.get('upstreams') or []]},
            'account': account_status(self.slow.get('autoheal')),
        }
