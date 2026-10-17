"""Run with:  python3 -m unittest discover -s tests

The dashboard page's model, from fixtures shaped like the real cache (the Accid Chernobyl pack, two Obsession releases)."""
import os
import sys
import time
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

    def test_a_year_recorded_as_a_number(self):
        titles = dict(TITLES, **{PACK: dict(TITLES[PACK], year=2019), BEN: dict(TITLES[BEN], year=2026)})
        _, details = pm.library(rows(), titles, [])
        self.assertEqual(details['tt7366338']['meta_line'], '2019 · Accid · 3.8 GB here')
        self.assertEqual(details['tt37287335']['meta_line'], '2026 · Movie · 2 releases · 2.8 GB here')

    def test_two_copies_of_one_episode_carry_their_release(self):
        a, b = rows()[0], dict(rows()[0], ih='e' * 40, name='Chernobyl.S01.1080p.WEB-DL.DDP5.1-NTb', last_used=10.0,
                                videos=[video(0, 'Chernobyl.S01E03.1080p.WEB-DL.DDP5.1-NTb.mkv', 2.0, 1.0)])
        _, details = pm.library([a, b], dict(TITLES, **{'e' * 40: TITLES[PACK]}), [])
        tags = [(e['label'], e['tag']) for e in details['tt7366338']['episodes'] if e['label'] == 'S01E03']
        self.assertEqual(sorted(tags), [('S01E03', 'Accid'), ('S01E03', 'NTb')])
        self.assertEqual(details['tt7366338']['summary'], 'S1 · 1 episode here')
        self.assertEqual(details['tt7366338']['ihs'], [PACK, 'e' * 40])


def session(idx=2, live=True, nxt=3, file='Chernobyl - S01E03 - Open Wide, O Earth ' + ACCID):
    files = {3: ('Chernobyl - S01E04 - The Happiness of All Mankind ' + ACCID, 6.4)}
    n = {'idx': nxt, 'label': 'S01E04', 'name': files[3][0], 'length': int(files[3][1] * GB), 'done': 0.0} if nxt is not None else None
    return {'ih': PACK, 'idx': idx, 'live': live, 'title': 'Chernobyl', 'poster': 'P', 'file': file, 'next': n}


class UpNext(unittest.TestCase):
    def test_after_the_episode_playing(self):
        _, details = pm.library(rows(), TITLES, [])
        u = pm.up_next([session()], details, TITLES, [])
        self.assertEqual({k: u[k] for k in ('key', 'label', 'season', 'episode', 'name', 'state', 'backdrop', 'idx')},
                         {'key': 'tt7366338', 'label': 'S01E04', 'season': 1, 'episode': 4, 'name': 'The Happiness of All Mankind',
                          'state': 'not_here', 'backdrop': 'BG', 'idx': 3})

    def test_its_download_and_when_it_is_here(self):
        jobs = [job(PACK, 3, 'S01E04')]
        _, details = pm.library(rows(), TITLES, jobs)
        u = pm.up_next([session()], details, TITLES, jobs)
        self.assertEqual((u['state'], u['done'], u['job']['mbps']), ('downloading', 0.34, 21.3))
        done = [dict(job(PACK, 3, 'S01E04', 'done', 1.0))]
        _, details = pm.library(rows(), TITLES, done)
        self.assertEqual(pm.up_next([session()], details, TITLES, done)['state'], 'ready')

    def test_without_a_session_it_follows_the_most_recent_series_on_the_mac(self):
        _, details = pm.library(rows(), TITLES, [])
        u = pm.up_next([], details, TITLES, [])
        self.assertEqual((u['label'], u['name'], u['title']), ('S01E04', 'The Happiness of All Mankind', 'Chernobyl'))

    def test_a_movie_or_the_last_episode_has_no_next(self):
        _, details = pm.library(rows()[1:], TITLES, [])
        self.assertIsNone(pm.up_next([dict(session(), ih=BEN, idx=0, next=None)], details, TITLES, []))

    def test_the_last_episode_playing_is_not_offered_from_an_older_session(self):
        _, details = pm.library(rows(), TITLES, [])
        last = session(idx=4, nxt=None, file='Chernobyl - S01E05 - Vichnaya Pamyat ' + ACCID)
        before = dict(session(idx=3, live=False, file='Chernobyl - S01E04 - The Happiness of All Mankind ' + ACCID), next={
            'idx': 4, 'label': 'S01E05', 'name': 'Chernobyl - S01E05 - Vichnaya Pamyat ' + ACCID, 'length': int(6.4 * GB), 'done': 0.3})
        self.assertIsNone(pm.up_next([last, before], details, TITLES, []), 'the end of the pack: nothing is up next')

    def test_a_movie_playing_leaves_up_next_to_the_series_watched_before(self):
        _, details = pm.library(rows(), TITLES, [])
        movie = dict(session(), ih=BEN, idx=0, next=None, file='Obsession.2026.2160p.WEB-DL.mp4')
        u = pm.up_next([movie, session(live=False)], details, TITLES, [])
        self.assertEqual(u['label'], 'S01E04')


class OnTheDrive(unittest.TestCase):
    """Torrents kept on the external drive, beside those on this Mac."""
    def drive(self, r):
        return dict(r, place='external', place_name='Extreme SSD')

    def test_the_card_and_sheet_say_where_a_movie_is(self):
        r = self.drive(dict(rows()[1], videos=[video(0, 'Obsession.2026.2160p.mp4', 21.7, 1.0)], bytes=int(21.7 * GB)))
        shelf, details = pm.library([r], TITLES, [])
        self.assertEqual(shelf[0]['summary'], 'Movie · on Extreme SSD · 21.7 GB')
        self.assertEqual(details['tt37287335']['meta_line'], '2026 · Movie · 21.7 GB on Extreme SSD')
        self.assertEqual(details['tt37287335']['releases'][0]['place_name'], 'Extreme SSD')

    def test_a_series_on_the_drive(self):
        shelf, details = pm.library([self.drive(rows()[0])], TITLES, [])
        self.assertEqual(shelf[0]['summary'], 'S1 · 1 episode on Extreme SSD')
        self.assertEqual(details['tt7366338']['meta_line'], '2019 · Accid · 3.8 GB on Extreme SSD')

    def test_an_episode_knows_whether_it_comes_in_a_pack(self):
        _, details = pm.library(rows(), TITLES, [])
        self.assertEqual({e['pack'] for e in details['tt7366338']['episodes']}, {True})
        one = dict(rows()[0], videos=[video(0, 'Show.S01E01.mkv', 1, 0.0)])
        _, details = pm.library([one], TITLES, [])
        self.assertEqual([e['pack'] for e in details['tt7366338']['episodes']], [False])

    def test_episodes_and_up_next_carry_their_torrents_place(self):
        _, details = pm.library([self.drive(rows()[0])], TITLES, [])
        self.assertEqual({e['place'] for e in details['tt7366338']['episodes']}, {'external'})
        self.assertEqual(pm.up_next([], details, TITLES, [])['place'], 'external')

    def test_releases_in_both_places(self):
        a, b = rows()[1], self.drive(rows()[2])
        _, details = pm.library([a, b], TITLES, [])
        self.assertEqual(details['tt37287335']['meta_line'], '2026 · Movie · 2 releases · 2.8 GB here and on Extreme SSD')

    def test_the_size_of_a_release_once_downloaded(self):
        # 2.4 GB on disk, of which its video takes 2.3: done, the video takes its 21.7 GB, the rest stays.
        r = dict(rows()[1], videos=[dict(video(0, 'Obsession.2026.2160p.mp4', 21.7, 0.11), alloc=int(2.3 * GB))])
        _, details = pm.library([r], TITLES, [])
        self.assertEqual(details['tt37287335']['releases'][0]['when_done'], int(2.4 * GB) - int(2.3 * GB) + int(21.7 * GB))


def live(**over):
    s = {'live': True, 'download_mbps': 31.0, 'bitrate_mbps': 15.0, 'ahead_s': 260, 'complete': False, 'waiting_s': 0,
         'waiting_mid': False, 'peers': {'connected': 38, 'sending': 12, 'seeds': 30}, 'events': [],
         'health': {'level': 'good', 'text': 'Playing smoothly, 4:20 buffered ahead'}}
    s.update(over)
    return s


SERVER = {'cpu': 3.2, 'rss_mb': 80, 'uptime_s': 58000, 'download_cap_mbps': 48, 'learning': True}
CACHE = {'used': int(4.4 * GB), 'budget': 15 * GB, 'free': 22 * GB}
LIB = {'movies': 0, 'shows': 0, 'other': 0, 'files': 0, 'advisor': {'profile': 'TCL Smart TV Pro', 'facts': 11, 'upstreams': ['Torrentio']},
       'account': {'text': 'Your Stremio account is up to date', 'ok': True, 'at': None}}
WIFI = {'band': '5GHz', 'channel': 149, 'width': '80MHz', 'rssi': -42, 'noise': -91, 'rate': 526}
TV_OK = {'state': 'ok', 'ip': '192.168.0.100', 'model': 'Smart TV Pro', 'android': '12',
         'wifi': {'rssi': -58, 'link': 433, 'band': '5GHz', 'standard': 'Wi-Fi 5'}, 'memory': {'total_mb': 1900, 'available_mb': 610},
         'stremio': {'cpu': 21.0, 'memory': '300M'},
         'player': {'name': 'ExoPlayer', 'video_decoder': 'OMX.amlogic.dolby-vision.dvhe.decoder.awesome2', 'error': None,
                    'error_at': None, 'dts_on_tv': False, 'low_memory_kills': 0}}


class Chain(unittest.TestCase):
    def test_the_swarm_step(self):
        s = pm.swarm_step(live(), [])
        self.assertEqual((s['level'], s['line'], s['reason']), ('good', '31 Mbps in, the file needs 15 · 38 peers, 12 sending', None))
        stalled = pm.swarm_step(live(health={'level': 'bad', 'text': 'Stalled, waiting for pieces (8 s) — the torrent can\'t keep up'}), [])
        self.assertEqual(stalled['level'], 'bad')
        self.assertTrue(stalled['reason'].startswith('Stalled'))
        low = pm.swarm_step(live(health={'level': 'warn', 'text': 'Buffer is low: the swarm sends 9 Mbps, the file needs 15'}), [])
        self.assertEqual((low['level'], low['reason']), ('warn', 'Buffer is low: the swarm sends 9 Mbps, the file needs 15'))
        jump = pm.swarm_step(live(health={'level': 'warn', 'text': 'Buffering after a jump (4 s)'}), [])
        self.assertEqual((jump['level'], jump['reason']), ('good', None), 'a seek is not the swarm\'s fault')
        later = pm.swarm_step(None, [job(PACK, 3, 'S01E04')])
        self.assertEqual((later['level'], later['line']), ('good', 'Downloading S01E04 for later · 21 Mbps'))
        self.assertEqual(pm.swarm_step(None, [])['level'], 'idle')

    def test_this_mac_and_its_services(self):
        m = pm.mac_step(SERVER, CACHE, LIB, 3 * GB)
        self.assertEqual(m['level'], 'good')
        self.assertEqual(m['line'], 'up 16 h 6 min · cache 4.4 GB of 15.0 GB · 22.0 GB free')
        self.assertEqual([(v['name'], v['level']) for v in m['services']],
                         [('Torrent server', 'good'), ('Library', 'good'), ('Stream Advisor', 'good'), ('Stremio account', 'good')])
        down = pm.mac_step(SERVER, CACHE, None, 3 * GB)
        self.assertEqual((down['level'], [v['level'] for v in down['services']]), ('warn', ['good', 'bad', 'bad', 'idle']))
        self.assertIn('Library', down['reason'])
        out = dict(LIB, account={'text': "Stremio desktop is signed out, so the account can't be updated", 'ok': False, 'at': None})
        signed_out = pm.mac_step(SERVER, CACHE, out, 3 * GB)
        self.assertEqual((signed_out['level'], signed_out['reason']), ('warn', "Stremio desktop is signed out, so the account can't be updated"))
        full = pm.mac_step(SERVER, dict(CACHE, free=2 * GB), LIB, 3 * GB)
        self.assertEqual((full['level'], full['reason']), ('warn', 'Only 2.0 GB free on the disk'))

    def test_this_mac_shows_the_drive(self):
        drive = {'id': 'external', 'name': 'Extreme SSD', 'budget': 50 * GB, 'available': True, 'why': None,
                 'used': 20 * GB, 'free': 65 * GB}
        mac = {'id': 'mac', 'name': 'This Mac', 'budget': 15 * GB, 'available': True, 'why': None, 'used': int(4.4 * GB), 'free': 22 * GB}
        m = pm.mac_step(SERVER, dict(CACHE, places=[mac, drive]), LIB, 3 * GB)
        self.assertIn(['Extreme SSD', '20.0 GB of 50.0 GB · 65.0 GB free'], m['details'])
        self.assertEqual(m['level'], 'good')
        away = pm.mac_step(SERVER, dict(CACHE, places=[mac, dict(drive, available=False, why='Not connected')]), LIB, 3 * GB)
        self.assertIn(['Extreme SSD', 'Not connected'], away['details'])
        self.assertEqual(away['level'], 'good', 'a drive that is away is not a problem')
        blocked = pm.mac_step(SERVER, dict(CACHE, places=[mac, dict(drive, available=False,
                                                                     why="macOS isn't letting the torrent server use it")]), LIB, 3 * GB)
        self.assertEqual(blocked['level'], 'warn')
        self.assertIn('Extreme SSD', blocked['reason'])
        self.assertIn('Privacy & Security', blocked['advice'])

    def test_before_the_first_readings_nothing_is_called_down(self):
        m = pm.mac_step({'uptime_s': 5}, CACHE, None, 3 * GB, checked=False)
        self.assertEqual((m['level'], m['reason']), ('good', None))
        self.assertEqual([(v['name'], v['level'], v['text']) for v in m['services']][:3],
                         [('Torrent server', 'good', 'Checking…'), ('Library', 'idle', 'Checking…'), ('Stream Advisor', 'idle', 'Checking…')])
        self.assertIsNone(pm.problem([m]))
        self.assertEqual(pm.mac_step(SERVER, CACHE, None, 3 * GB)['level'], 'warn', 'once read, no answer is down')
        w = pm.wifi_step(None, None, None, None, True, checked=False)
        self.assertEqual(w['line'], 'Checking this Mac\'s Wi-Fi…')

    def test_wifi_is_judged_while_something_plays(self):
        w = pm.wifi_step(WIFI, {'ms': 4.0, 'loss': 0, 'ip': '192.168.0.100'}, 4.0, TV_OK, True)
        self.assertEqual((w['level'], w['line']), ('good', 'Mac on 5GHz, −42 dBm, excellent · ping to the TV 4 ms · TV −58 dBm, good'))
        slow = pm.wifi_step(WIFI, {'ms': 120.0, 'loss': 0, 'ip': '192.168.0.100'}, 4.0, TV_OK, True)
        self.assertEqual((slow['level'], slow['reason']), ('warn', 'The TV\'s Wi-Fi is slow to answer'))
        self.assertEqual(slow['advice'], 'Ping 120 ms (usually 4) — playback may stutter. Restarting the TV usually clears it.')
        idle = pm.wifi_step(WIFI, {'ms': 120.0, 'loss': 0, 'ip': '192.168.0.100'}, 4.0, TV_OK, False)
        self.assertEqual(idle['level'], 'good', 'a slow answer while nothing plays is not worth a banner')
        silent = pm.wifi_step(None, {'ms': None, 'loss': 100, 'ip': '192.168.0.100'}, None, None, True)
        self.assertEqual((silent['level'], silent['line']), ('good', 'Mac not on Wi-Fi'), 'no answer to ping proves nothing')

    def test_the_tv(self):
        now = time.mktime(time.strptime('2026-10-13 21:00:00', '%Y-%m-%d %H:%M:%S'))
        t = pm.tv_step(TV_OK, live(), now)
        self.assertEqual((t['level'], t['line']), ('good', 'ExoPlayer · Dolby Vision decoder · 610 MB free'))
        asleep = pm.tv_step({'state': 'unreachable', 'ip': '192.168.0.100'}, None, now)
        self.assertEqual((asleep['level'], asleep['line']), ('idle', 'Asleep — not answering'))
        err = dict(TV_OK, player=dict(TV_OK['player'], error='Decoder init failed: OMX.amlogic.hevc', error_at='20:55:10'))
        self.assertEqual(pm.tv_step(err, live(), now)['level'], 'bad')
        old = dict(TV_OK, player=dict(TV_OK['player'], error='Decoder init failed', error_at='19:10:00'))
        self.assertEqual(pm.tv_step(old, live(), now)['level'], 'good', 'an error from two hours ago is history')
        mpv = dict(TV_OK, player=dict(TV_OK['player'], name='mpv'))
        self.assertEqual(pm.tv_step(mpv, live(), now)['level'], 'warn')
        self.assertEqual(pm.tv_step(mpv, None, now)['level'], 'good', 'which player was last used matters only while playing')
        self.assertEqual(pm.tv_step(None, None, now)['level'], 'idle')

    def test_the_worst_problem_makes_the_banner(self):
        steps = [pm.swarm_step(live(health={'level': 'bad', 'text': 'Stalled, waiting for pieces (8 s)'}), []),
                 pm.mac_step(SERVER, CACHE, LIB, 3 * GB),
                 pm.wifi_step(WIFI, {'ms': 120.0, 'loss': 0, 'ip': 'x'}, 4.0, TV_OK, True),
                 pm.tv_step(TV_OK, live(), time.time())]
        p = pm.problem(steps)
        self.assertEqual((p['level'], p['step']), ('bad', 'swarm'))
        steps[0] = pm.swarm_step(live(), [])
        self.assertEqual(pm.problem(steps)['step'], 'wifi')
        steps[2] = pm.wifi_step(WIFI, {'ms': 4.0, 'loss': 0, 'ip': 'x'}, 4.0, TV_OK, True)
        self.assertIsNone(pm.problem(steps))

    def test_usual_ping_is_the_median_of_the_last_hour(self):
        now = 10000.0
        history = [(now - 4000, 300.0)] + [(now - i * 5, ms) for i, ms in enumerate([4, 5, 3, 4, 90, 4])]
        self.assertEqual(pm.usual_ping(history, now), 4.0)
        self.assertIsNone(pm.usual_ping(history[:3], now), 'too few readings to say')


def summary(**over):
    s = {'ih': PACK, 'idx': 2, 'live': True, 'complete': False, 'title': 'Chernobyl', 'subtitle': 'Season 1, episode 3',
         'poster': 'P', 'file': 'Chernobyl - S01E03 - Open Wide, O Earth ' + ACCID, 'position': 0.384, 'position_s': 1510.0,
         'duration_s': 3930.0, 'ahead_bytes': 500 * MB, 'ahead_end': 0.45, 'ahead_s': 260.0, 'bitrate_mbps': 15.0,
         'download_mbps': 31.0, 'peers': {'connected': 38, 'sending': 12, 'seeds': 30}, 'strip': [1.0] * 54 + [0.0] * 66,
         'chips': ['MKV', 'HEVC', '4K', 'Dolby Vision 8', 'AC3 + 1 more'], 'verified': True, 'mp4_fix': None,
         'events': [{'kind': 'seek', 'pos': 0.38, 'at': 1, 'text': 'Jumped to 25:00', 'cause': None, 'recent': False}],
         'history': [[1.0, 250.0, 30.0, 480.0]], 'health': {'level': 'good', 'text': 'Playing smoothly, 4:20 buffered ahead'}}
    s.update(over)
    return s


class Glance(unittest.TestCase):
    def test_what_the_glance_says(self):
        g = pm.glance(summary(), TITLES)
        self.assertEqual({k: g[k] for k in ('title', 'subtitle', 'backdrop', 'level', 'word', 'ahead', 'elapsed', 'remaining', 'stats', 'seeks')},
                         {'title': 'Chernobyl', 'subtitle': 'Season 1 · Episode 3 · Open Wide, O Earth', 'backdrop': 'BG',
                          'level': 'good', 'word': 'Smooth', 'ahead': '4 min 20 s', 'elapsed': '25:10', 'remaining': '−40:20',
                          'stats': '31 Mbps · file needs 15 · 38 peers', 'seeks': [0.38]})

    def test_words_for_each_state(self):
        words = [pm.glance(summary(health=h), TITLES)['word'] for h in (
            {'level': 'bad', 'text': 'Stalled, waiting for pieces (8 s)'}, {'level': 'idle', 'text': 'Starting, getting the first pieces (4 s)'},
            {'level': 'warn', 'text': 'Buffering after a jump (4 s)'}, {'level': 'warn', 'text': 'Buffer is low'},
            {'level': 'warn', 'text': 'The TV\'s Wi-Fi is slow to answer — playback may stutter'})]
        self.assertEqual(words, ['Stalled', 'Starting', 'Buffering', 'Low buffer', 'Smooth'])

    def test_without_bitrate_or_duration_and_a_movie(self):
        g = pm.glance(summary(ahead_s=None, duration_s=None, position_s=None, bitrate_mbps=None, ih=BEN, idx=0,
                              file='Obsession.2026.2160p.WEB-DL.mp4', subtitle='2026',
                              mp4_fix='Hiding 2 subtitle tracks stored away from the video, so the player doesn\'t jump around the file'), TITLES)
        self.assertEqual((g['ahead'], g['elapsed'], g['remaining'], g['stats'], g['subtitle']),
                         ('500 MB', '38%', None, '31 Mbps · 38 peers', '2026'))
        self.assertIn('subtitles fixed', g['chips'])

    def test_recent(self):
        r = pm.recent(summary())
        self.assertEqual((r['live'], r['unit'], len(r['history']), r['events'][0]['kind']), (True, 's', 1, 'seek'))
        self.assertEqual(pm.recent(summary(ahead_s=None))['unit'], 'MB')
        self.assertIsNone(pm.recent(None))


CERT = {'not_before': 1790339571, 'not_after': 1798115570, 'stremio_at': 1791974136, 'renewal': None}   # 25 Sep – 24 Dec 2026
OCT_14, DEC_1, DEC_15, DEC_25 = 1791979200, 1796126400, 1797336000, 1798200000                       # noon, UTC


def checked(result, at, message=None):
    return dict(CERT, renewal={'at': at, 'result': result, 'message': message})


class Certificate(unittest.TestCase):
    """Dates as the Mac's clock shows them: the tests run in UTC."""
    @classmethod
    def setUpClass(cls):
        cls.tz = os.environ.get('TZ')
        os.environ['TZ'] = 'UTC'
        time.tzset()

    @classmethod
    def tearDownClass(cls):
        if cls.tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = cls.tz
        time.tzset()

    def test_months_ahead_it_says_when_renewal_checks_start(self):
        c = pm.certificate(CERT, OCT_14)
        self.assertEqual((c['level'], c['reason']), ('good', None))
        self.assertEqual(c['text'], 'HTTPS certificate issued 25 Sep, valid until 24 Dec (71 days left) · renewal checks start 24 Nov'
                                    ' · Stremio last opened on this Mac 14 Oct, 10:35')

    def test_in_the_last_month_it_says_what_the_last_check_found(self):
        c = pm.certificate(checked('same', DEC_1), DEC_1)
        self.assertEqual(c['level'], 'good')
        self.assertIn('valid until 24 Dec (23 days left) · last checked 1 Dec: Stremio hasn\'t issued a new one yet', c['text'])
        self.assertIn('valid until 24 Dec (23 days left) · checking daily for a new one', pm.certificate(CERT, DEC_1)['text'])

    def test_a_check_from_before_the_last_month_is_history(self):
        old = pm.certificate(checked('failed', 1788264000, 'Stremio\'s API: HTTP 502'), OCT_14)
        self.assertIn('renewal checks start 24 Nov', old['text'])
        self.assertNotIn('502', old['text'])

    def test_two_weeks_before_the_expiry_it_is_a_problem_with_what_to_do(self):
        c = pm.certificate(checked('signed-out', 1797325200, 'Stremio desktop is signed out'), DEC_15)
        self.assertEqual((c['level'], c['reason']), ('warn', 'The HTTPS certificate expires in 9 days'))
        self.assertEqual(c['advice'], 'Sign in to Stremio desktop on this Mac; the certificate is then renewed by itself.')
        self.assertIn('last check failed: Stremio desktop is signed out', c['text'])
        late = pm.certificate(checked('same', 1797325200), DEC_15)
        self.assertEqual((late['level'], late['advice']),
                         ('warn', 'Stremio hasn\'t issued a new one yet; it is asked again every day.'))

    def test_expired_the_tv_cant_connect(self):
        c = pm.certificate(checked('failed', DEC_25 - 3600, 'Stremio\'s API: HTTP 502'), DEC_25)
        self.assertEqual((c['level'], c['reason']), ('bad', 'The HTTPS certificate expired on 24 Dec'))
        self.assertEqual(c['advice'], 'The TV can\'t connect to this Mac until it is renewed. The last try failed '
                                      '(Stremio\'s API: HTTP 502); it is tried again every day.')
        self.assertTrue(c['text'].startswith('HTTPS certificate expired on 24 Dec · last check failed: Stremio\'s API: HTTP 502'))

    def test_another_year_is_named_and_a_missing_launch_left_out(self):
        renewed = {'not_before': 1795593600, 'not_after': 1803369600, 'stremio_at': None, 'renewal': None}
        self.assertEqual(pm.certificate(renewed, 1795694400)['text'],
                         'HTTPS certificate issued 25 Nov, valid until 23 Feb 2027 (88 days left) · renewal checks start 24 Jan 2027')
        self.assertIn('(1 day left)', pm.certificate(CERT, 1798115570 - 86400 - 60)['text'])

    def test_no_certificate(self):
        self.assertEqual(pm.certificate(None, OCT_14)['level'], 'idle')

    def test_this_mac_shows_it_and_a_problem_with_it(self):
        good = pm.mac_step(SERVER, CACHE, LIB, 3 * GB, cert=pm.certificate(CERT, OCT_14))
        self.assertEqual(good['level'], 'good')
        self.assertIn(['HTTPS certificate', 'valid until 24 Dec (71 days left)'], good['details'])
        soon = pm.certificate(checked('signed-out', 1797325200, 'Stremio desktop is signed out'), DEC_15)
        warn = pm.mac_step(SERVER, CACHE, LIB, 3 * GB, cert=soon)
        self.assertEqual((warn['level'], warn['reason'], warn['advice']), ('warn', soon['reason'], soon['advice']))
        self.assertEqual(pm.problem([warn])['title'], 'The HTTPS certificate expires in 9 days')
        self.assertIn('Library', pm.mac_step(SERVER, CACHE, None, 3 * GB, cert=soon)['reason'], 'a service down comes first')
        gone = pm.mac_step(SERVER, CACHE, None, 3 * GB, cert=pm.certificate(CERT, DEC_25))
        self.assertEqual((gone['level'], gone['reason']), ('bad', 'The HTTPS certificate expired on 24 Dec'))


if __name__ == '__main__':
    unittest.main()
