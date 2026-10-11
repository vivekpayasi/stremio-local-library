"""Run with:  python3 -m unittest discover -s tests

ffprobe output below is trimmed from real probes of the releases named (info hashes are made up).
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from stream_facts import FactStore, Prober, summarize_probe  # noqa: E402

DOVI_P7 = {'side_data_type': 'DOVI configuration record', 'dv_version_major': 1, 'dv_version_minor': 0,
           'dv_profile': 7, 'dv_level': 6, 'rpu_present_flag': 1, 'el_present_flag': 1, 'bl_present_flag': 1,
           'dv_bl_signal_compatibility_id': 6}
DOVI_P8 = dict(DOVI_P7, dv_profile=8, el_present_flag=0, dv_bl_signal_compatibility_id=1)

FRAMESTOR = {   # Chernobyl S01E01 UHD BluRay REMUX: DV P7 FEL + DTS-HD MA
    'streams': [
        {'index': 0, 'codec_type': 'video', 'codec_name': 'hevc', 'profile': 'Main 10', 'width': 3840,
         'height': 2160, 'pix_fmt': 'yuv420p10le', 'color_transfer': 'smpte2084',
         'disposition': {'default': 1, 'attached_pic': 0}, 'side_data_list': [DOVI_P7]},
        {'index': 1, 'codec_type': 'audio', 'codec_name': 'dts', 'profile': 'DTS-HD MA', 'channels': 6,
         'disposition': {'default': 1}, 'tags': {'language': 'eng'}},
        {'index': 2, 'codec_type': 'subtitle', 'codec_name': 'hdmv_pgs_subtitle', 'disposition': {'default': 0},
         'tags': {'language': 'eng'}},
    ],
    'format': {'format_name': 'matroska,webm', 'duration': '3534.700000', 'size': '28232110080',
               'bit_rate': '59493330'},
}

BTM_MP4 = {     # Obsession, Ben The Men MP4: cover art first, DV P8, four E-AC3 tracks, no bit_rate
    'streams': [
        {'index': 0, 'codec_type': 'video', 'codec_name': 'mjpeg', 'width': 600, 'height': 900,
         'disposition': {'default': 0, 'attached_pic': 1}},
        {'index': 1, 'codec_type': 'video', 'codec_name': 'hevc', 'profile': 'Main 10', 'width': 3840,
         'height': 1600, 'pix_fmt': 'yuv420p10le', 'color_transfer': 'smpte2084',
         'disposition': {'default': 1, 'attached_pic': 0}, 'side_data_list': [DOVI_P8]},
    ] + [{'index': 2 + i, 'codec_type': 'audio', 'codec_name': 'eac3', 'channels': 6,
          'disposition': {'default': int(i == 0)}, 'tags': {'language': lang}}
         for i, lang in enumerate(['eng', 'fre', 'spa', 'spa'])],
    'format': {'format_name': 'mov,mp4,m4a,3gp,3g2,mj2', 'duration': '6554.0', 'size': '25071854796'},
}


class SummarizeProbe(unittest.TestCase):
    def test_dolby_vision_profile_7_and_dts_hd(self):
        f = summarize_probe(FRAMESTOR, 'Chernobyl.S01E01.2160p.DTS-HD.MA.5.1.DV.HEVC.REMUX-FraMeSToR.mkv')
        self.assertEqual(f['container'], 'mkv')
        self.assertEqual(f['bitrateMbps'], 59.5)
        self.assertEqual(f['video'], {'codec': 'hevc', 'profile': 'Main 10', 'width': 3840, 'height': 2160,
                                      'bitDepth': 10, 'transfer': 'smpte2084',
                                      'dv': {'profile': 7, 'level': 6, 'el': True, 'compat': 6}})
        self.assertEqual(f['audio'], [{'codec': 'dts', 'profile': 'DTS-HD MA', 'channels': 6, 'lang': 'eng',
                                       'default': True}])
        self.assertEqual(f['subtitles'], [{'codec': 'hdmv_pgs_subtitle', 'lang': 'eng'}])
        self.assertEqual(f['source'], 'ffprobe')

    def test_skips_cover_art_and_derives_bitrate_from_size(self):
        f = summarize_probe(BTM_MP4, 'Obsession.2026.2160p.iT.WEB-DL.DV.HDR10+[Ben The Men].mp4', mp4_subs_hidden=2)
        self.assertEqual(f['container'], 'mp4')
        self.assertEqual(f['video']['codec'], 'hevc')
        self.assertEqual(f['video']['dv']['profile'], 8)
        self.assertEqual(f['bitrateMbps'], 30.6)
        self.assertEqual([a['default'] for a in f['audio']], [True, False, False, False])
        self.assertEqual(f['mp4SubsHidden'], 2)

    def test_container_falls_back_to_the_format_name(self):
        f = summarize_probe(FRAMESTOR, 'no extension')
        self.assertEqual(f['container'], 'mkv')

    def test_sdr_video_has_no_dv(self):
        probe = json.loads(json.dumps(FRAMESTOR))
        v = probe['streams'][0]
        v.pop('side_data_list')
        v.update(color_transfer='bt709', pix_fmt='yuv420p', codec_name='h264', profile='High')
        f = summarize_probe(probe, 'x.mkv')
        self.assertIsNone(f['video']['dv'])
        self.assertEqual(f['video']['bitDepth'], 8)
        self.assertEqual(f['video']['transfer'], 'bt709')


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'stream-facts.json'

    def tearDown(self):
        self.dir.cleanup()

    def test_put_writes_every_key_and_survives_a_reload(self):
        s = FactStore(self.path)
        s.put(['a' * 40 + ':1', 'a' * 40 + ':-1'], {'container': 'mkv'})
        again = FactStore(self.path)
        self.assertEqual(again.get('a' * 40 + ':1')['container'], 'mkv')
        self.assertEqual(again.get('a' * 40 + ':-1')['container'], 'mkv')
        self.assertEqual(json.loads(self.path.read_text())['version'], 1)

    def test_a_recent_failure_is_remembered_and_expires(self):
        s = FactStore(self.path)
        s.fail('b' * 40 + ':0', 'ffprobe exited 1')
        self.assertTrue(s.failed_recently('b' * 40 + ':0', within_s=3600))
        self.assertFalse(s.failed_recently('b' * 40 + ':0', within_s=0))

    def test_malformed_failure_records_are_ignored(self):
        self.path.write_text(json.dumps({'version': 1, 'facts': {}, 'failed': {'k': 'oops', 'j': {'at': 'x'}}}))
        s = FactStore(self.path)
        self.assertFalse(s.failed_recently('k', within_s=3600))
        self.assertFalse(s.failed_recently('j', within_s=3600))

    def test_a_corrupt_file_starts_empty(self):
        self.path.write_text('{not json')
        self.assertIsNone(FactStore(self.path).get('c' * 40 + ':0'))


class ProberQueue(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = FactStore(Path(self.dir.name) / 'f.json')
        self.calls = []
        self.done = threading.Event()

        def runner(url):
            self.calls.append(url)
            self.done.set()
            return FRAMESTOR

        self.prober = Prober(self.store, base_url='http://127.0.0.1:11472', log=lambda *a: None, runner=runner)

    def tearDown(self):
        self.prober.stop()
        self.dir.cleanup()

    def test_probes_once_and_stores_the_summary(self):
        ih = 'd' * 40
        self.assertTrue(self.prober.schedule(ih, 1, 'x.mkv'))
        self.assertFalse(self.prober.schedule(ih, 1, 'x.mkv'), 'a pending probe is not queued twice')
        self.assertTrue(self.done.wait(5))
        deadline = time.time() + 5
        while self.store.get(f'{ih}:1') is None and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.store.get(f'{ih}:1')['video']['dv']['profile'], 7)
        self.assertEqual(self.calls, [f'http://127.0.0.1:11472/{ih}/1'])
        self.assertFalse(self.prober.schedule(ih, 1, 'x.mkv'), 'known files are not probed again')

    def test_extra_facts_are_read_after_the_probe(self):
        ih = 'e' * 40
        self.prober.schedule(ih, 0, 'x.mp4', extra=lambda: {'mp4SubsHidden': 3})
        self.assertTrue(self.done.wait(5))
        deadline = time.time() + 5
        while self.store.get(f'{ih}:0') is None and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.store.get(f'{ih}:0')['mp4SubsHidden'], 3)

    def test_the_worker_survives_a_store_that_cannot_save(self):
        logs = []
        store = FactStore(Path(self.dir.name) / 'no-such-dir' / 'f.json')   # every save fails
        ran = []

        def runner(url):
            ran.append(url)
            return FRAMESTOR

        p = Prober(store, base_url='http://x', log=logs.append, runner=runner)
        try:
            p.schedule('1' * 40, 0, 'a.mkv')
            p.schedule('2' * 40, 0, 'b.mkv')
            deadline = time.time() + 5
            while len(ran) < 2 and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(ran), 2, 'the second probe still ran')
            self.assertTrue(any('could not save' in str(line) for line in logs), logs)
        finally:
            p.stop()

    def test_delay_holds_the_probe_back(self):
        self.prober.schedule('f' * 40, 0, 'x.mkv', delay=0.5)
        self.assertFalse(self.done.wait(0.25))
        self.assertTrue(self.done.wait(5))


if __name__ == '__main__':
    unittest.main()
