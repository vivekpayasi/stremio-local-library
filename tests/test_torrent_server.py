"""Run with the server's own Python (libtorrent is installed there):
    ~/.stremio-local-addon/venv/bin/python -m unittest discover -s tests
Skipped where libtorrent isn't installed. The engine's pure logic is run on stand-ins for libtorrent's handle.
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
os.environ['TORRENTD_CONFIG'] = os.devnull + '.none'   # the built-in defaults, not this Mac's torrentd.json

try:
    import torrent_server as ts
except ImportError as e:
    raise unittest.SkipTest(f'torrent_server needs libtorrent ({e})')

MB = 1024 * 1024


class Handle:
    """What Torrent._apply uses of a libtorrent handle: the pieces it has, and the priorities and deadlines set."""
    def __init__(self, n, have=()):
        self.pieces = [i in set(have) for i in range(n)]
        self.prio = {}
        self.deadlines = {}

    def status(self, flags=None):
        return SimpleNamespace(pieces=self.pieces)

    def prioritize_pieces(self, changes):
        self.prio.update(dict(changes))

    def reset_piece_deadline(self, p):
        self.deadlines.pop(p, None)

    def set_piece_deadline(self, p, ms):
        self.deadlines[p] = ms


def torrent(have=(), n=300, plen=16 * MB):
    """A Torrent with 16 MB pieces: a stream's urgent part is 4 pieces, its window starts at 2 and grows to 28."""
    t = object.__new__(ts.Torrent)
    t.ti = SimpleNamespace(piece_length=lambda: plen)
    t.handle = Handle(n, have)
    t.readers, t.prio, t.deadlines, t.lock = {}, {}, set(), threading.RLock()
    return t


def reader(at, last, bg=False):
    return {'at': at, 'last': last, 'released': None, 'hi': at, 'urgent_to': at, 'bg': bg, 'short': not bg}


class Windows(unittest.TestCase):
    def test_a_stream_alone(self):
        t = torrent()
        t.readers['tv'] = reader(10, 200)
        t._apply()
        self.assertEqual(t.handle.prio, {10: 7, 11: 7, 12: 7})
        self.assertEqual(sorted(t.handle.deadlines), [10, 11, 12])

    def test_a_download_for_later_beside_a_stream(self):
        t = torrent()
        t.readers['tv'] = reader(10, 200)
        t.readers['later'] = reader(5, 100, bg=True)
        t._apply()
        want = {p: 1 for p in range(5, 34)}       # its whole window (28 ahead) at once, at the lowest priority
        want.update({10: 7, 11: 7, 12: 7})        # the stream's pieces keep their own priority...
        self.assertEqual(t.handle.prio, want)
        self.assertEqual(sorted(t.handle.deadlines), [10, 11, 12], '...and only the stream gets deadlines')

    def test_a_download_for_later_alone_ends_at_its_file(self):
        t = torrent()
        t.readers['later'] = reader(40, 50, bg=True)
        t._apply()
        self.assertEqual(t.handle.prio, {p: 1 for p in range(40, 51)})
        self.assertEqual(t.handle.deadlines, {})

    def test_a_stream_is_short_until_half_its_read_ahead_is_in(self):
        for have, last, short in ((range(10, 23), 200, True),     # 13 pieces in
                                  (range(10, 24), 200, False),    # 14 = half of 28
                                  (range(10, 16), 15, False),     # everything it asked for is in
                                  ((), 200, True)):
            t = torrent(have=have)
            t.readers['tv'] = reader(10, last)
            t._apply()
            self.assertEqual(t.readers['tv']['short'], short, (list(have)[-1:], last))


class StreamsShort(unittest.TestCase):
    def engine(self, *readers):
        t = SimpleNamespace(readers=dict(enumerate(readers)), lock=threading.RLock())
        return SimpleNamespace(torrents={'a' * 40: t})

    def test_only_live_streams_count(self):
        short = ts.Engine.streams_short
        self.assertTrue(short(self.engine(dict(reader(0, 9), short=True))))
        self.assertFalse(short(self.engine(dict(reader(0, 9), short=False))))
        self.assertFalse(short(self.engine(dict(reader(0, 9), short=True, released=time.time()))), 'a closed request')
        self.assertFalse(short(self.engine(dict(reader(0, 9, bg=True), short=True))), 'a download for later')
        self.assertFalse(short(SimpleNamespace(torrents={})))


class PieceRange(unittest.TestCase):
    def test_the_pieces_a_file_spans(self):
        fs = SimpleNamespace(file_offset=lambda i: [0, 10 * MB, 18 * MB][i], file_size=lambda i: [10 * MB, 8 * MB, 0][i])
        t = object.__new__(ts.Torrent)
        t.ti = SimpleNamespace(piece_length=lambda: 4 * MB, layout=lambda: fs)
        self.assertEqual([t.piece_range(i) for i in range(3)], [(0, 2), (2, 4), (4, 4)])


class Eviction(unittest.TestCase):
    def test_the_torrent_of_an_unfinished_download_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            for ih, used in (('a' * 40, 1000), ('b' * 40, 2000)):
                (Path(d) / ih).mkdir()
                (Path(d) / ih / 'E01.mkv').write_bytes(b'x' * 2 * MB)
                (Path(d) / ih / '.last-used').write_text(str(used))
            engine = SimpleNamespace(cache=Path(d), torrents={}, pinned=lambda: {'a' * 40})
            with mock.patch.dict(ts.CONFIG, cache_budget_gb=3 / 1024, min_free_gb=0), mock.patch.object(ts, 'log'):
                ts.Engine._evict(engine)
            self.assertTrue((Path(d) / ('a' * 40)).exists(), 'the oldest, but a download for later is under way')
            self.assertFalse((Path(d) / ('b' * 40)).exists())


class DiskLow(unittest.TestCase):
    def test_below_the_space_kept_free(self):
        with tempfile.TemporaryDirectory() as d:
            engine = SimpleNamespace(cache=Path(d))
            free = ts.shutil.disk_usage(d).free / 1024 / MB
            with mock.patch.dict(ts.CONFIG, min_free_gb=free + 1):
                self.assertTrue(ts.Engine.disk_low(engine))
            with mock.patch.dict(ts.CONFIG, min_free_gb=max(0, free - 1)):
                self.assertFalse(ts.Engine.disk_low(engine))


if __name__ == '__main__':
    unittest.main()
