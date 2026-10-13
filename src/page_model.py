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
