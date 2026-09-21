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
https://<your-dashed-ip>.<your-hash>.stremio.rocks:12470/
```

Click the menu bar icon to copy the current one, or run `~/.stremio-local-addon/urls.sh` to print
it. Restart the TV app after a network change so it picks up the updated addon.

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
tail -f ~/.stremio-local-addon/addon.log                       # watch it
~/.stremio-local-addon/urls.sh                                 # current URLs + health
launchctl kickstart -k gui/$(id -u)/com.local.stremio-library  # restart
./uninstall.sh                                                 # remove
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
H.264 and HEVC with AAC/AC3 are safe; exotic audio like DTS-HD may not play. Embedded PGS
subtitles are bitmap-based and often won't render — use an external `.srt` instead.

**The addon vanished after changing networks.** Give the watcher a minute, then restart the TV
app so it re-syncs your addon collection.

## Notes on your account

The self-healing feature reads your Stremio `authKey` from the desktop app's local storage on
demand — it is never copied or written to disk — and uses it only against `api.strem.io` to keep
the addon's URL current. The full addon collection is backed up to
`~/.stremio-local-addon/collection-backups/` before every change.

## Limitations

- Absolute seasons (`E47` with no season marker) and date-based episode naming aren't detected.
- No transcoding — the client must be able to decode the file natively.
- LAN only by design. For access across different networks, put both devices on a Tailscale
  network and serve under a `*.ts.net` certificate instead.
