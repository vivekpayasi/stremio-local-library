"""Run with:  python3 -m unittest discover -s tests

The prefetcher runs against a stand-in for the torrent engine that behaves like torrent_server's: a piece arrives
only while a live reader wants it, and a torrent taken out of the session raises what libtorrent raises.
"""
import collections
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from prefetch import Prefetcher  # noqa: E402

MB = 1024 * 1024
IH = 'b02c69e9c46feb40c23c10f785e31c673291240a'


class FakeTorrent:
    """Three episodes, 1 MB pieces: E01 = pieces 0-3, E02 = pieces 4-9, E03 = pieces 10-11."""
    SIZES = [4 * MB, 6 * MB, 2 * MB]

    def __init__(self, have=()):
        self.have = set(have)
        self.held = set()             # pieces that don't arrive until the test lets them
        self.lock = threading.RLock()
        self.reading = collections.Counter()
        self.readers = {}             # rid -> (piece, last piece, background), live readers only
        self.wanted = []              # every want(), in order
        self.ensured = []
        self.touched = 0
        self.error = None
        self.dropping = False
        self.removed = False
        self.metadata = True

    def _check(self):
        if self.removed:
            raise RuntimeError('invalid torrent handle used')

    def wait_metadata(self, timeout):
        return self.metadata

    def files(self):
        out, off = [], 0
        for i, n in enumerate(self.SIZES):
            out.append({'name': f'Show.S01E0{i + 1}.mkv', 'path': f'Show S01/Show.S01E0{i + 1}.mkv', 'length': n, 'offset': off})
            off += n
        return out

    def piece_range(self, idx):
        f = self.files()[idx]
        return f['offset'] // MB, (f['offset'] + f['length'] - 1) // MB

    def have_piece(self, p):
        self._check()
        return p in self.have

    def ensure_file_wanted(self, idx):
        self._check()
        self.ensured.append(idx)

    def want(self, rid, piece, last_piece, background=False):
        self._check()
        self.readers[rid] = (piece, last_piece, background)
        self.wanted.append((piece, last_piece, background))

    def release(self, rid):
        self.readers.pop(rid, None)

    def wait_piece(self, p, timeout):
        self._check()
        if p not in self.held and any(r[0] == p for r in self.readers.values()):
            self.have.add(p)
            return True
        time.sleep(min(timeout, 0.005))
        return p in self.have

    def touch(self):
        self.touched += 1


class FakeEngine:
    def __init__(self, torrent):
        self.torrents = {IH: torrent}
        self.short = False
        self.disk_full = False
        self.gets = 0

    def get(self, ih):
        self.gets += 1
        t = self.torrents.get(ih)
        if t is None:
            t = self.torrents[ih] = FakeTorrent()
        t.dropping = False
        return t

    def find(self, ih):
        return self.torrents.get(ih)

    def streams_short(self):
        return self.short

    def disk_low(self):
        return self.disk_full

    def file_progress(self, ih):
        t = self.torrents.get(ih)
        if t is None:
            return None
        out = []
        for i, f in enumerate(t.files()):
            first, last = t.piece_range(i)
            got = sum(1 for p in range(first, last + 1) if p in t.have)
            out.append({'idx': i, 'name': f['name'], 'length': f['length'], 'done': round(got / (last - first + 1), 4)})
        return out


def until(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.002)
    return False


class Prefetch(unittest.TestCase):
    def setUp(self):
        self.t = FakeTorrent(have={6})
        self.engine = FakeEngine(self.t)
        self.lines = []
        self.p = Prefetcher(self.engine, self.lines.append, wait_s=0.01)

    def job(self, idx=1):
        return next((j for j in self.p.jobs() if j['idx'] == idx), None)

    def state(self, idx=1):
        j = self.job(idx)
        return j and j['state']

    def test_every_missing_piece_of_the_file_is_fetched_in_order_in_the_background(self):
        ok, _ = self.p.start(IH, 1, 'S01E02')
        self.assertTrue(ok)
        self.assertTrue(until(lambda: self.state() == 'done'), self.p.jobs())
        self.assertEqual(self.t.wanted, [(4, 9, True), (5, 9, True), (7, 9, True), (8, 9, True), (9, 9, True)],
                         'piece 6 was already here; nothing outside E02 is asked for; every window is a background one')
        self.assertEqual(self.t.ensured, [1])
        self.assertEqual(self.job()['done'], 1.0)
        self.assertTrue(until(lambda: not self.t.readers), 'the reader is let go at the end')
        self.assertEqual(self.t.reading[1], 0)

    def test_the_file_counts_as_being_read_while_it_downloads(self):
        self.t.held = {5}
        self.p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: (5, 9, True) in self.t.wanted))
        self.assertEqual(self.t.reading[1], 1, 'so the dashboard refuses to delete it, or anything else in the torrent')
        self.assertEqual(self.state(), 'downloading')
        self.t.held = set()
        self.assertTrue(until(lambda: self.state() == 'done'))
        self.assertTrue(until(lambda: self.t.reading[1] == 0))

    def test_it_waits_while_a_stream_is_short_of_buffer(self):
        self.engine.short = True
        self.p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: self.state() == 'waiting'))
        time.sleep(0.05)
        self.assertEqual(self.t.readers, {}, 'no window competes with the stream')
        self.assertEqual(self.t.have, {6}, 'nothing was fetched')
        self.assertGreater(self.t.touched, 0, 'the torrent stays "used", so it is not dropped as idle meanwhile')
        self.assertEqual(self.t.reading[1], 1)
        self.engine.short = False
        self.assertTrue(until(lambda: self.state() == 'done'))

    def test_cancel_stops_it_and_keeps_what_arrived(self):
        self.t.held = {8}
        self.p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: (8, 9, True) in self.t.wanted))
        ok, _ = self.p.cancel(IH, 1)
        self.assertTrue(ok)
        self.assertIn('cancelled at 67%', self.lines[-1], 'E02 has 4 of its 6 pieces, whether or not the page polled')
        self.assertIsNone(self.job(), 'a cancelled job leaves the list')
        self.assertTrue(until(lambda: not self.t.readers and self.t.reading[1] == 0))
        asked = len(self.t.wanted)
        time.sleep(0.05)
        self.assertEqual(len(self.t.wanted), asked, 'nothing more is asked for')
        self.assertEqual(self.t.have, {4, 5, 6, 7})
        self.assertFalse(self.p.cancel(IH, 1)[0], 'nothing left to cancel')

    def test_a_torrent_taken_out_of_the_session_is_added_back(self):
        # Stremio's /remove takes a torrent out of the session whoever is reading it. Its pieces stay on disk.
        self.t.held = {7}
        self.p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: (7, 9, True) in self.t.wanted))
        old = self.t
        del self.engine.torrents[IH]
        old.removed = True
        self.engine.torrents[IH] = new = FakeTorrent(have=old.have)
        self.assertTrue(until(lambda: self.state() == 'done'), self.p.jobs())
        self.assertEqual(self.engine.gets, 2)
        self.assertEqual(new.wanted[0], (7, 9, True), 'it carries on where it was')
        self.assertEqual(new.ensured, [1])
        self.assertEqual((old.reading[1], new.reading[1]), (0, 0))

    def test_a_torrent_added_back_with_data_missing_is_checked_from_its_first_piece(self):
        # Whatever happened to the cache while it was out (eviction), the job ends only with every piece of the file here.
        self.t.held = {8}
        self.p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: (8, 9, True) in self.t.wanted))
        self.t.removed = True
        del self.engine.torrents[IH]
        self.engine.torrents[IH] = new = FakeTorrent()
        self.assertTrue(until(lambda: self.state() == 'done'), self.p.jobs())
        self.assertEqual(new.wanted[0], (4, 9, True))
        self.assertTrue({4, 5, 6, 7, 8, 9} <= new.have)

    def test_the_torrents_of_unfinished_jobs_are_kept_in_the_cache(self):
        self.t.held = {5}
        self.p.start(IH, 1, 'S01E02')
        self.p.start('c' * 40, 0, 'S02E01')
        self.assertEqual(self.p.pinned(), {IH, 'c' * 40}, 'running or queued: eviction must leave them alone')
        self.p.cancel('c' * 40, 0)
        self.t.held = set()
        self.assertTrue(until(lambda: self.state() == 'done'))
        self.assertEqual(self.p.pinned(), set())

    def test_it_waits_while_the_disk_is_nearly_full(self):
        self.engine.disk_full = True
        self.p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: self.state() == 'waiting'))
        self.assertIn('disk', self.job()['message'])
        time.sleep(0.05)
        self.assertEqual(self.t.have, {6}, 'nothing was fetched')
        self.engine.disk_full = False
        self.assertTrue(until(lambda: self.state() == 'done'))

    def test_stop_lets_go_of_the_torrent(self):
        # At shutdown: the engine is about to save and drop every torrent; the job must not hold one back.
        self.t.held = {5}
        self.p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: (5, 9, True) in self.t.wanted))
        self.p.stop()
        self.assertTrue(until(lambda: not self.t.readers and self.t.reading[1] == 0))
        gets = self.engine.gets
        self.t.dropping = True
        time.sleep(0.05)
        self.assertEqual(self.engine.gets, gets, 'a torrent on its way out is not asked for again')

    def test_a_failing_log_does_not_stop_later_jobs(self):
        def log(line):
            raise OSError('log file gone')
        p = Prefetcher(self.engine, log, wait_s=0.01)
        p.start(IH, 1, 'S01E02')
        p.start(IH, 2, 'S01E03')
        self.assertTrue(until(lambda: all(j['state'] in ('done', 'failed') for j in p.jobs())), p.jobs())

    def test_a_torrent_on_its_way_out_is_kept(self):
        self.t.held = {7}
        self.p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: (7, 9, True) in self.t.wanted))
        self.t.dropping = True
        self.assertTrue(until(lambda: self.engine.gets == 2), 'asked for again, which keeps it in the session')
        self.t.held = set()
        self.assertTrue(until(lambda: self.state() == 'done'))
        self.assertEqual(self.t.reading[1], 0)

    def test_one_at_a_time_in_the_order_asked(self):
        self.t.held = {5}
        self.p.start(IH, 1, 'S01E02')
        self.p.start(IH, 2, 'S01E03')
        self.assertTrue(until(lambda: (5, 9, True) in self.t.wanted))
        self.assertEqual(self.state(2), 'queued')
        self.assertFalse(any(w[0] >= 10 for w in self.t.wanted))
        self.t.held = set()
        self.assertTrue(until(lambda: self.state(2) == 'done'))
        self.assertEqual(self.state(1), 'done')

    def test_asking_twice_is_one_job(self):
        self.t.held = {5}
        self.p.start(IH, 1, 'S01E02')
        ok, msg = self.p.start(IH.upper(), 1, 'S01E02')
        self.assertTrue(ok)
        self.assertIn('Already', msg)
        self.assertEqual(len(self.p.jobs()), 1)

    def test_a_cancelled_queued_job_never_runs(self):
        self.t.held = {5}
        self.p.start(IH, 1, 'S01E02')
        self.p.start(IH, 2, 'S01E03')
        self.p.cancel(IH, 2)
        self.t.held = set()
        self.assertTrue(until(lambda: self.state(1) == 'done'))
        time.sleep(0.05)
        self.assertFalse(any(w[0] >= 10 for w in self.t.wanted))

    def test_no_metadata_or_no_such_file(self):
        self.t.metadata = False
        self.p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: self.state() == 'failed', 5))
        self.assertTrue(self.job()['message'])
        self.t.metadata = True
        self.p.start(IH, 7, 'S01E08')
        self.assertTrue(until(lambda: self.state(7) == 'failed'))
        self.assertEqual(self.t.reading, collections.Counter())


class Progress(unittest.TestCase):
    def test_rate_and_time_left_from_the_bytes_arriving(self):
        now = [100.0]
        t = FakeTorrent()
        t.held = set(range(12))          # nothing arrives on its own: the test adds pieces
        p = Prefetcher(FakeEngine(t), lambda *a: None, wait_s=0.01, clock=lambda: now[0])
        p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: p.jobs()[0]['state'] == 'downloading'))
        t.have |= {4}
        first = p.jobs()[0]
        self.assertEqual((first['done'], first['length']), (0.1667, 6 * MB))
        self.assertIsNone(first['mbps'], 'one reading is no rate yet')
        now[0] = 110.0
        t.have |= {5, 6}                  # 2 MB in 10 s
        j = p.jobs()[0]
        self.assertEqual(j['mbps'], 1.7)   # 2 * 1048576 * 8 / 10 / 1e6 = 1.68
        self.assertEqual(j['eta_s'], 15)   # 3 MB left at 209715.2 B/s
        p.cancel(IH, 1)

    def test_no_whole_piece_for_a_few_seconds_is_not_yet_a_stall(self):
        # Progress counts whole pieces: with 16 MB ones, the first seconds after starting show nothing arriving.
        now = [100.0]
        t = FakeTorrent()
        t.held = set(range(12))
        p = Prefetcher(FakeEngine(t), lambda *a: None, wait_s=0.01, clock=lambda: now[0])
        p.start(IH, 1, 'S01E02')
        self.assertTrue(until(lambda: p.jobs()[0]['state'] == 'downloading'))
        for at in range(102, 112, 2):
            now[0] = float(at)
            self.assertIsNone(p.jobs()[0]['mbps'], at)
        for at in range(112, 120, 2):
            now[0] = float(at)
            j = p.jobs()[0]
        self.assertEqual(j['mbps'], 0.0, 'nothing for 18 s is a stall')
        p.cancel(IH, 1)

    def test_finished_jobs_are_listed_for_a_while(self):
        now = [100.0]
        p = Prefetcher(FakeEngine(FakeTorrent()), lambda *a: None, wait_s=0.01, clock=lambda: now[0])
        p.start(IH, 2, 'S01E03')
        self.assertTrue(until(lambda: p.jobs()[0]['state'] == 'done'))
        now[0] += 599
        self.assertEqual(len(p.jobs()), 1)
        now[0] += 2
        self.assertEqual(p.jobs(), [])


if __name__ == '__main__':
    unittest.main()
