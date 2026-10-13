"""What the dashboard page shows, worked out from the state dashboard.py gathers: the titles on this Mac, what to watch
next, the path from the swarm to the TV, the problem worth a banner, the glance at what plays. The page only renders it.

Pure functions over plain dicts, tested in tests/test_page_model.py."""
import collections
import statistics
import time

from media_text import GB, MB, SAMPLE, VIDEO, clock, episode_key, episode_label, episode_title, release_group, size, spoken
from prefetch import ACTIVE

LEVELS = {'idle': 0, 'good': 1, 'warn': 2, 'bad': 3}


def title_key(ih, titles):
    """Which title a cached torrent belongs to: its IMDb id as the advisor recorded it, else the torrent itself."""
    return (titles.get(ih) or {}).get('imdb') or f'ih:{ih}'


def _job_for(jobs, ih, idx):
    return next((j for j in jobs if j['ih'] == ih and j['idx'] == idx), None)


def library(rows, titles, jobs):
    """(shelf, details): one card per title on this Mac, most recently used first, and per title everything its sheet
    shows. `rows` are dashboard.cache_entries rows; `titles` the advisor's store; `jobs` the downloads for later."""
    groups = collections.OrderedDict()
    for r in rows:
        groups.setdefault(title_key(r['ih'], titles), []).append(r)
    details = {key: _title(key, rs, titles, jobs) for key, rs in groups.items()}
    shelf = [{k: d[k] for k in ('key', 'title', 'kind', 'poster', 'summary', 'sub', 'last_used', 'bytes')} for d in details.values()]
    shelf.sort(key=lambda t: -t['last_used'])
    return shelf, details


def _title(key, rows, titles, jobs):
    rows = sorted(rows, key=lambda r: -r['last_used'])
    info = next((titles[r['ih']] for r in rows if titles.get(r['ih'])), {})
    meta = titles.get('meta:' + info['imdb'], {}) if info.get('imdb') else {}
    episodes = [_episode_row(r, f, meta, jobs) for r in rows for f in r.get('videos') or []
                if episode_key(f['name']) and not SAMPLE.search(f['name'])]
    kind = info.get('type') or ('series' if episodes else 'movie')
    t = {'key': key, 'title': info.get('name') or rows[0]['name'], 'kind': kind if episodes or kind == 'movie' else 'movie',
         'year': info.get('year'), 'poster': info.get('poster'), 'backdrop': meta.get('background'),
         'bytes': sum(r['bytes'] for r in rows), 'last_used': max(r['last_used'] for r in rows),
         'in_use': any(r['in_use'] for r in rows), 'ihs': [r['ih'] for r in rows], 'episodes': [], 'seasons': [], 'releases': []}
    if t['kind'] == 'series':
        _mark_next(episodes)
        copies = collections.Counter((e['season'], e['episode']) for e in episodes)
        for e in episodes:
            e['tag'] = e['release'] if copies[(e['season'], e['episode'])] > 1 else None
        episodes.sort(key=lambda e: (e['season'], e['episode'], e['tag'] or ''))
        t.update(episodes=episodes, seasons=sorted({e['season'] for e in episodes}))
        group = release_group(rows[0]['name'])
        t['meta_line'] = ' · '.join(x for x in (t['year'], group, size(t['bytes']) + ' here') if x)
    else:
        t['releases'] = [_release(r, titles, jobs) for r in rows]
        n = len(t['releases'])
        t['meta_line'] = ' · '.join(x for x in (t['year'], 'Movie', f'{n} releases' if n > 1 else None, size(t['bytes']) + ' here') if x)
    t['summary'], t['sub'] = _summary(t)
    return t


def _episode_row(row, f, meta, jobs):
    s, e = episode_key(f['name'])
    job = _job_for(jobs, row['ih'], f['idx'])
    return {'season': s, 'episode': e, 'label': f'S{s:02d}E{e:02d}',
            'name': (meta.get('episodes') or {}).get(f'{s}:{e}') or episode_title(f['name']),
            'file': f['name'], 'ih': row['ih'], 'idx': f['idx'], 'length': f['length'],
            'done': max(f['done'], job['done']) if job else f['done'], 'busy': bool(f.get('busy')), 'job': job,
            'release': release_group(row['name']) or row['name'], 'tag': None, 'next': False}


def _mark_next(episodes):
    """The episode after the furthest one with data gets the filled Download button (the largest copy, if two)."""
    started = [(e['season'], e['episode']) for e in episodes if e['done'] >= 0.01]
    if not started:
        return
    later = sorted({(e['season'], e['episode']) for e in episodes if (e['season'], e['episode']) > max(started)})
    copies = [e for e in episodes if later and (e['season'], e['episode']) == later[0] and e['done'] < 1]
    if copies:
        max(copies, key=lambda e: e['length'])['next'] = True


def _release(r, titles, jobs):
    main = max(r.get('videos') or [], key=lambda f: f['length'], default=None)
    idx = main['idx'] if main else None
    rec = titles.get(f"{r['ih']}:{idx}") or titles.get(r['ih']) or {}
    job = _job_for(jobs, r['ih'], idx) if main else None
    ext = VIDEO.search(main['name']) if main else None
    done = main['done'] if main else 0
    return {'ih': r['ih'], 'idx': idx, 'name': r['name'], 'group': release_group(r['name']),
            'chips': rec.get('label') or ([ext.group(1).upper()] if ext else []), 'verdict': rec.get('verdict'),
            'length': main['length'] if main else 0, 'done': max(done, job['done']) if job else done,
            'bytes': r['bytes'], 'last_used': r['last_used'], 'in_use': r['in_use'], 'busy': bool(main and main.get('busy')),
            'job': job}


def _summary(t):
    """The shelf card's line, and its accent line while something of it downloads for later."""
    active = [x for x in t['episodes'] + t['releases'] if x['job'] and x['job']['state'] in ACTIVE]
    sub = None
    if active:
        a, j = active[0], active[0]['job']
        if 'episode' in a:
            sub = f"E{a['episode']} downloading {int(j['done'] * 100)}%" if j['state'] == 'downloading' else f"E{a['episode']} {j['state']}"
        else:
            sub = f"Downloading {int(j['done'] * 100)}%" if j['state'] == 'downloading' else f"Download {j['state']}"
    if t['kind'] == 'series':
        here = sorted({(e['season'], e['episode']) for e in t['episodes'] if e['done'] >= 0.01})
        if not here:
            return 'Nothing downloaded', sub
        seasons = sorted({s for s, _ in here})
        span = f'S{seasons[0]}' if len(seasons) == 1 else f'S{seasons[0]}–S{seasons[-1]}'
        return f"{span} · {len(here)} episode{'s' if len(here) != 1 else ''} here", sub
    rel = t['releases']
    if len(rel) > 1:
        return f"Movie · {len(rel)} releases · {size(t['bytes'])}", sub
    r = rel[0]
    if r['done'] >= 1:
        return f"Movie · on the Mac · {size(r['length'])}", sub
    if r['done'] >= 0.01:
        return f"Movie · {int(r['done'] * 100)}% here · {size(t['bytes'])}", sub
    return 'Movie · nothing downloaded', sub


def up_next(sessions, details, titles, jobs):
    """The episode to watch next: after the one playing, else after the one played last, else after the furthest one
    on the Mac of the most recently used series. None when there is no series to follow."""
    for s in sessions:
        n = s.get('next')
        if n:
            t = details.get(title_key(s['ih'], titles))
            return _next_card(t, s['ih'], n['idx'], n['name'], n['length'], n['done'], s, jobs)
    for t in sorted(details.values(), key=lambda t: -t['last_used']):
        e = next((e for e in t['episodes'] if e['next']), None)
        if e:
            return _next_card(t, e['ih'], e['idx'], e['file'], e['length'], e['done'], None, jobs)
    return None


def _next_card(t, ih, idx, file_name, length, done, s, jobs):
    t, s = t or {}, s or {}
    e = next((e for e in t.get('episodes', []) if e['ih'] == ih and e['idx'] == idx), {})
    job = _job_for(jobs, ih, idx)
    done = max([done, e.get('done', 0)] + ([job['done']] if job else []))
    season, episode = episode_key(file_name)
    if job and job['state'] in ACTIVE + ('failed',):
        state = job['state']
    elif done >= 1 or (job and job['state'] == 'done'):
        state = 'ready'
    else:
        state = 'not_here'
    return {'key': t.get('key'), 'ih': ih, 'idx': idx, 'title': t.get('title') or s.get('title'), 'season': season, 'episode': episode,
            'label': episode_label(file_name), 'name': e.get('name') or episode_title(file_name), 'length': length, 'done': done,
            'job': job, 'state': state, 'poster': t.get('poster') or s.get('poster'), 'backdrop': t.get('backdrop')}


def _step(id_, name, level, line, reason=None, advice=None, details=(), services=None):
    out = {'id': id_, 'name': name, 'level': level, 'line': line, 'reason': reason, 'advice': advice, 'details': [list(d) for d in details]}
    if services is not None:
        out['services'] = services
    return out


def _signal(dbm):
    return 'excellent' if dbm >= -55 else 'good' if dbm >= -67 else 'fair' if dbm >= -75 else 'weak'


def _dbm(v):
    return f'−{abs(v)}' if v < 0 else str(v)


def swarm_step(live, jobs):
    """Swarm -> this Mac: the stream's download against what the file needs; else a download for later; else idle."""
    if live:
        dl, need, p = live['download_mbps'], live.get('bitrate_mbps'), live['peers']
        line = f'{dl:.0f} Mbps in' + (f', the file needs {need:.0f}' if need else '') + f" · {p['connected']} peers, {p['sending']} sending"
        h = live.get('health') or {}
        level, reason, advice = 'good', None, None
        if h.get('level') == 'bad':
            level, reason = 'bad', h['text'].split(' — ')[0]
            advice = 'The torrent can\'t keep up. A release with more peers usually plays better.'
        elif h.get('text', '').startswith('Buffer is low'):
            level, reason, advice = 'warn', h['text'], 'It may stop to buffer; pausing for a minute lets it catch up.'
        details = [('Download', f'{dl:.1f} Mbps'), ('The file needs', f'{need:.1f} Mbps' if need else 'not known yet'),
                   ('Peers', f"{p['connected']} connected, {p['sending']} sending, {p['seeds']} seeds")]
        return _step('swarm', 'Swarm', level, line, reason, advice, details)
    j = next((j for j in jobs if j['state'] in ('downloading', 'waiting', 'starting')), None)
    if j:
        if j['state'] == 'waiting':
            line = f"{j['label']} for later: {(j.get('message') or 'waiting').lower()}"
        else:
            line = f"Downloading {j['label']} for later" + (f" · {j['mbps']:.0f} Mbps" if j.get('mbps') else '')
        return _step('swarm', 'Swarm', 'good', line, details=[('Download for later', f"{j['label']}, {int(j['done'] * 100)}%")])
    return _step('swarm', 'Swarm', 'idle', 'Nothing streaming right now')


def mac_step(server, cache, library, min_free):
    """This Mac: the torrent server, the cache and the disk, and the services running here."""
    lib = library or {}
    adv, acct = lib.get('advisor'), lib.get('account')
    services = [
        {'name': 'Torrent server', 'level': 'good', 'text': f"{server.get('cpu') or 0:.0f}% CPU, {server.get('rss_mb') or 0} MB"},
        {'name': 'Library', 'level': 'good' if library else 'bad',
         'text': f"{lib.get('movies') or 0} movies, {lib.get('shows') or 0} shows" if library else 'Not answering'},
        {'name': 'Stream Advisor', 'level': ('good' if adv else 'idle') if library else 'bad',
         'text': f"Rating streams for {adv['profile']}" if adv else ('Off' if library else 'Not answering')},
        {'name': 'Stremio account', 'level': ('good' if acct['ok'] else 'warn') if acct else 'idle',
         'text': acct['text'] if acct else 'Not checked yet'},
    ]
    used, budget, free = cache.get('used') or 0, cache.get('budget') or 0, cache.get('free')
    line = f"up {spoken(server.get('uptime_s') or 0)} · cache {size(used)} of {size(budget)}" + (f' · {size(free)} free' if free is not None else '')
    reason = advice = None
    if not library:
        reason = 'The Local Library addon isn\'t answering'
        advice = 'Local files aren\'t served and streams aren\'t rated until it\'s back; it restarts by itself.'
    elif acct and not acct['ok']:
        reason = acct['text']
        advice = 'Sign in to Stremio desktop on this Mac.' if 'signed out' in acct['text'] else None
    elif free is not None and free < min_free:
        reason, advice = f'Only {size(free)} free on the disk', 'Delete downloads you\'ve watched to make room.'
    level = 'warn' if reason else 'good'
    cap = server.get('download_cap_mbps')
    details = [('Running for', spoken(server.get('uptime_s') or 0)), ('Download limit', f'{cap} Mbps' if cap else 'none'),
               ('Torrent cache', f'{size(used)} of {size(budget)}'), ('Free disk', size(free) if free is not None else '—'),
               ('Learning from plays', 'on' if server.get('learning') else 'off')]
    return _step('mac', 'This Mac', level, line, reason, advice, details, services)


def wifi_step(mac_wifi, tv_ping, usual, tv, playing):
    """Wi-Fi: the Mac's link and the TV's answer to ping. Judged only while something plays."""
    parts, details = [], []
    level, reason, advice = 'good', None, None
    if mac_wifi and mac_wifi.get('rssi') is not None:
        parts.append(f"Mac on {mac_wifi.get('band') or 'Wi-Fi'}, {_dbm(mac_wifi['rssi'])} dBm, {_signal(mac_wifi['rssi'])}")
        details += [('Mac Wi-Fi', f"{mac_wifi.get('band') or ''}, channel {mac_wifi.get('channel')}, {mac_wifi.get('width') or ''}"),
                    ('Mac signal', f"{mac_wifi['rssi']} dBm (noise {mac_wifi.get('noise')})"), ('Mac link speed', f"{mac_wifi.get('rate')} Mbps")]
        if playing and mac_wifi['rssi'] < -75:
            level, reason = 'warn', 'The Mac\'s Wi-Fi signal is weak'
            advice = 'The stream crosses the Wi-Fi twice; Ethernet for the Mac, or the router closer, helps.'
    else:
        parts.append('Mac not on Wi-Fi')
    ms = (tv_ping or {}).get('ms')
    if ms is not None:
        parts.append(f'ping to the TV {ms:.0f} ms')
        details.append(('Ping to the TV', f'{ms:.0f} ms' + (f' (usually {usual:.0f})' if usual else '')))
        if playing and ms >= 50:
            level, reason = 'warn', 'The TV\'s Wi-Fi is slow to answer'
            advice = f'Ping {ms:.0f} ms' + (f' (usually {usual:.0f})' if usual else '') + ' — playback may stutter. Restarting the TV usually clears it.'
    tvw = (tv or {}).get('wifi') if (tv or {}).get('state') == 'ok' else None
    if tvw and tvw.get('rssi') is not None:
        parts.append(f"TV {_dbm(tvw['rssi'])} dBm, {_signal(tvw['rssi'])}")
        details.append(('TV signal', f"{tvw['rssi']} dBm, {tvw.get('band') or ''}, link {tvw.get('link')} Mbps"))
        if playing and tvw['rssi'] < -75 and not reason:
            level, reason = 'warn', 'The TV\'s Wi-Fi signal is weak'
            advice = 'Playback may stutter; moving the router or the TV, or a 5 GHz network, helps.'
    if (tv_ping or {}).get('ip'):
        details.append(('TV address', tv_ping['ip']))
    return _step('wifi', 'Wi-Fi', level, ' · '.join(parts), reason, advice, details)


def _decoder(name):
    n = (name or '').lower()
    for key, label in (('dolby-vision', 'Dolby Vision'), ('hevc', 'HEVC'), ('avc', 'H.264'), ('vp9', 'VP9'), ('av1', 'AV1')):
        if key in n:
            return label
    return name


def _recent_clock(hms, now, minutes=10):
    """Whether a logcat time ('20:55:10', today, or yesterday if that is in the future) is within `minutes` of now."""
    try:
        h, m, s = (int(x) for x in hms.split(':'))
    except (AttributeError, ValueError):
        return False
    lt = time.localtime(now)
    at = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, s, 0, 0, -1))
    if at > now + 60:
        at -= 86400
    return now - at <= minutes * 60


def tv_step(tv, live, now):
    """The TV: asleep or not reachable, else its player, decoder and memory."""
    if not tv:
        return _step('tv', 'The TV', 'idle', 'Waiting for the first reading')
    st = tv.get('state')
    if st != 'ok':
        line = {'unreachable': 'Asleep — not answering', 'unauthorized': 'Accept "Allow debugging?" on the TV to see its details',
                'no-adb': 'Turn on network debugging on the TV to see its details'}.get(st)
        if line is None:
            line = ('Install adb on this Mac to see its details: brew install --cask android-platform-tools'
                    if tv.get('why') == 'adb is not installed' else 'Seen once it has played something through this server')
        return _step('tv', 'The TV', 'idle', line)
    p, m = tv.get('player') or {}, tv.get('memory') or {}
    dec = _decoder(p.get('video_decoder'))
    free = m.get('available_mb')
    line = ' · '.join(x for x in (p.get('name') or 'Not playing', dec and f'{dec} decoder', f'{free} MB free' if free is not None else None) if x)
    level, reason, advice = 'good', None, None
    if p.get('error') and _recent_clock(p.get('error_at'), now):
        level, reason, advice = 'bad', 'The TV\'s player hit an error', p['error']
    elif live and p.get('name') == 'mpv':
        level, reason = 'warn', 'This title plays in mpv'
        advice = 'Stremio remembers a player per title; ExoPlayer is the one that handles Dolby Vision on Android TVs.'
    elif live and free is not None and free < 400:
        level, reason, advice = 'warn', 'The TV is low on memory', f'{free} MB free — closing other apps on the TV helps.'
    elif live and p.get('low_memory_kills'):
        level, reason, advice = 'warn', 'Android closed apps to free memory', 'Closing other apps on the TV helps.'
    elif live and (live.get('health') or {}).get('text', '').startswith('The player keeps re-reading'):
        level, reason, advice = 'warn', 'The player keeps re-reading one spot', 'The file\'s layout or the player; another release usually plays better.'
    st_ = tv.get('stremio') or {}
    details = [('Model', f"{tv.get('model') or ''}" + (f", Android {tv['android']}" if tv.get('android') else '')),
               ('Player', p.get('name') or 'not playing'), ('Video decoder', p.get('video_decoder') or '—'),
               ('Audio', 'DTS decoded by the TV' if p.get('dts_on_tv') else '—'),
               ('Free memory', f"{free} of {m.get('total_mb')} MB" if free is not None else '—'),
               ('Stremio', f"{st_.get('cpu', 0):.0f}% CPU, {st_.get('memory', '')}" if st_ else '—')]
    if p.get('error'):
        details.append(('Last player error', f"{p.get('error_at') or ''} {p['error']}".strip()))
    return _step('tv', 'The TV', level, line, reason, advice, details)


ORDER = ('swarm', 'wifi', 'tv', 'mac')


def problem(steps):
    """The worst problem along the chain, for the banner at the top of the page: bad before warning, then in the order
    the stream travels."""
    found = [s for s in steps if s['reason'] and s['level'] in ('warn', 'bad')]
    if not found:
        return None
    s = max(found, key=lambda s: (LEVELS[s['level']], -ORDER.index(s['id'])))
    return {'level': s['level'], 'title': s['reason'], 'advice': s['advice'], 'step': s['id']}


def usual_ping(history, now):
    """The median ping to the TV over the last hour, or None with fewer than 5 readings."""
    recent = [ms for at, ms in history if now - at <= 3600]
    return float(statistics.median(recent)) if len(recent) >= 5 else None
