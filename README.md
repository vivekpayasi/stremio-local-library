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
- serves each file with `Accept-Ranges` / `206 Partial Content` — this is what makes seeking work
- builds stream URLs from the request's `Host` header, so they follow your Mac's IP automatically

### Self-healing across networks

The certificate is a **wildcard** for your install hash, so it stays valid on every network. The
only thing that goes stale when your IP changes is the addon's URL stored in your Stremio account.
A built-in watcher fixes that for you: it notices the IP change and rewrites that URL through
Stremio's own API, which then syncs to your TV.

Move to a different wifi and the library just reappears. You only ever update the TV's
**Server URL** field by hand, because that one is a per-device setting Stremio doesn't sync.

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

Run `~/.stremio-local-addon/urls.sh` any time to print the current one. Restart the TV app after a
network change so it picks up the updated addon.

## Usage

Drop files into `~/Media/Movies`. They're picked up within 10 minutes, or immediately on restart.

Filenames matter for matching — `Movie Name (2015).mkv` or `Movie.Name.2015.1080p.mkv` both work.
Anything under 50 MB is ignored so samples and clips don't clutter the catalog.

External subtitles (`.srt`, `.vtt`, `.ass`, `.sub`) sitting next to a video are served with it.

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
| `rescanMs` | 10 min | how often to rescan |
| `minBytes` | 50 MB | ignore files smaller than this |

## Troubleshooting

**TV shows "Error" for the server URL.** Usually the URL is stale — your Mac's IP changed. Run
`urls.sh` and re-enter what it prints. Also check the TV's clock is correct, since a wrong date
makes the certificate look invalid, and that Private DNS on the TV isn't blocking the lookup
(Cloudflare, NextDNS and AdGuard strip answers that point at private IPs).

**The library is empty.** Check `addon.log`. Files under 50 MB are skipped, and only the
extensions `.mkv .mp4 .avi .m4v .mov .webm` are scanned.

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

- Movies only. TV series episodes appear as separate entries rather than a grouped show.
- No transcoding — the client must be able to decode the file natively.
- LAN only by design. For access across different networks, put both devices on a Tailscale
  network and serve under a `*.ts.net` certificate instead.
