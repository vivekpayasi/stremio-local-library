"""Reading media file names, and writing sizes and times, for people: episode codes and titles, release groups,
"4 min 20 s", "3.8 GB". Shared by dashboard.py and page_model.py."""
import re

MB = 1024 * 1024
GB = 1024 * MB
VIDEO = re.compile(r'\.(mkv|mp4|m4v|avi|mov|ts|m2ts|mts|webm|wmv|mpg)$', re.I)
SAMPLE = re.compile(r'\bsample\b', re.I)
EPISODE = re.compile(r'(?:\bS(\d{1,2})[ ._-]?E(\d{1,3})|\b(\d{1,2})x(\d{2,3}))', re.I)
# Words that end release names without being the group that made them.
TECH = re.compile(r'^(?:\d{3,4}p|\d+(?:\.\d+)?|x26[45]|h\.?26[45]|hevc|avc|av1|\d+bit|hdr\d*\+?|dv|dovi|sdr|atmos|truehd|'
                  r'dts(?:-?hd)?(?:\.?ma)?|ddp?\d?(?:\.\d)?|e?ac3|aac\d?(?:\.\d)?|flac|bdrip|brrip|bluray|web(?:-?dl|rip)?|dl|'
                  r'remux|uhd|repack|proper|multi|dual|ita|eng|fre|sub|subs)$', re.I)


def episode_key(name):
    """(season, episode) from a file name, or None."""
    m = EPISODE.search(name or '')
    return (int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4))) if m else None


def episode_label(name):
    """'S01E03' for a file name with an episode code, else the name."""
    k = episode_key(name)
    return f'S{k[0]:02d}E{k[1]:02d}' if k else name


def episode_title(name):
    """The episode's own title when the file name carries one: 'Show - S01E03 - Open Wide, O Earth (2160p ...).mkv'."""
    m = re.search(r'S\d{1,2}[ ._-]?E\d{1,3}\s+-\s+(.+?)\s*(?:[(\[]|\.[A-Za-z0-9]{2,4}$|$)', name or '', re.I)
    return m.group(1).strip() if m else None


def release_group(name):
    """The group that made a release, from the end of its name: 'x265-NAHOM', '[Accid]', '(... r00t)'. None when unsure."""
    base = re.sub(r'\.(?:mkv|mp4|m4v|avi|ts|m2ts|mov|webm)$', '', (name or '').strip(), flags=re.I)
    m = re.search(r'\[([^\[\]]{2,24})\]$', base) or re.search(r'-([A-Za-z0-9][A-Za-z0-9.]{1,23})$', base)
    if m and not TECH.match(m.group(1)):
        return m.group(1)
    m = re.search(r'\(([^()]*)\)$', base)
    words = m.group(1).split() if m else []
    last = words[-1] if words else ''
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.]{1,23}', last) and re.search('[A-Za-z]', last) and not TECH.match(last):
        return last
    return None


def next_episode(files, current_idx=None):
    """The file of a season pack to download for later: {idx, label, name, length, done} of the episode after the
    one at `current_idx` — or, with none given, after the furthest episode with data. None at the end of the pack,
    or when the file names carry no episode codes. `files` as resume_progress lists them."""
    episodes = {}
    for f in files:
        key = episode_key(f['name'])
        if not key or not VIDEO.search(f['name']) or SAMPLE.search(f['name']):
            continue
        if key not in episodes or f['length'] > episodes[key]['length']:   # two copies of one episode: the bigger
            episodes[key] = f
    if current_idx is None:
        started = [k for k, f in episodes.items() if f['done'] >= 0.01]
        current = max(started, default=None)
    else:       # the playing file's own episode: it may be the smaller of two copies
        current = next((episode_key(f['name']) for f in files if f['idx'] == current_idx), None)
    later = sorted(k for k in episodes if current is not None and k > current)
    if not later:
        return None
    f = episodes[later[0]]
    return {'idx': f['idx'], 'label': episode_label(f['name']), 'name': f['name'], 'length': f['length'], 'done': f['done']}


def clock(s):
    s = int(round(s))
    return f'{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}' if s >= 3600 else f'{s // 60}:{s % 60:02d}'


def spoken(s):
    """A duration as said aloud: '45 s', '4 min 20 s', '1 h 5 min'."""
    s = int(round(s))
    if s < 60:
        return f'{s} s'
    if s < 3600:
        return f'{s // 60} min' + (f' {s % 60} s' if s % 60 else '')
    return f'{s // 3600} h' + (f' {s // 60 % 60} min' if s // 60 % 60 else '')


def size(n):
    """Bytes as the page shows them: '480 MB', '3.8 GB'."""
    return f'{n / GB:.1f} GB' if n >= GB else f'{round(n / MB)} MB'
