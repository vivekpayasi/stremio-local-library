"""Where the cache lives: this Mac, and an external drive while it is connected. Run with:
python3 -m unittest discover -s tests

Volumes are temporary folders here; `mounted` says which of them count as connected drives.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from storage import EXTERNAL_DIR, Places, allocate, bencode, covers, file_paths, footprint, move_folder  # noqa: E402

MB = 1024 * 1024
GB = 1024 * MB
IH = 'a' * 40


def resume(info, pieces=b''):
    return bencode({'info': info, 'pieces': pieces})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mac = self.root / 'torrent-cache'
        self.mac.mkdir()
        self.volumes = self.root / 'Volumes'
        self.ssd = self.volumes / 'Extreme SSD'
        self.ssd.mkdir(parents=True)
        self.connected = {str(self.ssd)}
        self.file = self.root / 'torrentd.json'

    def tearDown(self):
        os.chmod(self.ssd, 0o700)
        self.tmp.cleanup()

    def places(self, look=True, **config):
        """Places as the server has them once its places thread has looked at the disks (look=False: before)."""
        config = dict({'cache_dir': str(self.mac), 'cache_budget_gb': 15, 'min_free_gb': 3}, **config)
        p = Places(config, self.file, mounted=lambda p: str(p) in self.connected, volumes_root=self.volumes)
        if look:
            p.refresh()
        return p


class WhichPlaces(Base):
    def test_without_an_external_drive_everything_is_on_this_mac(self):
        p = self.places()
        self.assertEqual([x['id'] for x in p.all()], ['mac'])
        self.assertEqual(p.destination()['id'], 'mac')
        self.assertEqual(p.mac()['budget'], 15 * GB)
        self.assertEqual(p.mac()['name'], 'This Mac')

    def test_a_connected_drive_takes_downloads_for_later(self):
        p = self.places(external_volume=str(self.ssd), external_budget_gb=50, downloads_to='external')
        ext = p.get('external')
        self.assertTrue(ext['available'], ext)
        self.assertEqual(ext['name'], 'Extreme SSD')
        self.assertEqual(ext['dir'], self.ssd / EXTERNAL_DIR)
        self.assertEqual(ext['budget'], 50 * GB)
        self.assertEqual(p.destination()['id'], 'external')
        self.assertEqual([x['id'] for x in p.available()], ['mac', 'external'])
        self.assertEqual(self.places(external_volume=str(self.ssd), downloads_to='mac').destination()['id'], 'mac')

    def test_a_disconnected_drive_is_listed_and_never_written_to(self):
        # macOS can leave an empty folder under /Volumes after a drive goes; writing there would fill the Mac's disk.
        self.connected.clear()
        p = self.places(external_volume=str(self.ssd), downloads_to='external')
        ext = p.get('external')
        self.assertFalse(ext['available'])
        self.assertEqual(ext['why'], 'Not connected')
        self.assertEqual(p.destination()['id'], 'mac', 'downloads for later fall back to this Mac')
        self.assertEqual([x['id'] for x in p.available()], ['mac'])
        self.assertFalse((self.ssd / EXTERNAL_DIR).exists())

    def test_a_drive_the_server_may_not_write_to_is_not_used(self):
        os.chmod(self.ssd, 0o500)
        p = self.places(external_volume=str(self.ssd), downloads_to='external')
        ext = p.get('external')
        self.assertFalse(ext['available'])
        self.assertIn('macOS', ext['why'])
        self.assertEqual(p.destination()['id'], 'mac')

    def test_the_disks_are_looked_at_only_by_refresh(self):
        # Off the streaming path: a drive can block for a while (a consent prompt, a drive pulled out).
        p = self.places(look=False, external_volume=str(self.ssd), downloads_to='external')
        self.assertEqual(p.get('external')['why'], 'Checking…')
        self.assertEqual(p.destination()['id'], 'mac')
        self.assertFalse((self.ssd / EXTERNAL_DIR).exists())
        p.refresh()
        self.assertTrue(p.get('external')['available'])
        self.connected.clear()
        self.assertTrue(p.get('external')['available'], 'until it is looked at again')
        p.refresh()
        self.assertEqual(p.get('external')['why'], 'Not connected')

    def test_the_probe_waits_for_the_write_to_reach_the_disk(self):
        # macOS's exFAT driver (fskit) counts a file's space only once its data is flushed: until then even exFAT
        # looks like it can skip empty space.
        import storage
        from types import SimpleNamespace
        from unittest import mock
        flushed, real = set(), os.fstat
        fstat = lambda fd: SimpleNamespace(st_blocks=real(fd).st_blocks if fd in flushed else 0)  # noqa: E731
        exfat = lambda fd: SimpleNamespace(st_blocks=17 * 2048 if fd in flushed else 0)  # noqa: E731
        with mock.patch.object(storage.os, 'fsync', flushed.add):
            with mock.patch.object(storage.os, 'fstat', exfat):
                self.assertIs(storage.can_skip_empty_space(self.mac), False)
            with mock.patch.object(storage.os, 'fstat', fstat):
                self.assertIs(storage.can_skip_empty_space(self.mac), True)

    def test_whether_a_disk_can_skip_empty_space(self):
        # APFS can (a file written only at its end takes almost nothing); exFAT can't, which the e2e check covers.
        self.assertIs(self.places().mac()['sparse'], True)


class Locate(Base):
    def copy(self, base, done):
        (base / IH).mkdir(parents=True)
        info = {'name': 'Movie.mkv', 'piece length': 4 * MB, 'length': 8 * MB}
        (base / IH / '.resume').write_bytes(resume(info, bytes([{0: 0, 1: 0b10000000, 2: 0b11000000}[done]])))

    def test_the_folder_of_a_torrent_wherever_it_is(self):
        p = self.places(external_volume=str(self.ssd))
        self.assertIsNone(p.locate(IH))
        self.copy(self.mac, 1)
        self.assertEqual(p.locate(IH), self.mac / IH)

    def test_with_a_copy_on_each_the_one_with_more_downloaded(self):
        # The drive was away, and the TV played the movie again from this Mac.
        ext = self.ssd / EXTERNAL_DIR
        self.copy(self.mac, 2)
        self.copy(ext, 1)
        p = self.places(external_volume=str(self.ssd))
        self.assertEqual(p.locate(IH), self.mac / IH)
        self.assertEqual(sorted(str(d) for _, d in p.copies(IH)), sorted([str(self.mac / IH), str(ext / IH)]))

    def test_with_as_much_on_each_the_drive_wins(self):
        self.copy(self.mac, 1)
        self.copy(self.ssd / EXTERNAL_DIR, 1)
        p = self.places(external_volume=str(self.ssd))
        self.assertEqual(p.locate(IH), self.ssd / EXTERNAL_DIR / IH)

    def test_a_copy_on_a_disconnected_drive_is_not_found(self):
        self.copy(self.ssd / EXTERNAL_DIR, 2)
        self.connected.clear()
        self.assertIsNone(self.places(external_volume=str(self.ssd)).locate(IH))

    def test_what_the_drive_holds_is_known_from_the_last_look_and_the_moves_since(self):
        # So a torrent the TV starts that isn't on the drive costs no look at the drive.
        p = self.places(external_volume=str(self.ssd))
        self.copy(self.ssd / EXTERNAL_DIR, 2)
        self.assertIsNone(p.locate(IH), 'not there at the last look')
        p.note(IH, p.get('external'))
        self.assertEqual(p.locate(IH), self.ssd / EXTERNAL_DIR / IH)
        q = self.places(external_volume=str(self.ssd))
        self.assertEqual(q.locate(IH), self.ssd / EXTERNAL_DIR / IH, 'and the next look finds it')

    def test_a_drive_that_cant_be_listed_keeps_what_it_was_known_to_hold(self):
        self.copy(self.ssd / EXTERNAL_DIR, 2)
        p = self.places(external_volume=str(self.ssd))
        self.assertIsNotNone(p.locate(IH))
        os.chmod(self.ssd / EXTERNAL_DIR, 0o300)       # writable, not listable
        try:
            p.refresh()
        finally:
            os.chmod(self.ssd / EXTERNAL_DIR, 0o700)
        self.assertEqual(p.locate(IH), self.ssd / EXTERNAL_DIR / IH)

    def test_which_copy_holds_every_piece_of_the_other(self):
        a, b = self.mac / 'a', self.mac / 'b'
        for d, bits in ((a, 0b11000000), (b, 0b10000000)):
            d.mkdir()
            (d / '.resume').write_bytes(resume({'name': 'M.mkv', 'piece length': MB, 'length': 2 * MB}, bytes([bits])))
        self.assertTrue(covers(a, b))
        self.assertFalse(covers(b, a))
        (b / '.resume').write_bytes(resume({'name': 'M.mkv', 'piece length': MB, 'length': 2 * MB}, bytes([0b01000000])))
        self.assertTrue(covers(a, b))
        (a / '.resume').write_bytes(resume({'name': 'M.mkv', 'piece length': MB, 'length': 2 * MB}, bytes([0b10000000])))
        self.assertFalse(covers(a, b), 'each has a piece the other lacks')

    def test_which_place_holds_a_path(self):
        p = self.places(external_volume=str(self.ssd))
        self.assertEqual(p.place_of(self.ssd / EXTERNAL_DIR / IH)['id'], 'external')
        self.assertEqual(p.place_of(self.mac / IH)['id'], 'mac')
        self.connected.clear()
        self.assertIsNone(self.places(external_volume=str(self.ssd)).place_of(self.ssd / EXTERNAL_DIR / IH))


class Volumes(Base):
    def test_the_drives_that_can_be_chosen(self):
        (self.volumes / 'Macintosh HD').symlink_to('/')
        (self.volumes / 'Leftover').mkdir()           # an empty folder macOS left behind: not a mount
        locked = self.volumes / 'Recovery'
        locked.mkdir()
        os.chmod(locked, 0o500)
        self.connected |= {str(locked), str(self.volumes / 'Macintosh HD')}
        try:
            names = [v['name'] for v in self.places().volumes()]
        finally:
            os.chmod(locked, 0o700)
        self.assertEqual(names, ['Extreme SSD'])
        v = self.places().volumes()[0]
        self.assertEqual(v['path'], str(self.ssd))
        self.assertGreater(v['free'], 0)


class Configure(Base):
    def saved(self):
        return json.loads(self.file.read_text())

    def test_a_budget_is_applied_and_saved_with_the_other_settings(self):
        self.file.write_text(json.dumps({'dashboard_lan': True}))
        config = {'cache_dir': str(self.mac), 'cache_budget_gb': 15}
        p = Places(config, self.file, mounted=lambda _: False, volumes_root=self.volumes)
        ok, msg = p.configure({'place': 'mac', 'budget_gb': 25})
        self.assertTrue(ok, msg)
        self.assertEqual(config['cache_budget_gb'], 25)
        self.assertEqual(p.mac()['budget'], 25 * GB)
        self.assertEqual(self.saved(), {'dashboard_lan': True, 'cache_budget_gb': 25})

    def test_the_drives_budget(self):
        p = self.places(external_volume=str(self.ssd))
        ok, _ = p.configure({'place': 'external', 'budget_gb': 120})
        self.assertTrue(ok)
        self.assertEqual(p.get('external')['budget'], 120 * GB)
        self.assertEqual(self.saved()['external_budget_gb'], 120)
        ok, _ = self.places().configure({'place': 'external', 'budget_gb': 120})
        self.assertFalse(ok, 'no drive chosen')

    def test_what_is_refused(self):
        p = self.places()
        for change in ({'place': 'mac', 'budget_gb': True}, {'place': 'mac', 'budget_gb': '20'}, {'place': 'mac', 'budget_gb': 0},
                       {'place': 'mac', 'budget_gb': -5}, {'place': 'mac', 'budget_gb': 10 ** 7}, {'place': 'nas', 'budget_gb': 5},
                       {'downloads_to': 'external', 'volume': '/etc'}, {'downloads_to': 'everywhere'}, {}, None):
            ok, _ = p.configure(change)
            self.assertFalse(ok, change)
        self.assertFalse(self.file.exists(), 'nothing was saved')
        self.assertEqual(p.mac()['budget'], 15 * GB)

    def test_choosing_a_drive_and_back(self):
        p = self.places()
        kicked = []
        p.kick = lambda: kicked.append(1)
        ok, msg = p.configure({'downloads_to': 'external', 'volume': str(self.ssd)})
        self.assertTrue(ok, msg)
        self.assertEqual(kicked, [1], 'the drive just chosen is looked at at once')
        p.refresh()
        self.assertEqual(p.destination()['id'], 'external')
        self.assertEqual(self.saved(), {'external_volume': str(self.ssd), 'downloads_to': 'external'})
        ok, _ = p.configure({'downloads_to': 'mac'})
        self.assertTrue(ok)
        self.assertEqual(p.destination()['id'], 'mac')
        self.assertEqual(p.get('external')['name'], 'Extreme SSD', 'what is on the drive stays in use')
        self.connected.clear()
        ok, _ = p.configure({'downloads_to': 'external', 'volume': str(self.ssd)})
        self.assertTrue(ok, 'the chosen drive, while it is away')

    def test_a_settings_file_that_cant_be_read_is_left_alone(self):
        self.file.write_text('{"dashboard_lan": tru')
        ok, msg = self.places().configure({'place': 'mac', 'budget_gb': 25})
        self.assertFalse(ok)
        self.assertIn('torrentd.json', msg)
        self.assertEqual(self.file.read_text(), '{"dashboard_lan": tru')


class Files(Base):
    INFO = {'name': 'Show S01', 'piece length': 4 * MB,
            'files': [{'length': 64 * MB, 'path': ['Show.S01E01.mkv']}, {'length': 1, 'path': ['.pad', '1'], 'attr': 'p'},
                      {'length': 8 * MB, 'path': ['Extras', 'Show.S01E02.mkv']}]}

    def test_the_path_of_each_file(self):
        d = self.mac / IH
        d.mkdir()
        (d / '.resume').write_bytes(resume(self.INFO))
        self.assertEqual(file_paths(d), [d / 'Show S01' / 'Show.S01E01.mkv', None, d / 'Show S01' / 'Extras' / 'Show.S01E02.mkv'])
        (d / '.resume').write_bytes(resume({'name': 'Movie.mkv', 'piece length': 4 * MB, 'length': 8 * MB}))
        self.assertEqual(file_paths(d), [d / 'Movie.mkv'])
        (d / '.resume').write_bytes(resume({'name': '..', 'piece length': 4 * MB, 'length': 8 * MB}))
        self.assertEqual(file_paths(d), [None], 'nothing outside the folder')
        (d / '.resume').unlink()
        self.assertEqual(file_paths(d), [])

    def test_the_space_a_torrent_takes_once_a_file_is_complete(self):
        d = self.mac / IH
        (d / 'Show S01').mkdir(parents=True)
        (d / '.resume').write_bytes(resume(self.INFO))
        # E01: its first MB and its last byte were written (the TV reads the end of a file first), so on disk 64 MB
        # long. (APFS fills a gap of up to 16 MB itself; this one it leaves empty.)
        with open(d / 'Show S01' / 'Show.S01E01.mkv', 'wb') as f:
            f.write(b'x' * MB)
            f.seek(64 * MB - 1)
            f.write(b'x')
        small = footprint(d, {2: 8 * MB}, sparse=True)
        self.assertLess(small, 3 * MB + 8 * MB, 'APFS: only what was written, plus E02 in full')
        self.assertGreaterEqual(small, 1 * MB + 8 * MB)
        full = footprint(d, {2: 8 * MB}, sparse=False)
        self.assertGreaterEqual(full, 72 * MB, 'exFAT: E01 takes its whole length once written at its end')
        self.assertLess(full, 73 * MB)
        self.assertGreaterEqual(footprint(d, {0: 64 * MB}, sparse=True), 64 * MB)
        self.assertLess(footprint(d, {0: 64 * MB}, sparse=True), 65 * MB, 'E01 counted once, at its full length')


class Move(Base):
    def test_a_folder_moves_with_everything_in_it(self):
        src = self.mac / IH
        (src / 'Show S01').mkdir(parents=True)
        (src / '.resume').write_bytes(b'resume')
        (src / '.last-used').write_text('1000')
        (src / 'Show S01' / 'E01.mkv').write_bytes(os.urandom(3 * MB))
        want = (src / 'Show S01' / 'E01.mkv').read_bytes()
        dest = self.ssd / EXTERNAL_DIR
        ok, msg = move_folder(src, dest)
        self.assertTrue(ok, msg)
        self.assertFalse(src.exists())
        self.assertEqual((dest / IH / 'Show S01' / 'E01.mkv').read_bytes(), want)
        self.assertEqual((dest / IH / '.resume').read_bytes(), b'resume')
        self.assertEqual((dest / IH / '.last-used').read_text(), '1000')
        self.assertEqual(sorted(p.name for p in dest.iterdir()), [IH], 'nothing left over')

    def test_an_older_copy_there_is_replaced(self):
        src, dest = self.mac / IH, self.ssd / EXTERNAL_DIR
        src.mkdir()
        (src / 'new.mkv').write_bytes(b'new')
        (dest / IH).mkdir(parents=True)
        (dest / IH / 'old.mkv').write_bytes(b'old')
        self.assertTrue(move_folder(src, dest)[0])
        self.assertEqual(sorted(p.name for p in (dest / IH).iterdir()), ['new.mkv'])

    def test_a_move_stops_when_asked_and_keeps_the_original(self):
        src = self.mac / IH
        src.mkdir()
        (src / 'E01.mkv').write_bytes(os.urandom(3 * MB))
        asked = []
        ok, msg = move_folder(src, self.ssd / EXTERNAL_DIR, stop=lambda: asked.append(1) or len(asked) > 1, chunk=MB)
        self.assertFalse(ok)
        self.assertEqual(msg, 'Stopped')
        self.assertEqual((src / 'E01.mkv').stat().st_size, 3 * MB)
        self.assertEqual(list((self.ssd / EXTERNAL_DIR).iterdir()), [])

    def test_only_regular_files_are_copied(self):
        src = self.mac / IH
        src.mkdir()
        (src / 'E01.mkv').write_bytes(b'x')
        (src / 'link').symlink_to(self.file)
        self.assertTrue(move_folder(src, self.ssd / EXTERNAL_DIR)[0])
        self.assertEqual(sorted(p.name for p in (self.ssd / EXTERNAL_DIR / IH).iterdir()), ['E01.mkv'])

    def test_a_failed_copy_leaves_the_torrent_where_it_was(self):
        src = self.mac / IH
        src.mkdir()
        (src / 'E01.mkv').write_bytes(b'x' * MB)
        dest = self.ssd / EXTERNAL_DIR
        dest.mkdir()
        os.chmod(dest, 0o500)
        try:
            ok, msg = move_folder(src, dest)
        finally:
            os.chmod(dest, 0o700)
        self.assertFalse(ok)
        self.assertTrue(msg)
        self.assertEqual((src / 'E01.mkv').read_bytes(), b'x' * MB)
        self.assertEqual(list(dest.iterdir()), [])


class Allocate(Base):
    def test_a_file_is_written_out_to_its_full_length(self):
        f = self.root / 'E01.mkv'
        f.write_bytes(b'head')
        allocate(f, 4 * MB)
        self.assertEqual(f.stat().st_size, 4 * MB)
        data = f.read_bytes()
        self.assertEqual(data[:4], b'head')
        self.assertEqual(data[4:], bytes(4 * MB - 4))

    def test_what_is_already_there_is_kept(self):
        f = self.root / 'E01.mkv'
        f.write_bytes(b'a' * (MB - 1) + b'z')
        allocate(f, MB)
        self.assertEqual(f.read_bytes(), b'a' * (MB - 1) + b'z')
        g = self.root / 'new' / 'E02.mkv'
        allocate(g, 2 * MB)
        self.assertEqual(g.read_bytes(), bytes(2 * MB), 'a file not started yet')

    def test_writing_out_stops_when_asked(self):
        f = self.root / 'E01.mkv'
        asked = []
        self.assertFalse(allocate(f, 4 * MB, stop=lambda: asked.append(1) or len(asked) > 2, step=MB))
        self.assertEqual(f.stat().st_size, 2 * MB, 'two steps of 1 MB, then stopped')
        self.assertTrue(allocate(f, 4 * MB, step=MB))
        self.assertEqual(f.stat().st_size, 4 * MB)


if __name__ == '__main__':
    unittest.main()
