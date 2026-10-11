#!/usr/bin/env python3
"""Stremio-compatible torrent streaming server backed by libtorrent.

Stremio's own streaming server runs its torrent engine (a fork of torrent-stream) in JavaScript on a
single thread. On big torrents with many small pieces that thread saturates, and once it does it stops
feeding the player even while the download keeps going. This server answers the torrent routes of the
same HTTP API with libtorrent instead, and passes every other route (settings, transcoding, subtitles,
...) through to Stremio's server untouched. Point a client's Server URL here instead of at :12470.

Nothing is downloaded until a player asks for it: each reader gets hard deadlines on the pieces just
ahead of it and a read-ahead window beyond that. Downloaded pieces stay on disk, so rewinds are served
from disk; inactive torrents are deleted oldest-first once the cache exceeds its budget.
"""
import bisect
import http.client
import json
import mimetypes
import os
import re
import shutil
import signal
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import libtorrent as lt

from stream_facts import FFPROBE, FactStore, Prober, ffprobe_runner

HOME = Path.home()
INSTALL_DIR = HOME / '.stremio-local-addon'

CONFIG = {
    'https_port': 12472,
    'http_port': 11472,
    'upstream': '127.0.0.1:11470',     # Stremio's own streaming server, for every non-torrent route
    'cert_file': str(HOME / 'Library/Application Support/stremio-server/httpsCert.json'),
    'cache_dir': str(INSTALL_DIR / 'torrent-cache'),
    'cache_budget_gb': 15,             # inactive torrents beyond this are deleted, oldest first
    'min_free_gb': 3,                  # ... and so is anything inactive while the disk is this full
    'start_window_mb': 16,             # what a reader asks for before anything ahead of it has arrived
    'urgent_mb': 64,                   # just ahead of a reader: piece deadlines, spaced as below
    'deadline_rate_mbps': 48,          # deadlines assume this speed, so pieces arrive in reading order
    'readahead_mb': 384,               # most a reader's window grows to beyond the urgent part
    'idle_drop_s': 180,                # a torrent nobody has read for this long leaves the session
    'piece_wait_s': 120,               # a reader gives up on a piece after this long
    'listen_port': 6891,
    'upload_limit_kbps': 1024,         # KB/s; 0 = unlimited
    # The torrent comes in over the same Wi-Fi the TV stream goes out on (twice: Mac -> router -> TV).
    # Uncapped it can pull 100+ Mbps and crowd out the stream on a weak link. Stremio caps at ~64 Mbps;
    # 48 is still well above a 4K WEB-DL's bitrate. 0 = unlimited.
    'download_limit_mbps': 48,
    'max_connections': 300,
    # Hide MP4 subtitle tracks whose samples are stored far from the video they belong to (e.g. all at
    # the end of the file). ExoPlayer reads every track's samples, selected or not, so such a track
    # makes it jump across the file for every subtitle line and re-read the video — endless buffering.
    'hide_misplaced_mp4_subtitles': True,
    # Once a file has been played, read its header with ffprobe and record what it really contains (codecs,
    # Dolby Vision profile, bitrate) for the Stream Advisor. Delayed so the player's own startup reads go first.
    'learn_from_plays': True,
    'probe_delay_s': 20,
    'ffprobe': FFPROBE,
    'facts_file': str(INSTALL_DIR / 'stream-facts.json'),
}
CONFIG_FILE = Path(os.environ.get('TORRENTD_CONFIG', INSTALL_DIR / 'torrentd.json'))   # optional overrides, same keys
if CONFIG_FILE.exists():
    CONFIG.update(json.loads(CONFIG_FILE.read_text()))

# Added to whatever the stream supplies (Torrentio usually supplies none), so a bare info hash still
# finds peers quickly. The same public list Stremio's own server searches.
DEFAULT_TRACKERS = [
    'udp://tracker.opentrackr.org:1337/announce', 'udp://open.stealth.si:80/announce',
    'udp://tracker.torrent.eu.org:451/announce', 'udp://open.demonii.com:1337/announce',
    'udp://tracker.qu.ax:6969/announce', 'udp://exodus.desync.com:6969/announce',
    'udp://tracker.tryhackx.org:6969/announce', 'udp://tracker.theoks.net:6969/announce',
    'udp://explodie.org:6969/announce', 'udp://retracker01-msk-virt.corbina.net:80/announce',
    'udp://tracker.bittor.pw:1337/announce', 'udp://tracker.gmi.gd:6969/announce',
    'udp://tracker.corpscorp.online:80/announce', 'udp://tracker.ducks.party:1984/announce',
    'udp://tracker-udp.gbitt.info:80/announce', 'udp://tracker.0x7c0.com:6969/announce',
    'udp://tracker.dler.org:6969/announce', 'http://tracker2.dler.org:80/announce',
    'http://tracker.dler.com:6969/announce',
]
PIECE_CACHE_BYTES = 96 * 1024 * 1024    # recently served pieces, per torrent

IH = r'[0-9a-fA-F]{40}'
# Stremio's own list, plus the Blu-ray/AVCHD and iTunes containers (.m2ts, .mts, .m4v) its loose regex happens to match.
MEDIA_EXT = re.compile(r'\.(mkv|avi|mp4|m4v|wmv|vp8|mov|mpg|ts|m2ts|mts|m3u8|webm|flac|mp3|wav|wma|aac|ogg)$', re.I)
# Third path segments that belong to Stremio's own HLS/transcoding routes, not to a torrent stream.
UPSTREAM_SUFFIX = re.compile(r'^(?:(?:hls|master|stream|stream-[^/]*|stream-q-[^/]*|subs-[^/]*|mp4stream-q-[^/]*)\.m3u8'
                             r'|thumb\.jpg|dlna)$')
HOP_BY_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
              'trailers', 'transfer-encoding', 'upgrade'}
# Status routes the TV checks to decide whether the streaming server is "online". Their last good
# answers are kept, so torrents still play while Stremio desktop (the upstream) is closed.
REMEMBERED = ('/settings', '/network-info', '/device-info', '/casting')
REMEMBERED_FILE = INSTALL_DIR / 'upstream-cache.json'
CHUNK = 256 * 1024
MB = 1024 * 1024
mimetypes.add_type('video/x-matroska', '.mkv')


def layout(ti):
    """The torrent's file_storage: layout() since libtorrent 2.1 (files() is deprecated there)."""
    return ti.layout() if hasattr(ti, 'layout') else ti.files()


LOG_LOCK = threading.Lock()


def log(*a):
    line = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z ' + ' '.join(str(x) for x in a)
    with LOG_LOCK:     # request threads log concurrently; keep lines whole
        print(line, flush=True)


def human(n):
    return f'{n / MB:.1f}MB' if n < 1024 * MB else f'{n / 1024 / MB:.2f}GB'


def write_atomic(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(data if isinstance(data, bytes) else data.encode())
    os.replace(tmp, path)


def disk_usage(path):
    """Bytes actually allocated under path — files are sparse, so st_size would overstate it."""
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_blocks * 512
            except OSError:
                pass
    return total


# ------------------------------------------------------------------------------------------ files

def parse_season_episode(path):
    """(season, [episodes]) from a file path, or (None, [])."""
    name = path.replace('\\', '/')
    m = re.search(r'[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})(?:[ ._-]?[Ee]?(\d{1,3}))?', name)
    if m:
        eps = [int(m.group(2))] + ([int(m.group(3))] if m.group(3) else [])
        return int(m.group(1)), eps
    m = re.search(r'(?<![\d])(\d{1,2})x(\d{2,3})(?![\d])', name)
    if m:
        return int(m.group(1)), [int(m.group(2))]
    m = re.search(r'season[ ._-]*(\d{1,2}).*?(?:episode|ep|e)[ ._-]*(\d{1,3})', name, re.I)
    if m:
        return int(m.group(1)), [int(m.group(2))]
    return None, []


def guess_file_idx(files, series=None):
    """Port of Stremio's GuessFileIdx: the largest media file, preferring the requested episode."""
    media = [(i, f) for i, f in enumerate(files) if MEDIA_EXT.search(f['path'])]
    if not media:
        return -1
    if series and series.get('season') and series.get('episode'):
        want_s, want_e = int(series['season']), int(series['episode'])
        ep = [(i, f) for i, f in media if parse_season_episode(f['path']) [0] == want_s
              and want_e in parse_season_episode(f['path'])[1]]
        media = ep or media
    return max(media, key=lambda x: x[1]['length'])[0]


def file_must_include(files, patterns):
    """Index of the first file whose name matches one of the patterns ('/re/flags' or plain regex)."""
    regs = []
    for p in patterns or []:
        m = re.fullmatch(r'/(.*)/(\w*)', p or '')
        try:
            regs.append(re.compile(m.group(1), re.I if 'i' in m.group(2) else 0) if m else re.compile(p))
        except re.error:
            pass
    for i, f in enumerate(files):
        if any(r.search(f['name']) for r in regs):
            return i
    return None


def parse_sources(sources):
    """Stremio peer sources ('tracker:udp://...', 'dht:<hash>') -> tracker URLs. DHT is always on."""
    out = []
    for s in sources or []:
        if isinstance(s, str) and s.startswith('tracker:'):
            out.append(s[len('tracker:'):])
        elif isinstance(s, str) and re.match(r'^(udp|https?|wss?)://', s):
            out.append(s)
    return out


# ------------------------------------------------------------------------------------------- mp4

MP4_EXT = re.compile(r'\.(mp4|m4v|mov)$', re.I)
TEXT_HANDLERS = (b'text', b'sbtl', b'subt', b'clcp')
MISPLACED_BYTES = 64 * MB


def mp4_boxes(b, pos, end):
    """(type, start, header length, end) for each box in b[pos:end]."""
    while pos + 8 <= end:
        size, typ = struct.unpack_from('>I4s', b, pos)
        hdr = 8
        if size == 1:
            if pos + 16 > end:
                return
            size, hdr = struct.unpack_from('>Q', b, pos + 8)[0], 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            return
        yield typ, pos, hdr, pos + size
        pos += size


def mp4_child(b, start, end, path):
    for typ, s, h, e in mp4_boxes(b, start, end):
        if typ == path[0]:
            return (s, h, e) if len(path) == 1 else mp4_child(b, s + h, e, path[1:])
    return None


def mp4_track(b, s, h, e):
    """Handler type and [(chunk start time in s, file offset)] of one 'trak' box."""
    mdia = mp4_child(b, s + h, e, [b'mdia'])
    if not mdia:
        return None
    ms, mh, me = mdia
    hdlr, mdhd = mp4_child(b, ms + mh, me, [b'hdlr']), mp4_child(b, ms + mh, me, [b'mdhd'])
    stbl = mp4_child(b, ms + mh, me, [b'minf', b'stbl'])
    if not (hdlr and mdhd and stbl):
        return None
    handler = b[hdlr[0] + hdlr[1] + 8: hdlr[0] + hdlr[1] + 12]
    p = mdhd[0] + mdhd[1]
    timescale = struct.unpack_from('>I', b, p + 4 + (16 if b[p] == 1 else 8))[0] or 1
    bs, bh, be = stbl

    def table(name):
        box = mp4_child(b, bs + bh, be, [name])
        if not box:
            return None
        q = box[0] + box[1]
        return q + 8, struct.unpack_from('>I', b, q + 4)[0]

    stts, stsc = table(b'stts'), table(b'stsc')
    co, wide = table(b'stco'), False
    if co is None:
        co, wide = table(b'co64'), True
    if not (stts and stsc and co):
        return None
    offsets = [struct.unpack_from('>Q' if wide else '>I', b, co[0] + i * (8 if wide else 4))[0] for i in range(co[1])]
    runs = [struct.unpack_from('>III', b, stsc[0] + i * 12)[:2] for i in range(stsc[1])]
    deltas = [struct.unpack_from('>II', b, stts[0] + i * 8) for i in range(stts[1])]
    chunks, t, di, left = [], 0, 0, deltas[0][0] if deltas else 0
    for ci in range(len(offsets)):
        while len(runs) > 1 and runs[1][0] <= ci + 1:
            runs.pop(0)
        chunks.append((t / timescale, offsets[ci]))
        n = runs[0][1] if runs else 1
        while n > 0 and di < len(deltas):
            take = min(n, left)
            t += take * deltas[di][1]
            n -= take
            left -= take
            if left == 0:
                di += 1
                left = deltas[di][0] if di < len(deltas) else 0
    return {'handler': handler, 'box_start': s, 'chunks': chunks}


def find_misplaced_mp4_subtitles(read, size):
    """[(file offset of a 'trak' type field, description)] for subtitle tracks stored away from their
    video. read(offset, length) -> bytes or None."""
    pos = 0
    while pos + 8 <= size:
        hdr = read(pos, min(16, size - pos))
        if not hdr or len(hdr) < 8:
            return []
        box, typ = struct.unpack_from('>I4s', hdr, 0)
        hlen = 8
        if box == 1:
            box, hlen = struct.unpack_from('>Q', hdr, 8)[0], 16
        elif box == 0:
            box = size - pos
        if box < hlen:
            return []
        if typ == b'moov':
            if box > 256 * MB:
                return []
            moov = read(pos, box)
            if not moov:
                return []
            tracks = [mp4_track(moov, s, h, e) for t2, s, h, e in mp4_boxes(moov, hlen, len(moov)) if t2 == b'trak']
            tracks = [t for t in tracks if t]
            videos = [t for t in tracks if t['handler'] == b'vide' and t['chunks']]
            if not videos:
                return []
            video = max(videos, key=lambda t: len(t['chunks']))
            vt, vo = [c[0] for c in video['chunks']], [c[1] for c in video['chunks']]
            found = []
            for n, t in enumerate(tracks):
                if t['handler'] not in TEXT_HANDLERS or not t['chunks']:
                    continue
                far = sum(1 for tm, off in t['chunks']
                          if abs(off - vo[max(0, bisect.bisect_right(vt, tm) - 1)]) > MISPLACED_BYTES)
                if far > max(3, len(t['chunks']) // 10):
                    found.append((pos + t['box_start'] + 4,
                                  f'track {n + 1} ({t["handler"].decode(errors="replace")}, {far}/{len(t["chunks"])} '
                                  f'chunks stored >64 MB from their video)'))
            return found
        pos += box
    return []


# ----------------------------------------------------------------------------------------- engine

class Torrent:
    def __init__(self, engine, ih, handle, save_path):
        self.engine, self.ih, self.handle, self.save_path = engine, ih, handle, Path(save_path)
        self.cond = threading.Condition()
        self.ti = None
        self.readers = {}         # reader id -> {'at', 'last', 'released', 'hi', 'urgent_to'}
        self.prio = {}            # piece -> priority we set (only pieces we raised above 0)
        self.deadlines = set()    # pieces we gave a deadline
        self.piece_cache = {}     # piece -> bytes, insertion-ordered (oldest first)
        self.piece_waits = {}     # piece -> [Event, bytes], for reads in flight
        self.cache_lock = threading.Lock()
        self.last_used = time.time()
        self.last_announce = 0.0
        self.dropping = False
        self.error = None         # set by file/torrent error alerts; readers fail fast while it is
        self.mp4_patches = {}     # file index -> [(file offset, 4 replacement bytes)]
        self.mp4_lock = threading.Lock()
        self.lock = threading.RLock()
        self.touch()
        if handle.status().has_metadata:
            # Resumed from the cache: the saved piece priorities would keep downloading with no reader.
            self.on_metadata()
            handle.prioritize_pieces([0] * self.ti.num_pieces())

    def touch(self):
        self.last_used = time.time()
        try:
            (self.save_path / '.last-used').write_text(str(int(self.last_used)))
        except OSError:
            pass

    def on_metadata(self):
        with self.cond:
            self.ti = self.handle.torrent_file()
            self.cond.notify_all()

    def wait_metadata(self, timeout):
        end = time.time() + timeout
        with self.cond:
            while self.ti is None and time.time() < end:
                self.cond.wait(min(1.0, end - time.time()))
                # Polled as well: metadata arriving before this torrent was registered has no alert left.
                if self.ti is None and self.handle.status().has_metadata:
                    self.ti = self.handle.torrent_file()
        return self.ti is not None

    def files(self):
        fs = layout(self.ti)
        return [{'name': fs.file_name(i), 'path': fs.file_path(i), 'length': fs.file_size(i),
                 'offset': fs.file_offset(i)} for i in range(fs.num_files())]

    def ensure_file_wanted(self, idx):
        # Torrents are added with every file set to "don't download" so that metadata arriving cannot
        # start a full download. A file at priority 0 is stored in a part file, though, so the file being
        # read must be raised above 0. Changing a file priority makes libtorrent recompute every piece
        # priority in the torrent from the file priorities — later, on its disk thread — which would set
        # every piece of every opened file to 4 (a full download of each episode opened so far). So wait
        # for that recompute, then put back the complete vector of what readers actually want.
        with self.lock:
            if self.handle.file_priority(idx) != 0:
                return
            self.handle.file_priority(idx, 4)
            end = time.time() + 5
            while self.handle.get_file_priorities()[idx] == 0 and time.time() < end:
                time.sleep(0.02)
            self.handle.prioritize_pieces([self.prio.get(p, 0) for p in range(self.ti.num_pieces())])

    def read_bytes(self, idx, start, length, timeout=60):
        """File bytes [start, start+length), downloading what is missing; None on timeout."""
        fs, plen = layout(self.ti), self.ti.piece_length()
        foff, end = fs.file_offset(idx), start + length - 1
        first, last = (foff + start) // plen, (foff + end) // plen
        rid, out = object(), bytearray()
        self.ensure_file_wanted(idx)
        try:
            for p in range(first, last + 1):
                self.want(rid, p, last)
                if not self.wait_piece(p, timeout):
                    return None
                data = self.piece_data(p)
                if data is None:
                    return None
                out += data[max(0, foff + start - p * plen): min(len(data), foff + end + 1 - p * plen)]
        finally:
            self.release(rid)
        return bytes(out)

    def patches_for(self, idx, name, size):
        """Byte patches to apply when serving this file (computed once per file)."""
        if not CONFIG['hide_misplaced_mp4_subtitles'] or not MP4_EXT.search(name):
            return []
        with self.mp4_lock:
            if idx not in self.mp4_patches:
                patches = []
                try:
                    for off, why in find_misplaced_mp4_subtitles(lambda o, n: self.read_bytes(idx, o, n), size):
                        patches.append((off, b'free'))
                        log(f'mp4 {self.ih[:8]}/{idx}: hiding {why} — ExoPlayer would otherwise keep jumping to it')
                except Exception as e:
                    log(f'mp4 {self.ih[:8]}/{idx}: could not inspect ({type(e).__name__}: {e}); serving unchanged')
                self.mp4_patches[idx] = patches
            return self.mp4_patches[idx]

    def want(self, rid, piece, last_piece):
        """A reader is now at `piece` and will read no further than `last_piece`."""
        with self.lock:
            prev = self.readers.get(rid)
            if prev and prev['at'] == piece and prev['released'] is None:
                return
            fresh = not self.readers
            self.readers[rid] = {'at': piece, 'last': last_piece, 'released': None, 'hi': piece, 'urgent_to': piece}
            self._apply()
            # With nothing wanted, our last announce told trackers we had nothing left to download,
            # so they answered with few or no seeds. Now that pieces are wanted, ask again.
            if fresh and time.time() - self.last_announce > 60:
                self.last_announce = time.time()
                self.handle.force_reannounce()
                self.handle.force_dht_announce()

    def release(self, rid):
        with self.lock:
            r = self.readers.get(rid)
            if r:
                # Kept briefly: players drop and reopen connections around the same spot all the time.
                r['released'] = time.time()
        self.touch()

    def piece_data(self, p, timeout=60):
        """A verified piece's bytes, through libtorrent. Reading the file directly is not safe:
        have_piece() turns true once a piece passes its hash check, which can be before the
        disk thread has written it out."""
        with self.cache_lock:
            data = self.piece_cache.get(p)
            if data is not None:
                return data
            slot = self.piece_waits.get(p)
            issue = slot is None
            if issue:
                slot = self.piece_waits[p] = [threading.Event(), None]
        if issue:
            self.handle.read_piece(p)
        if not slot[0].wait(timeout):
            with self.cache_lock:
                if self.piece_waits.get(p) is slot:
                    del self.piece_waits[p]
            return None
        return slot[1]

    def on_piece_read(self, p, data):
        with self.cache_lock:
            if data is not None:
                self.piece_cache[p] = data
                total = sum(len(v) for v in self.piece_cache.values())
                for old in list(self.piece_cache):
                    if total <= PIECE_CACHE_BYTES or old == p:
                        break
                    total -= len(self.piece_cache.pop(old))
            slot = self.piece_waits.pop(p, None)
        if slot:
            slot[1] = data
            slot[0].set()

    def expire_readers(self, linger=30):
        with self.lock:
            dead = [k for k, r in self.readers.items()
                    if r['released'] is not None and time.time() - r['released'] > linger]
            for k in dead:
                del self.readers[k]
            if dead or self.readers:
                self._apply()

    def live_readers(self):
        with self.lock:
            return sum(1 for r in self.readers.values() if r['released'] is None)

    def _apply(self):
        """Turn reader positions into piece priorities and deadlines.

        Each reader's window starts small and grows with the run of downloaded pieces right ahead of
        it. While the window is small, libtorrent has few pieces to choose from and duplicates block
        requests across peers (end game), so the first pieces arrive fast; spreading every peer over a
        large window from the start leaves the piece being read stuck behind a slow peer's blocks.
        Called whenever a reader moves and once a second, since a player with a full buffer stops
        moving while its window should keep growing."""
        plen = self.ti.piece_length()
        urgent_n = max(2, CONFIG['urgent_mb'] * MB // plen)
        start_n = max(2, CONFIG['start_window_mb'] * MB // plen)
        max_n = urgent_n + max(0, CONFIG['readahead_mb'] * MB // plen)
        spacing = max(50, plen * 8 * 1000 // (CONFIG['deadline_rate_mbps'] * 1_000_000))
        have = self.handle.status(lt.status_flags_t.query_pieces).pieces if self.readers else []
        want, urgent = {}, {}
        for r in self.readers.values():
            if r['released'] is not None:
                # A closed connection's window would keep downloading for a position the player has left
                # — after a few seeks that is several hundred MB competing with the one being watched.
                # It lingers only so the torrent counts as in use; a reconnect rebuilds its own window.
                continue
            at, last = r['at'], r['last']
            run = 0
            while at + run <= last and run < max_n and have[at + run]:
                run += 1
            hi = min(last, at + min(max_n, start_n + 2 * run))
            urgent_to = min(hi, at + urgent_n)
            r['hi'], r['urgent_to'] = hi, urgent_to
            for p in range(at, hi + 1):
                pr = 7 if p <= urgent_to else 5
                if want.get(p, 0) < pr:
                    want[p] = pr
            for p in range(at, urgent_to + 1):
                if not have[p]:
                    urgent[p] = min(urgent.get(p, 1 << 30), (p - at) * spacing)
        # reset_piece_deadline() leaves the piece at priority 1, so clear deadlines first and then set
        # every cleared piece's priority explicitly along with the rest.
        cleared = self.deadlines - urgent.keys()
        for p in cleared:
            self.handle.reset_piece_deadline(p)
        changes = {p: pr for p, pr in want.items() if self.prio.get(p) != pr}
        changes.update({p: 0 for p in self.prio if p not in want})
        changes.update({p: want.get(p, 0) for p in cleared})
        if changes:
            self.handle.prioritize_pieces(list(changes.items()))
            for p, pr in changes.items():
                if pr:
                    self.prio[p] = pr
                else:
                    self.prio.pop(p, None)
        for p, ms in urgent.items():
            if p not in self.deadlines:
                self.handle.set_piece_deadline(p, ms)
        self.deadlines = set(urgent)

    def wait_piece(self, p, timeout):
        if self.handle.have_piece(p):
            return True
        end = time.time() + timeout
        with self.cond:
            while not self.handle.have_piece(p):
                left = end - time.time()
                if left <= 0 or self.error:
                    return False
                self.cond.wait(min(1.0, left))
        return True

    def debug(self):
        """Where readers are, and what libtorrent is fetching right now."""
        q = self.handle.get_download_queue()
        partial = sorted((d['piece_index'], sum(1 for b in d['blocks'] if b['state'] == 3), len(d['blocks']),
                          len({str(b.get('peer')) for b in d['blocks'] if b['state'] in (1, 2)}))
                         for d in q)
        have = self.handle.status(lt.status_flags_t.query_pieces).pieces
        readers = []
        for r in list(self.readers.values()):
            missing = [i - r['at'] for i in range(r['at'], r['hi'] + 1) if not have[i]]
            readers.append({'at': r['at'], 'window': r['hi'] - r['at'] + 1, 'urgent': r['urgent_to'] - r['at'] + 1,
                            'missing_in_window': len(missing), 'first_missing': missing[:12],
                            'released': r['released'] is not None})
        peers = self.handle.get_peer_info()
        lt_prios = self.handle.get_piece_priorities()
        wanted = [i for i, v in enumerate(lt_prios) if v > 0 and not have[i]]
        return {
            'libtorrent_wants': {'missing_pieces_wanted': len(wanted),
                                 'outside_reader_windows': sum(1 for i in wanted if i not in self.prio)},
            'peers': {'connected': len(peers),
                      'sending_to_us': sum(1 for p in peers if not p.flags & lt.peer_info.remote_choked),
                      'we_are_interested': sum(1 for p in peers if p.flags & lt.peer_info.interesting),
                      'seeds': sum(1 for p in peers if p.flags & lt.peer_info.seed)},
            'readers': readers, 'deadlines': len(self.deadlines), 'prioritized': len(self.prio),
            'downloading': [{'piece': p, 'blocks_done': d, 'blocks': n, 'peers': k} for p, d, n, k in partial],
        }

    def stats(self, idx=None):
        """Same shape as Stremio's getStatistics. The Android TV app's core deserializes it strictly:
        every field below is required, counters must be integers, and the stream fields must be filled
        even when the requested file index is unknown."""
        s = self.handle.status()
        try:
            unchoked = sum(1 for p in self.handle.get_peer_info() if not p.flags & lt.peer_info.remote_choked)
        except Exception:
            unchoked = s.num_seeds
        files = self.files() if self.ti else []
        trackers = [t['url'] for t in self.handle.trackers()]
        out = {
            'infoHash': self.ih, 'name': (self.ti.name() if self.ti else s.name) or '',
            'files': files, 'sources': [],
            'opts': {'connections': CONFIG['max_connections'], 'dht': True, 'growler': {'flood': 0, 'pulse': 0},
                     'handshakeTimeout': 20000, 'path': str(self.save_path),
                     'peerSearch': {'min': 40, 'max': 150,
                                    'sources': [f'dht:{self.ih}'] + [f'tracker:{t}' for t in trackers]},
                     'swarmCap': {}, 'timeout': 4000, 'tracker': True, 'virtual': True,
                     'engine': 'libtorrent ' + lt.__version__},
            'downloadSpeed': float(s.download_payload_rate), 'uploadSpeed': float(s.upload_payload_rate),
            'downloaded': int(s.all_time_download), 'uploaded': int(s.all_time_upload),
            'unchoked': int(unchoked), 'peers': int(s.num_peers), 'queued': int(s.connect_candidates),
            'unique': int(s.list_peers), 'connectionTries': 0, 'peerSearchRunning': True,
            'swarmConnections': int(s.num_peers), 'swarmPaused': False, 'swarmSize': int(s.list_peers),
            'streamLen': 0, 'streamName': '', 'streamProgress': 0.0,
        }
        if self.ti:
            if idx is None or not 0 <= idx < len(files):
                idx = guess_file_idx(files, {})
            if 0 <= idx < len(files):
                size = files[idx]['length']
                done = self.handle.file_progress(lt.torrent_handle.piece_granularity)[idx]
                out.update(streamLen=size, streamName=files[idx]['name'],
                           streamProgress=float(done / size) if size else 0.0)
        return out


class Engine:
    def __init__(self):
        self.cache = Path(CONFIG['cache_dir'])
        self.cache.mkdir(parents=True, exist_ok=True)
        port = CONFIG['listen_port']
        self.ses = lt.session({
            'listen_interfaces': f'0.0.0.0:{port},[::]:{port}',
            'enable_dht': True, 'enable_lsd': True, 'enable_upnp': True, 'enable_natpmp': True,
            'dht_bootstrap_nodes': 'dht.libtorrent.org:25401,router.bittorrent.com:6881,'
                                   'router.utorrent.com:6881,dht.transmissionbt.com:6881',
            'alert_mask': (lt.alert_category.error | lt.alert_category.status
                           | lt.alert_category.storage | lt.alert_category.piece_progress),
            'alert_queue_size': 20000,
            'connections_limit': CONFIG['max_connections'],
            'upload_rate_limit': CONFIG['upload_limit_kbps'] * 1024,
            'download_rate_limit': CONFIG['download_limit_mbps'] * 1_000_000 // 8,
            # Between reads a torrent can have nothing left to download; by default libtorrent then
            # treats it as finished and drops its seeds, and the next seek waits for them to come back.
            'close_redundant_connections': False,
            'mixed_mode_algorithm': 0,          # prefer_tcp: don't throttle TCP peers in favour of uTP
            # All trackers share one tier, and by default only the first working one is announced to.
            'announce_to_all_trackers': True, 'announce_to_all_tiers': True,
            # Streaming needs the pieces right ahead of the reader first, with several peers sharing each
            # one. By default each peer takes a whole piece (or a whole 4 MiB extent) for itself, so with
            # big pieces the reader waits on whichever peer happens to be slowest.
            'piece_extent_affinity': False, 'whole_pieces_threshold': 0,
            # A block stuck at a slow peer holds up the piece the player is waiting for: give up on it
            # sooner, and let it be requested from a second peer instead of waiting it out.
            'piece_timeout': 5, 'request_timeout': 10, 'strict_end_game_mode': False,
            # Startup time is mostly how fast enough peers that will send to us are found; connect faster.
            'connection_speed': 100,
        })
        self.torrents = {}
        self.lock = threading.RLock()
        self.running = True
        self.last_resume_save = time.time()
        threading.Thread(target=self._alerts, name='alerts', daemon=True).start()
        threading.Thread(target=self._housekeeping, name='housekeeping', daemon=True).start()

    def get(self, ih, trackers=(), ti=None):
        """The torrent for an info hash, added to the session if needed (from the cache's resume data,
        a .torrent's info `ti`, or a magnet link)."""
        ih = ih.lower()
        with self.lock:
            t = self.torrents.get(ih)
            if t and t.dropping:
                t.dropping = False    # wanted again before it left: the pending resume save just won't remove it
                log(f'torrent {ih[:8]}: wanted again, staying in the session')
            if t:
                for tr in trackers:
                    t.handle.add_tracker({'url': tr})
                t.touch()
                return t
            d = self.cache / ih
            d.mkdir(parents=True, exist_ok=True)
            atp, resumed = None, False
            if (d / '.resume').exists():
                try:
                    atp, resumed = lt.read_resume_data((d / '.resume').read_bytes()), True
                except Exception as e:
                    log(f'torrent {ih[:8]}: resume data unreadable ({e}), starting from the magnet')
            if atp is None and ti is not None:
                atp = lt.add_torrent_params()
                atp.ti = ti
            if atp is None:
                atp = lt.parse_magnet_uri('magnet:?xt=urn:btih:' + ih)
            atp.save_path = str(d)
            atp.trackers = list(dict.fromkeys(list(atp.trackers) + list(trackers) + DEFAULT_TRACKERS))
            # default_dont_download on every add — resume data saved before metadata arrived carries no
            # file priorities, and would otherwise download the whole torrent. Sequential: the window
            # fills in order from the reader rather than rarest-first. Never paused, never auto-managed,
            # and never left in upload mode (libtorrent's reaction to a full disk, which it only undoes
            # for auto-managed torrents and which resume data would carry across restarts).
            atp.flags = ((atp.flags | lt.torrent_flags.default_dont_download | lt.torrent_flags.sequential_download)
                         & ~lt.torrent_flags.auto_managed & ~lt.torrent_flags.paused
                         & ~lt.torrent_flags.upload_mode)
            h = self.ses.add_torrent(atp)
            t = Torrent(self, ih, h, d)
            self.torrents[ih] = t
            log(f'torrent {ih[:8]}: added ({"resumed from cache" if resumed else ".torrent" if ti else "magnet"}, '
                f'{len(atp.trackers)} trackers)')
            return t

    def find(self, ih):
        return self.torrents.get(ih.lower())

    def drop(self, t, reason, only_if_idle=False):
        with self.lock:
            if t.dropping or (only_if_idle and (t.live_readers()
                                                or time.time() - t.last_used <= CONFIG['idle_drop_s'])):
                return
            t.dropping = True
            log(f'torrent {t.ih[:8]}: leaving the session ({reason}); downloaded pieces stay cached')
            if t.ti is None:
                # No metadata yet: resume data would hold neither the info dict nor file priorities.
                return self._remove(t)
            try:
                t.handle.save_resume_data(lt.torrent_handle.save_info_dict)   # removed once it is written
            except Exception:
                self._remove(t)

    def _remove(self, t):
        """Take a dropping torrent out of the session, unless it was wanted again in the meantime."""
        with self.lock:
            if not t.dropping or self.torrents.get(t.ih) is not t:
                return
            del self.torrents[t.ih]
            try:
                self.ses.remove_torrent(t.handle)
            except Exception:
                pass

    def _alerts(self):
        while self.running:
            self.ses.wait_for_alert(1000)
            for a in self.ses.pop_alerts():
                try:
                    self._on_alert(a)
                except Exception as e:
                    log('alert handling failed:', type(a).__name__, e)

    def _on_alert(self, a):
        h = getattr(a, 'handle', None)
        t = self.torrents.get(str(h.info_hash())) if h is not None and h.is_valid() else None
        if isinstance(a, lt.piece_finished_alert):
            if t:
                with t.cond:
                    t.cond.notify_all()
        elif isinstance(a, lt.read_piece_alert):
            if t:
                t.on_piece_read(a.piece, None if a.error.value() else bytes(a.buffer))
        elif isinstance(a, lt.metadata_received_alert):
            if t:
                t.on_metadata()
                log(f'torrent {t.ih[:8]}: metadata received — {t.ti.name()} '
                    f'({t.ti.num_files()} files, {t.ti.num_pieces()} pieces of {human(t.ti.piece_length())})')
        elif isinstance(a, lt.save_resume_data_alert):
            ih = str(a.handle.info_hash())
            try:
                write_atomic(self.cache / ih / '.resume', lt.write_resume_data_buf(a.params))
            except Exception as e:
                log(f'torrent {ih[:8]}: could not write resume data: {e}')
            t = self.torrents.get(ih)
            if t:
                self._remove(t)        # no-op unless it is dropping
        elif isinstance(a, lt.save_resume_data_failed_alert):
            if t:
                self._remove(t)
        elif isinstance(a, (lt.file_error_alert, lt.torrent_error_alert)):
            # Disk full or another I/O error: libtorrent pauses the torrent or puts it in upload mode.
            # Readers give up at once instead of waiting out their timeout; housekeeping retries.
            log('libtorrent:', a.message())
            if t:
                t.error = a.message()
                with t.cond:
                    t.cond.notify_all()
        elif isinstance(a, (lt.metadata_failed_alert, lt.listen_failed_alert, lt.fastresume_rejected_alert)):
            log('libtorrent:', a.message())

    def _housekeeping(self):
        tick = 0
        while self.running:
            time.sleep(1)
            tick += 1
            for t in list(self.torrents.values()):
                if t.readers and t.ti:
                    try:
                        t.expire_readers()
                    except Exception as e:
                        log(f'torrent {t.ih[:8]}: window refresh failed: {e}')
            if tick % 5:
                continue
            try:
                now = time.time()
                for t in list(self.torrents.values()):
                    self.drop(t, f'unused for {CONFIG["idle_drop_s"]}s', only_if_idle=True)
                if now - self.last_resume_save > 300:
                    self.last_resume_save = now
                    for t in list(self.torrents.values()):
                        if t.ti and t.handle.need_save_resume_data():
                            t.handle.save_resume_data(lt.torrent_handle.save_info_dict)
                self._evict()
                self._recover()
            except Exception as e:
                log('housekeeping failed:', e)

    def _recover(self):
        """Retry torrents stopped by a disk error once there is enough free space again."""
        min_free = CONFIG['min_free_gb'] * 1024 * MB
        free = shutil.disk_usage(self.cache).free
        for t in list(self.torrents.values()):
            stuck = t.error or (t.handle.flags() & lt.torrent_flags.upload_mode)
            if not stuck:
                continue
            if free < min_free:
                if time.time() - getattr(t, 'warned_full', 0) > 300:
                    t.warned_full = time.time()
                    log(f'torrent {t.ih[:8]}: stopped ({t.error or "upload mode"}); only {human(free)} free — '
                        f'nothing inactive left to delete. Free up disk space or lower cache_budget_gb.')
                continue
            t.handle.clear_error()
            t.handle.unset_flags(lt.torrent_flags.upload_mode)
            t.handle.resume()
            log(f'torrent {t.ih[:8]}: resumed after "{t.error or "upload mode"}" ({human(free)} free)')
            t.error = None

    def _evict(self):
        budget = CONFIG['cache_budget_gb'] * 1024 * MB
        min_free = CONFIG['min_free_gb'] * 1024 * MB
        entries = []
        for d in self.cache.iterdir():
            if not d.is_dir() or not re.fullmatch(IH, d.name):
                continue
            try:
                used = float((d / '.last-used').read_text())
            except (OSError, ValueError):
                used = d.stat().st_mtime
            entries.append((used, d))
        sizes = {d: disk_usage(d) for _, d in entries}
        total = sum(sizes.values())
        free = shutil.disk_usage(self.cache).free
        for used, d in sorted(entries):
            if total <= budget and free >= min_free:
                break
            if d.name in self.torrents:          # never delete what is in the session
                continue
            shutil.rmtree(d, ignore_errors=True)
            total -= sizes[d]
            free += sizes[d]
            log(f'cache: deleted {d.name[:8]} ({human(sizes[d])}, last used '
                f'{time.strftime("%b %d %H:%M", time.localtime(used))}) — cache {human(total)}, free {human(free)}')

    def health(self):
        return {
            'ok': True, 'engine': 'libtorrent ' + lt.__version__,
            'torrents': [{
                'infoHash': t.ih, 'name': t.ti.name() if t.ti else None, 'readers': t.live_readers(),
                'peers': (s := t.handle.status()).num_peers, 'downloadSpeed': s.download_payload_rate,
                'uploadSpeed': s.upload_payload_rate,
            } for t in list(self.torrents.values())],
            'cache': {'dir': str(self.cache), 'bytes': disk_usage(self.cache),
                      'budget': CONFIG['cache_budget_gb'] * 1024 * MB,
                      'free': shutil.disk_usage(self.cache).free},
        }

    def shutdown(self):
        pending = [t for t in self.torrents.values() if t.ti and not t.dropping]
        for t in pending:
            self.drop(t, 'shutting down')
        end = time.time() + 5
        while self.torrents and time.time() < end:
            time.sleep(0.1)
        self.running = False


# ------------------------------------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'stremio-libtorrent'
    timeout = 300
    engine = None   # set at startup
    prober = None   # set at startup when ffprobe is available

    def log_message(self, fmt, *args):
        pass

    def log_request(self, code='-', size='-'):
        # Every request from another device (the TV), so a client problem shows up in torrent.log.
        if not self.client_address[0].startswith(('127.', '::1')):
            log(f'{self.client_address[0]} {self.command} {self.path[:160]} -> {code}')

    def end_headers(self):
        super().end_headers()
        self.headers_sent = True

    def do_GET(self):
        self.route()

    do_POST = do_HEAD = do_PUT = do_DELETE = do_OPTIONS = do_PATCH = do_GET

    def route(self):
        u = urllib.parse.urlsplit(self.path)
        path, q = u.path, urllib.parse.parse_qs(u.query)
        self.t0, self.sent, self.headers_sent = time.time(), 0, False
        try:
            if path == '/torrentd/health':
                return self.json(self.engine.health())
            m = re.fullmatch(rf'/torrentd/debug/({IH})', path)
            if m:
                t = self.engine.find(m.group(1))
                return self.json(t.debug() if t else None)
            m = re.fullmatch(rf'/torrentd/probe/({IH})/(\d+)', path)
            if m:
                return self.probe(m.group(1).lower(), int(m.group(2)), (q.get('name') or [''])[0])
            if path == '/torrentd/facts':
                return self.json(self.prober.store.all() if self.prober else None)
            if self.command == 'OPTIONS' and re.match(rf'^/{IH}(/|$)', path):
                return self.cors_preflight()
            m = re.fullmatch(rf'/({IH})/create', path)
            if m:
                return self.create(m.group(1), self.body_json())
            if path == '/create':
                return self.create_from_file(self.body_json())
            m = re.fullmatch(rf'/({IH})/stats\.json', path)
            if m:
                t = self.engine.find(m.group(1))
                return self.json(t.stats() if t else None)
            m = re.fullmatch(rf'/({IH})/([^/]+)/stats\.json', path)
            if m:
                t = self.engine.find(m.group(1))
                idx = int(m.group(2)) if re.fullmatch(r'-?\d+', m.group(2)) else None
                return self.json(t.stats(idx) if t else None)
            if path == '/stats.json':
                return self.json({t.ih: t.stats() for t in list(self.engine.torrents.values())})
            m = re.fullmatch(rf'/({IH})/remove', path)
            if m:
                t = self.engine.find(m.group(1))
                if t:
                    self.engine.drop(t, 'removed by client')
                return self.json({})
            if path == '/removeAll':
                for t in list(self.engine.torrents.values()):
                    self.engine.drop(t, 'removed by client')
                return self.json({})
            m = re.fullmatch(rf'/({IH})/([^/]+)(?:/([^/]*))?', path)
            if m and self.command in ('GET', 'HEAD') and not (m.group(3) and UPSTREAM_SUFFIX.match(m.group(3))):
                return self.stream(m.group(1), urllib.parse.unquote(m.group(2)), q)
            return self.proxy()
        except OSError:
            self.close_connection = True        # the client went away (broken pipe, reset, TLS, timeout)
        except Exception as e:
            log(f'{self.command} {self.path} failed: {type(e).__name__}: {e}\n' + traceback.format_exc().rstrip())
            self.close_connection = True
            if not self.headers_sent:           # otherwise an error page would land inside the body
                try:
                    self.send_error(500)
                except Exception:
                    pass

    # ---- helpers

    def read_body(self):
        """The request body, Content-Length or chunked — always consumed, so keep-alive stays in sync."""
        if 'chunked' in (self.headers.get('Transfer-Encoding') or '').lower():
            out = bytearray()
            while True:
                size = int(self.rfile.readline().split(b';')[0].strip() or b'0', 16)
                if size == 0:
                    while self.rfile.readline() not in (b'\r\n', b'\n', b''):
                        pass                     # trailers
                    return bytes(out)
                out += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(n) if n else b''

    def body_json(self):
        raw = self.read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode(errors='replace')).items()}

    def cors(self):
        if self.headers.get('Origin'):
            self.send_header('Access-Control-Allow-Origin', '*')

    def cors_preflight(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', self.headers.get('Access-Control-Request-Headers') or 'Range')
        self.send_header('Access-Control-Max-Age', '1728000')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.cors()
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    # ---- torrent routes

    def mp4_hidden(self, ih, idx):
        """How many subtitle tracks the server hides in this MP4 (see patches_for)."""
        t = self.engine.find(ih)
        return {'mp4SubsHidden': len(t.mp4_patches.get(idx, [])) if t else 0}

    def probe(self, ih, idx, name):
        """Ask for a file's facts before anyone plays it (the advisor's optional background probe)."""
        if not self.prober:
            return self.json({'error': 'ffprobe not available'}, 503)
        known = self.prober.store.get(f'{ih}:{idx}') is not None
        queued = not known and self.prober.schedule(ih, idx, name, extra=lambda: self.mp4_hidden(ih, idx))
        return self.json({'known': known, 'queued': queued})

    def create(self, ih, body):
        sources = parse_sources((body.get('peerSearch') or {}).get('sources'))
        torrent = body.get('torrent') or body.get('stream') or {}
        if isinstance(torrent, dict):
            sources += [a for a in torrent.get('announce') or [] if isinstance(a, str)]
        t = self.engine.get(ih, sources)
        if not t.wait_metadata(60):
            log(f'create {ih[:8]}: no metadata after 60s')
            return self.json(t.stats())
        st = t.stats()
        idx = file_must_include(st['files'], body.get('fileMustInclude'))
        if idx is not None:
            st['guessedFileIdx'] = idx
        elif isinstance(body.get('guessFileIdx'), dict):
            st['guessedFileIdx'] = guess_file_idx(st['files'], body['guessFileIdx'])
        log(f'create {ih[:8]}: {t.ti.name()} ({len(st["files"])} files)'
            + (f', guessed file {st["guessedFileIdx"]}' if 'guessedFileIdx' in st else ''))
        return self.json(st)

    def create_from_file(self, body):
        blob, src = body.get('blob'), body.get('from')
        if isinstance(blob, str):
            data = bytes.fromhex(blob)
        elif isinstance(src, str) and src.startswith('http'):
            # python.org builds ship without a CA bundle; curl uses the system trust store.
            data = subprocess.run(['curl', '-sfL', '-m', '30', src], capture_output=True, check=True).stdout
        elif isinstance(src, str):
            data = Path(src).read_bytes()
        else:
            return self.json({'error': 'no torrent'}, 500)
        ti = lt.torrent_info(lt.bdecode(data))
        trackers = [t['url'] if isinstance(t, dict) else t.url for t in ti.trackers()]
        t = self.engine.get(str(ti.info_hash()), trackers, ti=ti)
        t.wait_metadata(5)
        return self.json(t.stats())

    def stream(self, ih, idx_raw, q):
        t = self.engine.get(ih, parse_sources(q.get('tr')))
        if not t.wait_metadata(60):
            self.close_connection = True
            return self.send_error(504, 'no torrent metadata')
        files = t.files()
        idx = int(idx_raw) if re.fullmatch(r'-?\d+', idx_raw) else None
        must = file_must_include(files, q.get('f'))
        if must is not None:
            idx = must
        elif idx is None:
            idx = next((i for i, f in enumerate(files) if f['name'] == idx_raw), None)
            if idx is None:
                return self.send_error(500, 'invalid file index or name')
        elif idx == -1:
            idx = guess_file_idx(files, {})
        if not 0 <= idx < len(files):
            return self.send_error(500, f'torrent has no file {idx}')
        f = files[idx]
        if q.get('external'):
            self.send_response(307)
            self.send_header('Location', f'/{ih}/{urllib.parse.quote(f["name"])}'
                             + ('?download=1' if q.get('download') else ''))
            self.send_header('Content-Length', '0')
            return self.end_headers()
        if self.prober and CONFIG['learn_from_plays']:
            # A no-op once the file is known; ffprobe's own requests come back through here too.
            try:
                self.prober.schedule(ih, idx, f['name'], aliases=[f'{ih.lower()}:-1'] if idx_raw == '-1' else (),
                                     delay=CONFIG['probe_delay_s'], extra=lambda: self.mp4_hidden(ih, idx))
            except Exception as e:      # learning is optional; playback is not
                log(f'facts {ih[:8]}/{idx}: could not schedule a probe — {type(e).__name__}: {e}')

        size = f['length']
        rng = self.headers.get('Range')
        start, end = 0, size - 1
        if rng:
            m = re.fullmatch(r'bytes=(\d*)-(\d*)', rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                m = None
            elif not m.group(1):
                start = max(0, size - int(m.group(2)))
            else:
                start = int(m.group(1))
                end = min(size - 1, int(m.group(2))) if m.group(2) else size - 1
            if m is None or start > end or start >= size:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                return self.end_headers()
        self.send_response(206 if rng else 200)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Type', mimetypes.guess_type(f['name'])[0] or 'application/octet-stream')
        self.send_header('Cache-Control', 'max-age=0, no-cache')
        self.send_header('Content-Length', str(end - start + 1))
        if rng:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('transferMode.dlna.org', 'Streaming')
        self.send_header('contentFeatures.dlna.org', 'DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000')
        if q.get('download'):
            ascii_name = f['name'].encode('ascii', 'replace').decode().replace('"', "'")
            self.send_header('Content-Disposition', f'attachment; filename="{ascii_name}"; '
                             f"filename*=UTF-8''{urllib.parse.quote(f['name'])}")
        self.cors()
        self.end_headers()
        if self.command == 'HEAD':
            return
        ok = self.send_range(t, idx, start, end, t.patches_for(idx, f['name'], size))
        took = time.time() - self.t0
        tm = getattr(self, 'timing', {})
        log(f'{self.command} {ih[:8]}/{idx} {rng or "full"} -> {human(self.sent)} in {took:.1f}s'
            + (f' (waiting for pieces {tm["wait"]:.1f}s, reading {tm["read"]:.1f}s, sending {tm["write"]:.1f}s)' if tm else '')
            + ('' if ok else ' (ended early)'))
        if not ok:
            self.close_connection = True

    def send_range(self, t, idx, start, end, patches=()):
        """Write file bytes [start, end] as their pieces arrive. False if the reader stopped early."""
        fs = layout(t.ti)
        plen = t.ti.piece_length()
        foff = fs.file_offset(idx)
        last_piece = (foff + end) // plen
        rid = object()
        t.ensure_file_wanted(idx)
        pos = start
        self.timing = timing = {'wait': 0.0, 'read': 0.0, 'write': 0.0}
        try:
            while pos <= end:
                piece = (foff + pos) // plen
                t0 = time.time()
                t.want(rid, piece, last_piece)
                ok = t.wait_piece(piece, CONFIG['piece_wait_s'])
                t1 = time.time()
                timing['wait'] += t1 - t0
                if not ok:
                    log(f'stream {t.ih[:8]}/{idx}: piece {piece} not available after {CONFIG["piece_wait_s"]}s')
                    return False
                data = t.piece_data(piece)
                t2 = time.time()
                timing['read'] += t2 - t1
                if data is None:
                    log(f'stream {t.ih[:8]}/{idx}: could not read piece {piece}')
                    return False
                a = foff + pos - piece * plen                  # where we are inside the piece
                b = min(len(data), foff + end + 1 - piece * plen)
                view = memoryview(data)[a:b]
                for off, rb in patches:        # e.g. a misplaced subtitle 'trak' relabelled 'free'
                    if off < pos + len(view) and off + len(rb) > pos:
                        view = memoryview(bytearray(view))
                        for i, ch in enumerate(rb):
                            if pos <= off + i < pos + len(view):
                                view[off + i - pos] = ch
                for i in range(0, len(view), CHUNK):
                    self.wfile.write(view[i:i + CHUNK])
                timing['write'] += time.time() - t2
                pos += b - a
                self.sent += b - a
            return True
        except OSError:
            return False   # the player closed the connection — normal on every seek
        except RuntimeError as e:
            log(f'stream {t.ih[:8]}/{idx}: stopped — {e}')   # e.g. torrent removed under the reader
            return False
        finally:
            t.release(rid)

    # ---- everything else: Stremio's own server

    remembered = None   # path -> (status, content-type, body), loaded lazily

    @classmethod
    def _remembered(cls):
        if cls.remembered is None:
            try:
                cls.remembered = {k: (v[0], v[1], v[2].encode()) for k, v in json.loads(REMEMBERED_FILE.read_text()).items()}
            except (OSError, ValueError):
                cls.remembered = {}
        return cls.remembered

    def proxy(self):
        body = self.read_body() or None
        host, port = CONFIG['upstream'].split(':')
        key = urllib.parse.urlsplit(self.path).path if self.command == 'GET' else None
        if key not in REMEMBERED:
            key = None
        # The status routes are how the TV decides whether the server is online: don't hang them.
        conn = http.client.HTTPConnection(host, int(port), timeout=5 if key else 600)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        if body is not None:
            headers['Content-Length'] = str(len(body))
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            r = conn.getresponse()
        except OSError as e:
            conn.close()
            saved = self._remembered().get(key) if key else None
            if saved:
                # Stremio desktop is closed: answer from the last time it was up, so the TV keeps
                # treating the streaming server as online and torrents keep working.
                status, ctype, data = saved
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.cors()
                self.end_headers()
                return self.wfile.write(data)
            return self.json({'error': f'Stremio streaming server unreachable: {e}'}, 502)
        if key and r.status == 200:
            data = r.read()
            conn.close()
            mem = self._remembered()
            ctype = r.getheader('Content-Type') or 'application/json; charset=utf-8'
            if mem.get(key, (None, None, None))[2] != data:
                mem[key] = (200, ctype, data)
                try:
                    write_atomic(REMEMBERED_FILE, json.dumps({k: [v[0], v[1], v[2].decode(errors='replace')]
                                                              for k, v in mem.items()}))
                except OSError:
                    pass
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.cors()
            self.end_headers()
            return self.wfile.write(data)
        try:
            self.send_response_only(r.status, r.reason)
            length = r.getheader('Content-Length')
            for k, v in r.getheaders():
                if k.lower() not in HOP_BY_HOP:
                    self.send_header(k, v)
            if self.path.startswith('/settings') and r.getheader('Cache-Control') is None:
                self.send_header('Cache-Control', 'no-store')
            if length is None and self.command != 'HEAD' and r.status not in (204, 304):
                self.send_header('Connection', 'close')
                self.close_connection = True
            self.end_headers()
            if self.command != 'HEAD':
                while True:
                    chunk = r.read1(64 * 1024)      # forward as it arrives (HLS segments, slow transcodes)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except http.client.HTTPException:
            self.close_connection = True           # upstream ended mid-body
        finally:
            conn.close()


class FastBind:
    def server_bind(self):
        # HTTPServer.server_bind calls socket.getfqdn(), a reverse-DNS lookup that can take ~30 s.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class TLSServer(FastBind, ThreadingHTTPServer):
    """TLS handshakes happen on the request thread, so one slow client cannot stall accept()."""
    daemon_threads = True
    ssl_context = None

    def finish_request(self, request, client_address):
        request.settimeout(15)
        try:
            request = self.ssl_context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError) as e:
            if not client_address[0].startswith(('127.', '::1')):
                log(f'{client_address[0]} TLS handshake failed: {e}')
            request.close()
            return
        request.settimeout(None)
        try:
            super().finish_request(request, client_address)
        finally:
            request.close()


class PlainServer(FastBind, ThreadingHTTPServer):
    daemon_threads = True


def tls_context():
    """Loads Stremio's certificate into TLSServer.ssl_context and keeps it current."""
    tls_dir = INSTALL_DIR / 'tls'
    tls_dir.mkdir(mode=0o700, exist_ok=True)
    state = {'mtime': None}

    def load():
        mtime = os.stat(CONFIG['cert_file']).st_mtime
        if mtime == state['mtime']:
            return
        c = json.loads(Path(CONFIG['cert_file']).read_text())
        for name, pem in (('cert.pem', c['cert']), ('key.pem', c['key'])):
            p = tls_dir / name
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as fh:
                fh.write(pem)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls_dir / 'cert.pem', tls_dir / 'key.pem')
        TLSServer.ssl_context = ctx       # new handshakes use it; ones in progress keep the old one
        log(f'cert {"loaded" if state["mtime"] is None else "reloaded"}: {c["domain"]} (expires {c.get("notAfter")})')
        state['mtime'] = mtime

    def watch():
        while True:
            time.sleep(60)
            try:
                load()
            except Exception as e:
                log('cert reload failed:', e)

    load()
    threading.Thread(target=watch, name='cert-watch', daemon=True).start()


def main():
    engine = Engine()
    Handler.engine = engine
    if os.access(CONFIG['ffprobe'], os.X_OK):
        Handler.prober = Prober(FactStore(CONFIG['facts_file']), f'http://127.0.0.1:{CONFIG["http_port"]}', log,
                                runner=ffprobe_runner(CONFIG['ffprobe']))
    else:
        log(f'stream facts off — no ffprobe at {CONFIG["ffprobe"]}')
    servers = []
    plain = PlainServer(('0.0.0.0', CONFIG['http_port']), Handler)
    servers.append(plain)
    try:
        tls_context()
        tls = TLSServer(('0.0.0.0', CONFIG['https_port']), Handler)
        servers.append(tls)
    except Exception as e:
        log(f'HTTPS disabled — could not load {CONFIG["cert_file"]}: {e}')
    for s in servers:
        threading.Thread(target=s.serve_forever, name=f'http-{s.server_address[1]}', daemon=True).start()
    log(f'libtorrent {lt.__version__} — listening on '
        + ', '.join(f'{"https" if isinstance(s, TLSServer) else "http"}:{s.server_address[1]}' for s in servers)
        + f'; other routes -> {CONFIG["upstream"]}; cache {CONFIG["cache_dir"]} (budget {CONFIG["cache_budget_gb"]} GB)'
        + f'; download cap {CONFIG["download_limit_mbps"] or "none"} Mbps'
        + ('; learning stream facts from plays' if Handler.prober and CONFIG['learn_from_plays'] else ''))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.wait(1):
        pass
    log('stopping: saving resume data')
    for s in servers:
        s.shutdown()
    engine.shutdown()


if __name__ == '__main__':
    sys.exit(main())
