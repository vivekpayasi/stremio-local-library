"""Run with:  python3 -m unittest discover -s tests

Command output below follows the real formats (macOS ping and system_profiler, Android dumpsys/top/logcat
from the TCL TV), with names and addresses replaced.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from dashboard import (Dashboard, account_status, classify_requests, filter_log, health, last_tv_ip,  # noqa: E402
                       parse_mac_wifi, parse_ping, parse_tv_snapshot, piece_strip, summarize_session)

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

    def test_a_jump_is_a_seek_and_a_long_wait_is_a_stall(self):
        events = classify_requests([req(100, 0), req(110, 3_000_000_000, wait=6.5, took=7.0)], self.SIZE, self.DURATION)
        kinds = [e['kind'] for e in events]
        self.assertEqual(kinds, ['stall', 'seek'], 'newest first')
        self.assertIn('35:13', events[1]['text'])
        self.assertIn('6.5 s', events[0]['text'])
        self.assertEqual(events[0]['cause'], 'swarm')

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


if __name__ == '__main__':
    unittest.main()
