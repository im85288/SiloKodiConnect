# KodiConnectForSilo

A Kodi add-on that syncs the movies and TV shows on a [Silo](https://github.com/Silo-Server/silo-server)
server into Kodi's own video library, the way PlexKodiConnect does for Plex. Every skin, widget and
library feature (Recently added, In progress, Next up, smart playlists, info screens, service.upnext) treats
Silo titles as local media. Playback streams from Silo: the original file when Kodi can play it, a converted
stream otherwise.

Community project: not made or endorsed by the Silo team. Requires Kodi 20 (Nexus) or newer.

## What it does

- **Sign in from the Silo phone app, or with a TV code.** Kodi appears in the phone app's "set up a TV"
  sheet, as Silo's own TV apps do, and the phone approves it: nothing to type. Or Kodi shows an 8-digit code
  to approve at `<your server>/activate` (OIDC sign-ins such as Pocket ID work). Servers on the local network
  are found without typing an address. Then choose a household profile; PIN profiles ask for the PIN.
- **Movies and TV in Kodi's library.** Titles, plots, cast with photos, directors, writers, genres, studios,
  networks, countries, ratings (IMDb, TMDB and Rotten Tomatoes' critics), IMDb/TMDB/TVDB ids, artwork (poster, fanart, logo, clearart, banner, disc,
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
- **Box sets.** Silo's box sets (the films of a TMDB collection, such as Harry Potter, in a library with box sets
  on) become Kodi movie sets with Silo's name, overview, poster and backdrop. Each set's films but its first by
  title are tagged `Silo box set member`, so a skin's grid can hide them and show the first as the box set
  (Kodi groups sets only in its own Videos window); its set art carries `silocount` ("8 films"), `siloyears`
  ("2001 – 2011") and `silooverview` as text. `mode=info&type=set&dbid=<set>` opens a box set's page.
- **A screensaver** of your library's backdrops and logos, slowly zooming: choose KodiConnectForSilo in
  Settings > Interface > Screensaver.
- **Your ratings.** Rate a film or series from one to five stars (Love it ... Not for me), which Silo's
  recommendations learn from; Kodi's info page shows the current stars.
- **Theme tunes.** A show's theme plays while its page (or one of its episodes') is open, as Plex and Infuse
  play one: Silo's own theme.mp3 beside the show's media, cached on the device, never over your own playback.
- **Dolby Atmos on live UHD channels.** Kodi never passes a live stream's audio through, so IPTV Simple
  channels with UHD in their name or group are tagged `realtime="false"` whenever the playlist changes, and
  their E-AC-3 Atmos reaches the receiver as TiviMate's does.

## Install

Build a zip and use Kodi's **Add-ons -> Install from zip file**:

```bash
git archive --format=zip --prefix=plugin.video.kodiconnectforsilo/ -o plugin.video.kodiconnectforsilo.zip HEAD
```

Kodi installs `script.module.requests` and `script.module.websocket` from its official repository.
`inputstream.adaptive` is optional and used for converted (HLS) streams when present.

Then open **Add-ons -> Video add-ons -> KodiConnectForSilo -> Sign in to Silo**.

## Up Next and skipping intros

When a Silo episode starts, the add-on sends [Up Next](https://github.com/im85288/service.upnext) the
episode, the next one, the credits start from Silo's markers (`notification_offset`, so the popup appears as
the credits begin) and the intro and recap ranges (`skip_segments`, for Up Next's Skip button). Up Next hands
the choice to play the next episode back, and the add-on opens it as the Kodi library item. Episodes Silo has
no markers for keep Up Next's normal timing.

## Widgets

Kodi's own "In progress TV shows" lists every show with some but not all episodes watched. To see Silo's
rows instead, point skin widgets at the add-on:

- **Continue Watching (as in Silo)**: `plugin://plugin.video.kodiconnectforsilo/?mode=widget`
- **Silo home rows**: every row on the profile's Silo home screen (Popular on IMDb, Recently added,
  collections, recommendations...), each a folder usable as a widget
  (`plugin://plugin.video.kodiconnectforsilo/?mode=widget&section=<row id>`).
- **Watchlist, Favourites and History**, kept between visits so they open at once.
- **Coming Soon** (`section=comingsoon`): Silo's calendar for the next 14 days, each show you've started once
  with its next episode, soonest first, captioned "Tomorrow · S2 E5".

Kodi's context menu on a Continue Watching or Next Up card offers **Remove from Continue Watching**: Silo
hides it (an episode's whole show) until it is played again.

Rows list the matching Kodi library items, so art, resume points and play behave as usual. In "what to watch
next" rows a show stands for its next episode; elsewhere for the show. Titles not synced to Kodi are skipped.

## Skyline skin

[Skyline](https://github.com/im85288/Skyline) lays Kodi out like the Silo TV apps, and the add-on
gives it what to show, as Home window properties a skin reads without running the plugin. Other skins can use
the same properties.

- **Pages and rows:** `silo.pages`, `silo.<page>.count` and, for each row, `silo.<page>.<n>.title` / `.path` /
  `.style` (`poster`, `landscape` or `ranked`) / `.type`, following the profile's Silo home and libraries;
  published when the service starts (from last time's copy at once, then fresh) and after every sync.
- **Home's and Requests' cards:** each row's titles as `silo.<page>.<n>.<card>.<field>` in Silo's order, so a
  row draws at once: title and captions, the hero's details and its IMDb, TMDB and Rotten Tomatoes ratings, art, progress, and the marks
  below. Rows further down load through the plugin, whose items carry the same marks as `silo.*` properties.
- **Marks:** `playcount` (a show with every episode watched counts as played), `new` (a show you've started
  with an unwatched episode aired in the last 7 days), `left` (a part-watched title's time left, or "Next
  Episode"), `dismiss` (removable from Continue Watching), `rank` (a numbered Top 10 row) and `place` (a title
  in the first ten of a chart row such as Trending This Week, IMDb Popular or Popular on..., numbered or not).
- **For its pages:** a title's trailers, watchlist state and ambient tint, person pages, collections, More
  Like This, the Live TV quick guide (with its channel groups on Left, as TiviMate's), catch-up and recent channels, Silo's markers for the seek bar, and the
  playing title's details for the player, with its season and next episode for Episodes and Next Episode
  buttons (`silokodi.playing.season_path`, `.episode_index`, `.next`, `.next_label`). `silokodi.signed_in` says whether you are signed in.
- **Keys:** with Skyline as the skin, the add-on installs keymaps in `userdata/keymaps`: Live TV's (as
  TiviMate's), the player's (Back on the pause screen) and Home's (the context menu key on a Continue
  Watching card), and takes them away for any other skin.

## Settings

- **Playback -> Highest resolution / HDR this TV shows.** Defaults to 1080p SDR, so Silo converts 4K and
  HDR titles for this device. Set what the TV and Kodi device really support to direct-play everything. For
  an NVIDIA SHIELD on a 4K Dolby Vision TV choose 4K and "Dolby Vision including profile 7", and turn on
  Kodi's own **Settings -> Player -> Videos -> Dolby Vision compatibility mode**, which converts profile 7
  (UHD Blu-ray remuxes) to 8.1. Without the profile 7 option Silo strips such files to HDR10 in a converted
  4K stream, which the SHIELD can't hardware-decode in Kodi; the add-on refuses those with a message.
- **Playback -> Live TV keys as TiviMate** (with Skyline): OK the recent channels, Left the quick guide.
- **Playback -> Top 10 rows** chooses which Home rows Skyline numbers, as Netflix's Top 10 (Skyline's own
  settings open the same choice). Until you choose: rows titled Trending This Week or IMDb Popular.
- **Playback -> Theme music on show pages** (on by default; Skyline's skin settings turn it off too).
- **Sync -> Choose libraries** limits which Silo libraries are synced.
- **Sync -> Rewrite every item** re-fetches every title (use after changing the relay port).
- **Sync -> Remove all Silo items** removes everything this add-on wrote to the Kodi library.

## How it fits together

| Part | File |
| --- | --- |
| Silo /api/v2 client, token refresh | `resources/lib/silokodi/api.py` |
| TV-code sign-in and profile choice | `resources/lib/silokodi/auth.py` |
| Sign-in from the Silo phone app; servers on the network | `pairing.py`, `discovery.py` |
| Silo JSON -> Kodi fields | `resources/lib/silokodi/mapping.py` |
| Kodi video database writer (adapted from PKC) | `resources/lib/silokodi/kodidb.py` |
| Fingerprint sync of movies, shows, seasons, episodes | `resources/lib/silokodi/sync.py` |
| Realtime events | `resources/lib/silokodi/events.py` |
| Playback v3 sessions | `resources/lib/silokodi/playback.py` |
| Loopback relay (artwork, media, plugin <-> service) | `resources/lib/silokodi/relay.py` |
| Background service | `resources/lib/silokodi/service.py` |
| Plugin entry: play, menu, widget rows | `resources/lib/silokodi/plugin.py` |
| Home's rows as properties; titles' details and their cache | `rows.py`, `library.py` |
| Home's saved layout and cards at start-up | `quickstart.py` |
| Watchlist, Favourites, History | `personal.py` |
| Live TV: recent channels, quick guide, catch-up | `channels.py`, `quick.py`, `catchup.py` |
| Skyline's keymaps; context menu items | `keymap.py`, `context.py` |

Items are written with add-on paths (`plugin://plugin.video.kodiconnectforsilo/movies/?mode=play&id=...`), so
playing one asks the add-on, which starts a Silo session and hands Kodi the stream.

Silo's artwork URLs are signed and change daily, so Kodi's art table holds stable local addresses
(`http://127.0.0.1:57578/art/<id>/<kind>`) that the service resolves to Silo's current URL. Kodi caches each
image once.

Only the service refreshes sign-in tokens (Silo rotates refresh tokens); the plugin asks the service over the
loopback relay to start playback and for everything else (`/play`, `/command/...`). Those two routes need a
token the service makes at each start and shares only as a Kodi window property, so another app on the
device, or a web page in its browser, can't send them.

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
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pyflakes resources tests default.py service.py context_dismiss.py
.venv/bin/python -m pytest tests
```

The tests run the sync against Kodi 21's real video database schema (`tests/fixtures/MyVideos131.sql`,
extracted from Kodi's `VideoDatabase.cpp`) and a fake Silo server. CI runs them on Python 3.8 (Kodi 20 on
Android, in the `python:3.8` image), 3.11 (Kodi 21) and 3.14. Kodi's embedded Python can lose
`datetime.strptime` after its first use, so dates are parsed by hand.

## Changes

### Unreleased
- **Missing films:** a sync that failed part way could leave records of films Kodi no longer had, and later
  films then overwrote each other's library rows. That can't happen now, and each sync restores any film it
  affected.

### v0.13.0 (2026-11-06)
- **Renamed:** SiloKodiConnect is now KodiConnectForSilo (`plugin.video.kodiconnectforsilo`), following
  [Silo's naming policy](https://github.com/Silo-Server/siloserver.org/blob/main/TRADEMARK.md). Kodi installs it
  as a new add-on. On its first start it turns the old one off, copies its sign-in, settings and sync records,
  and updates the library's paths, so nothing needs signing in or syncing again. You can then uninstall the
  old add-on.
- **Syncing again:** Silo took its list of box sets out of its API on 3 November, which stopped each sync
  after Movies, before TV. Box sets are now found through their films, a few hundred films per sync, and a box
  set problem can no longer stop a sync.
- **Skyline's YouTube page:** Search lists your past searches and Back steps through them, and a section's
  first row takes focus once it has loaded.

### v0.12.3 (2026-11-03)
- **Skyline's YouTube page:** its search box opens the keyboard and the page shows the YouTube add-on's
  results; Back closes them for the page's Home. The menu key on a video offers Play, or Play choosing
  its quality first, as the YouTube app's menu does.

### v0.12.2 (2026-11-03)
- **Steadier service:** an unexpected error in its once-a-second loop is logged and the loop goes on
  (before, syncs, row refreshes and progress checks stopped until Kodi restarted).
- **No repeated jobs:** progress checks and row refreshes no longer pile up behind a long sync or a film.
- Tests for the service's queue, its main loop, playback stopping, the artwork relay's start-up and the
  plugin's commands.

### v0.12.1 (2026-11-03)
- **Artwork after Kodi starts:** the artwork relay's port opens first thing, so a title Kodi hadn't
  cached yet (newly added) no longer stays blank until the next restart.
- **Continue Watching** follows a finished or stopped episode in about 2 s (was up to 7 s): Home's rows
  are fetched together, straight after playback stops.
- **Full resync** says it started (or that it waits for playback to stop), shows its progress, and
  fetches Silo's artwork again.
- **Remove and re-add every Silo item** (was "Remove all Silo items"): the library comes back at once.

### v0.12.0 (2026-11-02)
- **Remembered subtitles:** an episode starts with its show's subtitle choice, as Silo's apps keep it
  (subtitle-prefs), and a change you make is saved for the next episode, here or in Silo's apps.
- **Card menu:** Remove from Continue Watching, the watchlist, Rate, watched, and **Not interested** (Silo's
  recommendations leave the title out); Home's menu key, and "Silo" in Kodi's context menu elsewhere.
- **Shuffle** (`mode=shuffle`): a random unwatched film, a started show's next episode, or a random episode of
  a show or film of a box set.
- **Play for a skin's hero** (`mode=playitem`) and a short time-left label for Resume (`silo.left_short`).
- **Screensaver:** the library's backdrops and logos, slowly zooming (Settings > Interface > Screensaver).

### v0.11.2 (2026-11-02)
- Subtitles downloaded through Silo (its apps') reach Kodi with the file's own; they were left out.
- `mode=subtitles` (a skin's Search online): Silo's subtitle providers for the playing file; the chosen one is
  stored with the file and playback goes on from the same moment with it showing. A server with no provider on
  says so.

### v0.11.1 (2026-11-02)
- Dates read "21 Oct 2026" (Aired, the playing title's), and each episode carries its air date as text art
  (`siloaired`), so a skin writes every date one way whatever Kodi's region.

### v0.11.0 (2026-11-01)
For [Skyline](https://github.com/im85288/Skyline) 0.4.0, tested on an NVIDIA SHIELD (Kodi 21).
- **Box sets:** Silo's box sets (a TMDB collection's films, in a library with box sets on) become Kodi movie
  sets, with Silo's name, overview and art; a skin's grid can show one card per box set, and a box set opens
  its own page.
- **Ratings:** your 1-5 stars for a film or series, and Rotten Tomatoes' critics' score beside IMDb's and TMDB's.
- **Coming Soon** row from Silo's calendar.
- **Theme tunes** on show and episode pages, from Silo.
- **Dolby Atmos** passes through on live UHD channels.
- **Quick guide groups** on Left, as TiviMate's.
- Watchlist and Mark watched no longer pop up a notification: the page shows the new state.
- **Player buttons:** the playing episode's season and next episode, for a skin's Episodes and Next Episode.
- Plugin rows no longer reload whenever something starts or stops playing (a theme tune made pages lag).

### v0.10.0 (2026-11-01)
For [Skyline](https://github.com/im85288/Skyline) 0.3.0, tested on an NVIDIA SHIELD (Kodi 21).
- **Requests:** Silo's Discover and Yours rows, a full info page, season choice and search.
- **Faster Home:** rows published as cards in Silo's order (no plugin per row), far rows loaded later, a cache
  of titles' details, and Home drawn from last time's copy while the service starts.
- **Remove from Continue Watching**, through Silo's own dismissal.
- **Top 10 rows** chosen in the settings, chart places for a hero's "#3 in ..." line, New Episode and time-left
  marks, Silo's Watchlist, Favourites and History.
- For a skin: trailers, person pages, collections, the Live TV quick guide, catch-up that plays the past
  programme, TiviMate-style recent channels, Showcase player details and keys.
- Sign in from the Silo phone app.
- Fixes: marking unwatched in Kodi reaches Silo right after playback; a late intro with no credits after it
  counts as the credits; SVG logos (which Kodi can't draw) are skipped so the title shows; fully watched shows
  are marked in plugin rows; the relay's commands need a per-start token.

### v0.9.3 (2026-10-25)
- For the [Skyline](https://github.com/im85288/Skyline) skin: the profile's name and avatar (DiceBear
  pictures fetched as PNG; the sign-in token never goes to an outside image host), each title's ambient tint,
  More Like This, an episode's show and season paths, each library's collections with Silo's posters and
  counts, and a `mode=info` route that opens a title's info page.
- Continue Watching from the add-on's menu works again: Silo now addresses home rows by id.
- Browsed Silo rows are named after the row.

### v0.9.2 (2026-10-25)
- Publishes Silo's pages and rows, and whether you are signed in, for the
  [Skyline](https://github.com/im85288/Skyline) skin.
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
