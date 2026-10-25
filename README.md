# SiloKodiConnect

A Kodi add-on that syncs the movies and TV shows on a [Silo](https://github.com/Silo-Server/silo-server)
server into Kodi's own video library, the way PlexKodiConnect does for Plex. Every skin, widget and
library feature (Recently added, In progress, Next up, smart playlists, info screens, service.upnext) treats
Silo titles as local media. Playback streams from Silo: the original file when Kodi can play it, a converted
stream otherwise.

Community project: not made or endorsed by the Silo team. Requires Kodi 20 (Nexus) or newer.

## What it does

- **Sign in with a TV code.** Kodi shows an 8-digit code; approve it at `<your server>/activate` on a phone
  or computer where you're signed in to Silo (OIDC sign-ins such as Pocket ID work). Then choose a
  household profile; PIN profiles ask for the PIN.
- **Movies and TV in Kodi's library.** Titles, plots, cast with photos, directors, writers, genres, studios,
  networks, countries, ratings, IMDb/TMDB/TVDB ids, artwork (poster, fanart, logo, clearart, banner, disc,
  landscape, season posters, episode stills) and video/audio details (4K, HDR10, Dolby Vision, Atmos-capable
  tracks) so skins show the right flags. Each Silo library becomes a Kodi tag.
- **Kept current.** Silo's realtime event socket reports changed titles, finished scans and watch-state
  changes as they happen; a fallback check runs every 60 minutes (configurable). Only new or changed titles
  are fetched: a sync pages each library's catalog (200 titles per request) and compares fingerprints.
- **Watched state and resume points both ways.** Silo -> Kodi from the catalog, the event socket and Silo's
  progress feed (so a film paused on your phone resumes at that point in Kodi). Kodi -> Silo when you mark
  something watched or unwatched in Kodi, and through playback progress reports.
- **Playback** through Silo's playback protocol v3: start, progress every 10 seconds, stop. Silo's media
  routes need the sign-in header, so Kodi plays through the service's loopback relay, which adds a fresh
  token to every request: long films outlive the hourly token and no token reaches Kodi's log. External text
  subtitles are added to the player.

## Install

Build a zip and use Kodi's **Add-ons -> Install from zip file**:

```bash
git archive --format=zip --prefix=plugin.video.silokodiconnect/ -o plugin.video.silokodiconnect.zip HEAD
```

Kodi installs `script.module.requests` and `script.module.websocket` from its official repository.
`inputstream.adaptive` is optional and used for converted (HLS) streams when present.

Then open **Add-ons -> Video add-ons -> SiloKodiConnect -> Sign in to Silo**.

## Up Next and skipping intros

When a Silo episode starts, the add-on sends [Up Next](https://github.com/im85288/service.upnext) the
episode, the next one, the credits start from Silo's markers (`notification_offset`, so the popup appears as
the credits begin) and the intro and recap ranges (`skip_segments`, for Up Next's Skip button). Up Next hands
the choice to play the next episode back, and the add-on opens it as the Kodi library item. Episodes Silo has
no markers for keep Up Next's normal timing.

## Widgets

Kodi's own "In progress TV shows" lists every show with some but not all episodes watched. To see Silo's
rows instead, point skin widgets at the add-on:

- **Continue Watching (as in Silo)**: `plugin://plugin.video.silokodiconnect/?mode=widget`
- **Silo home rows**: every row on the profile's Silo home screen (Popular on IMDb, Recently added,
  collections, recommendations...), each a folder usable as a widget
  (`plugin://plugin.video.silokodiconnect/?mode=widget&section=<row id>`).

Rows list the matching Kodi library items, so art, resume points and play behave as usual. In "what to watch
next" rows a show stands for its next episode; elsewhere for the show. Titles not synced to Kodi are skipped.

## Silo Skyline skin

[Silo Skyline](https://github.com/im85288/SiloSkyline) lays Kodi out like the Silo TV apps. The add-on
tells it which pages and rows to show, following the profile's Silo home and libraries: Home window
properties `silo.pages`, `silo.<page>.count` and `silo.<page>.<n>.title` / `.path` / `.style`, published when
the service starts and after every sync, plus `silokodi.signed_in`. Widget items carry what Silo's hero and
detail page show (`silo.code`, `silo.aired`, `silo.mpaa`, `silo.genre`, `silo.rating`, `silo.cast`,
`silo.badge1`-`3`) and their cast, directors and studios. Other skins can use the same properties.

## Settings

- **Playback -> Highest resolution / HDR this TV shows.** Defaults to 1080p SDR, so Silo converts 4K and
  HDR titles for this device. Set what the TV and Kodi device really support to direct-play everything. For
  an NVIDIA SHIELD on a 4K Dolby Vision TV choose 4K and "Dolby Vision including profile 7", and turn on
  Kodi's own **Settings -> Player -> Videos -> Dolby Vision compatibility mode**, which converts profile 7
  (UHD Blu-ray remuxes) to 8.1. Without the profile 7 option Silo strips such files to HDR10 in a converted
  4K stream, which the SHIELD can't hardware-decode in Kodi; the add-on refuses those with a message.
- **Sync -> Choose libraries** limits which Silo libraries are synced.
- **Sync -> Rewrite every item** re-fetches every title (use after changing the relay port).
- **Sync -> Remove all Silo items** removes everything this add-on wrote to the Kodi library.

## How it fits together

| Part | File |
| --- | --- |
| Silo /api/v2 client, token refresh | `resources/lib/silokodi/api.py` |
| TV-code sign-in and profile choice | `resources/lib/silokodi/auth.py` |
| Silo JSON -> Kodi fields | `resources/lib/silokodi/mapping.py` |
| Kodi video database writer (adapted from PKC) | `resources/lib/silokodi/kodidb.py` |
| Fingerprint sync of movies, shows, seasons, episodes | `resources/lib/silokodi/sync.py` |
| Realtime events | `resources/lib/silokodi/events.py` |
| Playback v3 sessions | `resources/lib/silokodi/playback.py` |
| Loopback relay (artwork, media, plugin <-> service) | `resources/lib/silokodi/relay.py` |
| Background service | `resources/lib/silokodi/service.py` |
| Plugin entry: play and menu | `resources/lib/silokodi/plugin.py` |

Items are written with add-on paths (`plugin://plugin.video.silokodiconnect/movies/?mode=play&id=...`), so
playing one asks the add-on, which starts a Silo session and hands Kodi the stream.

Silo's artwork URLs are signed and change daily, so Kodi's art table holds stable local addresses
(`http://127.0.0.1:57578/art/<id>/<kind>`) that the service resolves to Silo's current URL. Kodi caches each
image once.

Only the service refreshes sign-in tokens (Silo rotates refresh tokens); the plugin asks the service over the
loopback relay to start playback.

## Known limits

- Don't run Kodi's **Clean library**: it can't check add-on paths and may remove Silo items.
- Seeking far ahead in a converted stream that is still being produced may stall; direct play is unaffected.
  Silo's replan (re-anchoring a converted stream at a new position) isn't implemented yet.
- PIN-protected profiles: if Silo expires the profile proof, sign in again.
- Episodes get their HDR flag from Silo's season listing, which says only "HDR", so Dolby Vision and HLG
  episodes show as HDR10 in skins. Movies show the exact type.
- Skip buttons and Up Next timing need Up Next (service.upnext) with `skip_segments` support.

## Measured against a real server

Syncing a library of 1,077 movies, 525 shows and 28,342 episodes took 54 s the first time and 8 s for a
check with no changes. The media relay moved a 4K remux at about 350 Mbit/s on a Mac.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install pytest requests websocket-client
.venv/bin/python -m pytest tests
```

The tests run the sync against Kodi 21's real video database schema (`tests/fixtures/MyVideos131.sql`,
extracted from Kodi's `VideoDatabase.cpp`) and a fake Silo server.

## Changes

### v0.9.2 (2026-10-25)
- Publishes Silo's pages and rows, and whether you are signed in, for the
  [Silo Skyline](https://github.com/im85288/SiloSkyline) skin.
- Widget items carry the hero's details (episode code, air date, age rating, genre, IMDb rating, cast,
  quality badges) and their cast, directors and studios, so info pages list the cast.
- Asks Kodi for a library update only when titles are added or removed: skin helpers rescan on every update.

### v0.9.1 (2026-10-25)
- Sync once per Silo library scan, after Silo goes quiet, instead of a full sync for every scan step: one
  scan on a real server caused ten in 25 minutes. Silo's duplicate legacy `library.changed` event is ignored.

### v0.9.0 (2026-10-24)
First release, tested on an NVIDIA SHIELD (Kodi 21) against a library of 1,079 movies and 28,342 episodes.
- Movies and TV synced into Kodi's library, kept current by Silo's live events; a first sync takes about
  6 minutes on a SHIELD, later checks about 30 seconds.
- Sign in with a TV code; household profiles, PIN profiles included.
- Playback through Silo's playback protocol v3: the original file when Kodi can play it (Dolby Vision
  profiles 5, 7 and 8, TrueHD and DTS passthrough), a converted stream otherwise.
- Watched state and resume points both ways.
- Continue Watching and every Silo home row as Kodi widgets.
- Skip intro and recap, and the Up Next popup at the credits, through Up Next 1.3.0 or later.
- Gentle on small devices: syncs pause while a video plays and hand memory back afterwards.

## Licence

GPL-2.0-or-later. The Kodi database code is adapted from
[PlexKodiConnect](https://github.com/croneter/PlexKodiConnect) (GPL-2.0).
