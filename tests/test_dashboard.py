"""Run with:  python3 -m unittest discover -s tests

Command output below follows the real formats (macOS ping and system_profiler, Android dumpsys/top/logcat
from the TCL TV), with names and addresses replaced.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from dashboard import (Dashboard, account_status, bdecode, bencode, cache_entries, cached_progress,  # noqa: E402
                       classify_requests, clear_cache, delete_file_cached,
                       delete_cached, resume_progress, filter_log, health, last_tv_ip, next_episode, parse_mac_wifi, parse_ping, parse_tv_snapshot,
                       piece_strip, summarize_session, title_for, ui_allowed, ui_write_allowed)

MB = 1024 * 1024


class Ping(unittest.TestCase):
    def test_average_and_loss(self):
        out = ('--- 192.168.0.100 ping statistics ---\n3 packets transmitted, 3 packets received, 0.0% packet loss\n'
               'round-trip min/avg/max/stddev = 3.104/4.250/5.320/0.900 ms\n')
        self.assertEqual(parse_ping(out), {'ms': 4.2, 'loss': 0})

    def test_unreachable(self):
        out = '--- 192.168.0.100 ping statistics ---\n3 packets transmitted, 0 packets received, 100.0% packet loss\n'
        self.assertEqual(parse_ping(out), {'ms': None, 'loss': 100})


class MacWifi(unittest.TestCase):
    OUT = """Wi-Fi:

      Software Versions:
          CoreWLAN: 16.0 (1657)
      Interfaces:
        en0:
          Card Type: Wi-Fi  (0x14E4, 0x4387)
          Status: Connected
          Current Network Information:
            Home Network 5G:
              PHY Mode: 802.11ac
              Channel: 149 (5GHz, 80MHz)
              Country Code: IN
              Network Type: Infrastructure
              Security: WPA2 Personal
              Signal / Noise: -41 dBm / -89 dBm
              Transmit Rate: 650
              MCS Index: 7
          Other Local Wi-Fi Networks:
            Neighbour:
              PHY Mode: 802.11n
              Channel: 6 (2GHz, 20MHz)
              Signal / Noise: -80 dBm / -90 dBm
"""

    def test_reads_only_the_current_network(self):
        self.assertEqual(parse_mac_wifi(self.OUT), {
            'phy': '802.11ac', 'channel': 149, 'band': '5GHz', 'width': '80MHz', 'rssi': -41, 'noise': -89, 'rate': 650,
        })

    def test_not_connected(self):
        self.assertIsNone(parse_mac_wifi('Wi-Fi:\n  Interfaces:\n    en0:\n      Status: Off\n'))


TV_SNAPSHOT = """Smart TV Pro
12
===
mWifiInfo SSID: "Home Network 5G", BSSID: 00:11:22:33:44:55, MAC: 66:77:88:99:aa:bb, Security type: 2, Supplicant state: COMPLETED, Wi-Fi standard: 5, RSSI: -62, Link speed: 702Mbps, Tx Link speed: 702Mbps, Max Supported Tx Link speed: 866Mbps, Rx Link speed: -1Mbps, Max Supported Rx Link speed: 866Mbps, Frequency: 5745MHz, Net ID: 3
===
MemTotal:        1957140 kB
MemAvailable:     541200 kB
===
 3868 12.5 487M com.stremio.one
 4120  0.0  47M com.stremio.one:stremio_server_process
===
10-12 00:16:58.100 I/ExoPlayerImpl( 3868): Init 1e16566 [AndroidXMedia3/1.10.1-stremio01] [G09, Smart TV Pro, TCL, 31]
10-12 00:16:58.900 D/MediaCodec( 3868): create +OMX.amlogic.avc.decoder.awesome2
10-12 00:17:01.010 I/aml_audio_dts_dec(  405): _dts_pcm_output, mute duration = 110 ms
"""

TV_ERROR_LOG = """10-11 23:56:54.928 I/ExoPlayerImpl( 3868): Init 1e16566 [AndroidXMedia3/1.10.1-stremio01] [G09, Smart TV Pro, TCL, 31]
10-11 23:56:56.017 D/MediaCodec( 3868): create +OMX.amlogic.dolby-vision.dvhe.decoder.awesome2
10-11 23:56:56.254 E/ExoPlayerImplInternal( 3868): Playback error
10-11 23:56:56.254 E/ExoPlayerImplInternal( 3868):   androidx.media3.exoplayer.ExoPlaybackException: MediaCodecVideoRenderer error, index=0, format=Format(1, null, video/x-matroska, video/dolby-vision, dvhe.07.06, -1, und, [3840, 2160, -1.0, null], [-1, -1]), format_supported=NO_EXCEEDS_CAPABILITIES
10-11 23:56:57.100 I/ExoPlayerImpl( 3868): Release 1e16566 [AndroidXMedia3/1.10.1-stremio01]
10-11 23:58:02.000 V/mpv     ( 3868): [lavf] Found Dolby Vision config record: profile 8 level 6
10-11 23:58:03.000 I/lowmemorykiller(  301): Kill 'com.netflix.ninja' (2210), uid 10071, oom_score_adj 900
"""


class TvSnapshot(unittest.TestCase):
    def test_device_wifi_memory_and_stremio(self):
        tv = parse_tv_snapshot(TV_SNAPSHOT)
        self.assertEqual(tv['model'], 'Smart TV Pro')
        self.assertEqual(tv['android'], '12')
        self.assertEqual(tv['wifi'], {'rssi': -62, 'link': 702, 'band': '5GHz', 'standard': 'Wi-Fi 5'})
        self.assertEqual(tv['memory'], {'total_mb': 1911, 'available_mb': 529})
        self.assertEqual(tv['stremio'], {'cpu': 12.5, 'memory': '487M'})
        self.assertNotIn('Home Network', repr(tv), 'network names and addresses stay off the page')
        self.assertNotIn('00:11:22', repr(tv))

    def test_player_and_decoders_from_logcat(self):
        p = parse_tv_snapshot(TV_SNAPSHOT)['player']
        self.assertEqual(p['name'], 'ExoPlayer')
        self.assertEqual(p['video_decoder'], 'OMX.amlogic.avc.decoder.awesome2')
        self.assertTrue(p['dts_on_tv'])
        self.assertIsNone(p['error'])

    def test_the_last_error_and_a_switch_to_mpv(self):
        p = parse_tv_snapshot('Smart TV Pro\n12\n===\n===\n===\n===\n' + TV_ERROR_LOG)['player']
        self.assertEqual(p['name'], 'mpv')
        self.assertIn('NO_EXCEEDS_CAPABILITIES', p['error'])
        self.assertIn('dvhe.07.06', p['error'])
        self.assertEqual(p['error_at'], '23:56:56')
        self.assertEqual(p['low_memory_kills'], 1)


class Strip(unittest.TestCase):
    def test_buckets_hold_the_fraction_downloaded(self):
        have = [True] * 10 + [False] * 10
        self.assertEqual(piece_strip(have, buckets=4), [1.0, 1.0, 0.0, 0.0])
        self.assertEqual(piece_strip([True, False, True, False], buckets=2), [0.5, 0.5])

    def test_fewer_pieces_than_buckets(self):
        self.assertEqual(piece_strip([True, False], buckets=4), [1.0, 1.0, 0.0, 0.0])
        self.assertEqual(piece_strip([], buckets=3), [])


def req(at, start, sent=8 * MB, took=1.0, wait=0.0, write=0.2, ok=False):
    return {'at': at, 'start': start, 'sent': sent, 'took': took, 'wait': wait, 'write': write, 'ok': ok}


class Requests(unittest.TestCase):
    SIZE, DURATION = 7_220_830_837, 5085.0   # Chernobyl S01E01 (Accid)

    def test_a_jump_is_a_seek(self):
        events = classify_requests([req(100, 0), req(110, 3_000_000_000)], self.SIZE, self.DURATION, now=115)
        self.assertEqual([e['kind'] for e in events], ['seek'])
        self.assertIn('35:13', events[0]['text'])
        self.assertTrue(events[0]['recent'])

    def test_stalls_come_from_the_moment_a_piece_was_waited_for(self):
        # A long request's total wait says nothing about when: stalls are recorded as they happen.
        stalls = [{'at': 300, 'pos': 3_000_000_000, 'wait': 6.5, 'mid': True}]
        events = classify_requests([req(100, 0, sent=2_000 * MB, took=900, wait=7.2)], self.SIZE, self.DURATION,
                                   now=310, stalls=stalls)
        self.assertEqual([e['kind'] for e in events], ['stall'])
        self.assertIn('6.5 s', events[0]['text'])
        self.assertIn('35:13', events[0]['text'])
        self.assertEqual(events[0]['cause'], 'swarm')
        self.assertTrue(events[0]['recent'])

    def test_waiting_for_the_first_pieces_is_start_up_not_a_stall(self):
        events = classify_requests([req(100, 0)], self.SIZE, self.DURATION, now=110,
                                   stalls=[{'at': 108, 'pos': 0, 'wait': 7.8, 'mid': False}])
        self.assertEqual([e['kind'] for e in events], ['startup'])
        self.assertIsNone(events[0]['cause'])

    def test_waiting_after_a_jump_is_buffering(self):
        events = classify_requests([req(100, 0), req(200, 3_000_000_000)], self.SIZE, self.DURATION, now=210,
                                   stalls=[{'at': 205, 'pos': 3_000_000_000, 'wait': 4.0, 'mid': False}])
        self.assertEqual([e['kind'] for e in events], ['buffering', 'seek'])
        self.assertIn('35:13', events[0]['text'])

    def test_small_files_have_no_index_reads(self):
        small = 50 * MB
        events = classify_requests([req(100, 0, sent=MB), req(101, 45 * MB, sent=MB)], small, None)
        self.assertEqual([e['kind'] for e in events], ['seek'])

    def test_index_reads_near_the_end_are_not_seeks(self):
        events = classify_requests([req(100, 0), req(100.1, self.SIZE - 400_000, sent=400_000)], self.SIZE, self.DURATION)
        self.assertEqual(events, [])

    def test_reopening_the_same_spot_points_at_the_player(self):
        reqs = [req(100 + i * 5, 3_518_423_928 - i * 3840) for i in range(4)]
        events = classify_requests(reqs, self.SIZE, self.DURATION)
        self.assertEqual(events[0]['kind'], 'reread')
        self.assertEqual(events[0]['cause'], 'player')

    def test_a_player_reading_at_its_own_pace_is_not_a_problem(self):
        # Steady playback: the TV reads only as fast as it plays, so most of a long request is spent sending.
        events = classify_requests([req(100, 0, sent=900 * MB, took=200.0, wait=0.4, write=150.0)], self.SIZE, self.DURATION)
        self.assertEqual(events, [])


SNAP = {
    'ih': 'b02c69e9c46feb40c23c10f785e31c673291240a', 'idx': 1, 'torrent': 'Chernobyl - S01 [Accid]',
    'file': 'Chernobyl - S01E01 - 1.23.45 (2160p DV HDR x265) [Accid].mkv', 'size': 100 * MB,
    'piece_len': 4 * MB, 'file_offset': 2 * MB, 'first_piece': 0, 'pos_byte': 10 * MB, 'pos_at': 1000.0,
    'have': [True] * 7 + [False] + [True] * 18, 'download_bps': 6_000_000, 'peers': {'connected': 40, 'sending': 12, 'seeds': 30},
    'mp4_hidden': None, 'live': True, 'requests': [],
}
FACTS = {'container': 'mkv', 'bitrateMbps': 16.0, 'durationS': 5085.0,
         'video': {'codec': 'hevc', 'width': 3840, 'height': 2160, 'transfer': 'smpte2084', 'dv': {'profile': 8}},
         'audio': [{'codec': 'ac3', 'lang': 'ita', 'default': True}, {'codec': 'eac3', 'lang': 'eng', 'default': False}],
         'mp4SubsHidden': 0}
TITLE = {'name': 'Chernobyl', 'type': 'series', 'season': 1, 'episode': 1, 'poster': 'https://images.metahub.space/poster/small/tt7366338/img'}


class Session(unittest.TestCase):
    def test_buffer_ahead_counts_the_contiguous_run_from_the_reader(self):
        s = summarize_session(SNAP, FACTS, TITLE, now=1001.0)
        # Reader at file byte 10 MB = torrent byte 12 MB = piece 3; pieces 3..6 are in, 7 is missing: up to 28 MB.
        self.assertEqual(s['ahead_bytes'], 16 * MB)
        self.assertAlmostEqual(s['ahead_s'], 16 * MB * 8 / 16e6, places=1)
        self.assertAlmostEqual(s['position'], 0.1)
        self.assertEqual(s['title'], 'Chernobyl')
        self.assertEqual(s['subtitle'], 'Season 1, episode 1')
        self.assertTrue(s['verified'])
        self.assertEqual(s['bitrate_mbps'], 16.0)
        self.assertEqual(s['download_mbps'], 48.0)

    def test_chips_describe_the_file(self):
        chips = summarize_session(SNAP, FACTS, TITLE, now=1001.0)['chips']
        self.assertEqual(chips, ['MKV', 'HEVC', '4K', 'Dolby Vision 8', 'AC3 + 1 more'])

    def test_without_facts_or_a_title_it_falls_back_to_the_torrent(self):
        s = summarize_session(SNAP, None, None, now=1001.0)
        self.assertEqual(s['title'], 'Chernobyl - S01 [Accid]')
        self.assertIsNone(s['ahead_s'])
        self.assertEqual(s['ahead_bytes'], 16 * MB)
        self.assertFalse(s['verified'])

    def test_reaching_the_end_of_the_file_is_fully_buffered_not_low(self):
        end = dict(SNAP, pos_byte=SNAP['size'] - MB, have=[True] * 26, download_bps=0)
        s = summarize_session(end, FACTS, TITLE, now=1001.0)
        self.assertTrue(s['complete'])
        h = health(s)
        self.assertEqual(h['level'], 'good')
        self.assertIn('rest of the file', h['text'])

    def test_a_finished_read_at_the_end_stays_live_while_the_tv_plays_its_buffer(self):
        end = dict(SNAP, pos_byte=SNAP['size'], have=[True] * 26, live=False, pos_at=1000.0)
        self.assertTrue(summarize_session(end, FACTS, TITLE, now=1060.0)['live'])
        self.assertFalse(summarize_session(end, FACTS, TITLE, now=1200.0)['live'])

    def test_a_stall_happening_now_is_reported_now(self):
        s = summarize_session(dict(SNAP, waiting_s=7.5, waiting_mid=True), FACTS, TITLE, now=1001.0)
        h = health(s)
        self.assertEqual(h['level'], 'bad')
        self.assertIn('8 s', h['text'])

    def test_waiting_at_the_start_or_after_a_jump_is_not_a_stall(self):
        starting = summarize_session(dict(SNAP, waiting_s=5.0, waiting_mid=False, requests=[req(995, 0)]), FACTS, TITLE, now=1001.0)
        self.assertEqual(health(starting)['level'], 'idle')
        self.assertIn('Starting', health(starting)['text'])
        jumped = summarize_session(dict(SNAP, waiting_s=5.0, waiting_mid=False, requests=[req(900, 0), req(998, 40 * MB)]),
                                   FACTS, TITLE, now=1001.0)
        self.assertEqual(health(jumped)['level'], 'warn')
        self.assertIn('Buffering', health(jumped)['text'])

    def test_mp4_fix_status(self):
        self.assertIsNone(summarize_session(SNAP, FACTS, None, now=1001.0)['mp4_fix'])
        hidden = summarize_session({**SNAP, 'mp4_hidden': 5}, FACTS, None, now=1001.0)['mp4_fix']
        self.assertIn('5 subtitle tracks', hidden)
        clean = summarize_session({**SNAP, 'mp4_hidden': 0}, FACTS, None, now=1001.0)['mp4_fix']
        self.assertIn('no fix needed', clean)


class Health(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(health(None)['level'], 'idle')
        self.assertEqual(health({'live': True, 'ahead_s': 120, 'events': [], 'download_mbps': 40, 'bitrate_mbps': 16})['level'], 'good')
        low = health({'live': True, 'ahead_s': 8, 'events': [], 'download_mbps': 10, 'bitrate_mbps': 16})
        self.assertEqual(low['level'], 'warn')
        self.assertIn('10 Mbps', low['text'])
        stalled = health({'live': True, 'ahead_s': 30, 'download_mbps': 40, 'bitrate_mbps': 16,
                          'events': [{'kind': 'stall', 'recent': True, 'cause': 'swarm'}]})
        self.assertEqual(stalled['level'], 'bad')

    def test_a_slow_tv_link_is_a_warning_while_playing(self):
        good = {'live': True, 'ahead_s': 120, 'events': [], 'download_mbps': 40, 'bitrate_mbps': 16}
        slow = health(good, tv_ping={'ms': 140.0, 'loss': 0})
        self.assertEqual(slow['level'], 'warn')
        self.assertIn('Wi-Fi', slow['text'])
        self.assertEqual(health(good, tv_ping={'ms': 4.0, 'loss': 0})['level'], 'good')
        self.assertEqual(health(None, tv_ping={'ms': 140.0, 'loss': 0})['level'], 'idle')
        self.assertEqual(health(good, tv_ping={'ms': None, 'loss': 100})['level'], 'good',
                         'a TV that ignores ping is not a slow one')


class State(unittest.TestCase):
    def test_a_finished_stream_stays_listed_as_last_played(self):
        import tempfile
        current = [dict(SNAP, pos_at=__import__('time').time())]
        with tempfile.TemporaryDirectory() as d:
            dash = Dashboard(sessions=lambda: current, facts=lambda k: FACTS, install_dir=d, log=lambda *a: None,
                             config={'cache_dir': d, 'cache_budget_gb': 15, 'download_limit_mbps': 48, 'learn_from_plays': True})
            first = dash.state()
            self.assertEqual(first['health']['level'], 'warn', 'live: 8 s buffered ahead')
            self.assertTrue(first['sessions'][0]['live'])
            current.clear()            # the torrent left the session
            later = dash.state()
            self.assertEqual(len(later['sessions']), 1)
            self.assertFalse(later['sessions'][0]['live'])
            self.assertEqual(later['sessions'][0]['title'], 'Chernobyl - S01 [Accid]')
            self.assertEqual(later['health']['level'], 'idle')


class Account(unittest.TestCase):
    def test_autoheal_lines_in_plain_words(self):
        ok = account_status('2026-10-11T19:03:58.051Z autoheal: account URL already correct -> https://x:12471/manifest.json')
        self.assertEqual(ok, {'text': 'Your Stremio account is up to date', 'ok': True, 'at': '2026-10-11T19:03:58.051Z'})
        self.assertTrue(account_status('2026-10-11T17:58:26.230Z autoheal: INSTALLED addon at https://x (13 -> 14 addons)')['ok'])
        signed_out = account_status('2026-10-11T17:58:26.230Z autoheal: cannot read authKey (Stremio signed out?) — will retry quietly')
        self.assertEqual(signed_out['text'], 'Stremio desktop is signed out, so the account can\'t be updated')
        self.assertFalse(signed_out['ok'])
        failed = account_status('2026-10-11T17:58:26.230Z autoheal: org.local.advisor: addonCollectionGet HTTP 502')
        self.assertFalse(failed['ok'])
        self.assertIn('HTTP 502', failed['text'])
        self.assertIsNone(account_status(None))


class Titles(unittest.TestCase):
    TITLES = {
        'aaa:3': {'name': 'Chernobyl', 'type': 'series', 'season': 1, 'episode': 3, 'poster': 'p'},
        'aaa': {'name': 'Chernobyl', 'type': 'series', 'season': 1, 'episode': 1, 'poster': 'p'},
        'bbb': {'name': 'Troy', 'type': 'movie', 'season': None, 'episode': None, 'poster': 'q', 'year': '2004'},
    }

    def test_the_exact_file_wins(self):
        self.assertEqual(title_for(self.TITLES, 'aaa', 3, 'Chernobyl.S01E03.mkv')['episode'], 3)

    def test_a_pack_found_by_hash_alone_takes_the_episode_from_the_file_name(self):
        t = title_for(self.TITLES, 'aaa', 4, 'Chernobyl.S01E04.1080p.mkv')
        self.assertEqual((t['name'], t['season'], t['episode']), ('Chernobyl', 1, 4))
        t = title_for(self.TITLES, 'aaa', 9, 'Extras - Making of.mkv')
        self.assertEqual((t['season'], t['episode']), (None, None))

    def test_movies_and_unknown_torrents(self):
        self.assertEqual(title_for(self.TITLES, 'bbb', 0, 'Troy.mkv')['name'], 'Troy')
        self.assertIsNone(title_for(self.TITLES, 'ccc', 0, 'x.mkv'))


class Access(unittest.TestCase):
    OWN = {'127.0.0.1', '192.168.0.101'}
    SUFFIX = '.519b6502d940.stremio.rocks'

    def ok(self, client, host, site=None, lan=False):
        return ui_allowed(client, host, site, lan=lan, own=self.OWN, cert_suffix=self.SUFFIX)

    def test_this_mac(self):
        self.assertTrue(self.ok('127.0.0.1', 'localhost:11472'))
        self.assertTrue(self.ok('127.0.0.1', '127.0.0.1:11472', 'same-origin'))

    def test_other_devices_only_with_lan_on(self):
        self.assertFalse(self.ok('192.168.0.57', '192.168.0.101:11472'))
        self.assertTrue(self.ok('192.168.0.57', '192.168.0.101:11472', lan=True))
        self.assertTrue(self.ok('192.168.0.57', '192-168-0-101.519b6502d940.stremio.rocks:12472', 'none', lan=True))

    def test_other_websites_cannot_read_it(self):
        self.assertFalse(self.ok('127.0.0.1', 'localhost:11472', 'cross-site'), 'a web page fetching localhost')
        self.assertFalse(self.ok('127.0.0.1', 'evil.example:11472'), 'DNS rebinding')
        self.assertFalse(self.ok('192.168.0.57', 'evil.example:12472', lan=True))


class Cache(unittest.TestCase):
    A, B, C = 'a' * 40, 'b' * 40, 'c' * 40

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for ih, name, mb, used in ((self.A, 'Chernobyl - S01 [Accid]', 3, 2000), (self.B, 'Troy.2004.mkv', 1, 1000),
                                   (self.C, 'Obsession.mkv', 2, 3000)):
            d = self.dir / ih
            (d / name).mkdir(parents=True) if not name.endswith('.mkv') else d.mkdir(parents=True)
            target = d / name / 'E01.mkv' if not name.endswith('.mkv') else d / name
            target.write_bytes(b'x' * mb * MB)
            (d / '.last-used').write_text(str(used))
            (d / '.resume').write_bytes(b'd')
        (self.dir / 'not-a-torrent').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_each_torrent_newest_first_with_its_name_size_and_title(self):
        titles = {self.A: {'name': 'Chernobyl', 'poster': 'p'}}
        rows = cache_entries(self.dir, in_session={self.C}, titles=titles)
        self.assertEqual([r['ih'] for r in rows], [self.C, self.A, self.B])
        a = rows[1]
        self.assertEqual(a['name'], 'Chernobyl - S01 [Accid]')
        self.assertEqual(a['title'], 'Chernobyl')
        self.assertGreaterEqual(a['bytes'], 3 * MB)
        self.assertEqual(a['last_used'], 2000)
        self.assertTrue(rows[0]['in_use'])
        self.assertFalse(a['in_use'])

    def test_delete_one_unless_it_is_in_use(self):
        ok, msg, freed = delete_cached(self.dir, self.C, in_session={self.C})
        self.assertFalse(ok)
        self.assertIn('in use', msg)
        self.assertTrue((self.dir / self.C).exists())
        ok, msg, freed = delete_cached(self.dir, self.A, in_session={self.C})
        self.assertTrue(ok)
        self.assertGreaterEqual(freed, 3 * MB)
        self.assertFalse((self.dir / self.A).exists())

    def test_a_torrent_used_moments_ago_counts_as_in_use(self):
        # Between a server restart and the reader reconnecting, a torrent being read is briefly not in the session.
        import time as _t
        (self.dir / self.B / '.last-used').write_text(str(_t.time() - 30))
        ok, msg, _ = delete_cached(self.dir, self.B, in_session=set())
        self.assertFalse(ok)
        self.assertIn('in use', msg)
        self.assertTrue([r for r in cache_entries(self.dir, set(), {}) if r['ih'] == self.B][0]['in_use'])
        deleted, _ = clear_cache(self.dir, in_session=set())
        self.assertNotIn(self.B, deleted)
        self.assertTrue((self.dir / self.B).exists())

    def test_only_cache_folders_can_be_deleted(self):
        for bad in ('../x', 'not-a-torrent', self.A[:39], '/etc'):
            ok, _, _ = delete_cached(self.dir, bad, in_session=set())
            self.assertFalse(ok, bad)
        self.assertTrue((self.dir / 'not-a-torrent').exists())

    def test_clear_everything_not_in_use(self):
        deleted, freed = clear_cache(self.dir, in_session={self.C})
        self.assertEqual(sorted(deleted), sorted([self.A, self.B]))
        self.assertGreaterEqual(freed, 4 * MB)
        self.assertTrue((self.dir / self.C).exists())

    def test_changes_need_the_dashboard_itself(self):
        args = dict(lan=True, own={'127.0.0.1', '192.168.0.101'}, cert_suffix='.x.stremio.rocks')
        self.assertTrue(ui_write_allowed('127.0.0.1', 'localhost:11472', 'same-origin', '1', **args))
        self.assertTrue(ui_write_allowed('192.168.0.57', '192.168.0.101:11472', 'same-origin', '1', **args))
        self.assertFalse(ui_write_allowed('127.0.0.1', 'localhost:11472', 'same-origin', None, **args), 'a plain form post')
        self.assertFalse(ui_write_allowed('127.0.0.1', 'localhost:11472', 'same-site', '1', **args))
        self.assertFalse(ui_write_allowed('192.168.0.57', '192.168.0.101:11472', 'same-origin', '1',
                                          **dict(args, lan=False)))


def benc(v):
    if isinstance(v, int):
        return b'i%de' % v
    if isinstance(v, bytes):
        return b'%d:%s' % (len(v), v)
    if isinstance(v, str):
        return benc(v.encode())
    if isinstance(v, list):
        return b'l' + b''.join(benc(x) for x in v) + b'e'
    return b'd' + b''.join(benc(k) + benc(v[k]) for k in sorted(v)) + b'e'


class Resume(unittest.TestCase):
    def test_bdecode(self):
        self.assertEqual(bdecode(benc({'a': [1, b'xy'], 'b': {'c': 'd'}})), {b'a': [1, b'xy'], b'b': {b'c': b'd'}})

    def test_progress_per_file_from_the_piece_bitmap(self):
        # A two-episode pack, 4 pieces of 4 MB: E01 is pieces 0-1 (8 MB), E02 is pieces 2-3 (6 MB, the last piece short).
        # Pieces 0, 1 and 2 are in: E01 complete, E02 at 4 of 6 MB.
        info = {'name': 'Show S01', 'piece length': 4 * MB,
                'files': [{'length': 8 * MB, 'path': ['Show.S01E01.mkv']}, {'length': 6 * MB, 'path': ['Show.S01E02.mkv']}]}
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '.resume'
            p.write_bytes(benc({'info': info, 'pieces': bytes([0b11100000])}))
            files = resume_progress(p)
        self.assertEqual([(f['name'], f['length']) for f in files], [('Show.S01E01.mkv', 8 * MB), ('Show.S01E02.mkv', 6 * MB)])
        self.assertEqual([round(f['done'], 3) for f in files], [1.0, 0.667])

    def test_a_single_file_torrent_and_a_missing_resume(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '.resume'
            p.write_bytes(benc({'info': {'name': 'Movie.mkv', 'piece length': MB, 'length': 4 * MB}, 'pieces': bytes([0b10000000])}))
            self.assertEqual(resume_progress(p), [{'idx': 0, 'name': 'Movie.mkv', 'length': 4 * MB, 'done': 0.25}])
            self.assertEqual(resume_progress(Path(d) / 'nope'), [])

    def test_cache_rows_carry_the_files_with_data(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / ('a' * 40)
            (t / 'Show S01').mkdir(parents=True)
            (t / '.last-used').write_text('1000')
            info = {'name': 'Show S01', 'piece length': 4 * MB,
                    'files': [{'length': 8 * MB, 'path': ['Show.S01E01.mkv']}, {'length': 6 * MB, 'path': ['Show.S01E02.mkv']}]}
            (t / '.resume').write_bytes(benc({'info': info, 'pieces': bytes([0b00100000])}))
            row = cache_entries(d, set(), {})[0]
            self.assertEqual([(f['name'], round(f['done'], 2)) for f in row['files']], [('Show.S01E02.mkv', 0.67)])
            self.assertEqual([(f['name'], round(f['done'], 2)) for f in row['videos']],
                             [('Show.S01E01.mkv', 0.0), ('Show.S01E02.mkv', 0.67)], 'every video, for the title sheets')
            live = cache_entries(d, {'a' * 40}, {}, live_progress=lambda ih: [{'name': 'Show.S01E02.mkv', 'length': 6 * MB, 'done': 0.9}])
            self.assertEqual(live[0]['files'][0]['done'], 0.9, 'a torrent in the session reports live progress')

    def test_only_videos_that_are_really_started_are_listed_most_complete_first(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / ('a' * 40)
            (t / 'Show S01').mkdir(parents=True)
            progress = [{'name': 'Show.S01E01.mkv', 'length': 900 * MB, 'done': 0.003},   # a shared boundary piece
                        {'name': 'Show.S01E02.mkv', 'length': 900 * MB, 'done': 0.62},
                        {'name': 'Show.S01E03.mkv', 'length': 900 * MB, 'done': 1.0},
                        {'name': 'Visit our site.url', 'length': 120, 'done': 1.0},
                        {'name': 'poster.png', 'length': 80_000, 'done': 1.0}]
            row = cache_entries(d, {'a' * 40}, {}, live_progress=lambda ih: progress)[0]
            self.assertEqual([f['name'] for f in row['files']], ['Show.S01E03.mkv', 'Show.S01E02.mkv'])


class DeleteOneFile(unittest.TestCase):
    # A pack of three episodes, 4 MB pieces: E01 = bytes 0-10 MB (pieces 0-2), E02 = 10-18 MB (pieces 2-4),
    # E03 = 18-24 MB (pieces 4-5). Every piece is downloaded.
    IH = 'a' * 40
    INFO = {'name': 'Show S01', 'piece length': 4 * MB,
            'files': [{'length': 10 * MB, 'path': ['E01.mkv']}, {'length': 8 * MB, 'path': ['E02.mkv']},
                      {'length': 6 * MB, 'path': ['E03.mkv']}]}

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        t = self.dir / self.IH / 'Show S01'
        t.mkdir(parents=True)
        for name, size in (('E01.mkv', 10), ('E02.mkv', 8), ('E03.mkv', 6)):
            (t / name).write_bytes(b'x' * size * MB)
        resume = {'info': self.INFO, 'pieces': bytes([0b11111100]), 'file_priority': [4, 4, 4],
                  'unfinished': [{'piece': 3, 'bitmask': b'\x01'}], 'name': 'Show S01'}
        (self.dir / self.IH / '.resume').write_bytes(bencode(resume))

    def tearDown(self):
        self.tmp.cleanup()

    def test_bencode_round_trip(self):
        v = {b'a': [1, b'xy', {b'z': 0}], b'b': b''}
        self.assertEqual(bdecode(bencode(v)), v)

    def test_the_file_goes_and_its_pieces_are_marked_missing(self):
        ok, msg, freed = delete_file_cached(self.dir, self.IH, 1)
        self.assertTrue(ok, msg)
        self.assertGreaterEqual(freed, 8 * MB)
        self.assertFalse((self.dir / self.IH / 'Show S01' / 'E02.mkv').exists())
        self.assertTrue((self.dir / self.IH / 'Show S01' / 'E01.mkv').exists())
        r = bdecode((self.dir / self.IH / '.resume').read_bytes())
        # Pieces 2-4 overlap E02 (2 and 4 are shared with its neighbours): all three must be fetched again.
        self.assertEqual(r[b'pieces'], bytes([0b11000100]))
        self.assertEqual(r[b'file_priority'], [4, 0, 4])
        self.assertEqual(r[b'unfinished'], [], 'a half-done piece of the deleted file is forgotten too')
        self.assertEqual(r[b'info'], bdecode(bencode(self.INFO)), 'the torrent itself is untouched')

    def test_bad_requests(self):
        for ih, idx in (('../x', 0), (self.IH, 9), (self.IH, -1), ('b' * 40, 0), (self.IH, 'x')):
            ok, _, _ = delete_file_cached(self.dir, ih, idx)
            self.assertFalse(ok, (ih, idx))

    def dash(self, busy=(), in_session=(), offline=None):
        calls = []

        def run_offline(ih, fn):
            calls.append(ih)
            return fn() if offline is None else offline
        d = Dashboard(sessions=lambda: [], facts=lambda k: None, install_dir=self.tmp.name, log=lambda *a: None,
                      config={'cache_dir': str(self.dir), 'cache_budget_gb': 15}, in_session=lambda: set(in_session),
                      busy_files=lambda ih: set(busy), offline=run_offline)
        return d, calls

    def test_the_dashboard_deletes_a_file_with_the_torrent_held_out_of_the_session(self):
        d, calls = self.dash(in_session={self.IH})
        out = d.delete_file(self.IH, 0)
        self.assertTrue(out['ok'], out)
        self.assertEqual(calls, [self.IH])
        self.assertFalse((self.dir / self.IH / 'Show S01' / 'E01.mkv').exists())

    def test_a_file_being_read_is_not_deleted(self):
        d, calls = self.dash(busy={0}, in_session={self.IH})
        out = d.delete_file(self.IH, 0)
        self.assertFalse(out['ok'])
        self.assertIn('being read', out['message'])
        self.assertEqual(calls, [])
        self.assertTrue((self.dir / self.IH / 'Show S01' / 'E01.mkv').exists())

    def test_another_file_being_read_means_try_again_later(self):
        d, _ = self.dash(busy={1}, in_session={self.IH}, offline=False)
        out = d.delete_file(self.IH, 0)
        self.assertFalse(out['ok'])
        self.assertIn('once it stops', out['message'])
        self.assertTrue((self.dir / self.IH / 'Show S01' / 'E01.mkv').exists())


class CachedProgress(unittest.TestCase):
    def test_per_torrent_and_file_index_for_the_advisor(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for ih, bits in (('a' * 40, 0b11100000), ('b' * 40, 0b00000000)):
                t = Path(d) / ih
                t.mkdir()
                info = {'name': 'Show S01', 'piece length': 4 * MB,
                        'files': [{'length': 8 * MB, 'path': ['E01.mkv']}, {'length': 6 * MB, 'path': ['E02.mkv']}]}
                (t / '.resume').write_bytes(benc({'info': info, 'pieces': bytes([bits])}))
            out = cached_progress(d, in_session={'c' * 40}, live_progress=lambda ih: None)
            self.assertEqual(out, {'a' * 40: {'0': 1.0, '1': 0.6667}})
            live = cached_progress(d, in_session={'a' * 40},
                                   live_progress=lambda ih: [{'name': 'E01.mkv', 'length': 8 * MB, 'done': 0.5},
                                                             {'name': 'E02.mkv', 'length': 6 * MB, 'done': 0.004}])
            self.assertEqual(live, {'a' * 40: {'0': 0.5}})


class NextEpisode(unittest.TestCase):
    # The Accid Chernobyl pack as libtorrent lists it: the file indexes are not in episode order.
    PACK = [{'idx': 0, 'name': 'Chernobyl - S01E02 - Please Remain Calm (2160p DV HDR x265) [Accid].mkv', 'length': 7496392621, 'done': 1.0},
            {'idx': 1, 'name': 'Chernobyl - S01E01 - 1.23.45 (2160p DV HDR x265) [Accid].mkv', 'length': 7220830838, 'done': 0.0},
            {'idx': 2, 'name': 'Chernobyl - S01E03 - Open Wide, O Earth (2160p DV HDR x265) [Accid].mkv', 'length': 4041118984, 'done': 0.0},
            {'idx': 3, 'name': 'Chernobyl - S01E04 - The Happiness of All Mankind (2160p DV HDR x265) [Accid].mkv', 'length': 6880924528, 'done': 0.0},
            {'idx': 4, 'name': 'Chernobyl - S01E05 - Vichnaya Pamyat (2160p DV HDR x265) [Accid].mkv', 'length': 6880924528, 'done': 0.0}]

    def test_the_episode_after_the_one_playing(self):
        self.assertEqual(next_episode(self.PACK, 0), {
            'idx': 2, 'label': 'S01E03', 'name': 'Chernobyl - S01E03 - Open Wide, O Earth (2160p DV HDR x265) [Accid].mkv',
            'length': 4041118984, 'done': 0.0})
        self.assertEqual(next_episode(self.PACK, 1)['label'], 'S01E02', 'E01 is followed by E02, whatever its index')

    def test_nothing_after_the_last_episode(self):
        self.assertIsNone(next_episode(self.PACK, 4))

    def test_a_season_ends_and_the_next_one_begins(self):
        files = [{'idx': 0, 'name': 'Show.S01E10.mkv', 'length': 900 * MB, 'done': 1.0},
                 {'idx': 1, 'name': 'Show.S02E01.mkv', 'length': 900 * MB, 'done': 0.0},
                 {'idx': 2, 'name': 'Show.S02E02.mkv', 'length': 900 * MB, 'done': 0.0}]
        self.assertEqual(next_episode(files, 0)['idx'], 1)

    def test_without_a_file_playing_it_follows_the_furthest_episode_with_data(self):
        self.assertEqual(next_episode(self.PACK)['label'], 'S01E03', 'E02 is downloaded, E01 is not')
        pack = [dict(f, done=0.5) if f['idx'] == 2 else dict(f, done=0.004) if f['idx'] == 3 else f for f in self.PACK]
        self.assertEqual(next_episode(pack)['label'], 'S01E04', 'E03 is half in; a sliver of E04 shared with E03 is not a start')
        self.assertIsNone(next_episode([dict(f, done=0.0) for f in self.PACK]), 'nothing downloaded, nothing to follow')

    def test_samples_extras_and_duplicates(self):
        files = [{'idx': 0, 'name': 'Show.S01E01.1080p.mkv', 'length': 900 * MB, 'done': 1.0},
                 {'idx': 1, 'name': 'Show.S01E02.sample.mkv', 'length': 40 * MB, 'done': 0.0},
                 {'idx': 2, 'name': 'Show.S01E02.nfo', 'length': 2000, 'done': 0.0},
                 {'idx': 3, 'name': 'Show.S01E02.720p.mkv', 'length': 500 * MB, 'done': 0.0},
                 {'idx': 4, 'name': 'Show.S01E02.1080p.mkv', 'length': 900 * MB, 'done': 0.0}]
        self.assertEqual(next_episode(files, 0)['idx'], 4, 'the full-size video, not the sample, the .nfo or a smaller copy')
        files.append({'idx': 5, 'name': 'Show.S01E03.mkv', 'length': 900 * MB, 'done': 0.0})
        self.assertEqual(next_episode(files, 3)['idx'], 5, 'the smaller copy of E02 playing is still E02')

    def test_other_episode_codes(self):
        files = [{'idx': 0, 'name': 'Show 1x09.mkv', 'length': 900 * MB, 'done': 1.0},
                 {'idx': 1, 'name': 'Show 1x10.mkv', 'length': 900 * MB, 'done': 0.0}]
        self.assertEqual(next_episode(files, 0)['label'], 'S01E10')

    def test_a_movie_or_a_file_without_an_episode_code(self):
        files = [{'idx': 0, 'name': 'Movie.2019.2160p.mkv', 'length': 9000 * MB, 'done': 1.0},
                 {'idx': 1, 'name': 'Movie.2019.Extras.mkv', 'length': 900 * MB, 'done': 0.0}]
        self.assertIsNone(next_episode(files, 0))
        self.assertIsNone(next_episode(files))
        self.assertIsNone(next_episode(self.PACK, 9), 'a file index the pack does not have')
        self.assertIsNone(next_episode([], None))


class FakePrefetcher:
    """Stands in for prefetch.Prefetcher: records what the dashboard asks it to do."""
    def __init__(self, jobs=()):
        self.started, self.cancelled, self.listed = [], [], list(jobs)

    def start(self, ih, idx, label=None):
        self.started.append((ih, idx, label))
        return True, f'Downloading {label} for later'

    def cancel(self, ih, idx):
        self.cancelled.append((ih, idx))
        return True, 'Stopped'

    def jobs(self):
        return self.listed


class DownloadForLater(unittest.TestCase):
    # A pack listed out of episode order, 4 MB pieces: E02 = pieces 0-1 (downloaded), E01 = pieces 2-3, E03 = pieces 4-5.
    IH = 'b02c69e9c46feb40c23c10f785e31c673291240a'
    INFO = {'name': 'Show S01', 'piece length': 4 * MB,
            'files': [{'length': 8 * MB, 'path': ['Show.S01E02.mkv']}, {'length': 8 * MB, 'path': ['Show.S01E01.mkv']},
                      {'length': 8 * MB, 'path': ['Show.S01E03.mkv']}]}

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / self.IH / 'Show S01').mkdir(parents=True)
        (self.dir / self.IH / '.resume').write_bytes(benc({'info': self.INFO, 'pieces': bytes([0b11000000])}))

    def tearDown(self):
        self.tmp.cleanup()

    def dash(self, prefetch=None, in_session=(), live=None, sessions=(), busy=(), **config):
        return Dashboard(sessions=lambda: list(sessions), facts=lambda k: None, install_dir=self.tmp.name, log=lambda *a: None,
                         config=dict({'cache_dir': str(self.dir), 'cache_budget_gb': 15, 'min_free_gb': 3}, **config),
                         in_session=lambda: set(in_session), live_progress=live or (lambda ih: None), prefetch=prefetch,
                         busy_files=lambda ih: set(busy))

    def job(self, ih, idx, state='downloading'):
        return {'ih': ih, 'idx': idx, 'label': 'x', 'state': state, 'done': 0.0, 'length': 0, 'message': None,
                'mbps': None, 'eta_s': None}

    def test_a_pack_that_would_outgrow_the_cache_budget_is_refused(self):
        # Past the budget, the cache deletes an idle torrent whole — the episode just downloaded with it.
        p = FakePrefetcher()
        out = self.dash(p, cache_budget_gb=12 / 1024).prefetch(self.IH, 2)     # E03's 8 MB fits in 12 MB
        self.assertTrue(out['ok'], out)
        p = FakePrefetcher()
        out = self.dash(p, cache_budget_gb=12 / 1024, in_session={self.IH}, busy={1}).prefetch(self.IH, 2)
        self.assertFalse(out['ok'], 'E01 is being streamed: its 8 MB will land in the pack too')
        self.assertIn('watched', out['message'])
        p = FakePrefetcher([self.job(self.IH, 1, 'queued')])
        self.assertFalse(self.dash(p, cache_budget_gb=12 / 1024).prefetch(self.IH, 2)['ok'], 'E01 is queued: same')
        p = FakePrefetcher([self.job(self.IH, 1, 'done')])
        self.assertTrue(self.dash(p, cache_budget_gb=12 / 1024).prefetch(self.IH, 2)['ok'], 'a finished job is in the pack already')
        self.assertEqual(p.started, [(self.IH, 2, 'S01E03')])

    def test_other_downloads_still_to_come_count_against_the_free_disk(self):
        import shutil as _sh
        other = 'c' * 40
        (self.dir / other).mkdir()
        info = {'name': 'Big.mkv', 'piece length': 4 * MB, 'length': 6 * 1024 * MB}
        (self.dir / other / '.resume').write_bytes(benc({'info': info, 'pieces': b''}))
        GB = 1024 * MB
        # Leave room for 4 GB: E03 (8 MB) fits alone, not after the 6 GB still to come for the other torrent.
        keep = (_sh.disk_usage(self.dir).free - 4 * GB) / GB
        self.assertTrue(self.dash(FakePrefetcher(), min_free_gb=keep).prefetch(self.IH, 2)['ok'])
        p = FakePrefetcher([self.job(other, 0)])
        out = self.dash(p, min_free_gb=keep).prefetch(self.IH, 2)
        self.assertFalse(out['ok'])
        self.assertIn('free', out['message'])
        self.assertEqual(p.started, [])

    def test_the_file_is_queued_under_its_episode_code(self):
        p = FakePrefetcher()
        out = self.dash(p).prefetch(self.IH, 2)
        self.assertTrue(out['ok'], out)
        self.assertEqual(p.started, [(self.IH, 2, 'S01E03')])

    def test_what_is_refused(self):
        p = FakePrefetcher()
        d = self.dash(p)
        for ih, idx in (('../x', 2), (self.IH, '2'), (self.IH, True), (self.IH, 7), ('c' * 40, 0)):
            out = d.prefetch(ih, idx)
            self.assertFalse(out['ok'], (ih, idx))
        out = d.prefetch(self.IH, 0)
        self.assertFalse(out['ok'])
        self.assertIn('already on this Mac', out['message'])
        out = self.dash(p, min_free_gb=10 ** 9).prefetch(self.IH, 2)
        self.assertFalse(out['ok'], 'the disk would fill past what is kept free')
        self.assertIn('free', out['message'])
        self.assertEqual(p.started, [])
        self.assertFalse(self.dash(None).prefetch(self.IH, 2)['ok'], 'no prefetcher, no downloads')

    def test_a_torrent_in_the_session_is_judged_on_its_live_progress(self):
        live = lambda ih: [{'idx': 0, 'name': 'Show.S01E02.mkv', 'length': 8 * MB, 'done': 1.0},  # noqa: E731
                           {'idx': 1, 'name': 'Show.S01E01.mkv', 'length': 8 * MB, 'done': 0.0},
                           {'idx': 2, 'name': 'Show.S01E03.mkv', 'length': 8 * MB, 'done': 1.0}]
        out = self.dash(FakePrefetcher(), in_session={self.IH}, live=live).prefetch(self.IH, 2)
        self.assertFalse(out['ok'])
        self.assertIn('S01E03 is already on this Mac', out['message'])

    def test_cancel_goes_to_the_prefetcher(self):
        p = FakePrefetcher()
        out = self.dash(p).cancel_prefetch(self.IH, 2)
        self.assertTrue(out['ok'])
        self.assertEqual(p.cancelled, [(self.IH, 2)])
        self.assertFalse(self.dash(p).cancel_prefetch('../x', 2)['ok'])

    def test_the_state_carries_the_jobs_and_each_session_s_next_episode(self):
        import time as _t
        job = {'ih': self.IH, 'idx': 2, 'label': 'S01E03', 'state': 'downloading', 'done': 0.25, 'length': 8 * MB,
               'message': None, 'mbps': 21.0, 'eta_s': 3}
        snap = dict(SNAP, ih=self.IH, idx=0, file='Show.S01E02.mkv', size=8 * MB, file_offset=0, pos_at=_t.time())
        state = self.dash(FakePrefetcher([job]), sessions=[snap]).state()
        self.assertEqual(state['prefetch'], [job])
        self.assertEqual(state['sessions'][0]['next'],
                         {'idx': 2, 'label': 'S01E03', 'name': 'Show.S01E03.mkv', 'length': 8 * MB, 'done': 0.0})
        self.assertEqual(self.dash().state()['prefetch'], [])

    def test_a_season_pack_row_offers_the_episode_after_the_furthest_one_downloaded(self):
        row = cache_entries(self.dir, set(), {})[0]
        self.assertEqual(row['next']['label'], 'S01E03')
        self.assertEqual(row['next']['idx'], 2)

    def test_the_state_carries_the_page_model(self):
        import time as _t
        snap = dict(SNAP, ih=self.IH, idx=0, file='Show.S01E02.mkv', size=8 * MB, file_offset=0, pos_at=_t.time())
        d = self.dash(FakePrefetcher(), sessions=[snap])
        d.slow['cache'] = {'used': 8 * MB, 'budget': 15 * 1024 * MB, 'free': 50 * 1024 * MB}
        d.slow['cache_list'] = cache_entries(self.dir, set(), {})
        state = d.state()
        self.assertEqual([s['id'] for s in state['chain']], ['swarm', 'mac', 'wifi', 'tv'])
        self.assertEqual(state['glance']['title'], 'Chernobyl - S01 [Accid]', 'no advisor record: the torrent\'s name')
        self.assertEqual(state['up_next']['label'], 'S01E03')
        self.assertEqual([t['key'] for t in state['shelf']['items']], ['ih:' + self.IH])
        self.assertIn('ih:' + self.IH, state['titles'])
        self.assertEqual(state['recent']['live'], True)
        self.assertIsNone(self.dash().state()['shelf'], 'before the cache is first read')


class TvAddress(unittest.TestCase):
    def test_the_last_device_that_polled_like_a_stremio_client(self):
        text = '\n'.join([
            '2026-10-11T18:26:04.002Z 192.168.0.100 GET /settings -> 200',
            '2026-10-11T18:26:05.002Z 192.168.0.100 GET /b02c69e9c46feb40c23c10f785e31c673291240a/1/stats.json -> 200',
            '2026-10-11T18:26:06.002Z 192.168.0.101 GET /settings -> 200',       # this Mac
            '2026-10-11T18:26:07.002Z 192.168.0.57 GET /ui -> 200',              # a phone on the dashboard
        ])
        self.assertEqual(last_tv_ip(text, own={'192.168.0.101', '127.0.0.1'}), '192.168.0.100')
        self.assertIsNone(last_tv_ip('', own=set()))


class Log(unittest.TestCase):
    def test_noise_is_dropped_and_lines_are_shortened(self):
        text = '\n'.join([
            '2026-10-11T18:26:04.002Z 192.168.0.100 GET /settings -> 200',
            '2026-10-11T18:26:04.640Z 192.168.0.100 GET /b02c69e9c46feb40c23c10f785e31c673291240a/1/stats.json -> 200',
            '2026-10-11T18:26:04.641Z 192.168.0.101 GET /torrentd/health -> 200',
            '2026-10-11T18:26:04.642Z torrent b02c69e9: added (resumed from cache, 19 trackers)',
            '2026-10-11T18:26:05.375Z GET b02c69e9/1 bytes=7220524191- -> 0.3MB in 0.2s (waiting for pieces 0.0s, reading 0.0s, sending 0.0s)',
            'Traceback (most recent call last):',
        ])
        lines = filter_log(text)
        self.assertEqual([l['text'] for l in lines], [
            'torrent b02c69e9: added (resumed from cache, 19 trackers)',
            'GET b02c69e9/1 bytes=7220524191- -> 0.3MB in 0.2s (waiting for pieces 0.0s, reading 0.0s, sending 0.0s)',
            'Traceback (most recent call last):',
        ])
        self.assertEqual(lines[0]['time'], '18:26:04')

    def test_query_strings_are_hidden(self):
        lines = filter_log('2026-10-11T18:26:04.642Z 192.168.0.100 GET /opensubHash?videoUrl=https%3A%2F%2Fx%2Frealdebrid%2Fsecret -> 200')
        self.assertNotIn('secret', lines[0]['text'])
        self.assertIn('/opensubHash?…', lines[0]['text'])


if __name__ == '__main__':
    unittest.main()
