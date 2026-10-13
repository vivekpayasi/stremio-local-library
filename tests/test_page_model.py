"""Run with:  python3 -m unittest discover -s tests

The dashboard page's model, from fixtures shaped like the real cache (the Accid Chernobyl pack, two Obsession releases)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import page_model as pm  # noqa: E402

MB = 1024 * 1024
GB = 1024 * MB
PACK = 'b02c69e9c46feb40c23c10f785e31c673291240a'
BEN = '2ce4a94acc287b04bb190c0756d667e409c9fdb4'
ROOT = '31ceb850bb34449f4e3006c7e5f8d32648b1b078'
ACCID = '(2160p DV HDR x265) [Accid].mkv'

TITLES = {
    PACK: {'imdb': 'tt7366338', 'type': 'series', 'name': 'Chernobyl', 'year': '2019', 'season': 1, 'episode': 3, 'poster': 'P', 'at': 1},
    'meta:tt7366338': {'background': 'BG', 'episodes': {'1:3': 'Open Wide, O Earth', '1:4': 'The Happiness of All Mankind',
                                                        '1:5': 'Vichnaya Pamyat'}, 'at': 1},
    BEN: {'imdb': 'tt37287335', 'type': 'movie', 'name': 'Obsession', 'year': '2026', 'poster': 'PO', 'at': 1},
    BEN + ':0': {'imdb': 'tt37287335', 'type': 'movie', 'name': 'Obsession', 'year': '2026', 'poster': 'PO', 'at': 1,
                 'label': ['4K', 'WEB', 'DV + HDR10+', 'E-AC3 Atmos', 'MP4'],
                 'verdict': {'symbol': '✓✓', 'reason': None, 'summary': ['MP4', 'E-AC3', 'DV P8']}},
    ROOT: {'imdb': 'tt37287335', 'type': 'movie', 'name': 'Obsession', 'year': '2026', 'poster': 'PO', 'at': 1,
           'verdict': {'symbol': '✗', 'reason': "TrueHD: TV can't decode", 'summary': ['MKV', 'TrueHD']}},
}


def video(idx, name, gb, done, busy=False):
    return {'idx': idx, 'name': name, 'length': int(gb * GB), 'done': done, 'busy': busy}


def rows():
    return [
        {'ih': PACK, 'name': 'Chernobyl - S01 (2019 2160p x265 DV 10bit HDR Ita Ac3 Eng Eac3 Sub Ita Eng BDrip) [Accid]',
         'bytes': int(3.8 * GB), 'last_used': 3000.0, 'in_use': False,
         'videos': [video(0, 'Chernobyl - S01E02 - Please Remain Calm ' + ACCID, 7.0, 0.0),
                    video(1, 'Chernobyl - S01E01 - 1.23.45 ' + ACCID, 6.7, 0.0),
                    video(2, 'Chernobyl - S01E03 - Open Wide, O Earth ' + ACCID, 3.8, 1.0),
                    video(3, 'Chernobyl - S01E04 - The Happiness of All Mankind ' + ACCID, 6.4, 0.0),
                    video(4, 'Chernobyl - S01E05 - Vichnaya Pamyat ' + ACCID, 6.4, 0.0)]},
        {'ih': BEN, 'name': 'Obsession.2026.2160p.iT.WEB-DL.UNRATED.DV.HDR10+.MULTi.FRE.LAT.DDP5.1.Atmos.H265.MP4-BEN.THE.MEN',
         'bytes': int(2.4 * GB), 'last_used': 2000.0, 'in_use': False,
         'videos': [video(0, 'Obsession.2026.2160p.iT.WEB-DL.DV.HDR10+.DDP5.1.Atmos.H265-BEN.THE.MEN.mp4', 21.7, 0.11)]},
        {'ih': ROOT, 'name': 'Obsession (2026) (2160p UHD BluRay x265 10bit DV HDR TrueHD Atmos 7.1 r00t)',
         'bytes': 448 * MB, 'last_used': 1000.0, 'in_use': False,
         'videos': [video(4, 'Obsession (2026) (2160p UHD BluRay x265 10bit DV HDR r00t).mkv', 25.0, 0.02)]},
    ]


def job(ih, idx, label, state='downloading', done=0.34):
    return {'ih': ih, 'idx': idx, 'label': label, 'state': state, 'done': done, 'length': int(6.4 * GB), 'message': None,
            'mbps': 21.3, 'eta_s': 1500}


class Library(unittest.TestCase):
    def test_one_card_per_title_most_recent_first(self):
        shelf, _ = pm.library(rows(), TITLES, [])
        self.assertEqual([(t['title'], t['kind'], t['summary']) for t in shelf],
                         [('Chernobyl', 'series', 'S1 · 1 episode here'), ('Obsession', 'movie', 'Movie · 2 releases · 2.8 GB')])
        self.assertEqual([t['key'] for t in shelf], ['tt7366338', 'tt37287335'])

    def test_a_series_sheet_lists_every_episode_with_its_name_and_marks_the_next(self):
        _, details = pm.library(rows(), TITLES, [])
        t = details['tt7366338']
        self.assertEqual([(e['label'], e['name'], e['done'], e['next']) for e in t['episodes']], [
            ('S01E01', '1.23.45', 0.0, False), ('S01E02', 'Please Remain Calm', 0.0, False),
            ('S01E03', 'Open Wide, O Earth', 1.0, False), ('S01E04', 'The Happiness of All Mankind', 0.0, True),
            ('S01E05', 'Vichnaya Pamyat', 0.0, False)])
        self.assertEqual((t['seasons'], t['backdrop'], t['poster']), ([1], 'BG', 'P'))
        self.assertEqual(t['meta_line'], '2019 · Accid · 3.8 GB here')
        self.assertEqual(t['releases'], [])

    def test_a_movie_sheet_lists_its_releases_with_verdicts(self):
        _, details = pm.library(rows(), TITLES, [])
        t = details['tt37287335']
        self.assertEqual([(r['group'], r['done'], r['verdict']['symbol']) for r in t['releases']],
                         [('BEN.THE.MEN', 0.11, '✓✓'), ('r00t', 0.02, '✗')])
        self.assertEqual(t['releases'][0]['chips'], ['4K', 'WEB', 'DV + HDR10+', 'E-AC3 Atmos', 'MP4'])
        self.assertEqual(t['releases'][1]['chips'], ['MKV'], 'without the advisor\'s chips: the container')
        self.assertEqual(t['meta_line'], '2026 · Movie · 2 releases · 2.8 GB here')

    def test_a_download_shows_on_its_episode_and_the_card(self):
        shelf, details = pm.library(rows(), TITLES, [job(PACK, 3, 'S01E04')])
        e4 = details['tt7366338']['episodes'][3]
        self.assertEqual((e4['job']['state'], e4['done']), ('downloading', 0.34))
        self.assertEqual(shelf[0]['sub'], 'E4 downloading 34%')
        shelf, _ = pm.library(rows(), TITLES, [job(PACK, 3, 'S01E04', 'queued', 0.0)])
        self.assertEqual(shelf[0]['sub'], 'E4 queued')

    def test_a_torrent_the_advisor_never_saw(self):
        r = dict(rows()[2], ih='c' * 40)
        shelf, details = pm.library([r], {}, [])
        self.assertEqual(shelf[0]['key'], 'ih:' + 'c' * 40)
        self.assertEqual(shelf[0]['title'], r['name'])
        self.assertEqual(shelf[0]['summary'], 'Movie · 2% here · 448 MB')

    def test_a_torrent_with_no_file_list_still_gets_a_card(self):
        r = dict(rows()[2], ih='d' * 40, videos=[])
        shelf, details = pm.library([r], {}, [])
        self.assertEqual(shelf[0]['summary'], 'Movie · nothing downloaded')
        self.assertEqual(details['ih:' + 'd' * 40]['releases'][0]['length'], 0)

    def test_several_seasons(self):
        r = rows()[0]
        r['videos'] = [video(0, 'Show.S01E10.mkv', 1, 1.0), video(1, 'Show.S02E01.mkv', 1, 0.0), video(2, 'Show.S02E02.mkv', 1, 0.0)]
        _, details = pm.library([r], TITLES, [])
        t = details['tt7366338']
        self.assertEqual(t['seasons'], [1, 2])
        self.assertEqual([e['label'] for e in t['episodes'] if e['next']], ['S02E01'])

    def test_two_copies_of_one_episode_carry_their_release(self):
        a, b = rows()[0], dict(rows()[0], ih='e' * 40, name='Chernobyl.S01.1080p.WEB-DL.DDP5.1-NTb', last_used=10.0,
                                videos=[video(0, 'Chernobyl.S01E03.1080p.WEB-DL.DDP5.1-NTb.mkv', 2.0, 1.0)])
        _, details = pm.library([a, b], dict(TITLES, **{'e' * 40: TITLES[PACK]}), [])
        tags = [(e['label'], e['tag']) for e in details['tt7366338']['episodes'] if e['label'] == 'S01E03']
        self.assertEqual(sorted(tags), [('S01E03', 'Accid'), ('S01E03', 'NTb')])
        self.assertEqual(details['tt7366338']['summary'], 'S1 · 1 episode here')
        self.assertEqual(details['tt7366338']['ihs'], [PACK, 'e' * 40])


if __name__ == '__main__':
    unittest.main()
