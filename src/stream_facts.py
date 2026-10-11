"""What a torrent's video file really contains, learned by probing it once.

The Stream Advisor rates streams from their release names, which are guesses. Once a file has been played
through the torrent server, ffprobe (bundled with Stremio desktop) reads its header through the server
itself and the real codecs are recorded in stream-facts.json, keyed "<info hash>:<file index>". The
advisor prefers these facts over the name, and marks such ratings as verified.
"""
import heapq
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

FFPROBE = '/Applications/Stremio.app/Contents/MacOS/ffprobe'
FORMAT_CONTAINER = (('matroska', 'mkv'), ('mp4', 'mp4'), ('mpegts', 'ts'), ('avi', 'avi'), ('webm', 'webm'))


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def summarize_probe(probe, name, mp4_subs_hidden=0):
    """The facts the advisor rates by, from `ffprobe -show_format -show_streams` JSON."""
    streams = probe.get('streams') or []
    fmt = probe.get('format') or {}

    ext = re.search(r'\.([a-z0-9]{2,4})$', name or '', re.I)
    container = ext.group(1).lower() if ext else None
    if container not in ('mkv', 'mp4', 'm4v', 'avi', 'm2ts', 'ts', 'mov', 'webm'):
        fname = fmt.get('format_name') or ''
        container = next((c for key, c in FORMAT_CONTAINER if key in fname), None)

    # Cover art is a 'video' stream too; the real one is the first that is not an attached picture.
    video = next((s for s in streams if s.get('codec_type') == 'video'
                  and not (s.get('disposition') or {}).get('attached_pic')), None)
    v = None
    if video:
        dovi = next((d for d in video.get('side_data_list') or []
                     if d.get('side_data_type') == 'DOVI configuration record'), None)
        depth = re.search(r'p(\d+)', video.get('pix_fmt') or '')
        v = {
            'codec': video.get('codec_name'), 'profile': video.get('profile'),
            'width': video.get('width'), 'height': video.get('height'),
            'bitDepth': int(depth.group(1)) if depth and int(depth.group(1)) > 8 else 8,
            'transfer': video.get('color_transfer'),
            'dv': {'profile': dovi.get('dv_profile'), 'level': dovi.get('dv_level'),
                   'el': bool(dovi.get('el_present_flag')),
                   'compat': dovi.get('dv_bl_signal_compatibility_id')} if dovi else None,
        }

    audio = [{'codec': s.get('codec_name'), 'profile': s.get('profile'), 'channels': s.get('channels'),
              'lang': (s.get('tags') or {}).get('language'),
              'default': bool((s.get('disposition') or {}).get('default'))}
             for s in streams if s.get('codec_type') == 'audio']
    subtitles = [{'codec': s.get('codec_name'), 'lang': (s.get('tags') or {}).get('language')}
                 for s in streams if s.get('codec_type') == 'subtitle']

    duration, size = _num(fmt.get('duration')), _num(fmt.get('size'), int)
    bit_rate = _num(fmt.get('bit_rate'))
    if not bit_rate and size and duration:
        bit_rate = size * 8 / duration

    return {
        'source': 'ffprobe', 'probedAt': _now(), 'file': name, 'container': container,
        'durationS': round(duration, 1) if duration else None, 'sizeBytes': size,
        'bitrateMbps': round(bit_rate / 1e6, 1) if bit_rate else None,
        'video': v, 'audio': audio, 'subtitles': subtitles, 'mp4SubsHidden': mp4_subs_hidden,
    }


class FactStore:
    """stream-facts.json: {"version": 1, "facts": {key: fact}, "failed": {key: {at, why}}}. Written
    atomically, since the advisor (another process) reads it at any moment."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text())
            self.facts, self.failed = dict(data.get('facts') or {}), dict(data.get('failed') or {})
        except (OSError, ValueError, AttributeError):
            self.facts, self.failed = {}, {}

    def get(self, key):
        with self.lock:
            return self.facts.get(key)

    def put(self, keys, fact):
        with self.lock:
            for k in keys:
                self.facts[k] = fact
                self.failed.pop(k, None)
            self._save()

    def fail(self, key, why):
        with self.lock:
            self.failed[key] = {'at': time.time(), 'why': str(why)[:200]}
            self._save()

    def failed_recently(self, key, within_s):
        with self.lock:
            f = self.failed.get(key)
            at = f.get('at') if isinstance(f, dict) else None
            return isinstance(at, (int, float)) and time.time() - at < within_s

    def all(self):
        with self.lock:
            return {'version': 1, 'facts': dict(self.facts), 'failed': dict(self.failed)}

    def _save(self):
        tmp = self.path.with_name(self.path.name + '.tmp')
        tmp.write_text(json.dumps({'version': 1, 'facts': self.facts, 'failed': self.failed}, indent=1))
        os.replace(tmp, self.path)


def ffprobe_runner(ffprobe, timeout=150):
    def run(url):
        out = subprocess.run([ffprobe, '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', url],
                             capture_output=True, timeout=timeout)
        if out.returncode != 0:
            raise RuntimeError(f'ffprobe exited {out.returncode}: {out.stderr.decode(errors="replace").strip()[:200]}')
        return json.loads(out.stdout)
    return run


class Prober:
    """Probes one file at a time on a background thread, each file once. `runner(url)` returns ffprobe's
    JSON for a torrent server URL; the default runs Stremio's bundled ffprobe."""

    RETRY_FAILED_S = 3600

    def __init__(self, store, base_url, log, runner=None):
        self.store, self.base_url, self.log = store, base_url.rstrip('/'), log
        self.runner = runner or ffprobe_runner(FFPROBE)
        self.cond = threading.Condition()
        self.queue = []          # heap of (due time, seq, job)
        self.pending = set()
        self.seq = 0
        self.running = True
        self.thread = threading.Thread(target=self._loop, name='prober', daemon=True)
        self.thread.start()

    def schedule(self, ih, idx, name, aliases=(), delay=0, extra=None):
        """Queue a probe of file `idx` unless it is known, queued, or failed within the last hour."""
        key = f'{ih.lower()}:{idx}'
        with self.cond:
            if (key in self.pending or self.store.get(key) is not None
                    or self.store.failed_recently(key, self.RETRY_FAILED_S)):
                return False
            self.pending.add(key)
            self.seq += 1
            job = {'key': key, 'keys': [key] + [k for k in aliases if k != key], 'ih': ih.lower(), 'idx': idx,
                   'name': name, 'extra': extra}
            heapq.heappush(self.queue, (time.time() + delay, self.seq, job))
            self.cond.notify()
        return True

    def stop(self, timeout=5):
        """Stop after the probe in progress, if any."""
        with self.cond:
            self.running = False
            self.cond.notify()
        self.thread.join(timeout)

    def _loop(self):
        while True:
            with self.cond:
                while self.running and (not self.queue or self.queue[0][0] > time.time()):
                    self.cond.wait(None if not self.queue else max(0.01, self.queue[0][0] - time.time()))
                if not self.running:
                    return
                job = heapq.heappop(self.queue)[2]
            try:
                self._probe(job)
            except Exception as e:      # never let one job end the thread: later probes would queue forever
                self.log(f'facts {job["key"][:8]}: probe crashed — {type(e).__name__}: {e}')

    def _probe(self, job):
        t0, where = time.time(), f'facts {job["key"][:8]}/{job["idx"]}'
        try:
            try:
                probe = self.runner(f'{self.base_url}/{job["ih"]}/{job["idx"]}')
                extra = job['extra']() if job['extra'] else {}
                fact = summarize_probe(probe, job['name'], **({'mp4_subs_hidden': extra['mp4SubsHidden']}
                                                              if 'mp4SubsHidden' in extra else {}))
            except Exception as e:
                self.log(f'{where}: probe failed — {type(e).__name__}: {e}')
                self.store.fail(job['key'], f'{type(e).__name__}: {e}')
                return
            v, a = fact['video'] or {}, fact['audio']
            dv = f" DV P{v['dv']['profile']}" if v.get('dv') else ''
            self.log(f'{where}: {fact["container"]} {v.get("codec")} {v.get("width")}x{v.get("height")}{dv}, '
                     f'audio {"+".join(x["codec"] or "?" for x in a) or "none"}, {fact["bitrateMbps"]} Mbps '
                     f'(probed in {time.time() - t0:.1f}s)')
            self.store.put(job['keys'], fact)
        except OSError as e:
            self.log(f'{where}: could not save {self.store.path}: {e}')
        finally:
            with self.cond:
                self.pending.discard(job['key'])
