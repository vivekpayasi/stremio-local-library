# Stremio Local Library

Play media stored on your Mac through **Stremio's own player** on an Android TV — with full
seeking, which casting doesn't give you.

Stremio's built-in "Local Files" addon can't do this: it hands back `file://` paths, which are
meaningless to any other device. This addon serves your files over HTTPS with proper HTTP Range
support, so the TV streams them directly and you can scrub anywhere in the timeline.

## How it works

Stremio's desktop app maintains a real Let's Encrypt certificate for a hostname that encodes your
LAN IP — `192-168-1-50.<hash>.stremio.rocks`. That's how the TV can talk to a machine on your
private network over HTTPS without certificate warnings.

This addon reuses that certificate. It runs a small Node service on port `12471` that:

- scans `~/Media` and `~/Movies` for video files
- reuses Stremio's own already-IMDb-matched index where possible, falling back to filename parsing
  plus a Cinemeta lookup
- detects `SxxEyy` in filenames and presents those files as a **series** — one show card, a real
  episode list with titles and stills, per-episode watch progress and next-episode autoplay
- lists anything in `~/Media/Other` verbatim, with no matching at all — for home videos, recordings
  and clips that aren't films
- serves each file with `Accept-Ranges` / `206 Partial Content` — this is what makes seeking work
- builds stream URLs from the request's `Host` header, so they follow your Mac's IP automatically

### Self-healing across networks

The certificate is a **wildcard** for your install hash, so it stays valid on every network. The
only thing that goes stale when your IP changes is the addon's URL stored in your Stremio account.
A built-in watcher fixes that for you: it notices the IP change and rewrites that URL through
Stremio's own API, which then syncs to your TV.

Move to a different wifi and the library just reappears. You only ever update the TV's
**Server URL** field by hand, because that one is a per-device setting Stremio doesn't sync.

The same watcher keeps the addon placed **right after Cinemeta** in your account. Stremio orders
the home screen by addon, and a new addon normally lands last, so without this your rows —
**Local Library - Movie**, **Local Library - Series** and **Local Library · Other - Movie** — would
sit below every other addon's. It also refreshes the account's copy of the manifest when the addon
is upgraded. Every write is preceded by a backup in `~/.stremio-local-addon/collection-backups/`,
and your other addons are never modified. To leave the order alone, pass `pinAfter: null` to
`startAutoHeal` in `addon.mjs`.

## Requirements

- macOS
- **Node.js 22.5 or newer** (uses the built-in `node:sqlite` module)
- **Python 3.9–3.13** for the torrent server (the installer offers `brew install python@3.13`);
  libtorrent's prebuilt wheels need macOS 14+ on Apple Silicon, or 15+ on Intel
- **Stremio desktop** installed, signed in, and run at least once
- Stremio's **Streaming HTTPS endpoint** enabled — Settings → Streaming → set it to your LAN IP

## Install

```bash
git clone https://github.com/vivekpayasi/stremio-local-library.git
cd stremio-local-library
./install.sh
```

The installer checks prerequisites, copies files to `~/.stremio-local-addon`, creates
`~/Media/Movies`, installs a launch agent so it starts at login, and prints your URLs.

The addon registers itself on your Stremio account automatically, so it appears on the TV without
you typing anything there.

### On the TV

Settings → Server → URL:

```
https://<your-dashed-ip>.<your-hash>.stremio.rocks:12472/
```

That is the torrent server below, not Stremio's own (`:12470`). Click the menu bar icon to copy the
current one, or run `~/.stremio-local-addon/urls.sh` to print it. Restart the TV app after a network
change so it picks up the updated addon.

### Torrent server

Stremio's own streaming server runs its torrent engine (a fork of `torrent-stream`) in JavaScript on
a single thread. On big, high-bitrate torrents that thread can hit 100% CPU, and once it does it stops
feeding the player — the TV buffers endlessly while the download speed still looks fine.

`torrent_server.py` replaces just that part. It answers the same torrent requests the TV makes, using
[libtorrent](https://libtorrent.org) — the multi-threaded C++ engine inside qBittorrent — and passes
every other request (settings, transcoding, subtitles) through to Stremio's server untouched. The TV's
Server URL points at it (`:12472`); nothing in the Stremio app is modified, so its updates can't break
this. Torrent results from every addon (Torrentio, MediaFusion, AIOStreams…) go through it.

- Nothing is downloaded until a player asks. Each reader gets a window that starts small, so the first
  pieces arrive quickly, and grows to ~450 MB ahead as playback settles in.
- Every piece is verified against the torrent's hashes before it is sent to the TV.
- Downloaded pieces stay in `~/.stremio-local-addon/torrent-cache/`, so rewinding and re-watching are
  served from disk. Torrents nobody has used for a while are deleted oldest-first once the cache passes
  **15 GB**, or whenever the disk drops below 3 GB free.
- If Stremio desktop is closed, torrents still play (the TV's status checks are answered from the last
  time it was open); only transcoding and subtitle conversion need it running.

- MP4s whose subtitle tracks are stored far from their video (some releases put them all at the end of
  the file) get those tracks hidden: ExoPlayer reads every track, selected or not, and would otherwise
  jump across the file for each subtitle line and buffer endlessly. Only the 4-byte label of each such
  track in the file's index is changed, so video, audio, Dolby Vision and seeking are untouched; the
  built-in subtitles of that file are lost (use a subtitles addon instead). `torrent.log` names every
  track it hides. Turn it off with `{"hide_misplaced_mp4_subtitles": false}`.
- Downloads are capped at **48 Mbps** by default, so the torrent doesn't crowd the TV stream off a
  shared Wi-Fi link — that is still well above a 4K WEB-DL's bitrate. On a wired Mac, raise or remove
  it with `{"download_limit_mbps": 0}`.

Settings live at the top of `torrent_server.py`; override any of them in
`~/.stremio-local-addon/torrentd.json`, e.g. `{"cache_budget_gb": 30}`, then restart it.
`https://<host>:12472/torrentd/health` shows active torrents, speeds and cache use.

To go back to Stremio's own server, set the TV's Server URL to `:12470` again.

### Stream Advisor

Whether a torrent plays on a given TV is mostly decided before you press play: the container, the
video and audio codecs, the Dolby Vision profile, the bitrate and the swarm. The **Stream Advisor**
puts that verdict in Stremio's stream list, as 1–5 ⭐ on every Torrentio stream:

```
⭐⭐⭐⭐⭐  ✓ verified · MKV · E-AC3 · DV P8 · 26 Mbps · 173 peers
4k DV   Obsessão 2026 WEB-DL 2160p x265 DV HDR10 DUAL 5.1
        👤 173 💾 20.03 GB ⚙️ BluDV

⭐         ✗ TrueHD audio — this TV can't decode it · verified
4k DV   Obsession (2026) (2160p UHD BluRay x265 10bit DV HDR TrueHD Atmos 7.1 … r00t)
```

Stremio shows each addon's streams as that addon sends them, so the advisor is an addon of its own
(served by the same service at `/advisor/manifest.json`). It asks your Torrentio for the list, rates
each stream and hands back the very same streams with the stars added — playback still goes through
the torrent server exactly as before. It finds your Torrentio (including its configuration) in your
Stremio account on first start, saves it to `~/.stremio-local-addon/advisor.json`, and registers
itself right after Local Library. Once you're happy with it, **uninstall Torrentio** in Stremio so
each stream is listed once; the advisor keeps using it. If Torrentio isn't in your account, the
advisor stays off.

**How streams are rated.** The rules for one TV live in a *device profile*,
[`src/device-profile.default.json`](src/device-profile.default.json). Every rule is a cap — "TrueHD audio: at most 1
star", "MP4: at most 4", "over 48 Mbps: at most 2", "5 peers or fewer: at most 2" — and a stream gets
the lowest cap it hits, with that rule's reason shown. The shipped profile is a TCL Android TV with an
Amlogic chip running Stremio's ExoPlayer, learned from real plays and from the decoder list the TV
reports over ADB: Dolby Vision profile 8 plays, profile 7 (UHD Blu-ray remuxes) fails in the TV's Dolby
Vision decoder, TrueHD can't be decoded, DTS and DTS-HD MA play (the TV decodes the DTS core itself),
MP4s are fine because the torrent server hides misplaced subtitle tracks, and AV1, VP9 and VC-1 have
decoders but haven't been played yet. For another TV, override only what differs in
`~/.stremio-local-addon/device-profile.json` — objects merge, `null` removes a rule:

```json
{ "audio": { "truehd": null, "dts-hd": { "cap": 1, "why": "DTS-HD MA — this TV can't decode it" } } }
```

**A profile for another TV.** Android TVs report what they can decode, profile by profile — including
which Dolby Vision profiles their decoder accepts — and which HDR formats the screen shows. With ADB on,
`make-profile.mjs` reads that and writes the matching profile:

```bash
brew install --cask android-platform-tools                         # adb, once
node ~/.stremio-local-addon/make-profile.mjs 192.168.1.40          # what the TV supports, and the profile
node ~/.stremio-local-addon/make-profile.mjs 192.168.1.40 --write  # save it as device-profile.json
```

On the TV first: Settings → Device Preferences → About → select **Build** seven times, then Developer
options → **USB debugging** and **Network debugging** on, and accept *Allow debugging?* when the Mac
connects. On the TCL TV the report predicted every result its real plays gave — no Dolby Vision profile
7 in the decoder, DTS listed, no TrueHD — but a listed decoder is still not a play, so codecs that are
rare in practice stay at 4 stars until you have watched one. The advisor picks up a new profile on the
next stream list; no restart. Turn debugging off again on the TV when you're done.

**Verified ratings.** A release name is a guess. Whenever the torrent server plays a file, it reads
the file's header once with ffprobe (bundled with Stremio desktop), about 20 seconds in, and records
what is really inside — codecs, which audio track plays first, Dolby Vision profile, real bitrate,
hidden subtitle tracks — in `~/.stremio-local-addon/stream-facts.json`. From then on that stream is
rated from those facts and marked **verified**. Turn this off with `{"learn_from_plays": false}` in
`torrentd.json`.

Settings in `advisor.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `upstreams` | your Torrentio | stream addons whose lists are rated, `[{ "name", "url" }]` |
| `sort` | `"original"` | `"rating"` lists the best-rated streams first |
| `probe.enabled` | `false` | also probe the top `probe.top` unplayed streams of each list in the background (a few MB each), so their ratings are verified next time |
| `enabled` | `true` | `false` turns the advisor off |

`https://<host>:12471/advisor/health` shows the profile in use and how many files have verified facts;
`addon.log` has one line per list rated.

### Menu bar icon

The installer offers to add a status icon to the menu bar. It is a small
[SwiftBar](https://swiftbar.app) plugin, installed with Homebrew; decline and everything else
works the same.

| Icon colour | Meaning |
|---|---|
| green | the addon and Stremio's streaming server are both up |
| yellow | the addon is up, but Stremio desktop is closed — torrent streams won't play on the TV |
| red | the addon is not answering |
| grey | no network, or Stremio has not issued its certificate yet |

Click it for the status of each server, the **TV Server URL** and **addon install URL** for the
network you are on (click either to copy it), the last library scan and self-heal result, and
shortcuts to open Stremio, restart the addon, and open the log or the Media folder. It refreshes
every 30 seconds.

To keep it after a reboot, turn on SwiftBar → Preferences → **Launch at Login**.

## Usage

The installer creates three folders. Where you put a file decides how it's treated:

| Folder | Treated as | Matching |
| --- | --- | --- |
| `~/Media/Movies` | films | IMDb / Cinemeta lookup by filename |
| `~/Media/Shows` | episodes | grouped into shows by `SxxEyy` |
| `~/Media/Other` | listed verbatim | **none** — the filename is the title |

Files are picked up within 10 minutes, or immediately on restart.

### Movies and Shows

Filenames matter for matching:

| Kind | Examples that work |
| --- | --- |
| Movie | `Movie Name (2015).mkv`, `Movie.Name.2015.1080p.mkv` |
| Episode | `Show.Name.S01E03.1080p.mkv`, `Show Name 1x03.mkv`, `Show Name Season 1 Episode 3.mkv` |

Episodes of the same show are grouped automatically, and episode titles, synopses and stills come
from Cinemeta. Only the episodes you actually have are listed.

Anything under 50 MB is ignored so samples and clips don't clutter the catalog.

### Other

Put home videos, recordings, or anything without an IMDb entry in `~/Media/Other`. They appear in
their own **Other** row with the filename as the title, no artwork, and no lookup — so nothing can
be mis-identified. The 50 MB floor doesn't apply here, so short clips work too.

This is the fix for a real failure mode: a file called `TB00037_1.mp4` was matched by the Cinemeta
search to *Stargate SG-1*, poster and all. Filing it under `Other` sidesteps matching entirely.

External subtitles (`.srt`, `.vtt`, `.ass`, `.sub`) sitting next to a video are served with it.

### Skipping files

Add rules to `~/.stremio-local-addon/ignore.txt`, one per line — a plain string matches anywhere in
the path, `/slashes/` make it a regex, `#` starts a comment:

```
Sample.mkv
/trailer/
/\.part\d+\./
```

Use this to exclude files entirely. If you just want a file listed *without* matching, put it in
`~/Media/Other` instead.

## Managing it

```bash
tail -f ~/.stremio-local-addon/addon.log                       # watch the library addon
tail -f ~/.stremio-local-addon/torrent.log                     # watch the torrent server
~/.stremio-local-addon/urls.sh                                 # current URLs + health
launchctl kickstart -k gui/$(id -u)/com.local.stremio-library  # restart the addon
launchctl kickstart -k gui/$(id -u)/com.local.stremio-torrent  # restart the torrent server
./uninstall.sh                                                 # remove
```

Tests (no dependencies):

```bash
node --test                                                    # rating rules, the advisor, make-profile
~/.stremio-local-addon/venv/bin/python -m unittest discover -s tests   # stream facts
```

## Configuration

Edit the `CONFIG` block at the top of `~/.stremio-local-addon/addon.mjs`, then restart:

| Setting | Default | Meaning |
| --- | --- | --- |
| `port` | `12471` | HTTPS port for the addon |
| `roots` | `~/Media`, `~/Movies` | folders scanned for media |
| `otherRoots` | `~/Media/Other` | listed verbatim, never matched |
| `rescanMs` | 10 min | how often to rescan |
| `minBytes` | 50 MB | ignore files smaller than this |
| `ignoreFile` | `~/.stremio-local-addon/ignore.txt` | per-line skip rules |

## Troubleshooting

**TV shows "Error" for the server URL.** Usually the URL is stale — your Mac's IP changed. Run
`urls.sh` and re-enter what it prints. Also check the TV's clock is correct, since a wrong date
makes the certificate look invalid, and that Private DNS on the TV isn't blocking the lookup
(Cloudflare, NextDNS and AdGuard strip answers that point at private IPs).

**The library is empty.** Check `addon.log`. Files under 50 MB are skipped, and only the
extensions `.mkv .mp4 .avi .m4v .mov .webm` are scanned. The log reports how many files were
skipped and why the rest were grouped as they were.

**Episodes show up as a movie with several "sources".** The `SxxEyy` marker wasn't recognised in
the filename. Rename to `Show.Name.S01E03.…` and restart the service.

**Playback fails on the TV.** The file has to be decodable by the TV — there is no transcoding.
H.264 and HEVC with AAC/AC3 are safe; exotic audio like DTS-HD may not play. The Stream Advisor's
stars say this up front for torrents. Embedded PGS
subtitles are bitmap-based and often won't render — use an external `.srt` instead.

**The addon vanished after changing networks.** Give the watcher a minute, then restart the TV
app so it re-syncs your addon collection.

## Notes on your account

The self-healing feature reads your Stremio `authKey` from the desktop app's local storage on
demand — it is never copied or written to disk — and uses it only against `api.strem.io` to keep
the addons' URLs current and, once, to find your Torrentio for the Stream Advisor. Your Torrentio
URL can contain a debrid key, so `advisor.json` is readable only by you and logs show only its
host. The full addon collection is backed up to `~/.stremio-local-addon/collection-backups/` before
every change. The uninstaller removes both addons and, if you had removed Torrentio, puts it back.

## Limitations

- Absolute seasons (`E47` with no season marker) and date-based episode naming aren't detected.
- No transcoding — the client must be able to decode the file natively.
- LAN only by design. For access across different networks, put both devices on a Tailscale
  network and serve under a `*.ts.net` certificate instead.

## Legal

This project doesn't host, index or distribute any content. The library addon serves files from your
own disk, and the torrent server only fetches what your own Stremio addons point it to. You are
responsible for making sure you have the right to watch whatever you play with it.

## License

[MIT](LICENSE) © 2026 Vivek Payasi
