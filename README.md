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

## Settings

- **Playback -> Highest resolution / HDR this TV shows.** Defaults to 1080p SDR, so Silo converts 4K and
  HDR titles for this device. Set what the TV and Kodi device really support (for an NVIDIA SHIELD on a 4K
  HDR TV: 4K and "HDR10, HDR10+ and Dolby Vision") to direct-play everything.
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
- Skip intro/credits markers and Up Next hand-off are not in this version.

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

## Licence

GPL-2.0-or-later. The Kodi database code is adapted from
[PlexKodiConnect](https://github.com/croneter/PlexKodiConnect) (GPL-2.0).
