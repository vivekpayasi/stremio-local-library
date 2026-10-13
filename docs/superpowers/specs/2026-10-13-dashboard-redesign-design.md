# Dashboard redesign — design

Date: 2026-10-13 · Status: approved in conversation, awaiting review of this write-up

## Why

The dashboard at `/ui` shows everything the torrent server knows, one section after another. The user asked for it to be
"prettified" by deciding, for each piece of information, the form that fits *why* it is looked at. The design was
worked out section by section with mockups (brainstorming companion); this document records the decisions.

## How the page is used — and the order that follows

The phone is the main screen (the user manages the cache from it: this morning's next-episode download and six
deletions came from the phone). Four moments, in this order:

1. **A glance while watching** — is it fine, how much is buffered. Readable at arm's length in a second.
2. **Housekeeping** before or after an episode — what's on the Mac, download the next one, delete what's watched,
   space. One or two taps.
3. **System status** — server, library, advisor, account, TV: one compact view, reassuring when green.
4. **Diagnosis on demand** — the details of a problem, the history of the stream, the server log.

**Problems jump the queue:** whatever goes wrong (a stall, a slow Wi-Fi link, a player error on the TV, a service
down) puts its one-line reason, with what to do, at the top of the page — and marks where it happens in the system
view.

## Visual direction: cinematic

Dark and poster-led, like a media app: the title's background art (Cinemeta / metahub) as a soft blurred backdrop,
the title large, plain sentences, numbers secondary and muted. Status uses the existing reserved set — good ●,
warning ▲, bad ■, idle ○ — always with a word, never colour alone. System sans for everything (no display face on
figures; proportional figures for large numbers, tabular only where numbers line up). A light variant exists
(chosen by `prefers-color-scheme`): the same layout on a pale surface, the backdrop faded towards light. All colours
are tokens on `:root`; the data and status colours are re-validated with the dataviz palette validator against both
surfaces before shipping.

## Page structure

**Phone (< 760 px), one column, top to bottom:**

1. Problem banner (only when there is a problem)
2. The glance (only while something plays) — *or*, when idle, a one-line status ("Nothing is playing")
3. Up next card
4. On this Mac shelf
5. "From the swarm to your TV" chain (always open)
6. Recent (buffer history and events; open while something plays, folded otherwise)
7. Server log (folded; remembers being opened)

**Desktop (≥ 1100 px), two columns + a full-width foot:**

- Left (≈ 1.65 fr): the glance as a wide cinematic panel; under it Up next and the shelf side by side.
- Right (≈ 1 fr): the chain, then Recent.
- Below both columns, full width: the server log (long lines need the width).

Between 760 and 1100 px: one column, with Up next and the shelf side by side.

Title sheets open as a sheet sliding up over the page on the phone and as a 480 px panel from the right on desktop.
Each sheet has its own URL hash (`#title=<key>`), so the phone's back gesture and Escape close it.

## 1 · The glance

Shown for the live session (the stream the TV is reading now).

- Backdrop: the title's background art (else its poster, blurred), under a gradient into the surface.
- Status row: the level mark and one word — *Smooth*, *Starting*, *Buffering*, *Stalled* — "on the TV".
- Title, large; under it "Season 1 · Episode 3 · Open Wide, O Earth" (episode name from the advisor's Cinemeta data,
  else from the file name, else omitted) or the year for a movie.
- One sentence: "**4 min 20 s** ready ahead of you" (a clock when the file's bitrate is known, else "480 MB ready
  ahead"); "The rest of the episode is on this Mac" once it is complete.
- A player-style scrubber of the whole file: downloaded parts faint, the buffered stretch ahead of the TV in the
  accent, the playhead; elapsed time on the left, remaining time on the right (percentages when the duration is
  unknown). Seek points as small ticks. Hovering or touching it shows the time at that point and whether it is on the
  Mac. It carries an `aria-label` sentence with the same facts.
- One muted line: "31 Mbps · file needs 15 · 38 peers"; then a muted row of what the file is (MKV · HEVC · 4K ·
  Dolby Vision 8 · AC3 + 1, "subtitles fixed" when the MP4 fix is active) and whether that was read from the file or
  guessed from its name.

## 2 · Up next, the shelf, title sheets

### Up next card

A cinematic card (backdrop, show and season, "E4 · The Happiness of All Mankind"). The episode is the next one after
the most recent series activity: the live session's file, else the most recently ended session's, else the most
recently used cached season pack (the episode after the furthest one with data). States: *not here* (size, and
**Download for later**), *queued*, *downloading* (bar, percentage, speed, time left, Cancel), *waiting* (why), *ready*
("On this Mac — ready to watch"), *refused* (the server's reason, inline, for 15 s). No series activity → no card.

### On this Mac shelf

- Header: "On this Mac" with "4.4 / 15 GB" and a thin bar; free disk muted beside it.
- One card per **title**, not per torrent or episode: cache rows are grouped by the IMDb id the advisor recorded for
  their hash (falling back to the torrent's name). Most recently used first.
- Card: poster (or a placeholder with the initial), the title, and one summary: "S1 · 2 episodes here", "E4
  downloading 34%", "Movie · 2 releases · 3.1 GB", "11% · 2.4 GB".
- Below the shelf: "Delete everything not in use (X GB)".

### Series sheet

- Backdrop, poster, title, "2019 · Accid · 2160p DV · 3.8 GB here".
- A season picker only when the title has episodes from more than one season on the Mac.
- Every episode of the cached packs for that season, in order: number, name, state and size, one action:
  - not here → **Download** (the episode right after the furthest one with data gets the filled button);
  - partly here → "Download the rest (N% here)";
  - downloading / queued / waiting → bar, figures, **Cancel**;
  - on the Mac → **Delete** (asks first).
  An episode present in two releases appears twice, each with a small release tag.
- Any episode can be downloaded (not only the next); the existing rules apply (the server refuses one that would take
  the pack past the cache budget or the disk past the space kept free, and says why).
- "Delete all episodes (X GB)" at the bottom when nothing in it is in use.
- The server does not know what has been *watched*; the sheet only says what is on the Mac.

### Movie sheet

One card per release, most recently played first:

- Its name: the release group when the name has one, otherwise the release name.
- Chips: the quality the advisor read from the release name (4K · WEB-DL · DV + HDR10+ · DDP 5.1 Atmos · MP4).
- The Stream Advisor's verdict for that exact file, with the same symbols as the TV's stream cards (✓✓ checked in
  the file / ✓ / ⚠ / ✗) and its short reason ("its first audio track is TrueHD, which the TV can't decode").
- How much is here: a bar and "2.4 GB of 21.7 GB here (11%)", when it was last played.
- **Delete**; and **Download the rest** when the file fits the cache, otherwise "Too big to download fully: the
  cache keeps 15 GB".

"Delete all N releases (X GB)" at the bottom.

## 3 + 4 · From the swarm to your TV

One always-open chain, replacing both the old status sections (TV, Network, This Mac, Library) and the separate
status line. It is the system status when nothing plays and the diagnosis while something does. Four steps, each a
mark, a name, one muted line of its key facts; tapping a step opens its details (today's key/value lists).

| Step | Line | Problem when |
|---|---|---|
| **Swarm** | "31 Mbps in, the file needs 15 · 38 peers, 12 sending"; "Downloading S01E04 for later · 21 Mbps" when only a download runs; idle: "Nothing streaming right now" | stalled mid-stream (bad); download below what the file needs with a low buffer (warning) |
| **This Mac** | "server 3% CPU · cache 4.4 of 15 GB · 22 GB free", and a 2×2 grid of services: Torrent server, Library, Stream Advisor, Stremio account — each its own mark | a service down or the account not updatable (its mark red, the step amber); disk below the space kept free (warning) |
| **Wi-Fi** | "Mac on 5 GHz, −42 dBm, excellent · ping to the TV 4 ms"; with the TV's own signal when ADB is on | ping ≥ 50 ms (warning, "usually N ms" = the median of the last hour); no answer is *not* a problem (some TVs ignore ping) |
| **The TV** | "ExoPlayer · Dolby Vision decoder · 610 MB free"; asleep / not answering / allow debugging, as today | a player error in the last 10 minutes (bad); mpv in use, low memory or recent low-memory kills (warning) |

The worst problem of the page goes to the banner at the top (one at a time; the chain shows all of them).

### Recent

The buffer over the last minutes as a sparkline (hover crosshair and tooltip, as today), then the events list (seeks,
stalls, buffering, re-reads, with their cause). Open while something plays, folded otherwise.

### Server log

Full width at the foot of the page (desktop) or the last section (phone), folded by default and remembered when
opened (browser storage, wrapped in try/catch). Same filtering as today; monospace; long lines wrap.

## Data and server changes

**Stream Advisor (`advisor.mjs`)** — what the dashboard can't work out by itself:

- `metaFor` also keeps the title's `background` and its episode names (`videos`: season, episode, name).
- `recordTitles` also records, per stream (`ih` and `ih:idx`): the verdict (`symbol` and the short reason, as shown on
  the TV card), the quality label chips, and the release name.
- One entry per title, `meta:<imdb>`, holds `background` and the episode names, so they are not repeated on every
  stream entry. The store stays bounded as today.

**Dashboard backend (`dashboard.py`)** builds the page's model, so the page only renders:

- `glance` (from the live session, with episode name and backdrop), `up_next` (the selection rule above, joined with
  its download job), `shelf` (cache rows grouped by title, each with its summary), `titles` (per title key: its
  episodes or releases with state, size, verdict, chips — everything the sheets need), `chain` (the four steps with
  level, line, details and the services grid), `problem` (the banner: the worst problem and its advice), `recent`,
  `log`, plus today's `server`/`cache` figures where still used.
- The ping history (last hour) for "usually N ms".
- All file lists of cached torrents, not only the files with data (so sheets can offer every episode).

**Torrent server** — no change expected; the routes for delete, delete-file, clear, prefetch and cancel stay as they
are.

## Rendering and behaviour

- One self-contained page, no frameworks or CDNs, polling `/ui/api/state` every 2 s while visible.
- Each region is rendered to a string and written to the DOM only when it changed, so images (backdrops, posters)
  are not recreated every poll and nothing flickers; a failed poll keeps the last render with "last update N s ago".
- Buttons keep today's write guard (same-origin fetch with the `X-Dashboard` header). Deletes still ask first.
- Motion: sheets slide in, bars ease; all of it off under `prefers-reduced-motion`.
- Touch targets ≥ 40 px; visible keyboard focus; sheets trap focus while open and return it on close.
- Text on backdrops keeps ≥ 4.5:1 contrast through the gradient.

## Out of scope

- Knowing what has been watched (the server only knows what is on the Mac).
- Offering the next episode from a different torrent (single-episode releases) — still phase 2.
- More than one stream playing at once: the glance shows the live one.
- Changing settings from the page.

## Testing and verification

- Python unit tests for the new model: grouping by title, episode lists with names, up-next selection, each chain
  step's levels, problem precedence, usual ping.
- Node tests for the advisor's new records (verdict, chips, release, background, episode names).
- The page rendered against a stub DOM for every state (playing smooth / stalled, idle, downloading, refused, series
  sheet, movie sheet, services down, TV asleep), checking it renders without errors and says the right things.
- A look at the real page at phone and desktop widths, dark and light, before deploying (headless screenshots).
- Deploy only when the TV is idle; ask before pushing.
