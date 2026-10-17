"""Downloads for later: the dashboard's "Download next episode" button. A file of a torrent is fetched into the
cache from start to end, so it later plays from this Mac — and the TV's stream card shows it as downloaded. A job
can be for a place (storage.py): its torrent is moved there first if it is elsewhere (Engine.prepare), and the job
ends if that drive is disconnected rather than carry on onto this Mac.

One file at a time, in the order asked. Its pieces are asked for in the background — priority 1 and no deadlines,
so a stream's urgent pieces always go first — and not at all while a stream is short of buffer (the engine's
streams_short()) or its disk is nearly full (disk_low()): a download for later doesn't take bandwidth or disk
space a stream needs. The torrent stays in the session while it runs, the file counts as being read so the
dashboard won't delete it, and the torrents of unfinished jobs are kept out of the cache's eviction (pinned()).
Jobs live in memory: after a restart the pieces already fetched stay in the cache and asking again carries on.
"""
import collections
import threading
import time

ACTIVE = ('queued', 'starting', 'moving', 'downloading', 'waiting')
KEEP_S = 600         # a finished or failed job stays listed this long
RATE_S = 20          # the rate shown is over this many seconds
METADATA_S = 60      # give up on a torrent whose details no peer sends in this long
TOUCH_S = 30         # while waiting, mark the torrent used this often (it is dropped from the session after 180 s idle)


class Prefetcher:
    """`engine` is torrent_server's Engine: get(ih), find(ih), file_progress(ih), streams_short(), disk_low(ih), and for
    jobs for a place (its folder), prepare(ih, idx, place, note, stop) and place_ok(place)."""

    def __init__(self, engine, log, wait_s=2.0, clock=time.time):
        self.engine, self.log, self.wait_s, self.clock = engine, log, wait_s, clock
        self.lock = threading.Condition()
        self.queue = []
        self.thread = None
        self.stopped = False

    # ---- what the dashboard calls

    def start(self, ih, idx, label=None, place=None, where=None):
        """Queue a file, for a place (its id, and `where`: its name) or wherever its torrent is: (ok, message)."""
        ih = ih.lower()
        with self.lock:
            for j in self.queue:
                if (j['ih'], j['idx']) == (ih, idx) and j['state'] in ACTIVE:
                    return True, f'Already downloading {j["label"]}'
            self.queue = [j for j in self.queue if (j['ih'], j['idx']) != (ih, idx)]
            busy = any(j['state'] in ACTIVE for j in self.queue)
            self.queue.append({'ih': ih, 'idx': idx, 'label': label or f'file {idx}', 'state': 'queued', 'done': 0.0,
                               'length': 0, 'message': None, 'ended': None, 'cancel': threading.Event(),
                               'samples': collections.deque(), 'place': place, 'where': where or 'this Mac'})
            if self.thread is None:
                self.thread = threading.Thread(target=self._work, name='prefetch', daemon=True)
                self.thread.start()
            self.lock.notify_all()
        return True, f'Queued {label} after the current download' if busy else f'Downloading {label} for later'

    def cancel(self, ih, idx):
        """Stop a job (what it fetched stays in the cache): (ok, message)."""
        ih = ih.lower()
        with self.lock:
            job = next((j for j in self.queue if (j['ih'], j['idx']) == (ih, idx) and j['state'] in ACTIVE), None)
            if not job:
                return False, 'Nothing is downloading for that file'
            job['cancel'].set()
            self.queue.remove(job)
        self._progress(job)
        self.log(f'prefetch {ih[:8]}/{idx}: {job["label"]} cancelled at {job["done"]:.0%}')
        return True, f'Stopped downloading {job["label"]}'

    def pinned(self):
        """Info hashes with a job queued or running: the cache must not evict them."""
        with self.lock:
            return {j['ih'] for j in self.queue if j['state'] in ACTIVE}

    def stop(self):
        """At shutdown: let go of every torrent, so the engine can save and drop them."""
        with self.lock:
            self.stopped = True
            for j in self.queue:
                j['cancel'].set()
            self.lock.notify_all()

    def jobs(self):
        """Each job, for the page: state, how much of the file is here, and how fast the rest is coming."""
        now = self.clock()
        out = []
        with self.lock:
            self.queue = [j for j in self.queue if j['ended'] is None or now - j['ended'] <= KEEP_S]
            for j in self.queue:
                self._progress(j)
                s = j['samples']
                if j['state'] == 'downloading':
                    if s and s[-1][0] == now:
                        s.pop()
                    s.append((now, j['done'] * j['length']))
                    while s and now - s[0][0] > RATE_S:
                        s.popleft()
                else:
                    s.clear()
                rate = (s[-1][1] - s[0][1]) / (s[-1][0] - s[0][0]) if len(s) > 1 and s[-1][0] > s[0][0] else None
                if rate == 0 and s[-1][0] - s[0][0] < RATE_S * 0.8:
                    rate = None      # progress counts whole pieces: a few seconds without one isn't a stall yet
                left = j['length'] * (1 - j['done'])
                out.append({'ih': j['ih'], 'idx': j['idx'], 'label': j['label'], 'state': j['state'], 'place': j['place'],
                            'done': j['done'], 'length': j['length'], 'message': j['message'],
                            'mbps': None if rate is None else round(rate * 8 / 1e6, 1),
                            'eta_s': round(left / rate) if rate and rate > 0 else None})
        return out

    def _progress(self, job):
        """Refresh how much of the job's file is here, while its torrent is in the session."""
        f = next((f for f in self.engine.file_progress(job['ih']) or () if f['idx'] == job['idx']), None)
        if f:
            job['done'], job['length'] = f['done'], f['length']

    # ---- the worker

    def _work(self):
        while True:
            with self.lock:
                if self.stopped:
                    return
                job = next((j for j in self.queue if j['state'] == 'queued'), None)
                if job is None:
                    self.lock.wait()
                    continue
                job['state'] = 'starting'
            try:
                self._run(job)
            except Exception as e:
                try:
                    self._end(job, 'failed', f'{type(e).__name__}: {e}')
                except Exception:
                    pass         # the worker carries on with the next job whatever happens

    def _end(self, job, state, message=None):
        with self.lock:
            job['state'], job['message'], job['ended'] = state, message, self.clock()
            if state == 'done':
                job['done'] = 1.0
        if state == 'done':
            self.log(f'prefetch {job["ih"][:8]}/{job["idx"]}: {job["label"]} is on {job["where"]}')
        else:
            self.log(f'prefetch {job["ih"][:8]}/{job["idx"]}: {job["label"]} {state}{f" — {message}" if message else ""}')

    def _set(self, job, state, message=None):
        with self.lock:
            if job['state'] in ACTIVE:
                job['state'], job['message'] = state, message

    def _run(self, job):
        ih, idx, cancel = job['ih'], job['idx'], job['cancel']
        rid = object()          # this job's reader, as a stream's request is one
        held = None             # the torrent whose file is marked as being read by this job
        p = last = None
        touched = 0.0
        self.log(f'prefetch {ih[:8]}/{idx}: downloading {job["label"]} for later')
        if job['place'] and not self._ready(job):
            return
        try:
            while not cancel.is_set():
                try:
                    if held is None or held.dropping or self.engine.find(ih) is not held:
                        # First time round, or the torrent is leaving the session (Stremio's /remove takes it out
                        # whoever is reading it): asking for it keeps it in, or adds it back from the cache.
                        if job['place'] and not self.engine.place_ok(job['place']):
                            return self._end(job, 'failed', f'{job["where"]} was disconnected. What downloaded is kept on it.')
                        t = self.engine.get(ih)
                        if t is not held:
                            if held is not None:
                                self._let_go(held, rid, idx)
                                held = None
                            if not self._metadata(t, cancel):
                                if not cancel.is_set():
                                    self._end(job, 'failed', 'No peer sent the torrent\'s details')
                                return
                            if not 0 <= idx < len(t.files()):
                                return self._end(job, 'failed', 'The torrent has no such file')
                            # From the first piece again: whatever was done before is skipped quickly, and a
                            # torrent added back with data missing gets it fetched again.
                            p, last = t.piece_range(idx)
                            t.ensure_file_wanted(idx)
                            with t.lock:
                                t.reading[idx] += 1
                            held = t
                    t = held
                    if p > last:
                        return self._end(job, 'done')
                    if t.have_piece(p):
                        p += 1
                        continue
                    why = ('Waiting while the TV buffers' if self.engine.streams_short()
                           else 'Waiting for disk space' if self.engine.disk_low(ih) else None)
                    if why:
                        if job['state'] != 'waiting':
                            t.release(rid)
                        if time.time() - touched > TOUCH_S:
                            touched = time.time()
                            t.touch()    # not idle, so it isn't dropped while it waits
                        self._set(job, 'waiting', why)
                        cancel.wait(self.wait_s)
                        continue
                    self._set(job, 'downloading', t.error)
                    t.want(rid, p, last, background=True)
                    if not t.wait_piece(p, self.wait_s) and t.error:
                        cancel.wait(self.wait_s)     # a disk error stopped it; housekeeping resumes it once there's room
                except RuntimeError:
                    # The torrent left the session under us; the next round adds it back.
                    if held is not None:
                        self._let_go(held, rid, idx)
                        held = None
                    cancel.wait(self.wait_s)
        finally:
            if held is not None:
                self._let_go(held, rid, idx)

    def _ready(self, job):
        """Get a job's torrent ready in its place (moved there, its file written out): whether the download can go on.
        Waits while something reads the torrent; a move would pull it from under the reader."""
        while not job['cancel'].is_set():
            out = self.engine.prepare(job['ih'], job['idx'], job['place'], note=lambda m: self._set(job, 'moving', m),
                                      stop=job['cancel'].is_set)
            if out is None:
                self._set(job, 'waiting', f'Waiting for the TV to finish with this torrent, to move it to {job["where"]}')
                job['cancel'].wait(self.wait_s)
                continue
            ok, message = out
            if not ok and not job['cancel'].is_set():
                self._end(job, 'failed', message)
            return ok
        return False

    def _metadata(self, t, cancel):
        for _ in range(max(1, round(METADATA_S / self.wait_s))):
            if cancel.is_set():
                return False
            if t.wait_metadata(self.wait_s):
                return True
        return False

    @staticmethod
    def _let_go(t, rid, idx):
        t.release(rid)
        with t.lock:
            t.reading[idx] -= 1
