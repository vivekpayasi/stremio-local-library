"""Run with:  python3 -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from media_text import clock, episode_key, episode_label, episode_title, release_group, size, spoken  # noqa: E402

GB = 1024 ** 3


class Names(unittest.TestCase):
    def test_episode_codes(self):
        self.assertEqual(episode_key('Chernobyl - S01E03 - Open Wide, O Earth (2160p) [Accid].mkv'), (1, 3))
        self.assertEqual(episode_key('Show 1x10.mkv'), (1, 10))
        self.assertIsNone(episode_key('Movie.2019.2160p.mkv'))
        self.assertEqual(episode_label('show.s02e07.mkv'), 'S02E07')
        self.assertEqual(episode_label('Movie.mkv'), 'Movie.mkv')

    def test_the_episode_title_in_a_file_name(self):
        self.assertEqual(episode_title('Chernobyl - S01E03 - Open Wide, O Earth (2160p DV HDR x265) [Accid].mkv'), 'Open Wide, O Earth')
        self.assertEqual(episode_title('Chernobyl - S01E01 - 1.23.45 (2160p DV HDR x265) [Accid].mkv'), '1.23.45')
        self.assertIsNone(episode_title('Show.S01E02.1080p.WEB.mkv'))

    def test_release_groups(self):
        cases = {
            'Obsession.2026.2160p.iT.WEB-DL.UNRATED.DV.HDR10+.MULTi.FRE.LAT.DDP5.1.Atmos.H265.MP4-BEN.THE.MEN': 'BEN.THE.MEN',
            'Obsession.2025.2160p.MA.WEB-DL.DDP5.1.Atmos.H.265-BYNDR': 'BYNDR',
            'Obsession.2025.MULTi.VF2.HDR.DV.2160p.WEB.H265-SUPPLY.mkv': 'SUPPLY',
            'Obsession.2025.4K.HDR.DV.2160p.WEBDL Ita Eng x265-NAHOM': 'NAHOM',
            'Chernobyl - S01 (2019 2160p x265 DV 10bit HDR Ita Ac3 Eng Eac3 Sub Ita Eng BDrip) [Accid]': 'Accid',
            'Obsession (2026) (2160p UHD BluRay x265 10bit DV HDR TrueHD Atmos 7.1 r00t)': 'r00t',
            'Movie.2019.1080p.WEB-DL': None,
            'Obsession 2025 UHD BluRay 2160p HDR10 DV HEVC TrueHD Atmos 7.1 x265-E': None,
            'Troy.2004.mkv': None,
            '': None,
        }
        for name, group in cases.items():
            self.assertEqual(release_group(name), group, name)


class Figures(unittest.TestCase):
    def test_clock_spoken_and_size(self):
        self.assertEqual(clock(1510), '25:10')
        self.assertEqual(clock(3930), '1:05:30')
        self.assertEqual([spoken(s) for s in (45, 260, 240, 3900, 3600)], ['45 s', '4 min 20 s', '4 min', '1 h 5 min', '1 h'])
        self.assertEqual(size(int(3.8 * GB)), '3.8 GB')
        self.assertEqual(size(480 * 1024 * 1024), '480 MB')


if __name__ == '__main__':
    unittest.main()
