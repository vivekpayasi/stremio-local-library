# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the `/ui` dashboard as a cinematic, intent-ordered page — the glance at what plays, Up next, a title shelf with
per-title sheets, and one "From the swarm to your TV" chain that is both system status and diagnosis.

**Architecture:** The server works out everything the page shows (a new pure module, `page_model.py`, fed by `dashboard.py`'s
collectors) and serves it in `/ui/api/state`; the page (`dashboard.html`) only renders it, region by region, writing to the DOM
only what changed. The Stream Advisor records what the dashboard cannot work out itself (each stream's verdict and quality chips,
each title's background art and episode names) in `stream-titles.json`.

**Tech Stack:** Python 3.9–3.13 standard library (`unittest`), Node ≥ 20 (`node:test`), plain HTML/CSS/JS in one file, no
dependencies. libtorrent only in the venv (`~/.stremio-local-addon/venv/bin/python`).

**Spec:** `docs/superpowers/specs/2026-10-13-dashboard-redesign-design.md`

## Global Constraints

- One self-contained page: no frameworks, no CDNs, no external scripts or stylesheets; images only from `images.metahub.space`
  (the page's CSP: `default-src 'self'; img-src 'self' https://images.metahub.space; style-src 'unsafe-inline'; script-src 'unsafe-inline'`).
- Python code must run on `/usr/bin/python3` (3.9.6) and the venv (3.12): no `X | None` annotations, no `match`, no backslashes or
  reused quote characters inside f-string expressions.
- Polls `/ui/api/state` every 2 s while the page is visible; write actions keep the existing guard (same-origin `fetch` with the
  `X-Dashboard: 1` header) and the existing routes (`/ui/api/cache/delete`, `/delete-file`, `/clear`, `/ui/api/prefetch`, `/prefetch/cancel`).
- Status is never colour alone: good ● / warning ▲ / bad ■ / idle ○ always with a word.
- Phone first: one column below 760 px; two columns from 1100 px; touch targets ≥ 40 px (≥ 36 px inside list rows); no horizontal page scroll.
- Dark and light themes from `prefers-color-scheme`; every colour is a token on `:root`.
- Deletes ask first (`confirm`). The server never knows what was *watched* — the page says only what is on the Mac.
- Deploy only when the TV is idle; ask the user before pushing.
- Test commands: `node --test` (repo root); `~/.stremio-local-addon/venv/bin/python -m unittest discover -s tests` and
  `/usr/bin/python3 -m unittest discover -s tests` (both must pass; the second skips `test_torrent_server`).

## Review Focus

- **Hostile or odd text** (a release or Cinemeta name containing `<`, `"`, `&`): every string reaches the DOM through `esc()`.
  → Task 6 test `text from torrents is escaped everywhere`.
- **A cached torrent with no readable resume data** (`videos: []`): the shelf and sheet render it ("Nothing downloaded") instead of
  failing. → Task 2 test `a torrent with no file list still gets a card`.
- **The server going away** (restart while the page is open): the last render stays and a banner says since when.
  → Task 6 test `a failed poll keeps the page and says the server is unreachable`.
- **A sheet open for a title that is then deleted:** the sheet says it is gone instead of breaking. → Task 7 test
  `a sheet for a title that is gone says so`.
- **A show with several seasons on the Mac:** the season picker appears and defaults to the season of the next episode.
  → Task 2 test `several seasons` and Task 7 test `the season picker`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/rating.mjs` (modify) | `labelOf(stream)`: quality chips from a release name; `verdictOf(r)`; `adviseStreams(..., { onRated })` |
| `src/advisor.mjs` (modify) | `metaFor` keeps background + episode names; `recordTitles` records verdict, chips and a `meta:<imdb>` entry |
| `src/media_text.py` (create) | Reading file names (episode codes, episode titles, release groups) and writing sizes and times for people |
| `src/page_model.py` (create) | The page's model: library (shelf + title details), up next, the chain's four steps, the problem banner, the glance |
| `src/dashboard.py` (modify) | Imports the moved helpers from `media_text`; cache rows carry every video file; ping history; `state()` adds the model |
| `src/dashboard.html` (rewrite) | The page: tokens, layout, regions, sheets, actions |
| `tests/test_media_text.py`, `tests/test_page_model.py` (create) | Unit tests |
| `tests/dashboard-page.test.mjs` (create) | The page's script run against a stand-in browser |
| `tests/advisor.test.mjs`, `tests/rating.test.mjs`, `tests/test_dashboard.py` (modify) | Tests for the changed parts |
| `install.sh` (modify), `README.md` (modify) | Install the two new Python files; describe the new dashboard |

---

### Task 1: The advisor records verdicts, quality chips, background art and episode names

**Files:**
- Modify: `src/rating.mjs` (`fromName` return value; new exports `labelOf`, `verdictOf`; `adviseStreams` option `onRated`)
- Modify: `src/advisor.mjs:96` (`metas` comment), `:155-165` (`metaFor`), `:169-191` (`recordTitles`), `:268-273` (`streamsFor`)
- Test: `tests/rating.test.mjs`, `tests/advisor.test.mjs`

**Interfaces:**
- Produces (in `~/.stremio-local-addon/stream-titles.json`, read by later tasks):
  - per stream, keys `<ih>` and `<ih>:<fileIdx>`: the existing fields plus
    `verdict: { symbol: '✓✓'|'✓'|'⚠'|'✗', reason: string|null, summary: string[] }` and `label: string[]` (e.g. `['4K','WEB','DV + HDR10+','E-AC3 Atmos','MP4']`);
  - per title, key `meta:<imdb>`: `{ background: string|null, episodes: { '<season>:<episode>': name }, at: number }`.
- `labelOf(stream) -> string[]`, `verdictOf(r) -> { symbol, reason, summary }` exported from `rating.mjs`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/rating.test.mjs` (it already imports from `../src/rating.mjs`; add `labelOf, verdictOf` to that import line):

```js
test('labelOf: the quality chips a release name gives', () => {
  const s = (title, filename) => ({ name: 'Torrentio\n4k', title, behaviorHints: { filename } });
  assert.deepEqual(
    labelOf(s('Obsession.2026.2160p.iT.WEB-DL.UNRATED.DV.HDR10+.MULTi.FRE.LAT.DDP5.1.Atmos.H265.MP4-BEN.THE.MEN\n👤 50 💾 21.7 GB',
      'Obsession.2026.2160p.iT.WEB-DL.UNRATED.DV.HDR10+.MULTi.FRE.LAT.DDP5.1.Atmos.H265.MP4-BEN.THE.MEN.mp4')),
    ['4K', 'WEB', 'DV + HDR10+', 'E-AC3 Atmos', 'MP4']);
  assert.deepEqual(
    labelOf(s('Movie.2026.2160p.UHD.BluRay.x265.10bit.DV.HDR.TrueHD.Atmos.7.1-XYZ', 'Movie.2026.2160p.UHD.BluRay.x265.10bit.DV.HDR.TrueHD.Atmos.7.1-XYZ.mkv')),
    ['4K', 'BluRay', 'DV + HDR10', 'TrueHD Atmos', 'MKV']);
  assert.deepEqual(labelOf(s('Show.S01E01.1080p.WEB.h264', 'Show.S01E01.1080p.WEB.h264.mkv')), ['1080p', 'WEB', 'MKV']);
});

test('verdictOf: the symbol, the short reason for ⚠ and ✗, and the summary without the peer count', () => {
  const bad = { stars: 1, verified: false, issues: [{ cap: 1, why: 'TrueHD audio', short: "TrueHD: TV can't decode" }], summary: ['MKV', 'TrueHD', '266 peers'] };
  assert.deepEqual(verdictOf(bad), { symbol: '✗', reason: "TrueHD: TV can't decode", summary: ['MKV', 'TrueHD'] });
  const good = { stars: 5, verified: true, issues: [], summary: ['MKV', 'E-AC3', 'DV P8', '26 Mbps'] };
  assert.deepEqual(verdictOf(good), { symbol: '✓✓', reason: null, summary: ['MKV', 'E-AC3', 'DV P8', '26 Mbps'] });
});
```

In `tests/advisor.test.mjs`, make the Cinemeta stand-in in `setup()` return background art and episodes — replace the line
`return reply(200, { meta: { runtime: '109 min', name: 'Chernobyl', ... } });` with:

```js
      return reply(200, { meta: {
        runtime: '109 min', name: 'Chernobyl', releaseInfo: '2019', poster: 'https://images.metahub.space/poster/medium/tt7366338/img',
        background: 'https://images.metahub.space/background/medium/tt7366338/img',
        videos: [{ season: 1, episode: 1, name: '1:23:45' }, { season: 1, episode: 2, name: 'Please Remain Calm' }, { season: 0, episode: 1, title: 'Special' }],
      } });
```

Replace the test `which title each torrent belongs to is recorded for the dashboard` with:

```js
test('which title each torrent belongs to is recorded for the dashboard', async () => {
  const { advisor, files, cleanup } = setup({ config: CONFIGURED });
  await advisor.route('/advisor/stream/series/tt7366338:1:2.json');
  const titles = JSON.parse(fs.readFileSync(files.titles, 'utf8'));
  const { at, verdict, label, ...rest } = titles[IH_GOOD];
  assert.deepEqual(rest, {
    imdb: 'tt7366338', type: 'series', name: 'Chernobyl', year: '2019', season: 1, episode: 2,
    poster: 'https://images.metahub.space/poster/medium/tt7366338/img',
  });
  assert.equal(typeof at, 'number');
  assert.ok(verdict && label);
  assert.equal(titles[`${IH_GOOD}:0`].episode, 2, 'per file too: a season pack shares its hash across episodes');
  assert.equal(titles[`${IH_BAD}:4`].episode, 2);
  cleanup();
});

test('each stream\'s verdict and quality chips, and the title\'s art and episode names, are recorded for the dashboard', async () => {
  const { advisor, files, cleanup } = setup({ config: CONFIGURED });
  await advisor.route('/advisor/stream/series/tt7366338:1:2.json');
  const titles = JSON.parse(fs.readFileSync(files.titles, 'utf8'));
  const bad = titles[`${IH_BAD}:4`];
  assert.equal(bad.verdict.symbol, '✗');
  assert.match(bad.verdict.reason, /^TrueHD: TV can't decode/);
  assert.ok(!bad.verdict.summary.some((x) => / peers$/.test(x)), 'the peer count goes stale; it is left out');
  assert.deepEqual(bad.label, ['4K', 'BluRay', 'DV + HDR10', 'TrueHD Atmos', 'MKV']);
  assert.deepEqual(titles[`${IH_GOOD}:0`].label, ['4K', 'WEB', 'DV + HDR10', 'MKV']);
  const { at, ...meta } = titles['meta:tt7366338'];
  assert.deepEqual(meta, {
    background: 'https://images.metahub.space/background/medium/tt7366338/img',
    episodes: { '1:1': '1:23:45', '1:2': 'Please Remain Calm', '0:1': 'Special' },
  });
  assert.equal(typeof at, 'number');
  cleanup();
});
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `node --test 2>&1 | grep -E "^not ok|^# (pass|fail)"`
Expected: FAIL — `labelOf is not a function` (rating tests) and the two advisor tests (no `verdict`, no `meta:` entry).

- [ ] **Step 3: Implement**

In `src/rating.mjs`, `fromName`: right after `const discRemux = ...;` nothing changes; in its `return { ... }` object add two fields after `audio,`:

```js
    source: discRemux ? 'REMUX' : has('bluray') ? 'BluRay' : has('web') ? 'WEB' : null,
    atmos: has('atmos'),
```

After `symbolOf` (and before `function reason`), add:

```js
const RESOLUTION_LABEL = { '4320p': '8K', '2160p': '4K', '1080p': '1080p', '720p': '720p' };

/** What a release is, in a few chips for the dashboard: resolution, source, picture, the first audio track, container. */
export function labelOf(stream) {
  const n = fromName(stream);
  const picture = n.video.dv != null ? (n.hdr ? `DV + ${n.hdr}` : 'DV') : n.hdr;
  const audio = n.audio[0] ? `${audioLabel(n.audio[0])}${n.atmos ? ' Atmos' : ''}` : null;
  return [RESOLUTION_LABEL[n.resolution], n.source, picture, audio, n.container?.toUpperCase()].filter(Boolean);
}
```

After `function reason(r) {...}` add:

```js
/** A rating as the dashboard shows it: the symbol, the reason for a ⚠ or ✗, and what the file is (without the
 * peer count, which goes stale). */
export function verdictOf(r) {
  return { symbol: symbolOf(r), reason: r.stars <= 4 ? reason(r) : null, summary: r.summary.filter((x) => !/ peers$/.test(x)) };
}
```

In `adviseStreams`, add `onRated = () => {}` to the destructured options and call it right after rating:

```js
export function adviseStreams(streams, {
  profile, runtimeMin = null, factsFor = () => null, sort = 'original', display, cachedFor = () => null, onRated = () => {},
} = {}) {
  const rated = streams.filter((s) => s && typeof s === 'object').map((s) => {
    // One stream this code can't read is passed through as it came, not allowed to sink the list.
    try {
      const r = rate(s, { profile, runtimeMin, facts: factsFor(s) });
      onRated(s, r);
      return { s: decorate(s, r, { style: display, cached: cachedFor(s) }), stars: r.stars };
    } catch {
      return { s, stars: 0 };
    }
  });
```

In `src/advisor.mjs`: change the import to
`import { adviseStreams, defaultProfile, labelOf, mergeProfile, starsIn, verdictOf } from './rating.mjs';`,
the `metas` comment to `// imdb id -> { runtime, name, year, poster, background, episodes }`, and `metaFor`'s assignment to:

```js
      if (m) {
        meta = {
          runtime: parseRuntime(m.runtime), name: m.name ?? null, year: m.releaseInfo ?? m.year ?? null, poster: m.poster ?? null,
          background: m.background ?? null,
          episodes: Object.fromEntries((m.videos ?? [])
            .filter((v) => Number.isInteger(v.season) && Number.isInteger(v.episode) && (v.name || v.title))
            .map((v) => [`${v.season}:${v.episode}`, v.name || v.title])),
        };
      }
```

Replace `recordTitles` with:

```js
  // The torrent server only sees info hashes; this lets the dashboard name what is playing, say whether each cached
  // release plays here, and show the title's art and episode names. Kept to the most recent few thousand entries.
  function recordTitles(streams, type, id, meta, rated) {
    if (!files.titles) return;
    const [imdb, season, episode] = id.split(':');
    let titles = {};
    try { titles = JSON.parse(fs.readFileSync(files.titles, 'utf8')); } catch { /* first time */ }
    const at = Date.now();
    const entry = {
      imdb, type, name: meta?.name ?? null, year: meta?.year ?? null,
      season: season ? Number(season) : null, episode: episode ? Number(episode) : null,
      poster: meta?.poster ?? null, at,
    };
    for (const s of streams) {
      if (!s?.infoHash) continue;
      const ih = String(s.infoHash).toLowerCase();
      const r = rated.get(s);
      const e = r ? { ...entry, verdict: verdictOf(r), label: labelOf(s) } : entry;
      titles[ih] = e;
      // A season pack's hash comes up for every episode; the file index says which one this stream is.
      if (Number.isInteger(s.fileIdx)) titles[`${ih}:${s.fileIdx}`] = e;
    }
    if (meta) titles[`meta:${imdb}`] = { background: meta.background ?? null, episodes: meta.episodes ?? {}, at };
    const keep = Object.entries(titles).sort((a, b) => b[1].at - a[1].at).slice(0, 6000);
    try {
      fs.writeFileSync(`${files.titles}.tmp`, JSON.stringify(Object.fromEntries(keep)));
      fs.renameSync(`${files.titles}.tmp`, files.titles);
    } catch (e) { log(`advisor: could not save stream titles: ${e.message}`); }
  }
```

In `streamsFor`, collect the ratings and pass them on:

```js
    const factsFor = factsLookup(readFacts()?.facts ?? {});
    const rated = new Map();
    const out = adviseStreams(all, {
      profile: profile(), runtimeMin: runtime, factsFor, sort: cfg.sort, display: cfg.display, cachedFor: cachedLookup(onMac),
      onRated: (s, r) => rated.set(s, r),
    });
    if (cfg.probe.enabled) probeTop(all, factsFor, cfg);
    recordTitles(all, type, id, meta, rated);
```

- [ ] **Step 4: Run the tests**

Run: `node --test 2>&1 | grep -E "^# (tests|pass|fail)"`
Expected: `# fail 0`, 66 tests (63 + 3).

- [ ] **Step 5: Commit**

```bash
git add src/rating.mjs src/advisor.mjs tests/rating.test.mjs tests/advisor.test.mjs
git commit -m "Record each stream's verdict and chips, and each title's art and episode names, for the dashboard"
```

---

### Task 2: `media_text.py` and the library model (shelf + title details)

**Files:**
- Create: `src/media_text.py`, `src/page_model.py`
- Modify: `src/dashboard.py` (move `VIDEO`, `SAMPLE`, `EPISODE`, `_episode`, `episode_label`, `next_episode`, `clock` out; import them back;
  `cache_entries` adds `videos`)
- Test: `tests/test_media_text.py`, `tests/test_page_model.py`, `tests/test_dashboard.py` (one new assertion)

**Interfaces:**
- Produces in `media_text`: `MB`, `GB`, `VIDEO`, `SAMPLE`, `EPISODE`, `episode_key(name) -> (season, episode)|None`,
  `episode_label(name) -> str`, `episode_title(name) -> str|None`, `release_group(name) -> str|None`, `next_episode(files, current_idx=None)`,
  `clock(seconds) -> 'M:SS'|'H:MM:SS'`, `spoken(seconds) -> '4 min 20 s'`, `size(bytes) -> '3.8 GB'|'480 MB'`.
- Produces in `page_model`: `ACTIVE`, `title_key(ih, titles) -> str`, `library(rows, titles, jobs) -> (shelf_items, details)`.
  - shelf item: `{key, title, kind, poster, summary, sub, last_used, bytes}`.
  - details[key]: `{key, title, kind ('series'|'movie'), year, poster, backdrop, bytes, last_used, in_use, ihs, meta_line, summary,
    sub, seasons: [int], episodes: [Episode], releases: [Release]}`;
    Episode `{season, episode, label, name, file, ih, idx, length, done, busy, job, release, tag, next}`;
    Release `{ih, idx, name, group, chips, verdict, length, done, bytes, last_used, in_use, busy, job}`.
- Consumes: cache rows from `dashboard.cache_entries` (now with `videos`: every video file `{idx, name, length, done, busy}`);
  the titles store from Task 1; prefetch jobs `{ih, idx, label, state, done, length, message, mbps, eta_s}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_media_text.py`:

```python
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
```

Create `tests/test_page_model.py` with the shared fixtures and the library tests (later tasks append classes to this file):

```python
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
```

Add to `tests/test_dashboard.py`, in class `Resume`, method `test_cache_rows_carry_the_files_with_data`, after the first `assertEqual`:

```python
            self.assertEqual([(f['name'], round(f['done'], 2)) for f in row['videos']],
                             [('Show.S01E01.mkv', 0.0), ('Show.S01E02.mkv', 0.67)], 'every video, for the title sheets')
```

- [ ] **Step 2: Run them to see them fail**

Run: `~/.stremio-local-addon/venv/bin/python -m unittest tests.test_media_text tests.test_page_model 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'media_text'` (and `page_model`).

- [ ] **Step 3: Implement `media_text.py`**

Create `src/media_text.py`. Move (cut) `VIDEO`, `SAMPLE`, `EPISODE`, `_episode` (renamed `episode_key`), `episode_label`,
`next_episode` and `clock` out of `src/dashboard.py` into it, and add the new helpers:

```python
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
```

followed by the moved `next_episode` (its body unchanged except `_episode(` → `episode_key(`), the moved `clock`, and:

```python
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
```

In `src/dashboard.py`, after the stdlib imports add:

```python
from media_text import EPISODE, SAMPLE, VIDEO, clock, episode_key, episode_label, next_episode  # noqa: F401 (re-exported for tests)
```

delete the moved definitions, and replace the one remaining use of `_episode(` (none should remain after moving `next_episode`)
— check with `grep -n "_episode(\|^def clock\|^VIDEO\|^EPISODE\|^SAMPLE" src/dashboard.py` (expected: no output).
In `cache_entries`, add `'videos'` to the row dict, next to `'files'`:

```python
                     'videos': [f for f in sorted(files, key=lambda f: f['idx']) if VIDEO.search(f['name'])],
```

- [ ] **Step 4: Implement the library model**

Create `src/page_model.py`:

```python
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
```

(`statistics`, `time`, `GB`, `MB`, `clock`, `episode_label`, `spoken` are used by Tasks 3–5; the imports stay.)

- [ ] **Step 5: Run the tests**

Run: `~/.stremio-local-addon/venv/bin/python -m unittest discover -s tests 2>&1 | tail -3; /usr/bin/python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK` on both (the second with `(skipped=1)`).

- [ ] **Step 6: Commit**

```bash
git add src/media_text.py src/page_model.py src/dashboard.py tests/test_media_text.py tests/test_page_model.py tests/test_dashboard.py
git commit -m "Model the titles on this Mac for the dashboard: one card per title, every episode or release in its sheet"
```

---

### Task 3: Up next

**Files:**
- Modify: `src/page_model.py` (add `up_next`)
- Test: `tests/test_page_model.py` (append class `UpNext`)

**Interfaces:**
- Consumes: `library()` details (Task 2); sessions as `dashboard.Dashboard` builds them (live first, then most recently ended;
  each `{ih, idx, live, title, poster, next: {idx, label, name, length, done}|None}`).
- Produces: `up_next(sessions, details, titles, jobs) -> None | {key, ih, idx, title, season, episode, label, name, length, done,
  job, state ('not_here'|'queued'|'starting'|'downloading'|'waiting'|'failed'|'ready'), poster, backdrop}`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_page_model.py` (above `if __name__`):

```python
def session(idx=2, live=True, nxt=3):
    files = {3: ('Chernobyl - S01E04 - The Happiness of All Mankind ' + ACCID, 6.4)}
    n = {'idx': nxt, 'label': 'S01E04', 'name': files[3][0], 'length': int(files[3][1] * GB), 'done': 0.0} if nxt is not None else None
    return {'ih': PACK, 'idx': idx, 'live': live, 'title': 'Chernobyl', 'poster': 'P', 'next': n}


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
```

- [ ] **Step 2: Run to see it fail**

Run: `~/.stremio-local-addon/venv/bin/python -m unittest tests.test_page_model 2>&1 | tail -3`
Expected: FAIL — `AttributeError: module 'page_model' has no attribute 'up_next'`.

- [ ] **Step 3: Implement** — append to `src/page_model.py`:

```python
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
```

- [ ] **Step 4: Run the tests** — same command. Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add src/page_model.py tests/test_page_model.py
git commit -m "Pick the dashboard's Up next episode: after what plays, else what played last, else the furthest one on the Mac"
```

---

### Task 4: The chain from the swarm to the TV, and the problem banner

**Files:**
- Modify: `src/page_model.py` (add `swarm_step`, `mac_step`, `wifi_step`, `tv_step`, `problem`, `usual_ping`)
- Test: `tests/test_page_model.py` (append class `Chain`)

**Interfaces:**
- Consumes: the live session summary (`dashboard.summarize_session` output plus `health`), prefetch jobs, `Dashboard.slow` readings
  (`mac_wifi`, `tv_ping`, `tv`), the server figures (`cpu`, `rss_mb`, `uptime_s`, `download_cap_mbps`, `learning`), the cache
  figures (`used`, `budget`, `free`), `Dashboard._library_view()` (`None` or `{movies, shows, other, files, advisor, account}`).
- Produces: each step `{id ('swarm'|'mac'|'wifi'|'tv'), name, level ('idle'|'good'|'warn'|'bad'), line, reason, advice, details: [[k, v]], services?}`;
  `services` (mac only): `[{name, level, text}]`; `problem(steps) -> None | {level, title, advice, step}`;
  `usual_ping(history, now) -> float|None` with `history` a sequence of `(time, ms)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_page_model.py`:

```python
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
```

- [ ] **Step 2: Run to see it fail** — `~/.stremio-local-addon/venv/bin/python -m unittest tests.test_page_model 2>&1 | tail -3`
Expected: FAIL — no attribute `swarm_step`.

- [ ] **Step 3: Implement** — append to `src/page_model.py`:

```python
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
```

- [ ] **Step 4: Run the tests** — `~/.stremio-local-addon/venv/bin/python -m unittest tests.test_page_model 2>&1 | tail -3; /usr/bin/python3 -m unittest tests.test_page_model 2>&1 | tail -3`
Expected: `OK` on both.

- [ ] **Step 5: Commit**

```bash
git add src/page_model.py tests/test_page_model.py
git commit -m "Model the path from the swarm to the TV, each step's problem, and the one worth a banner"
```

---

### Task 5: The glance, and `/ui/api/state` serving the model

**Files:**
- Modify: `src/page_model.py` (add `glance`, `recent`)
- Modify: `src/dashboard.py` (`Dashboard.__init__`, `_ping`, `_state`)
- Test: `tests/test_page_model.py` (append class `Glance`), `tests/test_dashboard.py` (append to class `DownloadForLater`)

**Interfaces:**
- Produces `glance(s, titles) -> {ih, idx, title, subtitle, poster, backdrop, level, word, ahead, complete, position, ahead_end, strip,
  seeks, elapsed, remaining, duration_s, stats, chips, verified}`; `recent(s) -> None | {live, unit ('s'|'MB'), history, events}`.
- `/ui/api/state` gains: `problem`, `glance`, `idle_text`, `up_next`, `shelf` (`None` until the cache was read, else
  `{used, budget, free, items, idle_bytes, idle_count}`), `titles` (details by key), `chain` (the four steps in order), `recent`.
  The existing keys stay.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_page_model.py`:

```python
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
```

Append to `tests/test_dashboard.py`, class `DownloadForLater`:

```python
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
```

- [ ] **Step 2: Run to see them fail** — `~/.stremio-local-addon/venv/bin/python -m unittest tests.test_page_model tests.test_dashboard 2>&1 | tail -3`
Expected: FAIL — no attribute `glance`; `KeyError: 'chain'`.

- [ ] **Step 3: Implement the glance** — append to `src/page_model.py`:

```python
def _word(h):
    text = h.get('text', '')
    if h.get('level') == 'bad':
        return 'Stalled'
    for start, word in (('Starting', 'Starting'), ('Buffering', 'Buffering'), ('Buffer is low', 'Low buffer'),
                        ('The player keeps', 'Re-reading')):
        if text.startswith(start):
            return word
    return 'Smooth'


def glance(s, titles):
    """The live stream as the top of the page shows it."""
    t = titles.get(f"{s['ih']}:{s['idx']}") or titles.get(s['ih']) or {}
    meta = titles.get('meta:' + t['imdb'], {}) if t.get('imdb') else {}
    k = episode_key(s['file'])
    if k:
        name = (meta.get('episodes') or {}).get(f'{k[0]}:{k[1]}') or episode_title(s['file'])
        subtitle = ' · '.join(x for x in (f'Season {k[0]}', f'Episode {k[1]}', name) if x)
    else:
        subtitle = s.get('subtitle')
    h = s.get('health') or {}
    pos, dur = s.get('position_s'), s.get('duration_s')
    need = s.get('bitrate_mbps')
    stats = ' · '.join(x for x in (f"{s['download_mbps']:.0f} Mbps", f'file needs {need:.0f}' if need else None,
                                   f"{s['peers']['connected']} peers") if x)
    fixed = bool(s.get('mp4_fix') and s['mp4_fix'].startswith('Hiding'))
    return {'ih': s['ih'], 'idx': s['idx'], 'title': s['title'], 'subtitle': subtitle, 'poster': s.get('poster') or t.get('poster'),
            'backdrop': meta.get('background'), 'level': h.get('level') or 'good', 'word': _word(h),
            'ahead': spoken(s['ahead_s']) if s.get('ahead_s') is not None else size(s['ahead_bytes']),
            'complete': s['complete'], 'position': s['position'], 'ahead_end': s['ahead_end'], 'strip': s['strip'],
            'seeks': [e['pos'] for e in s.get('events') or [] if e['kind'] == 'seek' and e.get('pos') is not None],
            'elapsed': clock(pos) if pos is not None else f"{s['position']:.0%}",
            'remaining': '−' + clock(dur - pos) if dur and pos is not None else None, 'duration_s': dur,
            'stats': stats, 'chips': list(s['chips']) + (['subtitles fixed'] if fixed else []), 'verified': s['verified']}


def recent(s):
    """The buffer's recent history and the stream's events, for the Recent section."""
    if not s:
        return None
    return {'live': s['live'], 'unit': 's' if s.get('ahead_s') is not None else 'MB', 'history': s.get('history') or [],
            'events': s.get('events') or []}
```

- [ ] **Step 4: Serve the model** — in `src/dashboard.py`:

Add `import page_model` after the `media_text` import. In `Dashboard.__init__`, after `self.history = {}` add:

```python
        self.pings = deque(maxlen=720)   # (time, ms) of answered pings to the TV, an hour's worth: the "usually" figure
```

In `_ping`, record answered pings:

```python
    def _ping(self):
        ip = self.tv_ip()
        self.slow['tv_ping'] = dict(parse_ping(_run(['ping', '-c', '3', '-i', '0.2', '-t', '3', ip], 6)), ip=ip) if ip else None
        ms = (self.slow['tv_ping'] or {}).get('ms')
        if ms is not None:
            self.pings.append((time.time(), ms))
```

In `_state`, compute the jobs once (replace the `'prefetch': self.prefetcher.jobs() if self.prefetcher else [],` entry with
`'prefetch': jobs,`) and, right before `return {`, add:

```python
        jobs = self.prefetcher.jobs() if self.prefetcher else []
        rows = self.slow.get('cache_list')
        shelf, details = page_model.library(rows or [], titles, jobs)
        live = next((s for s in out if s['live']), None)
        cache = self.slow.get('cache') or {}
        server = {**(self.slow.get('process') or {}), 'uptime_s': round(now - self.started),
                  'download_cap_mbps': self.config.get('download_limit_mbps'), 'learning': self.config.get('learn_from_plays')}
        chain = [page_model.swarm_step(live, jobs),
                 page_model.mac_step(server, cache, self._library_view(), self.config.get('min_free_gb', 3) * 1024 * MB),
                 page_model.wifi_step(self.slow.get('mac_wifi'), self.slow.get('tv_ping'), page_model.usual_ping(self.pings, now),
                                      self.slow.get('tv'), live is not None),
                 page_model.tv_step(self.slow.get('tv'), live, now)]
```

and add these entries to the returned dict:

```python
            'problem': page_model.problem(chain),
            'glance': page_model.glance(live, titles) if live else None,
            'idle_text': 'Nothing is playing',
            'up_next': page_model.up_next(out, details, titles, jobs),
            'shelf': None if rows is None else {
                'used': cache.get('used'), 'budget': cache.get('budget'), 'free': cache.get('free'), 'items': shelf,
                'idle_bytes': sum(r['bytes'] for r in rows if not r['in_use']), 'idle_count': sum(1 for r in rows if not r['in_use'])},
            'titles': details,
            'chain': chain,
            'recent': page_model.recent(live or (out[0] if out else None)),
```

(`out` is already limited to 4 sessions and each has `next` and `health` from the existing code.)

- [ ] **Step 5: Run all Python tests** — `~/.stremio-local-addon/venv/bin/python -m unittest discover -s tests 2>&1 | tail -3; /usr/bin/python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK` (second: `OK (skipped=1)`).

- [ ] **Step 6: Commit**

```bash
git add src/page_model.py src/dashboard.py tests/test_page_model.py tests/test_dashboard.py
git commit -m "Serve the dashboard's page model: the glance, Up next, the shelf, title sheets, the chain and the banner"
```

---

### Task 6: The new page — tokens, layout, banner, glance

**Files:**
- Rewrite: `src/dashboard.html`
- Create: `tests/dashboard-page.test.mjs`

**Interfaces:**
- Consumes `/ui/api/state` as produced in Task 5.
- Produces top-level page functions later tasks call and test: `$`, `esc`, `lv`, `pct`, `bytes`, `span`, `ago`, `clock`, `put(id, html)`,
  `backdrop(url, poster)`, `poster(url, title, cls)`, `note(key)`, `jobLine(job)`, `problemHTML(p, offline)`, `glanceHTML(g, idleText)`,
  `scrubber(g)`, `render(state)`, `poll()`; globals `STATE`, `notes`.

- [ ] **Step 1: Write the failing page test** — create `tests/dashboard-page.test.mjs`:

```js
// Run with:  node --test   (from the repo root)
// The dashboard page's script, run against a stand-in for the browser: its render functions get states shaped like
// /ui/api/state and their HTML is checked.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('../src/dashboard.html', import.meta.url), 'utf8');
const script = /<script>([\s\S]*)<\/script>/.exec(html)[1];

export function page({ fetch = async () => { throw new Error('offline'); } } = {}) {
  const classList = () => {
    const s = new Set();
    return { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c), toggle: (c, on) => ((on ?? !s.has(c)) ? s.add(c) : s.delete(c)) };
  };
  const els = {};
  const el = (id) => (els[id] ||= { id, innerHTML: '', className: '', hidden: false, open: false, classList: classList(),
    addEventListener() {}, focus() {}, querySelector: () => null, querySelectorAll: () => [] });
  const ctx = {
    document: { getElementById: el, hidden: true, body: { classList: classList() }, addEventListener() {}, querySelector: () => null,
      querySelectorAll: () => [], contains: () => true, activeElement: null },
    location: { hash: '' }, history: { state: null, pushState() {}, back() {} },
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false }), requestAnimationFrame: (f) => f(), setTimeout: () => 0, clearTimeout() {},
    fetch, confirm: () => false, console, Intl, Date, Math, Number, String, JSON, Map, Set, Object, Array, Promise,
    encodeURIComponent, decodeURIComponent,
  };
  vm.createContext(ctx);
  vm.runInContext(script, ctx);
  return { ctx, els };
}

const GB = 1024 ** 3;
export const GLANCE = {
  ih: 'b'.repeat(40), idx: 2, title: 'Chernobyl', subtitle: 'Season 1 · Episode 3 · Open Wide, O Earth', poster: 'P', backdrop: 'BG',
  level: 'good', word: 'Smooth', ahead: '4 min 20 s', complete: false, position: 0.384, ahead_end: 0.45,
  strip: Array(54).fill(1).concat(Array(66).fill(0)), seeks: [0.38], elapsed: '25:10', remaining: '−40:20', duration_s: 3930,
  stats: '31 Mbps · file needs 15 · 38 peers', chips: ['MKV', 'HEVC', '4K'], verified: true,
};
export const STATE = {
  now: 1e9, phone_url: null, problem: null, glance: GLANCE, idle_text: 'Nothing is playing', up_next: null,
  shelf: { used: 4.4 * GB, budget: 15 * GB, free: 22 * GB, items: [], idle_bytes: 0, idle_count: 0 }, titles: {}, chain: [],
  recent: null, log: [], prefetch: [],
};

test('the glance says what plays and how much is ready', () => {
  const { ctx } = page();
  const h = ctx.glanceHTML(GLANCE, 'Nothing is playing');
  for (const s of ['Smooth', 'Chernobyl', 'Open Wide, O Earth', '4 min 20 s', 'ready ahead of you', '25:10', '−40:20', '31 Mbps']) {
    assert.ok(h.includes(s), s);
  }
  assert.match(h, /aria-label="Chernobyl: up to 25:10; 4 min 20 s downloaded ahead"/);
  assert.ok(ctx.glanceHTML({ ...GLANCE, complete: true }, '').includes('The rest is on this Mac'));
  assert.ok(ctx.glanceHTML(null, 'Nothing is playing').includes('Nothing is playing'));
});

test('a problem goes to the banner with its advice', () => {
  const { ctx } = page();
  const h = ctx.problemHTML({ level: 'warn', title: "The TV's Wi-Fi is slow to answer", advice: 'Restarting the TV usually clears it.', step: 'wifi' });
  assert.match(h, /banner-warn/);
  assert.ok(h.includes('The TV&#39;s Wi-Fi is slow to answer') && h.includes('Restarting the TV'));
  assert.equal(ctx.problemHTML(null), '');
});

test('text from torrents is escaped everywhere', () => {
  const { ctx } = page();
  const evil = '<img src=x onerror=alert(1)>"&';
  const h = ctx.glanceHTML({ ...GLANCE, title: evil, subtitle: evil, chips: [evil] }, '') + ctx.problemHTML({ level: 'bad', title: evil, advice: evil });
  assert.ok(!h.includes('<img src=x'));
  assert.ok(h.includes('&lt;img src=x onerror=alert(1)&gt;&quot;&amp;'));
});

test('render fills the regions and writes each only when it changed', () => {
  const { ctx, els } = page();
  ctx.render(STATE);
  assert.ok(els.glance.innerHTML.includes('Chernobyl'));
  assert.ok(els['glance-bg'].innerHTML.includes('src="BG"'));
  const before = els['glance-bg'].innerHTML;
  els['glance-bg'].innerHTML = 'untouched';
  ctx.render({ ...STATE, glance: { ...GLANCE, ahead: '4 min 22 s' } });
  assert.equal(els['glance-bg'].innerHTML, 'untouched', 'the backdrop is not rewritten when only the figures change');
  assert.ok(els.glance.innerHTML.includes('4 min 22 s'));
  assert.ok(before);
});

test('a failed poll keeps the page and says the server is unreachable', async () => {
  const { ctx, els } = page();
  ctx.render(STATE);
  await ctx.poll();
  assert.ok(els.glance.innerHTML.includes('Chernobyl'), 'the last render stays');
  assert.match(els.problem.innerHTML, /Can't reach the torrent server/);
});
```

- [ ] **Step 2: Run to see it fail** — `node --test tests/dashboard-page.test.mjs 2>&1 | grep -E "^not ok|# fail"`
Expected: FAIL — `ctx.glanceHTML is not a function` (the old page has no such function).

- [ ] **Step 3: Write the page** — replace `src/dashboard.html` entirely:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#0b1320" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#EEF2F8" media="(prefers-color-scheme: light)">
<title>Stream dashboard</title>
<style>
:root {
  --bg: #0b1320; --raise: #111c2e; --raise-2: #16243A; --rule: #1d2b42; --track: #1d2b42;
  --ink: #EAF0F8; --ink-2: #B4C2D6; --muted: #8193AD;
  --have: rgba(234, 240, 248, .28); --ahead: #129680; --accent: #7FA6FF; --space: #6A8FE8;
  --good: #4CC38A; --warn: #F2B53A; --bad: #FF6B6B; --idle: #61728c;
  --veil: 11, 19, 32;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #EEF2F8; --raise: #FFFFFF; --raise-2: #F4F7FB; --rule: #D3DCE9; --track: #E3E9F2;
    --ink: #0F1B2D; --ink-2: #33445E; --muted: #5A6B85;
    --have: rgba(15, 27, 45, .22); --ahead: #0E9682; --accent: #3D6CC7; --space: #3D6CC7;
    --good: #1E8E5A; --warn: #A86A00; --bad: #C93C3C; --idle: #8A97AB;
    --veil: 238, 242, 248;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.45 var(--sans);
  padding: max(10px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) max(24px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)); }
body.sheet-open { overflow: hidden; }
h1, h2, p, ol, ul, dl, dd { margin: 0; }
ul, ol { list-style: none; padding: 0; }
button, select { font: inherit; color: inherit; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 6px; }
.muted { color: var(--muted); } .small { font-size: 13px; }
.lv-good { color: var(--good); } .lv-warn { color: var(--warn); } .lv-bad { color: var(--bad); } .lv-idle { color: var(--idle); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.eyebrow { font-size: 12px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: var(--muted); }

.page { max-width: 1320px; margin: 0 auto; }
.layout { display: grid; gap: 18px; }
.main-col { display: grid; gap: 18px; min-width: 0; }
.pair { display: grid; gap: 18px; }
.side { display: grid; gap: 14px; min-width: 0; }
@media (min-width: 760px) { .pair { grid-template-columns: 1fr 1.25fr; align-items: start; } .pair.solo { grid-template-columns: 1fr; } }
@media (min-width: 1100px) { .layout { grid-template-columns: 1.65fr 1fr; align-items: start; } .side { position: sticky; top: 12px; } }

/* the banner: the worst problem, or the server being unreachable */
.banner { display: flex; gap: 10px; align-items: flex-start; border-radius: 14px; padding: 10px 14px; margin-bottom: 14px; }
.banner p { color: var(--ink-2); font-size: 13.5px; margin-top: 2px; }
.banner-warn { background: color-mix(in srgb, var(--warn) 15%, transparent); }
.banner-bad { background: color-mix(in srgb, var(--bad) 15%, transparent); }

/* cinematic panels: a soft backdrop under a gradient into the surface */
.cine { position: relative; overflow: hidden; border-radius: 20px; background: var(--raise); isolation: isolate; }
.bd-wrap { position: absolute; inset: 0; z-index: -1; overflow: hidden; }
.bd-wrap .bd { position: absolute; inset: -40px; width: calc(100% + 80px); height: calc(100% + 80px); object-fit: cover;
  filter: blur(18px) saturate(1.15); opacity: .55; }
.bd-wrap .bd-poster { filter: blur(30px) saturate(1.2); }
.bd-wrap::after { content: ""; position: absolute; inset: 0;
  background: linear-gradient(180deg, rgba(var(--veil), .2) 0%, rgba(var(--veil), .8) 55%, rgb(var(--veil)) 100%); }

/* the glance */
.glance.idle { background: none; }
.glance.live { min-height: 300px; }
.g-body { position: relative; padding: 18px 16px 16px; display: flex; flex-direction: column; min-height: inherit; }
.g-status { font-size: 13px; color: var(--ink-2); }
.g-title { font-size: 34px; line-height: 1.05; font-weight: 800; letter-spacing: -.02em; margin-top: auto; padding-top: 64px; overflow-wrap: anywhere; }
.g-sub { color: var(--ink-2); margin-top: 4px; }
.g-ahead { font-size: 17px; margin-top: 14px; color: var(--ink-2); } .g-ahead b { color: var(--ink); }
.scrub { position: relative; margin-top: 18px; touch-action: none; cursor: crosshair; }
.scrub svg { display: block; width: 100%; height: 12px; }
.scrub .head { position: absolute; top: -4px; width: 4px; height: 16px; border-radius: 2px; background: var(--ink); transform: translateX(-2px); }
.scrub-axis { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); margin-top: 4px; font-variant-numeric: tabular-nums; }
.scrub-axis span:first-child { color: var(--ink); }
.tip { position: absolute; bottom: 34px; transform: translateX(-50%); background: var(--bg); color: var(--ink); border: 1px solid var(--rule);
  border-radius: 6px; padding: 2px 8px; font-size: 12px; white-space: nowrap; pointer-events: none; }
.g-stats { font-size: 13px; color: var(--muted); margin-top: 12px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.chips li { font-size: 12px; padding: 2px 9px; border-radius: 999px; background: color-mix(in srgb, var(--ink) 9%, transparent); color: var(--ink-2); }
.chips li.proof { background: none; color: var(--muted); padding-left: 2px; }
.idle-line { font-size: 15px; color: var(--ink-2); padding: 4px 2px 2px; }
@media (min-width: 760px) { .glance.live { min-height: 340px; } .g-body { padding: 22px 24px 20px; } .g-title { font-size: 44px; } }

/* buttons, bars, notes */
.bar { display: block; height: 5px; border-radius: 3px; background: var(--track); overflow: hidden; }
.bar b { display: block; height: 100%; border-radius: 3px; background: var(--accent); }
.bar.space b { background: var(--space); }
button.solid, button.ghost { min-height: 40px; padding: 6px 16px; border-radius: 999px; cursor: pointer; font-size: 14px; font-weight: 600; white-space: nowrap; }
button.solid { background: var(--ink); color: var(--bg); border: 0; }
button.ghost { background: none; color: var(--ink-2); border: 1px solid var(--rule); }
button.ghost:hover { border-color: var(--ink-2); }
button.danger { color: var(--bad); } button.danger:hover { border-color: var(--bad); }
button.wide { width: 100%; margin-top: 12px; }
.note { font-size: 13px; color: var(--ink-2); margin-top: 8px; }

/* up next */
.un-body { position: relative; padding: 16px; }
.un-show { color: var(--ink-2); font-size: 13px; margin-top: 10px; }
.un-ep { font-size: 21px; font-weight: 800; line-height: 1.15; margin: 2px 0 10px; overflow-wrap: anywhere; }
.un-body button { margin-top: 10px; }
.un-body .bar { margin-top: 4px; }

/* the shelf */
.sh-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.sh-head .muted { margin-left: auto; }
.shelf-sec .bar.space { margin: 8px 0 14px; }
.shelf { display: flex; gap: 12px; overflow-x: auto; scroll-snap-type: x proximity; padding-bottom: 6px; }
.tile { display: flex; flex-direction: column; align-items: flex-start; width: 124px; flex: none; background: none; border: 0; padding: 0;
  text-align: left; cursor: pointer; scroll-snap-align: start; border-radius: 10px; }
.poster { width: 124px; aspect-ratio: 2 / 3; border-radius: 10px; object-fit: cover; background: var(--raise-2); display: block; }
.poster.ph { display: grid; place-items: center; font: 800 28px var(--sans); color: var(--muted); }
.t-name { display: block; font-weight: 700; font-size: 14px; margin-top: 7px; overflow-wrap: anywhere; }
.t-sum, .t-sub { display: block; font-size: 12px; color: var(--muted); line-height: 1.35; }
.t-sub { color: var(--accent); }

/* title sheets */
.sheet { position: fixed; inset: 0; z-index: 10; }
.sheet[hidden] { display: none; }
.sheet-scrim { position: absolute; inset: 0; background: rgba(0, 0, 0, .5); }
.sheet-panel { position: absolute; left: 0; right: 0; bottom: 0; top: 6vh; background: var(--bg); border-radius: 20px 20px 0 0; overflow-y: auto; isolation: isolate; }
@media (min-width: 760px) { .sheet-panel { left: auto; top: 0; width: 480px; border-radius: 0; } }
@media (prefers-reduced-motion: no-preference) {
  .sheet.in .sheet-panel { animation: rise .22s ease-out; }
  @media (min-width: 760px) { .sheet.in .sheet-panel { animation-name: slide; } }
}
@keyframes rise { from { transform: translateY(40px); opacity: .6; } }
@keyframes slide { from { transform: translateX(40px); opacity: .6; } }
.sheet-panel .bd-wrap { height: 260px; bottom: auto; }
.sh-top { position: relative; padding: 10px 16px 6px; }
.back { background: none; border: 0; color: var(--ink-2); font-size: 15px; min-height: 40px; padding: 0 6px 0 0; cursor: pointer; }
.sh-id { display: flex; gap: 14px; align-items: flex-end; margin-top: 26px; }
.sh-id .poster { width: 72px; }
.sh-id h2 { font-size: 28px; line-height: 1.05; font-weight: 800; overflow-wrap: anywhere; }
.sh-meta { color: var(--ink-2); font-size: 13px; margin-top: 4px; }
.sh-seasons { padding: 12px 16px 0; display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--muted); }
select { font-weight: 600; color: var(--ink); background: var(--raise); border: 1px solid var(--rule); border-radius: 10px; padding: 6px 10px; min-height: 40px; }
.eps { padding: 6px 16px 8px; }
.ep { display: grid; grid-template-columns: 30px 1fr auto; gap: 2px 10px; align-items: center; padding: 12px 0; border-top: 1px solid var(--rule); }
.ep-n { font-weight: 700; color: var(--muted); font-variant-numeric: tabular-nums; grid-row: 1 / span 2; align-self: start; padding-top: 1px; }
.ep.here .ep-n { color: var(--ink); }
.ep-t { font-weight: 650; font-size: 14px; overflow-wrap: anywhere; }
.ep-s { grid-column: 2; font-size: 12.5px; color: var(--muted); }
.ep-s .bar { margin: 5px 0 4px; max-width: 200px; }
.ep-a { grid-column: 3; grid-row: 1 / span 2; display: flex; gap: 6px; }
.ep-a button { min-height: 36px; padding: 4px 12px; font-size: 13px; }
.ep .note { grid-column: 2 / -1; }
.tag { font-size: 11px; font-weight: 500; color: var(--muted); border: 1px solid var(--rule); border-radius: 999px; padding: 0 6px; }
.rels { padding: 8px 16px 8px; display: grid; gap: 10px; }
.rel { background: var(--raise); border-radius: 14px; padding: 12px 14px; }
.rel-h { display: flex; align-items: baseline; gap: 10px; }
.rel-h b { font-size: 15px; overflow-wrap: anywhere; }
.rel-h span { margin-left: auto; font-size: 12px; color: var(--muted); white-space: nowrap; }
.rel .chips li { font-size: 11px; }
.verdict { font-size: 13px; margin-top: 8px; color: var(--ink-2); }
.sym { font-weight: 700; } .sym-good { color: var(--good); } .sym-warn { color: var(--warn); } .sym-bad { color: var(--bad); }
.rel .bar { margin: 10px 0 4px; }
.rel-f { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; } .rel-f .muted { font-size: 12.5px; flex: 1; }
.rel-f button { min-height: 36px; padding: 4px 12px; font-size: 13px; }
.sheet-foot { padding: 4px 16px 28px; }

/* the chain from the swarm to the TV */
.side-card { background: var(--raise); border-radius: 18px; padding: 16px; }
.chain { margin-top: 12px; }
.step { position: relative; padding: 0 0 16px 28px; }
.step::before { content: ""; position: absolute; left: 7px; top: 20px; bottom: -2px; width: 2px; background: var(--rule); }
.step:last-child { padding-bottom: 0; } .step:last-child::before { display: none; }
.dot { position: absolute; left: 0; top: 3px; width: 16px; height: 16px; border-radius: 50%; display: grid; place-items: center; font-size: 10px; font-weight: 800; color: var(--bg); }
.dot-good { background: var(--good); } .dot-warn { background: var(--warn); } .dot-bad { background: var(--bad); } .dot-idle { background: var(--idle); }
.step-head { background: none; border: 0; padding: 0; cursor: pointer; font-weight: 650; font-size: 14.5px; min-height: 24px; text-align: left; }
.step-head::after { content: " ▸"; color: var(--muted); font-weight: 400; } .step-head[aria-expanded="true"]::after { content: " ▾"; }
.step-warn .step-name { color: var(--warn); } .step-bad .step-name { color: var(--bad); }
.step-reason { font-size: 13px; margin-top: 2px; }
.step-line { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.svc { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin-top: 8px; font-size: 12.5px; color: var(--ink-2); }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin-top: 10px; font-size: 12.5px; }
.kv dt { color: var(--muted); } .kv dd { text-align: right; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }

/* recent and the log */
details.recent, details.log { border-top: 1px solid var(--rule); padding-top: 12px; }
details summary { cursor: pointer; min-height: 32px; font-weight: 650; }
.spark { position: relative; margin-top: 6px; } .spark svg { display: block; width: 100%; height: 56px; touch-action: none; }
.spark .label { font-size: 12.5px; color: var(--muted); }
.spark .tip { bottom: auto; top: 0; }
.cross { position: absolute; top: 22px; bottom: 0; width: 1px; background: var(--ink-2); pointer-events: none; }
.events li { display: grid; grid-template-columns: 1.4em 1fr auto; gap: 6px; padding: 7px 0; border-top: 1px solid var(--rule); font-size: 13px; }
.events .why { display: block; color: var(--muted); font-size: 12px; } .events time { color: var(--muted); font-size: 12px; }
details.log { margin-top: 22px; }
.log ol { margin-top: 8px; font: 12px/1.55 var(--mono); color: var(--ink-2); }
.log li { display: grid; grid-template-columns: 5.5em 1fr; gap: 8px; overflow-wrap: anywhere; }
.log time { color: var(--muted); }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
</head>
<body>
<div class="page">
  <div id="problem" aria-live="polite"></div>
  <main class="layout">
    <div class="main-col">
      <section class="glance idle" id="glance-wrap" aria-label="Now playing"><div class="bd-wrap" id="glance-bg"></div><div id="glance"></div></section>
      <div class="pair solo" id="pair">
        <section class="cine upnext" id="upnext-wrap" aria-label="Up next" hidden><div class="bd-wrap" id="upnext-bg"></div><div id="upnext"></div></section>
        <section class="shelf-sec" id="shelf" aria-label="On this Mac"></section>
      </div>
    </div>
    <aside class="side">
      <section class="side-card" id="chain" aria-label="From the swarm to your TV"></section>
      <div id="recent"></div>
    </aside>
  </main>
  <details class="log" id="log"><summary>Server log</summary><ol id="loglines"></ol></details>
</div>
<div class="sheet" id="sheet" hidden role="dialog" aria-modal="true" aria-labelledby="sheet-title">
  <div class="sheet-scrim" data-close></div>
  <div class="sheet-panel" id="sheet-panel">
    <div class="bd-wrap" id="sheet-bg"></div>
    <div id="sheet-head"></div><div id="sheet-seasons"></div><div id="sheet-body"></div>
  </div>
</div>
<script>
'use strict';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const MB = 1024 * 1024;
const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const MARK = { good: '●', warn: '▲', bad: '■', idle: '○' };
const ACTIVE = ['queued', 'starting', 'downloading', 'waiting'];
const lv = (level, text) => `<span class="lv-${level}" aria-hidden="true">${MARK[level] || MARK.idle}</span>${text ? ` ${esc(text)}` : ''}`;
const pct = (f) => Math.floor((f || 0) * 100);
const bytes = (b) => b == null ? '—' : b >= 1024 * MB ? `${nf.format(b / 1024 / MB)} GB` : `${Math.round(b / MB)} MB`;
const span = (s) => s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.floor(s / 3600)} h ${Math.round(s / 60) % 60} min`;
const ago = (s) => s == null ? '' : s < 10 ? 'just now' : s < 60 ? `${Math.round(s)} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago`
  : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
function clock(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

let STATE = null, lastOk = 0, timer = null;
const notes = {};   // key -> { text, at }: the answer to the last request about it, shown for 15 s

function note(key) {
  const n = notes[key];
  return n && Date.now() - n.at < 15000 ? `<p class="note" role="status">${esc(n.text)}</p>` : '';
}

// Write a region only when its HTML changed, so images aren't recreated and nothing flickers on every poll.
function put(id, html) {
  const el = $(id);
  if (el && el.__html !== html) { el.innerHTML = html; el.__html = html; }
}

function backdrop(url, poster) {
  const src = url || poster;
  return src ? `<img class="bd${url ? '' : ' bd-poster'}" src="${esc(src)}" alt="" referrerpolicy="no-referrer">` : '';
}

function poster(url, title, cls = 'poster') {
  return url ? `<img class="${cls}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : `<span class="${cls} ph" aria-hidden="true">${esc((title || '?').trim()[0] || '?')}</span>`;
}

function jobLine(j) {
  if (!j) return '';
  if (j.state === 'queued') return 'Queued after the current download';
  if (j.state === 'starting') return 'Starting';
  if (j.state === 'waiting') return j.message || 'Waiting';
  if (j.mbps === 0) return 'Nothing is arriving right now';
  return [`${pct(j.done)}%`, j.mbps != null ? `${nf.format(j.mbps)} Mbps` : null, j.eta_s != null ? `about ${span(j.eta_s)} left` : null,
    j.message].filter(Boolean).join(' · ');
}

// ---------------------------------------------------------------- the banner and the glance

function problemHTML(p, offline) {
  if (offline) {
    return `<div class="banner banner-bad" role="alert">${lv('bad', '')}<div><b>Can't reach the torrent server</b><p>${esc(offline)}</p></div></div>`;
  }
  if (!p) return '';
  return `<div class="banner banner-${p.level}" role="alert">${lv(p.level, '')}<div><b>${esc(p.title)}</b>${p.advice ? `<p>${esc(p.advice)}</p>` : ''}</div></div>`;
}

function scrubber(g) {
  const W = 1000, H = 8, n = g.strip.length;
  let r = `<rect width="${W}" height="${H}" rx="4" fill="var(--track)"/>`;
  for (let i = 0; i < n;) {
    if (g.strip[i] < 0.5) { i++; continue; }
    let j = i;
    while (j + 1 < n && g.strip[j + 1] >= 0.5) j++;
    r += `<rect x="${(i / n * W).toFixed(1)}" width="${((j - i + 1) / n * W).toFixed(1)}" height="${H}" rx="4" fill="var(--have)"/>`;
    i = j + 1;
  }
  const x0 = g.position * W, x1 = Math.max(x0, g.ahead_end * W);
  if (x1 - x0 > 0.5) r += `<rect x="${x0.toFixed(1)}" width="${(x1 - x0).toFixed(1)}" height="${H}" fill="var(--ahead)"/>`;
  for (const p of g.seeks || []) r += `<rect x="${(p * W - 1).toFixed(1)}" y="${H + 1}" width="2" height="3" fill="var(--muted)"/>`;
  const label = `${g.title}: up to ${g.elapsed}; ${g.complete ? 'the rest is on this Mac' : `${g.ahead} downloaded ahead`}`;
  return `<div class="scrub" data-scrub>
    <svg viewBox="0 0 ${W} 12" preserveAspectRatio="none" role="img" aria-label="${esc(label)}">${r}</svg>
    <span class="head" style="left:${(g.position * 100).toFixed(2)}%"></span>
    <div class="scrub-axis"><span>${esc(g.elapsed)}</span><span>${esc(g.remaining || '')}</span></div>
    <span class="tip" id="scrub-tip" hidden></span></div>`;
}

function glanceHTML(g, idleText) {
  if (!g) return `<p class="idle-line">${lv('idle', idleText || 'Nothing is playing')}</p>`;
  const ahead = g.complete ? 'The rest is on this Mac' : `<b>${esc(g.ahead)}</b> ready ahead of you`;
  return `<div class="g-body">
    <p class="g-status">${lv(g.level, g.word)} <span class="muted">on the TV</span></p>
    <h1 class="g-title">${esc(g.title)}</h1>
    ${g.subtitle ? `<p class="g-sub">${esc(g.subtitle)}</p>` : ''}
    <p class="g-ahead">${ahead}</p>
    ${scrubber(g)}
    <p class="g-stats">${esc(g.stats)}</p>
    <ul class="chips" aria-label="What the file contains">${(g.chips || []).map((c) => `<li>${esc(c)}</li>`).join('')}<li class="proof">${g.verified ? 'read from the file' : 'guessed from its name'}</li></ul>
  </div>`;
}

// ---------------------------------------------------------------- rendering and polling

function render(state) {
  STATE = state;
  put('problem', problemHTML(state.problem));
  const g = state.glance;
  $('glance-wrap').className = g ? 'cine glance live' : 'glance idle';
  put('glance-bg', g ? backdrop(g.backdrop, g.poster) : '');
  put('glance', glanceHTML(g, state.idle_text));
}

async function poll() {
  clearTimeout(timer);
  let state = null;
  try {
    const r = await fetch('/ui/api/state', { cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state = await r.json();
    lastOk = Date.now();
  } catch (e) {
    put('problem', problemHTML(null, lastOk ? `Last update ${ago((Date.now() - lastOk) / 1000)}. It restarts by itself after a moment.`
      : 'It restarts by itself after a moment.'));
  }
  if (state) {
    try { render(state); } catch (e) { console.error(e); }
  }
  if (!document.hidden) timer = setTimeout(poll, 2000);
}

// The scrubber: the time under the pointer (or finger), and whether that part is on the Mac.
function scrubTip(ev) {
  const s = ev.target.closest && ev.target.closest('[data-scrub]');
  const tip = $('scrub-tip');
  if (!tip) return;
  const g = STATE && STATE.glance;
  if (!s || !g) { tip.hidden = true; return; }
  const box = s.getBoundingClientRect();
  const f = Math.min(1, Math.max(0, (ev.clientX - box.left) / box.width));
  const here = g.strip[Math.min(g.strip.length - 1, Math.floor(f * g.strip.length))] >= 0.5;
  tip.textContent = `${g.duration_s ? clock(f * g.duration_s) : `${Math.round(f * 100)}%`} · ${here ? 'on this Mac' : 'not downloaded yet'}`;
  tip.style.left = `${f * 100}%`;
  tip.hidden = false;
}
document.addEventListener('pointermove', scrubTip);
document.addEventListener('pointerdown', scrubTip);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
poll();
</script>
</body>
</html>
```

- [ ] **Step 4: Run the page tests** — `node --test tests/dashboard-page.test.mjs 2>&1 | grep -E "^# (pass|fail)"`
Expected: `# fail 0`.

- [ ] **Step 5: Validate the palette** (dataviz rule: compute, don't eyeball). From the repo root:

```bash
V=/private/tmp/claude-501/bundled-skills/2.1.295/2305dc0d8cf94fef4f59db3c90f0f0b5/dataviz/scripts/validate_palette.js
node "$V" "#129680,#7FA6FF,#4CC38A,#F2B53A,#FF6B6B" --mode dark --surface "#111c2e"
node "$V" "#0E9682,#3D6CC7,#1E8E5A,#A86A00,#C93C3C" --mode light --surface "#FFFFFF"
```

Expected: no FAIL lines. A WARN on contrast is acceptable here only because every coloured mark has a text label or word beside it;
a FAIL (CVD separation or the normal-vision floor) means re-stepping that colour in both the token block and the validator call
before going on. (If the script's flags differ, run it with `--help` and pass the same colours and surfaces.)

- [ ] **Step 6: Commit**

```bash
git add src/dashboard.html tests/dashboard-page.test.mjs
git commit -m "Rebuild the dashboard page: cinematic glance, problem banner, regions written only when they change"
```

---

### Task 7: Up next, the shelf and the title sheets

**Files:**
- Modify: `src/dashboard.html` (add functions before the `// ---- rendering and polling` comment; extend `render`; add listeners before `poll();`)
- Test: `tests/dashboard-page.test.mjs` (append)

**Interfaces:**
- Consumes: `up_next`, `shelf`, `titles` from Task 5; page helpers from Task 6.
- Produces: `upNextHTML(u)`, `shelfHTML(sh)`, `tileText(t)`, `tileId(key)`, `sheetHead(t)`, `seasonsHTML(t, season)`, `sheetBody(t, season)`,
  `episodeRow(e)`, `releaseCard(r, budget)`, `verdictText(v)`, `actionsFor(x)`, `renderSheet()`, `act(path, body, key)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/dashboard-page.test.mjs`:

```js
const IH = 'b'.repeat(40);
const ep = (n, name, done, extra = {}) => ({ season: 1, episode: n, label: `S01E0${n}`, name, file: `Chernobyl - S01E0${n}.mkv`, ih: IH, idx: n,
  length: 6.4 * GB, done, busy: false, job: null, release: 'Accid', tag: null, next: false, ...extra });
const JOB = { ih: IH, idx: 4, label: 'S01E04', state: 'downloading', done: 0.34, length: 6.4 * GB, message: null, mbps: 21.3, eta_s: 1500 };
export const SERIES = {
  key: 'tt7366338', title: 'Chernobyl', kind: 'series', year: '2019', poster: 'P', backdrop: 'BG', bytes: 3.8 * GB, last_used: 1,
  in_use: false, ihs: [IH], meta_line: '2019 · Accid · 3.8 GB here', summary: 'S1 · 1 episode here', sub: null, seasons: [1], releases: [],
  episodes: [ep(1, '1:23:45', 0), ep(2, 'Please Remain Calm', 0), ep(3, 'Open Wide, O Earth', 1), ep(4, 'The Happiness of All Mankind', 0.34, { job: JOB }),
    ep(5, 'Vichnaya Pamyat', 0, { next: true })],
};
export const MOVIE = {
  key: 'tt37287335', title: 'Obsession', kind: 'movie', year: '2026', poster: null, backdrop: null, bytes: 2.8 * GB, last_used: 1, in_use: false,
  ihs: ['c'.repeat(40), 'd'.repeat(40)], meta_line: '2026 · Movie · 2 releases · 2.8 GB here', summary: 'Movie · 2 releases · 2.8 GB', sub: null,
  seasons: [], episodes: [], releases: [
    { ih: 'c'.repeat(40), idx: 0, name: 'Obsession...-BEN.THE.MEN', group: 'BEN.THE.MEN', chips: ['4K', 'WEB', 'MP4'],
      verdict: { symbol: '✓✓', reason: null, summary: ['MP4', 'E-AC3', 'DV P8'] }, length: 21.7 * GB, done: 0.11, bytes: 2.4 * GB,
      last_used: 1e9 - 172800, in_use: false, busy: false, job: null },
    { ih: 'd'.repeat(40), idx: 4, name: 'Obsession (2026) (... r00t)', group: 'r00t', chips: ['MKV'],
      verdict: { symbol: '✗', reason: "TrueHD: TV can't decode", summary: ['MKV', 'TrueHD'] }, length: 9 * GB, done: 0.02, bytes: 448 * 1024 * 1024,
      last_used: 1e9 - 259200, in_use: false, busy: false, job: null }],
};
const UP = { key: 'tt7366338', ih: IH, idx: 4, title: 'Chernobyl', season: 1, episode: 4, label: 'S01E04', name: 'The Happiness of All Mankind',
  length: 6.4 * GB, done: 0, job: null, state: 'not_here', poster: 'P', backdrop: 'BG' };

test('Up next offers the episode, then shows its download, then that it is ready', () => {
  const { ctx } = page();
  let h = ctx.upNextHTML(UP);
  assert.ok(h.includes('Chernobyl · Season 1') && h.includes('E4 · The Happiness of All Mankind') && h.includes('Download for later'));
  assert.match(h, /data-get="b{40}" data-idx="4"/);
  h = ctx.upNextHTML({ ...UP, state: 'downloading', job: JOB, done: 0.34 });
  assert.ok(h.includes('Cancel') && h.includes('34%') && h.includes('about 25 min left'));
  assert.ok(ctx.upNextHTML({ ...UP, state: 'ready', done: 1 }).includes('ready to watch'));
  assert.equal(ctx.upNextHTML(null), '');
});

test('the shelf: one tile per title, the space, and clearing what is not in use', () => {
  const { ctx } = page();
  const sh = { used: 4.4 * GB, budget: 15 * GB, free: 22 * GB, idle_bytes: 3.8 * GB, idle_count: 1,
    items: [{ key: 'tt7366338', title: 'Chernobyl', kind: 'series', poster: 'P', summary: 'S1 · 1 episode here', sub: 'E4 downloading 34%', last_used: 1, bytes: 1 }] };
  const h = ctx.shelfHTML(sh);
  assert.ok(h.includes('4.4 GB of 15 GB') && h.includes('data-open="tt7366338"') && h.includes('Delete everything not in use (3.8 GB)'));
  assert.ok(ctx.tileText(sh.items[0]).includes('E4 downloading 34%'));
  assert.ok(ctx.shelfHTML({ ...sh, items: [] }).includes('Nothing is stored'));
  assert.ok(ctx.shelfHTML(null).includes('Reading the cache'));
});

test('a series sheet: every episode with its one action, the next one filled', () => {
  const { ctx } = page();
  const h = ctx.sheetBody(SERIES, 1);
  assert.equal((h.match(/class="ep[ "]/g) || []).length, 5);
  assert.match(h, /class="solid" type="button" data-get="b{40}" data-idx="5"/);
  assert.match(h, /data-delfile="b{40}" data-idx="3"/);
  assert.match(h, /data-cancel="b{40}" data-idx="4"/);
  assert.ok(h.includes('Not on the Mac · 6.4 GB') && h.includes('Open Wide, O Earth'));
  assert.ok(ctx.sheetHead(SERIES).includes('2019 · Accid · 3.8 GB here'));
});

test('the season picker appears only with more than one season', () => {
  const { ctx } = page();
  assert.ok(!ctx.seasonsHTML(SERIES, 1).includes('<select'));
  const two = { ...SERIES, seasons: [1, 2] };
  const h = ctx.seasonsHTML(two, 2);
  assert.match(h, /<select id="season-pick"/);
  assert.match(h, /<option value="2" selected>Season 2<\/option>/);
});

test('a movie sheet: one card per release with its verdict and how much is here', () => {
  const { ctx } = page();
  ctx.render(STATE);   // the cache budget (15 GB) comes from the state
  const h = ctx.sheetBody(MOVIE, null);
  assert.ok(h.includes('BEN.THE.MEN') && h.includes('Plays here — checked in the file: MP4, E-AC3, DV P8'));
  assert.ok(h.includes('Won&#39;t play here — TrueHD: TV can&#39;t decode'));
  assert.ok(h.includes('2.4 GB of 21.7 GB here (11%)'));
  assert.ok(h.includes('Too big to download fully'), 'the 21.7 GB release does not fit a 15 GB cache');
  assert.ok(h.includes('Download the rest'), 'the 9 GB one does');
  assert.ok(h.includes('Delete all 2 releases'));
  assert.match(h, /data-del="c{40}" data-key="c{40}:0" data-label="BEN.THE.MEN" data-size="2.4 GB"/, 'Delete removes the release from the Mac');
});

test('a sheet for a title that is gone says so', () => {
  const { ctx, els } = page();
  ctx.render({ ...STATE, titles: { tt7366338: SERIES } });
  ctx.location.hash = '#title=tt0000000';
  ctx.renderSheet();
  assert.equal(els.sheet.hidden, false);
  assert.ok(els['sheet-head'].innerHTML.includes('no longer on this Mac'));
  ctx.location.hash = '#title=tt7366338';
  ctx.renderSheet();
  assert.ok(els['sheet-head'].innerHTML.includes('Chernobyl'));
  assert.ok(els['sheet-body'].innerHTML.includes('Vichnaya Pamyat'));
  ctx.location.hash = '';
  ctx.renderSheet();
  assert.equal(els.sheet.hidden, true);
});
```

- [ ] **Step 2: Run to see them fail** — `node --test tests/dashboard-page.test.mjs 2>&1 | grep -E "^not ok|# fail"`
Expected: FAIL — `ctx.upNextHTML is not a function` and the others.

- [ ] **Step 3: Implement** — in `src/dashboard.html`, insert before `// ---------------------------------------------------------------- rendering and polling`:

```js
// ---------------------------------------------------------------- up next, the shelf, title sheets

function upNextHTML(u) {
  if (!u) return '';
  const key = `${u.ih}:${u.idx}`;
  let body;
  if (ACTIVE.includes(u.state)) {
    body = `<span class="bar" role="img" aria-label="${pct(u.done)}% downloaded"><b style="width:${(u.done * 100).toFixed(1)}%"></b></span>
      <p class="muted small">${esc(jobLine(u.job))}</p>
      <button class="ghost" type="button" data-cancel="${esc(u.ih)}" data-idx="${u.idx}">Cancel</button>`;
  } else if (u.state === 'ready') {
    body = `<p class="small">${lv('good', 'On this Mac — ready to watch')}</p>`;
  } else {
    const left = u.length * (1 - u.done);
    const failed = u.state === 'failed' && u.job && u.job.message ? ` · the last try stopped: ${esc(u.job.message)}` : '';
    body = `<p class="muted small">${u.done >= 0.01 ? `${bytes(left)} to go, ${pct(u.done)}% is here` : bytes(u.length)}${failed}</p>
      <button class="solid" type="button" data-get="${esc(u.ih)}" data-idx="${u.idx}" data-label="${esc(u.label)}" data-left="${Math.round(left)}">${u.done >= 0.01 ? 'Download the rest' : 'Download for later'}</button>`;
  }
  return `<div class="un-body"><h2 class="eyebrow">Up next</h2>
    <p class="un-show">${esc(u.title)} · Season ${u.season}</p>
    <p class="un-ep">E${u.episode}${u.name ? ` · ${esc(u.name)}` : ''}</p>
    ${body}${note(key)}</div>`;
}

// (Function declarations, not consts: the page tests reach them as properties of the global object.)
function tileId(key) { return `tile-${String(key).replace(/[^A-Za-z0-9]/g, '')}`; }
function tileText(t) {
  return `<span class="t-name">${esc(t.title)}</span><span class="t-sum">${esc(t.summary)}</span>${t.sub ? `<span class="t-sub">${esc(t.sub)}</span>` : ''}`;
}

// The tiles' images sit in the structure, which changes only when the titles do; their text is written separately.
function shelfHTML(sh) {
  if (!sh) return '<h2 class="eyebrow">On this Mac</h2><p class="muted small">Reading the cache…</p>';
  const used = sh.used || 0, budget = sh.budget || 1;
  const head = `<div class="sh-head"><h2 class="eyebrow">On this Mac</h2><span class="muted small">${bytes(used)} of ${bytes(budget)} · ${bytes(sh.free)} free</span></div>
    <span class="bar space" role="img" aria-label="${Math.round(used / budget * 100)}% of the cache used"><b style="width:${Math.min(100, used / budget * 100).toFixed(1)}%"></b></span>`;
  if (!sh.items.length) return `${head}<p class="muted small">Nothing is stored. What you play is kept here for rewinds and re-watching.</p>`;
  return `${head}<ul class="shelf">${sh.items.map((t) => `<li><button class="tile" type="button" data-open="${esc(t.key)}">${poster(t.poster, t.title)}<span id="${tileId(t.key)}"></span></button></li>`).join('')}</ul>
    ${sh.idle_count ? `<button class="ghost danger wide" type="button" data-clear="${sh.idle_count}" data-size="${esc(bytes(sh.idle_bytes))}">Delete everything not in use (${bytes(sh.idle_bytes)})</button>` : ''}
    ${note('shelf')}`;
}

function verdictText(v) {
  const what = v.summary && v.summary.length ? v.summary.join(', ') : '';
  if (v.symbol === '✓✓') return `Plays here — checked in the file${what ? `: ${what}` : ''}`;
  if (v.symbol === '✓') return `Plays here${what ? ` — ${what}` : ''}`;
  if (v.symbol === '✗') return `Won't play here${v.reason ? ` — ${v.reason}` : ''}`;
  return v.reason || 'Plays, with a caveat';
}
const SYM = { '✓✓': 'good', '✓': 'good', '⚠': 'warn', '✗': 'bad' };

// One file's actions: Cancel while it downloads; Delete when it's here; Download (filled for the next episode) when not.
function actionsFor(x) {
  const j = x.job;
  if (j && ACTIVE.includes(j.state)) return `<button class="ghost" type="button" data-cancel="${esc(x.ih)}" data-idx="${x.idx}">Cancel</button>`;
  if (x.busy) return '<span class="muted small">Being read</span>';
  const label = x.label || x.group || x.name;
  const del = `<button class="ghost danger" type="button" data-delfile="${esc(x.ih)}" data-idx="${x.idx}" data-label="${esc(label)}" data-size="${esc(bytes(x.length * x.done))}">Delete</button>`;
  if (x.done >= 1) return del;
  const left = x.length * (1 - x.done);
  const get = `<button class="${x.next ? 'solid' : 'ghost'}" type="button" data-get="${esc(x.ih)}" data-idx="${x.idx}" data-label="${esc(label)}" data-left="${Math.round(left)}">${x.done >= 0.01 ? 'Download the rest' : 'Download'}</button>`;
  return x.done >= 0.01 ? del + get : get;
}

function episodeRow(e) {
  const j = e.job;
  let state;
  if (j && ACTIVE.includes(j.state)) state = `<span class="bar"><b style="width:${(e.done * 100).toFixed(1)}%"></b></span>${esc(jobLine(j))}`;
  else if (e.done >= 1) state = `${lv('good', '')} On the Mac · ${bytes(e.length)}`;
  else if (e.done >= 0.01) state = `${pct(e.done)}% here · ${bytes(e.length)}`;
  else state = `Not on the Mac · ${bytes(e.length)}`;
  return `<li class="ep${e.done >= 0.01 ? ' here' : ''}"><span class="ep-n">${e.episode}</span>
    <span class="ep-t">${esc(e.name || e.label)}${e.tag ? ` <span class="tag">${esc(e.tag)}</span>` : ''}</span>
    <span class="ep-s">${state}</span><span class="ep-a">${actionsFor(e)}</span>${note(`${e.ih}:${e.idx}`)}</li>`;
}

function releaseCard(r, budget) {
  const v = r.verdict, name = r.group || r.name;
  const left = r.length * (1 - r.done);
  const fits = r.bytes + left <= budget;
  const running = r.job && ACTIVE.includes(r.job.state);
  const key = `${r.ih}:${r.idx}`;
  const del = `<button class="ghost danger" type="button" data-del="${esc(r.ih)}" data-key="${esc(key)}" data-label="${esc(name)}" data-size="${esc(bytes(r.bytes))}">Delete</button>`;
  const get = !running && r.done < 1 && fits && r.length
    ? `<button class="ghost" type="button" data-get="${esc(r.ih)}" data-idx="${r.idx}" data-label="${esc(name)}" data-left="${Math.round(left)}">Download the rest</button>` : '';
  const act = running ? `<button class="ghost" type="button" data-cancel="${esc(r.ih)}" data-idx="${r.idx}">Cancel</button>`
    : r.busy || r.in_use ? '<span class="muted small">In use</span>' : del + get;
  const now = STATE ? STATE.now : Date.now() / 1000;
  return `<li class="rel"><div class="rel-h"><b>${esc(name)}</b><span>${r.last_used ? `played ${ago(now - r.last_used)}` : ''}</span></div>
    ${r.chips.length ? `<ul class="chips">${r.chips.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>` : ''}
    ${v ? `<p class="verdict"><span class="sym sym-${SYM[v.symbol] || 'warn'}" aria-hidden="true">${esc(v.symbol)}</span> ${esc(verdictText(v))}</p>` : ''}
    <span class="bar"><b style="width:${(r.done * 100).toFixed(1)}%"></b></span>
    <div class="rel-f"><span class="muted">${r.job && ACTIVE.includes(r.job.state) ? esc(jobLine(r.job)) : `${bytes(r.length * r.done)} of ${bytes(r.length)} here (${pct(r.done)}%)`}</span>${act}</div>
    ${!fits && r.done < 1 ? `<p class="muted small">Too big to download fully: the cache keeps ${bytes(budget)}.</p>` : ''}
    ${note(key)}</li>`;
}

function sheetHead(t) {
  return `<div class="sh-top"><button class="back" type="button" data-close aria-label="Back to the dashboard">‹ Back</button>
    <div class="sh-id">${poster(t.poster, t.title)}<div><h2 id="sheet-title">${esc(t.title)}</h2><p class="sh-meta">${esc(t.meta_line)}</p></div></div></div>`;
}

function seasonsHTML(t, season) {
  if (t.kind !== 'series') return '';
  const n = t.episodes.filter((e) => e.season === season).length;
  if (t.seasons.length < 2) return `<div class="sh-seasons">Season ${season} · ${n} episode${n === 1 ? '' : 's'}</div>`;
  return `<div class="sh-seasons"><label class="sr" for="season-pick">Season</label><select id="season-pick">${t.seasons.map((s) => `<option value="${s}"${s === season ? ' selected' : ''}>Season ${s}</option>`).join('')}</select>${n} episode${n === 1 ? '' : 's'}</div>`;
}

function sheetBody(t, season) {
  const budget = (STATE && STATE.shelf && STATE.shelf.budget) || Infinity;
  const all = t.in_use ? '' : `<div class="sheet-foot"><button class="ghost danger wide" type="button" data-delall="${esc(t.key)}" data-size="${esc(bytes(t.bytes))}">${t.kind === 'series' ? `Delete all episodes (${bytes(t.bytes)})` : `Delete all ${t.releases.length} release${t.releases.length === 1 ? '' : 's'} (${bytes(t.bytes)})`}</button>${note(t.key)}</div>`;
  if (t.kind === 'series') return `<ul class="eps">${t.episodes.filter((e) => e.season === season).map(episodeRow).join('')}</ul>${all}`;
  return `<ul class="rels">${t.releases.map((r) => releaseCard(r, budget)).join('')}</ul>${all}`;
}

let sheetKey = null, sheetSeason = null, opener = null;

function defaultSeason(t) {
  const next = t.episodes.find((e) => e.next);
  if (next) return next.season;
  const here = t.episodes.filter((e) => e.done >= 0.01).map((e) => e.season);
  return here.length ? Math.max(...here) : t.seasons[0];
}

function renderSheet() {
  const m = /^#title=(.+)$/.exec(location.hash || '');
  const key = m ? decodeURIComponent(m[1]) : null;
  const el = $('sheet');
  if (!key) {
    if (!el.hidden) {
      el.hidden = true; el.classList.remove('in'); document.body.classList.remove('sheet-open'); sheetKey = null;
      if (opener && opener.focus) opener.focus();
    }
    return;
  }
  const opening = el.hidden;
  if (opening) { el.hidden = false; document.body.classList.add('sheet-open'); requestAnimationFrame(() => el.classList.add('in')); }
  const t = STATE && STATE.titles ? STATE.titles[key] : null;
  if (!t) {
    put('sheet-bg', '');
    put('sheet-head', `<div class="sh-top"><button class="back" type="button" data-close>‹ Back</button><h2 id="sheet-title" style="margin-top:20px">This title is no longer on this Mac</h2></div>`);
    put('sheet-seasons', '');
    put('sheet-body', '');
  } else {
    if (sheetKey !== key) { sheetKey = key; sheetSeason = defaultSeason(t); }
    put('sheet-bg', backdrop(t.backdrop, t.poster));
    put('sheet-head', sheetHead(t));
    put('sheet-seasons', seasonsHTML(t, sheetSeason));
    put('sheet-body', sheetBody(t, sheetSeason));
  }
  if (opening) { const b = $('sheet-panel').querySelector('.back'); if (b) b.focus(); }
}

function closeSheet() {
  if (history.state && history.state.sheet) history.back();
  else { location.hash = ''; renderSheet(); }
}

async function act(path, body, key) {
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dashboard': '1' }, body: JSON.stringify(body || {}) });
    const out = await r.json();
    if (out.ok && !out.freed) delete notes[key];
    else notes[key] = { text: out.ok ? `${out.message}, ${bytes(out.freed)} freed.` : out.message, at: Date.now() };
  } catch (e) {
    notes[key] = { text: 'The server didn\'t answer. Nothing changed.', at: Date.now() };
  }
  poll();
}
```

Extend `render(state)` — append inside it, after the glance lines:

```js
  const u = state.up_next;
  $('upnext-wrap').hidden = !u;
  $('pair').className = u ? 'pair' : 'pair solo';
  put('upnext-bg', u ? backdrop(u.backdrop, u.poster) : '');
  put('upnext', upNextHTML(u));
  put('shelf', shelfHTML(state.shelf));
  for (const t of (state.shelf && state.shelf.items) || []) put(tileId(t.key), tileText(t));
  renderSheet();
```

Insert before the final `poll();` line:

```js
document.addEventListener('click', (ev) => {
  const t = ev.target.closest ? ev.target : null;
  if (!t) return;
  const open = t.closest('[data-open]');
  if (open) { opener = open; history.pushState({ sheet: true }, '', `#title=${encodeURIComponent(open.dataset.open)}`); renderSheet(); return; }
  if (t.closest('[data-close]')) { closeSheet(); return; }
  const get = t.closest('[data-get]');
  if (get) {
    const left = Number(get.dataset.left), c = STATE && STATE.shelf;
    const over = c && c.used + left > c.budget ? ` That takes the downloads past their ${bytes(c.budget)} limit, so the oldest ones not in use are deleted to make room.` : '';
    if (confirm(`Download ${get.dataset.label} (${bytes(left)}) to this Mac for later? It downloads whenever the TV doesn't need the bandwidth.${over}`)) {
      act('/ui/api/prefetch', { ih: get.dataset.get, idx: Number(get.dataset.idx) }, `${get.dataset.get}:${get.dataset.idx}`);
    }
    return;
  }
  const stop = t.closest('[data-cancel]');
  if (stop) { act('/ui/api/prefetch/cancel', { ih: stop.dataset.cancel, idx: Number(stop.dataset.idx) }, `${stop.dataset.cancel}:${stop.dataset.idx}`); return; }
  const one = t.closest('[data-delfile]');
  if (one && confirm(`Delete ${one.dataset.label} (${one.dataset.size}) from this Mac? It downloads again if you play it.`)) {
    act('/ui/api/cache/delete-file', { ih: one.dataset.delfile, idx: Number(one.dataset.idx) }, `${one.dataset.delfile}:${one.dataset.idx}`);
    return;
  }
  const rel = t.closest('[data-del]');
  if (rel) {
    if (confirm(`Delete ${rel.dataset.label} (${rel.dataset.size}) from this Mac? It downloads again if you play it.`)) {
      act('/ui/api/cache/delete', { ih: rel.dataset.del }, rel.dataset.key);
    }
    return;
  }
  const all = t.closest('[data-delall]');
  if (all) {
    const title = STATE && STATE.titles[all.dataset.delall];
    if (title && confirm(`Delete everything of ${title.title} (${all.dataset.size}) from this Mac? It downloads again if you play it.`)) {
      (async () => { for (const ih of title.ihs) await act('/ui/api/cache/delete', { ih }, title.key); })();
    }
    return;
  }
  const clr = t.closest('[data-clear]');
  if (clr && confirm(`Delete ${clr.dataset.clear} downloaded torrent${clr.dataset.clear === '1' ? '' : 's'} (${clr.dataset.size})? Anything playing now is kept.`)) {
    act('/ui/api/cache/clear', {}, 'shelf');
  }
});
document.addEventListener('change', (ev) => {
  if (ev.target && ev.target.id === 'season-pick') { sheetSeason = Number(ev.target.value); renderSheet(); }
});
window.addEventListener('popstate', renderSheet);
window.addEventListener('hashchange', renderSheet);
document.addEventListener('keydown', (ev) => {
  if ($('sheet').hidden) return;
  if (ev.key === 'Escape') { closeSheet(); return; }
  if (ev.key !== 'Tab') return;
  // Keep focus inside the open sheet.
  const f = [...$('sheet-panel').querySelectorAll('button, select, a[href]')].filter((x) => !x.disabled);
  if (!f.length) return;
  if (ev.shiftKey && document.activeElement === f[0]) { ev.preventDefault(); f[f.length - 1].focus(); }
  else if (!ev.shiftKey && document.activeElement === f[f.length - 1]) { ev.preventDefault(); f[0].focus(); }
});
```

The test harness needs `window`: add `window: { addEventListener() {} },` to the `ctx` object in `page()` in `tests/dashboard-page.test.mjs`.

- [ ] **Step 4: Run the page tests** — `node --test tests/dashboard-page.test.mjs 2>&1 | grep -E "^not ok|# (pass|fail)"`
Expected: `# fail 0`.

- [ ] **Step 5: Commit**

```bash
git add src/dashboard.html tests/dashboard-page.test.mjs
git commit -m "Dashboard: Up next, one shelf card per title, and title sheets with every episode or release"
```

---

### Task 8: The chain, Recent and the server log

**Files:**
- Modify: `src/dashboard.html` (add functions before `// ---- rendering and polling`; extend `render`; add a listener)
- Test: `tests/dashboard-page.test.mjs` (append)

**Interfaces:**
- Consumes: `chain`, `recent`, `log` from Task 5.
- Produces: `chainHTML(steps)`, `recentHTML(r, now)`, `sparkHTML(r)`, `logHTML(lines)`; global `openSteps` (a `Set` of step ids).

- [ ] **Step 1: Write the failing tests** — append to `tests/dashboard-page.test.mjs`:

```js
const CHAIN = [
  { id: 'swarm', name: 'Swarm', level: 'good', line: '31 Mbps in, the file needs 15 · 38 peers, 12 sending', reason: null, advice: null, details: [['Download', '31.0 Mbps']] },
  { id: 'mac', name: 'This Mac', level: 'good', line: 'up 16 h 6 min · cache 4.4 GB of 15.0 GB · 22.0 GB free', reason: null, advice: null, details: [],
    services: [{ name: 'Torrent server', level: 'good', text: '3% CPU, 80 MB' }, { name: 'Library', level: 'good', text: '0 movies, 0 shows' },
      { name: 'Stream Advisor', level: 'good', text: 'Rating streams for TCL' }, { name: 'Stremio account', level: 'warn', text: 'Signed out' }] },
  { id: 'wifi', name: 'Wi-Fi', level: 'warn', line: 'ping to the TV 120 ms', reason: "The TV's Wi-Fi is slow to answer", advice: 'Restart the TV', details: [['Ping to the TV', '120 ms (usually 4)']] },
  { id: 'tv', name: 'The TV', level: 'idle', line: 'Asleep — not answering', reason: null, advice: null, details: [] },
];

test('the chain: four steps, the weak one marked with its reason, services inside This Mac', () => {
  const { ctx } = page();
  const h = ctx.chainHTML(CHAIN);
  assert.equal((h.match(/class="step /g) || []).length, 4);
  assert.match(h, /step-warn[^]*The TV&#39;s Wi-Fi is slow to answer/);
  for (const s of ['Torrent server', 'Library', 'Stream Advisor', 'Stremio account', 'Asleep — not answering']) assert.ok(h.includes(s), s);
  assert.ok(!h.includes('120 ms (usually 4)'), 'details stay folded');
  ctx.openSteps.add('wifi');
  assert.ok(ctx.chainHTML(CHAIN).includes('120 ms (usually 4)'));
  assert.equal(ctx.chainHTML(null), '');
});

test('Recent opens by itself while something plays, with the buffer and the events', () => {
  const { ctx } = page();
  const r = { live: true, unit: 's', history: [[1, 250, 30, 480], [3, 255, 30, 485], [5, 260, 31, 490]],
    events: [{ kind: 'stall', text: 'Stalled for 6.0 s at 31:02', cause: 'swarm', at: 1e9 - 120, pos: 0.5, recent: false }] };
  const h = ctx.recentHTML(r, 1e9);
  assert.match(h, /<details class="recent" id="recent-d" open>/);
  assert.ok(h.includes('Stalled for 6.0 s at 31:02') && h.includes('The torrent sent pieces too slowly') && h.includes('2 min ago'));
  assert.ok(h.includes('<svg') && h.includes('id="spark-tip"'), 'the buffer sparkline, with its tooltip');
  assert.ok(!ctx.recentHTML({ ...r, live: false }, 1e9).includes(' open>'));
  assert.equal(ctx.recentHTML(null, 1e9), '');
});

test('the log lines, newest first', () => {
  const { ctx } = page();
  const h = ctx.logHTML([{ time: '18:04:56', text: 'first' }, { time: '18:05:00', text: '<b>second</b>' }]);
  assert.ok(h.indexOf('second') < h.indexOf('first'));
  assert.ok(h.includes('&lt;b&gt;second'));
});
```

- [ ] **Step 2: Run to see them fail** — `node --test tests/dashboard-page.test.mjs 2>&1 | grep -E "^not ok|# fail"`
Expected: FAIL — `ctx.chainHTML is not a function`.

- [ ] **Step 3: Implement** — insert before `// ---- rendering and polling` in `src/dashboard.html`:

```js
// ---------------------------------------------------------------- from the swarm to the TV, recent, the log

var openSteps = new Set();   // the steps opened for their details (var: the page tests reach it)
const STEP_MARK = { good: '✓', warn: '!', bad: '!', idle: '' };
const LEVEL_WORD = { good: 'fine', warn: 'needs a look', bad: 'problem', idle: 'not in use' };

function chainHTML(steps) {
  if (!steps) return '';
  return `<h2 class="eyebrow">From the swarm to your TV</h2><ol class="chain">${steps.map((s) => {
    const open = openSteps.has(s.id);
    const kv = (s.services || []).map((v) => `<dt>${esc(v.name)}</dt><dd>${esc(v.text)}</dd>`).join('')
      + s.details.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
    return `<li class="step step-${s.level}"><span class="dot dot-${s.level}" aria-hidden="true">${STEP_MARK[s.level]}</span>
      <button class="step-head" type="button" data-step="${s.id}" aria-expanded="${open}"><span class="step-name">${esc(s.name)}</span><span class="sr"> — ${LEVEL_WORD[s.level]}</span></button>
      ${s.reason ? `<p class="step-reason">${esc(s.reason)}</p>` : ''}
      <p class="step-line">${esc(s.line)}</p>
      ${s.services ? `<ul class="svc">${s.services.map((v) => `<li>${lv(v.level, v.name)}</li>`).join('')}</ul>` : ''}
      ${open && kv ? `<dl class="kv">${kv}</dl>` : ''}</li>`;
  }).join('')}</ol>`;
}

const EVENT_MARK = { seek: '↷', stall: '⏸', buffering: '⏸', startup: '…', reread: '↺' };
const CAUSE = { swarm: 'The torrent sent pieces too slowly', player: 'The player or the file\'s layout' };
let recentOpen = null;   // what the viewer chose; until then, open while something plays
let sparkPts = null;     // the sparkline's points, for its tooltip

function sparkHTML(r) {
  const pts = (r.history || []).map((h) => ({ t: h[0], v: r.unit === 's' ? h[1] : h[3] })).filter((p) => p.v != null);
  if (pts.length < 3) return '';
  const W = 600, H = 56, t0 = pts[0].t, t1 = pts[pts.length - 1].t, max = Math.max(...pts.map((p) => p.v), 1);
  const x = (t) => (t1 > t0 ? (t - t0) / (t1 - t0) : 1) * W, y = (v) => H - 2 - v / max * (H - 6);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
  const last = pts[pts.length - 1].v, mins = Math.max(1, Math.round((t1 - t0) / 60));
  const now = r.unit === 's' ? clock(last) : `${Math.round(last)} MB`;
  sparkPts = { pts, unit: r.unit };
  return `<div class="spark" data-spark><p class="label">Buffered ahead, last ${mins} min</p><span class="cross" id="spark-cross" hidden></span><span class="tip" id="spark-tip" hidden></span>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Buffered ahead over the last ${mins} minutes, now ${now}">
      <line x1="0" x2="${W}" y1="${H - 1}" y2="${H - 1}" stroke="var(--rule)" stroke-width="1" vector-effect="non-scaling-stroke"/>
      <path d="${line}L${W},${H}L0,${H}Z" fill="var(--ahead)" fill-opacity="0.12"/>
      <path d="${line}" fill="none" stroke="var(--ahead)" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    </svg></div>`;
}

function recentHTML(r, now) {
  if (!r) return '';
  const open = recentOpen === null ? r.live : recentOpen;
  const events = r.events.length ? `<ol class="events">${r.events.map((e) => `<li><span aria-hidden="true">${EVENT_MARK[e.kind] || '•'}</span>
      <span>${esc(e.text)}${e.cause ? `<span class="why">${esc(CAUSE[e.cause] || e.cause)}</span>` : ''}</span><time>${ago(now - e.at)}</time></li>`).join('')}</ol>`
    : '<p class="muted small">No seeks or stalls.</p>';
  return `<details class="recent" id="recent-d"${open ? ' open' : ''}><summary>Recent</summary>${sparkHTML(r)}${events}</details>`;
}

function logHTML(lines) {
  return (lines || []).slice().reverse().map((l) => `<li><time>${esc(l.time)}</time><span>${esc(l.text)}</span></li>`).join('');
}
```

Append inside `render(state)`:

```js
  put('chain', chainHTML(state.chain));
  put('recent', recentHTML(state.recent, state.now));
  put('loglines', logHTML(state.log));
```

Insert before the final `poll();` line:

```js
// The sparkline: the nearest reading under the pointer (or finger), with a crosshair.
function sparkTip(ev) {
  const box = ev.target.closest && ev.target.closest('[data-spark]');
  const tip = $('spark-tip'), cross = $('spark-cross');
  if (!tip || !cross) return;
  if (!box || !sparkPts) { tip.hidden = true; cross.hidden = true; return; }
  const svg = box.querySelector('svg').getBoundingClientRect();
  const { pts, unit } = sparkPts, t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const at = t0 + Math.min(1, Math.max(0, (ev.clientX - svg.left) / svg.width)) * (t1 - t0);
  const p = pts.reduce((a, b) => (Math.abs(b.t - at) < Math.abs(a.t - at) ? b : a));
  const left = `${(t1 > t0 ? (p.t - t0) / (t1 - t0) : 1) * 100}%`;
  tip.textContent = `${unit === 's' ? clock(p.v) : `${Math.round(p.v)} MB`} · ${ago(Date.now() / 1000 - p.t)}`;
  tip.style.left = left; cross.style.left = left;
  tip.hidden = false; cross.hidden = false;
}
document.addEventListener('pointermove', sparkTip);
document.addEventListener('pointerdown', sparkTip);
document.addEventListener('click', (ev) => {
  const b = ev.target.closest && ev.target.closest('[data-step]');
  if (!b) return;
  const id = b.dataset.step;
  if (openSteps.has(id)) openSteps.delete(id); else openSteps.add(id);
  if (STATE) put('chain', chainHTML(STATE.chain));
});
document.addEventListener('toggle', (ev) => {
  if (ev.target.id === 'recent-d') recentOpen = ev.target.open;
  if (ev.target.id === 'log') { try { localStorage.setItem('dash.logOpen', ev.target.open ? '1' : '0'); } catch (e) { /* private mode */ } }
}, true);
try { if (localStorage.getItem('dash.logOpen') === '1') $('log').open = true; } catch (e) { /* private mode */ }
```

- [ ] **Step 4: Run all tests** — `node --test 2>&1 | grep -E "^# (tests|pass|fail)"` and both Python suites as in Global Constraints.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dashboard.html tests/dashboard-page.test.mjs
git commit -m "Dashboard: the chain from the swarm to the TV, Recent, and the full-width server log"
```

---

### Task 9: Look at it, install, document, deploy

**Files:**
- Modify: `install.sh:146` (copy list), `README.md` (the Dashboard section)
- Scratch only (not committed): `/private/tmp/claude-501/-Users-vivekpayasi/2e13b564-a79c-40c5-9548-9d2cb71b9476/scratchpad/preview/` — a preview server, two states, the screenshots

- [ ] **Step 1: Screenshots of the real page with a realistic state** — write `/private/tmp/claude-501/-Users-vivekpayasi/2e13b564-a79c-40c5-9548-9d2cb71b9476/scratchpad/preview/serve.py`:

```python
"""Serves src/dashboard.html at /ui with a fixed state at /ui/api/state, for screenshots. argv: repo, state.json, theme (dark|light)."""
import http.server
import json
import sys
from pathlib import Path

repo, state, theme = Path(sys.argv[1]), Path(sys.argv[2]).read_bytes(), sys.argv[3]
page = (repo / 'src/dashboard.html').read_text()
# Headless Chrome prefers light; force the theme under test by switching the light block on or off.
page = page.replace('@media (prefers-color-scheme: light)', '@media all' if theme == 'light' else '@media not all')


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body, ctype = (state, 'application/json') if self.path.startswith('/ui/api/state') else (page.encode(), 'text/html; charset=utf-8')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer(('127.0.0.1', int(sys.argv[4]) if len(sys.argv) > 4 else 41800), H).serve_forever()
```

The idle state is the live server's own (after Task 1's advisor change is deployed and Chernobyl's streams were opened on the
TV once, it carries real verdicts and art; before that, posters and names only). The playing state adds a stream, its Up next
download and a Wi-Fi problem. Write `/private/tmp/claude-501/-Users-vivekpayasi/2e13b564-a79c-40c5-9548-9d2cb71b9476/scratchpad/preview/playing.mjs`:

```js
// argv: idle.json playing.json — the live state plus a stream, its Up next download and a Wi-Fi problem.
import fs from 'node:fs';
const s = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const first = s.shelf && s.shelf.items[0];
const t = first ? s.titles[first.key] : {};
const ih = (t.ihs && t.ihs[0]) || 'b'.repeat(40);
s.glance = {
  ih, idx: 2, title: t.title || 'Chernobyl', subtitle: 'Season 1 · Episode 3 · Open Wide, O Earth', poster: t.poster || null,
  backdrop: t.backdrop || null, level: 'good', word: 'Smooth', ahead: '4 min 20 s', complete: false, position: 0.384, ahead_end: 0.45,
  strip: Array(54).fill(1).concat(Array(66).fill(0)), seeks: [0.38], elapsed: '25:10', remaining: '−40:20', duration_s: 3930,
  stats: '31 Mbps · file needs 15 · 38 peers', chips: ['MKV', 'HEVC', '4K', 'Dolby Vision 8', 'AC3 + 1 more'], verified: true,
};
const job = { ih, idx: 3, label: 'S01E04', state: 'downloading', done: 0.34, length: 6.9e9, message: null, mbps: 21.3, eta_s: 1500 };
s.up_next = { key: first ? first.key : null, ih, idx: 3, title: s.glance.title, season: 1, episode: 4, label: 'S01E04',
  name: 'The Happiness of All Mankind', length: 6.9e9, done: 0.34, state: 'downloading', poster: s.glance.poster, backdrop: s.glance.backdrop, job };
s.problem = { level: 'warn', title: "The TV's Wi-Fi is slow to answer",
  advice: 'Ping 120 ms (usually 4) — playback may stutter. Restarting the TV usually clears it.', step: 'wifi' };
s.chain = s.chain.map((x) => (x.id === 'wifi' ? { ...x, level: 'warn', reason: s.problem.title, line: `${x.line} · ping to the TV 120 ms` } : x));
s.recent = { live: true, unit: 's', history: [[1, 240, 30, 470], [60, 250, 31, 480], [120, 260, 31, 490]],
  events: [{ kind: 'seek', text: 'Jumped to 30:40', cause: null, at: s.now - 180, pos: 0.46, recent: false }] };
fs.writeFileSync(process.argv[3], JSON.stringify(s));
```

Then, from the repo root:

```bash
P=/private/tmp/claude-501/-Users-vivekpayasi/2e13b564-a79c-40c5-9548-9d2cb71b9476/scratchpad/preview; mkdir -p "$P"
curl -s -H 'Host: localhost' http://127.0.0.1:11472/ui/api/state > "$P/idle.json"
node "$P/playing.mjs" "$P/idle.json" "$P/playing.json"
SHELL_BIN=$(ls -d ~/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell | head -1)
for st in idle playing; do for th in dark light; do
  python3 "$P/serve.py" "$PWD" "$P/$st.json" "$th" 41800 & SRV=$!; sleep 1
  "$SHELL_BIN" --headless --disable-gpu --hide-scrollbars --virtual-time-budget=4000 --window-size=390,1600 --screenshot="$P/$st-$th-phone.png" http://127.0.0.1:41800/ui
  "$SHELL_BIN" --headless --disable-gpu --hide-scrollbars --virtual-time-budget=4000 --window-size=1440,1000 --screenshot="$P/$st-$th-desktop.png" http://127.0.0.1:41800/ui
  kill $SRV; wait $SRV 2>/dev/null
done; done
```

Look at each image (Read tool). Check, and fix in `src/dashboard.html` (re-running Task 6–8 tests) anything that fails:
no horizontal scroll at 390 px; the title and figures readable over the backdrop; the banner on top; the shelf tiles in a row;
the chain's four steps with the services grid in two columns; on desktop two columns with the log full width below; light mode
legible. Also one sheet: serve `playing.json` again and screenshot `http://127.0.0.1:41800/ui#title=<the first shelf key>` at
390×1200 into `$P/sheet-phone.png`. Use screenshots sparingly (the user asked for them to be used wisely): these eight plus the sheet,
and a re-shot only of a view that changed after a fix.

- [ ] **Step 2: Install the new files** — in `install.sh`, extend the copy list:

```bash
for f in addon.mjs autoheal.mjs auth.mjs advisor.mjs rating.mjs device-profile.default.json make-profile.mjs urls.sh \
         torrent_server.py stream_facts.py dashboard.py dashboard.html prefetch.py media_text.py page_model.py; do
```

- [ ] **Step 3: Document it** — in `README.md`, replace the bullets of the `### Dashboard` section (from `- **Now playing**` through the
`- **Download next episode**` bullet's last line) with:

```markdown
- **What's playing**, at a glance: the title over its artwork, how much is ready ahead of the TV in plain words ("4 min 20 s
  ready ahead of you"), and the episode drawn as a player's scrubber — what is downloaded, where the TV is, the buffered stretch
  ahead. Any problem — a stall, a slow Wi-Fi link, a player error on the TV, a service down — goes to the top of the page in one
  sentence, with what to do about it.
- **Up next**: the episode after the one you're watching (or watched last), with a button to download it to this Mac for later —
  one file at a time, at the lowest priority, pausing whenever a stream is short of buffer or the disk is down to the space kept
  free; refused when it wouldn't fit. A downloaded episode stays as long as the cache budget allows.
- **On this Mac**: one card per show or movie. A show's card opens its season with every episode of the pack — on the Mac,
  downloading, or not here — each with its one action (Download, Cancel, Delete). A movie's card lists its releases with whether
  each plays on your TV (the same ✓✓ / ✓ / ⚠ / ✗ as the stream cards), how much of it is here, and Delete.
- **From the swarm to your TV**: the path a stream takes — the swarm, this Mac (with the torrent server, the library, the Stream
  Advisor and your Stremio account), the Wi-Fi, the TV — each step with its health and, on tap, its details. When something is
  wrong, the weak step says why.
- **Recent** seeks and stalls with their cause, the buffer's history, and the server log at the bottom.
```

- [ ] **Step 4: Run every test** — `node --test`, the venv suite and `/usr/bin/python3` suite. Expected: all pass. Commit:

```bash
git add install.sh README.md
git commit -m "Install the dashboard's new modules and describe the new page"
```

- [ ] **Step 5: Deploy** (only when the TV is idle: `curl -s -H 'Host: localhost' http://127.0.0.1:11472/ui/api/state` shows no `glance`,
and `torrent.log` has no request from the TV's address in the last minutes):

```bash
cp src/advisor.mjs src/rating.mjs ~/.stremio-local-addon/ && launchctl kickstart -k gui/$(id -u)/com.local.stremio-library
cp src/dashboard.py src/dashboard.html src/media_text.py src/page_model.py ~/.stremio-local-addon/ \
  && launchctl kickstart -k gui/$(id -u)/com.local.stremio-torrent
sleep 4; lsof -nP -iTCP:11472 -sTCP:LISTEN; lsof -nP -iTCP:12471 -sTCP:LISTEN
```

Check: `/ui` returns 200, `/ui/api/state` has `chain` with four steps and `shelf.items` naming Chernobyl, the library's
`/advisor/health` answers; open a title list on the TV once so the advisor records verdicts and art, then the Chernobyl sheet shows
episode names. Ask the user before pushing.
