# -*- coding: utf-8 -*-
"""The background service: owns the Silo session, keeps the library synced,
listens for Silo's realtime events and reports playback."""
import gc
import hashlib
import json
import os
import queue
import threading
import time

import requests
import xbmc
import xbmcgui

from . import jump, kodi, kodidb, mapping, tracks, upnext
from .api import ApiError, SignedOut, SiloClient
from .events import Debouncer, EventListener
from .playback import PlaybackError, PlaybackManager
from .relay import DEFAULT_PORT, Relay
from .store import Store
from . import rows as home_cards
from .library import DetailsCache
from .sync import MAX_ROWS, MOVIE_LIBRARY_TYPES, SHOW_LIBRARY_TYPES, Stopped, Syncer, collection_cards, home_row_items, page_of, skin_layout
from .syncdb import SyncDB

PROGRESS_POLL = 300          # seconds between resume-point checks while the socket is up
PROGRESS_POLL_OFFLINE = 120  # ... and while it is down
ART_RETRY = 3600             # don't ask Silo for the same missing artwork more often
ECHO_WINDOW = 90             # Kodi's own post-playback playcount update is not a user action
LIBRARY_QUIET = 120          # sync a library once Silo has sent no change for it this long...
LIBRARY_LIMIT = 900          # ...or this long after the first, during a long scan
ITEM_QUIET = 10
ITEM_LIMIT = 60
DICEBEAR = 'https://api.dicebear.com/'
COLLECTION_LIMIT = 500      # titles listed from one collection
AVATAR_TYPES = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/gif': '.gif'}


def kind_of(task):
    return task[0] if isinstance(task, tuple) else task


def release_memory():
    """Hand memory a sync freed back to Android. Python frees the objects,
    but Android's allocator keeps the space unless asked; on a 2 GB SHIELD
    the add-on otherwise left Kodi about 70 MB bigger after a sync."""
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL(None).mallopt(-101, 0)  # M_PURGE, bionic (Android 10+)
    except Exception:
        pass  # not Android, or an older one


# After Kodi starts, how long the sync and the check of the saved details
# wait, so Home's rows load first on a small device.
# Pages whose rows are published as cards, with how many cards a row has
# (the skin's slots: every slot costs it load time).
PUBLISHED_PAGES = {'home': 25, 'requests': 12}
STARTUP_QUIET = 60
PAGE_LOADING = 6  # seconds a page's rows show while they start loading (then empty ones hide)
DETAILS_SAVE_INTERVAL = 30


class Player(xbmc.Player):
    def __init__(self, service):
        xbmc.Player.__init__(self)
        self.service = service
        self.last_time = None

    def onAVStarted(self):
        session = self.service.playback.on_started()
        if session:
            self.last_time = None
            self.service.recently_played[session.content_id] = time.time()
            self.service.tell_upnext(session)
            self.show_markers(session)

    def onPlayBackStopped(self):
        self._ended()

    def onPlayBackEnded(self):
        self._ended()

    def onPlayBackError(self):
        self._ended()

    def show_markers(self, session):
        """Silo's intro, recap, credits and preview markers for the skin's
        seek bar (silokodi.markers.<kind>), as Silo's players colour them."""
        try:
            total = self.getTotalTime()
        except RuntimeError:
            total = 0
        ranges = upnext.marker_ranges(session.markers or [], total, session.origin)
        for kind in upnext.MARKER_KINDS:
            kodi.set_property('markers.' + kind, ranges.get(kind, ''))

    def _ended(self):
        for kind in upnext.MARKER_KINDS:
            kodi.clear_property('markers.' + kind)
        session = self.service.playback.on_stopped(self.last_time)
        if session:
            self.service.recently_played[session.content_id] = time.time()
            # Silo has the new watch state now; pull it in shortly.
            self.service.enqueue(('state', session.content_id))

    def tick(self):
        if not self.service.playback.active:
            return
        try:
            if not self.isPlayingVideo():
                return
            self.last_time = self.getTime()
            paused = xbmc.getCondVisibility('Player.Paused')
        except RuntimeError:
            return
        self.service.playback.on_tick(self.last_time, paused)


def library_facts(counts):
    """'1,087 films · 529 series · 28,374 episodes', leaving out what's missing."""
    parts = [('{:,} %s' % (single if n == 1 else plural)).format(n)
             for kind, single, plural in (('movie', 'film', 'films'), ('tvshow', 'series', 'series'),
                                          ('episode', 'episode', 'episodes'))
             for n in [counts.get(kind) or 0] if n]
    return '  ·  '.join(parts)


class Monitor(xbmc.Monitor):
    def __init__(self, service):
        xbmc.Monitor.__init__(self)
        self.service = service

    def onNotification(self, sender, method, data):
        if jump.handle(sender, method):  # a skin's A-Z bar
            return
        if tracks.handle(sender, method):  # a skin's player sheet
            return
        if method.endswith(upnext.PLAY_ACTION):
            # Up Next chose to play the next episode: open it as the library item.
            info = upnext.decode(data) or {}
            if info.get('episodeid'):
                kodi.jsonrpc('Player.Open', item={'episodeid': info['episodeid']})
            return
        if method == 'VideoLibrary.OnUpdate':
            try:
                update = json.loads(data)
                item = update.get('item') or {}
                if item.get('type') and item.get('id') is not None:
                    self.service.details.forget(item['type'], item['id'])
                    self.service.home_cards_soon()
                if not all(self.service.native_counts.values()):
                    self.service.native_recheck = True
                self.service.kodi_updated(update)
            except Exception as error:
                kodi.log_warning('Could not handle a Kodi library update: %s' % error)


class Service(object):
    def __init__(self):
        self.monitor = Monitor(self)
        self.version = kodi.version()
        self.store = Store(kodi.profile_dir())
        self.client = SiloClient(self.store, self.version, xbmc.getInfoLabel('System.FriendlyName'))
        self.db = SyncDB(kodi.profile_dir())
        self.port = kodi.setting_int('relay_port') or DEFAULT_PORT
        self.syncer = Syncer(self.client, self.db, self.open_kodi, self.port, log=kodi.log,
                             should_stop=self.monitor.abortRequested, progress=self.sync_progress,
                             wait_while_busy=self.wait_while_playing)
        self.relay = Relay(self.client, self.db, self.refresh_art, self.play, self.command, self.port, log=kodi.log,
                           media_allowed=self.media_allowed)
        self.playback = PlaybackManager(self.client, self.version, self.playback_settings, log=kodi.log,
                                        media_url=self.relay.media_url)
        self.player = Player(self)
        self.tasks = queue.Queue()
        self.listener = None
        self.recently_played = {}
        self.art_attempts = {}
        self.progress_dialog = None
        self.next_sync = 0
        self.next_progress = 0
        self.signed_out_notice = False
        self.published_signed_in = None
        self.home_rows = []  # the profile's Silo home rows, as last published
        self.details = DetailsCache()  # library details for rows, until they change
        self.home_cards = {}  # every page's rows as published properties (rows.py)
        self.page_sources = {}  # page -> [(row id, library id)], from the layout
        self.home_cards_due = 0  # when to publish them again (0: not due)
        self.native_counts = {}  # (Kodi library row path, refresh token) -> has items
        self.native_recheck = False  # set by a library change while a row is held back
        self.next_recheck = None  # when to confirm the details loaded from disk
        self.next_save = 0
        # The skin's layout as last published, and the pages viewed so far:
        # a page's rows load only once it has been opened (Home always).
        self.layout = {}
        self.viewed_pages = {'home'}
        self.refresh_token = None  # silokodi.library_updated, as last put in row paths
        self.loading_until = {}  # page -> when its silo.<page>.loading flag ends
        self.library_update_pending = False
        self.deferred = []
        self.library_events = Debouncer(quiet=LIBRARY_QUIET, limit=LIBRARY_LIMIT)
        self.item_events = Debouncer(quiet=ITEM_QUIET, limit=ITEM_LIMIT)

    # ----- Kodi's database -----

    def open_kodi(self):
        path, version = kodidb.find_video_db(kodi.database_dir())
        if not path:
            raise RuntimeError('Kodi video database not found')
        return kodidb.open_video_db(path, version)

    # ----- main loop -----

    def run(self):
        kodi.log('Starting SiloKodiConnect %s' % self.version)
        try:
            self.relay.start()
        except OSError as error:
            kodi.log_error('Cannot listen on 127.0.0.1:%d: %s' % (self.port, error))
            kodi.notify(kodi.string(30121) % self.port)
        self.start_quickly()
        worker = threading.Thread(target=self.work, name='silokodi-worker')
        worker.daemon = True
        worker.start()
        while not self.monitor.waitForAbort(1):
            if self.store.changed_on_disk():
                self.store.reload()
            self.publish_signed_in()
            if not self.store.signed_in:
                self.stop_listener()
                continue
            self.start_listener()
            now = time.time()
            if now >= self.next_sync:
                self.next_sync = now + 60 * max(5, kodi.setting_int('sync_interval') or 60)
                self.enqueue('sync')
            if now >= self.next_progress:
                connected = self.listener and self.listener.connected
                self.next_progress = now + (PROGRESS_POLL if connected else PROGRESS_POLL_OFFLINE)
                self.enqueue('progress')
            self.player.tick()
            self.note_viewed_page()
            self.note_refresh()
            if self.native_recheck and self.layout:
                # A library change may have filled a Kodi row held back as empty.
                self.native_recheck = False
                self.native_counts.clear()
                self.apply_layout()
            if self.home_cards_due and now >= self.home_cards_due and self.store.signed_in:
                self.home_cards_due = 0
                self.enqueue('homecards')
            self.end_loading()
            if self.next_recheck and now >= self.next_recheck and not self.playing():
                self.next_recheck = None
                self.enqueue('recheck')
            if now >= self.next_save and self.details.dirty:
                self.next_save = now + DETAILS_SAVE_INTERVAL
                self.save_details()
            for task in self.library_events.due() + self.item_events.due():
                self.enqueue(task)
            if self.deferred and not self.playing():
                for task in self.deferred:
                    self.enqueue(task)
                self.deferred = []
            if self.library_update_pending and self.tasks.empty():
                self.library_update_pending = not kodi.refresh_library_views(refresh_list=False)
        kodi.log('Stopping SiloKodiConnect')
        self.save_details()
        self.playback.on_stopped(self.player.last_time)
        self.stop_listener()
        self.relay.stop()
        self.tasks.put(None)
        worker.join(10)
        self.db.close()

    def start_quickly(self):
        """Give Home its rows as soon as Kodi starts: the last layout and the
        saved library details at once, then Silo's current layout before the
        sync, which waits a minute so it doesn't compete with Home loading on a
        small device (the first sync ever runs at once)."""
        self.publish_library_facts()  # a skin's header: what the library holds
        loaded = self.details.load(self.details_path())
        if loaded:
            self.next_recheck = time.time() + STARTUP_QUIET
        self.layout = self.db.meta('layout') or {}
        if self.layout:
            self.apply_layout()
        # Home's rows as last published: drawn at once, before any lookup.
        self.home_cards = self.db.meta('home_cards') or {}
        home = xbmcgui.Window(10000)
        for key, value in self.home_cards.items():
            home.setProperty(key, value)
        kodi.log('Started with %d saved titles and %s saved layout' % (loaded, 'a' if self.layout else 'no'))
        if self.store.signed_in:
            self.enqueue('layout')
            if self.db.meta('first_sync_done'):
                self.next_sync = time.time() + STARTUP_QUIET

    def details_path(self):
        return os.path.join(kodi.profile_dir(), 'details.json')

    def save_details(self):
        try:
            self.details.save(self.details_path())
        except (IOError, OSError) as error:
            kodi.log_warning('Could not save the library details: %s' % error)

    def start_listener(self):
        if self.listener is None or not self.listener.is_alive():
            self.listener = EventListener(self.client, self.on_event, self.monitor.abortRequested, log=kodi.log)
            self.listener.start()

    def stop_listener(self):
        if self.listener:
            self.listener.close()
            self.listener = None

    def enqueue(self, task):
        self.tasks.put(task)

    def work(self):
        """One worker, so Kodi database writes never overlap."""
        while True:
            task = self.tasks.get()
            if task is None or self.monitor.abortRequested():
                return
            try:
                self.do(task)
            except Stopped:
                return
            except SignedOut:
                self.signed_out()
            except (ApiError, requests.RequestException) as error:
                kodi.log_warning('Task %s failed: %s' % (task, error))
            except Exception as error:
                kodi.log_error('Task %s crashed: %s' % (task, error))
            if kind_of(task) in ('sync', 'fullsync', 'library'):
                release_memory()
            if self.tasks.empty() and self.syncer.changed:
                structural = self.syncer.added_or_removed
                if self.syncer.rewrote:
                    self.details.clear()
                else:
                    for kind, kodi_id in self.syncer.touched:
                        self.details.forget(kind, kodi_id)
                self.syncer.changed = self.syncer.added_or_removed = self.syncer.rewrote = False
                self.syncer.touched = set()
                updated = kodi.refresh_library_views(update_library=structural)
                self.library_update_pending = self.library_update_pending or not updated

    def do(self, task):
        if not self.store.signed_in:
            return
        kind = task[0] if isinstance(task, tuple) else task
        if kind in ('sync', 'fullsync', 'library') and self.playing():
            # A big sync during playback can starve a small device of memory
            # (Android killed Kodi mid-film on a 2 GB SHIELD). Wait until it stops.
            if task not in self.deferred:
                self.deferred.append(task)
            return
        if kind in ('sync', 'fullsync'):
            if kind == 'fullsync':
                self.db.forget_fingerprints()
            self.run_sync()
        elif kind == 'progress':
            self.poll_progress()
        elif kind == 'layout':
            self.publish_layout_safely()
            self.publish_home_cards()
        elif kind == 'homecards':
            self.publish_home_cards()
        elif kind == 'recheck':
            started = time.time()
            if self.details.recheck(lambda: not self.playing() and not self.monitor.abortRequested()):
                kodi.set_property('library_updated', str(int(time.time())))
            if self.playing():
                self.next_recheck = time.time() + STARTUP_QUIET  # interrupted: again after playback
            kodi.log('Checked the saved library details in %.0f s' % (time.time() - started))
        elif kind == 'item':
            self.syncer.resync_item(task[1], task[2])
        elif kind == 'library':
            library = self.syncer.library_by_id(task[1])
            if library and str(library['id']) not in self.excluded():
                self.syncer.sync_library(library)
        elif kind == 'state':
            self.syncer.refresh_state(task[1])
        elif kind == 'reset':
            for library_id in self.db.libraries():
                self.syncer.remove_library(library_id)
            self.db.clear()

    def wait_while_playing(self):
        """Pause a running sync while a video plays (it resumes afterwards)."""
        paused = False
        while self.playing() and not self.monitor.abortRequested():
            if not paused:
                kodi.log('Sync paused while a video plays')
                paused = True
            self.monitor.waitForAbort(5)
        if paused:
            kodi.log('Sync resumed')

    def playing(self):
        try:
            return self.player.isPlayingVideo()
        except RuntimeError:
            return False

    def excluded(self):
        return [value for value in (kodi.setting('excluded_libraries') or '').split(',') if value]

    def run_sync(self):
        # Until one sync has finished, keep showing progress, even across restarts.
        first = not self.db.meta('first_sync_done')
        show = first or kodi.setting_bool('show_sync_progress')
        if show:
            self.progress_dialog = xbmcgui.DialogProgressBG()
            self.progress_dialog.create('SiloKodiConnect', kodi.string(30140))
        started = time.time()
        # The skin's rows first: they only need Silo, not the sync.
        self.publish_layout_safely()
        try:
            self.syncer.sync(self.excluded())
        finally:
            if self.progress_dialog:
                self.progress_dialog.close()
                self.progress_dialog = None
        self.db.set_meta('history_since', self.syncer.sync_history(self.db.meta('history_since')))
        self.publish_layout_safely()
        self.publish_library_facts()
        kodi.log('Sync finished in %.0f s' % (time.time() - started))
        if first:
            self.db.set_meta('first_sync_done', True)
            kodi.notify(kodi.string(30141))

    def publish_library_facts(self):
        """A Silo-style skin's facts line: what the library holds."""
        counts = self.db.counts()
        xbmcgui.Window(10000).setProperty('silo.library.facts', library_facts(counts))
        self.db.set_meta('library_facts', library_facts(counts))

    def sync_progress(self, percent, message):
        if self.progress_dialog:
            self.progress_dialog.update(percent, 'SiloKodiConnect', message)

    def poll_progress(self):
        """Resume points set by other Silo apps, newest first, since the last check."""
        since = self.db.meta('progress_since')
        newest, entries = None, []
        for entry in self.client.progress():
            stamp = entry.get('updated_at') or ''
            if since and stamp <= since:
                break
            newest = newest or stamp
            entries.append(entry)
            if not since and len(entries) >= 200:
                break  # first run: the full sync already wrote current state
        if since:
            self.syncer.apply_progress(entries)
        if newest:
            self.db.set_meta('progress_since', newest)
        if self.db.meta('history_since'):
            self.db.set_meta('history_since', self.syncer.sync_history(self.db.meta('history_since')))

    def signed_out(self):
        if not self.signed_out_notice:
            self.signed_out_notice = True
            kodi.notify(kodi.string(30122))
        self.store.update(access_token=None, refresh_token=None)

    # ----- realtime events -----

    def on_event(self, channel, event, data):
        # Changes are held until Silo goes quiet: a scan sends many events, and
        # Silo publishes each library change twice (this name and the legacy
        # "library.changed", which is ignored).
        if channel == 'catalog' and event == 'catalog.item.changed' and data.get('content_id'):
            self.item_events.add(('item', data['content_id'], data.get('library_id')))
        elif channel == 'catalog' and event == 'catalog.library.changed':
            if data.get('library_id'):
                self.library_events.add(('library', str(data['library_id'])))
        elif channel == 'user_state' and event == 'user_state.changed':
            if data.get('profile_id') and data['profile_id'] != self.store.get('profile_id'):
                return
            content_id = data.get('content_id') or data.get('series_id')
            if content_id:
                self.enqueue(('state', content_id))

    # ----- Up Next -----

    def tell_upnext(self, session):
        """For a Silo episode, send Up Next the episode, the next one, when the
        credits start and the intro and recap ranges to offer skipping."""
        row = self.db.get(session.content_id)
        if not row or row['kind'] != 'episode':
            return
        try:
            current = (kodi.jsonrpc('VideoLibrary.GetEpisodeDetails', episodeid=row['kodi_id'],
                                    properties=upnext.EPISODE_PROPERTIES) or {}).get('episodedetails')
            if not current:
                return
            episodes = (kodi.jsonrpc('VideoLibrary.GetEpisodes', tvshowid=current['tvshowid'],
                                     properties=upnext.EPISODE_PROPERTIES) or {}).get('episodes') or []
            following = upnext.next_episode(episodes, current)
            data = upnext.payload(current, following, session.markers, session.origin)
            if not following and not data.get('skip_segments'):
                return  # the last episode, with nothing to skip: nothing to offer
            kodi.jsonrpc('JSONRPC.NotifyAll', sender=upnext.SENDER, message='upnext_data', data=upnext.encode(data))
            kodi.log('Told Up Next about %s (credits at %s s, %d skippable ranges)'
                     % (session.content_id, data.get('notification_offset'), len(data.get('skip_segments') or [])))
        except Exception as error:  # Up Next is a nicety; never break playback for it
            kodi.log_warning('Could not tell Up Next about %s: %s' % (session.content_id, error))

    # ----- Kodi -> Silo -----

    def kodi_updated(self, data):
        """The viewer marked something watched or unwatched in Kodi."""
        item = data.get('item') or {}
        if 'playcount' not in data or item.get('type') not in ('movie', 'episode'):
            return
        row = self.db.by_kodi(item['type'], item.get('id'))
        if not row:
            return
        content_id = row['content_id']
        if time.time() - self.recently_played.get(content_id, 0) < ECHO_WINDOW:
            return
        watched = int(data['playcount'] or 0) > 0
        if row['state'] == ('1' if watched else '0'):
            return  # Kodi is only repeating what we wrote
        try:
            if watched:
                self.client.mark_watched(content_id)
            else:
                self.client.mark_unwatched(content_id)
            self.db.set_state(content_id, '1' if watched else '0')
            kodi.log('Marked %s %s on Silo' % (content_id, 'watched' if watched else 'unwatched'))
        except (ApiError, requests.RequestException) as error:
            kodi.log_warning('Could not update watched state on Silo: %s' % error)

    # ----- relay callbacks -----

    def playback_settings(self):
        return {'max_resolution': kodi.setting('max_resolution') or '1080p',
                'dynamic_range': kodi.setting('dynamic_range') or 'sdr',
                'quality': 'auto'}

    def play(self, query):
        if not self.store.signed_in:
            return {'error': kodi.string(30123)}
        try:
            return self.playback.start(query.get('id'), query.get('resume') == '1')
        except PlaybackError as error:
            return {'error': str(error)}
        except SignedOut:
            self.signed_out()
            return {'error': kodi.string(30122)}
        except (ApiError, requests.RequestException) as error:
            kodi.log_warning('Playback start failed: %s' % error)
            return {'error': kodi.string(30124)}

    def command(self, name, query):
        if name == 'signed_in':
            self.store.reload()
            self.signed_out_notice = False
            self.stop_listener()
            self.next_sync = 0
        elif name == 'signout':
            self.client.logout()
            self.stop_listener()
            self.store.sign_out()
        elif name in ('sync', 'fullsync', 'reset'):
            self.enqueue(name)
        elif name == 'tint':
            # A title's ambient page tint, for an info page opened by the plugin.
            try:
                item = self.client.item(query.get('id') or '')
            except (ApiError, requests.RequestException):
                return {'tint': ''}
            return {'tint': mapping.ambient_tint(item.get('poster_thumbhash') or '')}
        elif name == 'sections':
            try:
                return {'sections': self.home_sections()}
            except (ApiError, requests.RequestException) as error:
                return {'error': str(error)}
        elif name == 'requests':
            try:
                return {'cards': self.request_cards(query.get('row') or 'mine')}
            except (ApiError, requests.RequestException) as error:
                return {'error': str(error)}
        elif name == 'request_detail':
            try:
                return mapping.request_summary(self.client.request_detail(query.get('type') or 'movie',
                                                                          query.get('tmdb') or ''))
            except (ApiError, requests.RequestException) as error:
                return {'error': str(error)}
        elif name == 'request':
            try:
                seasons = [int(n) for n in (query.get('seasons') or '').split(',') if n.strip().isdigit()]
                answer = self.make_request(query.get('type') or 'movie', query.get('tmdb') or '', seasons)
                self.home_cards_soon()  # Your requests shows it
                return answer
            except (ApiError, requests.RequestException) as error:
                return {'error': str(error)}
        elif name == 'collections':
            try:
                return {'collections': self.library_collections(query.get('libraries') or '')}
            except (ApiError, requests.RequestException) as error:
                return {'error': str(error)}
        elif name == 'widget' and query.get('collection'):
            try:
                return {'items': self.collection_items(query.get('library'), query['collection'])}
            except (ApiError, requests.RequestException) as error:
                return {'error': str(error)}
        elif name == 'widget':
            try:
                section = query.get('section') or 'continue_watching'
                return {'items': self.with_details(self.widget_items(section, query.get('library'))),
                        'title': self.row_title(section, query.get('library'))}
            except (ApiError, requests.RequestException) as error:
                return {'error': str(error)}
        elif name == 'list_libraries':
            excluded = self.excluded()
            try:
                return {'libraries': [{'id': str(library['id']), 'name': library.get('name') or '',
                                       'type': library.get('type') or '',
                                       'synced': str(library['id']) not in excluded}
                                      for library in self.syncer.libraries()]}
            except (ApiError, requests.RequestException) as error:
                return {'error': str(error)}
        else:
            return {'error': 'unknown command'}
        return {'ok': True}

    def home_sections(self):
        """The profile's Silo home rows, in Silo's order."""
        sections = self.client.get('/home/sections').get('sections') or []
        return [{'id': section['id'], 'title': section.get('title') or section['id'],
                 'type': section.get('section_type') or ''} for section in sections if section.get('id')]

    def row_title(self, section, library_id=None):
        """A home row's title in Silo, from the rows last published (no extra
        request: skins load many widgets at once); '' when not known."""
        if library_id:
            return ''
        return next((row.get('title') or '' for row in self.home_rows
                     if str(row.get('id')) == str(section) or row.get('section_type') == section), '')

    def library_collections(self, library_ids):
        """The Collections tabs of the given libraries (comma-separated ids), as
        cards that each know their library."""
        cards = []
        for library_id in [i for i in library_ids.split(',') if i.strip()]:
            for card in collection_cards(self.client.get('/library/%s/collections' % library_id)):
                card['library'] = library_id
                # Silo signs the poster link but gives it relative to the server.
                if card['poster'].startswith('/'):
                    card['poster'] = self.client.server_url.rstrip('/') + card['poster']
                cards.append(card)
        return cards

    def collection_items(self, library_id, collection_id):
        """A library collection's titles as Kodi library ids, in Silo's order."""
        items = []
        for item in self.client.pages('/library/%s/collections/%s/items' % (library_id, collection_id)):
            items.append(item)
            if len(items) >= COLLECTION_LIMIT:
                break
        return home_row_items({'items': items}, 'collection', self.db)

    def widget_items(self, section, library_id=None):
        """One Silo row (a home row, or a library's own) as Kodi library ids, in Silo's order."""
        if library_id:
            page = self.client.get('/library/%s/sections/%s/items' % (library_id, section))
        else:
            if not str(section).isdigit():
                # A row named by its type (the menu's Continue Watching, older
                # widget paths): Silo's home rows are addressed by id now.
                section = next((row['id'] for row in self.home_sections() if row['type'] == section), None)
                if not section:
                    return []
            page = self.client.get('/home/sections/%s/items' % section)
        return home_row_items(page, section, self.db)

    def with_details(self, items):
        """Row items with their cached library details, so the plugin needn't look them up."""
        for item in items:
            item['cached'] = self.details.get(item['type'], item['kodi_id'])
        return items

    def publish_signed_in(self):
        """silokodi.signed_in for a Silo-style skin, so it can tell "still
        starting" (no property yet) from "signed out"."""
        state = 'true' if self.store.signed_in else 'false'
        if state != self.published_signed_in:
            kodi.set_property('signed_in', state)
            self.published_signed_in = state

    def publish_layout_safely(self):
        try:
            self.publish_layout()
        except (ApiError, requests.RequestException) as error:
            kodi.log_warning('Could not read Silo\'s home layout: %s' % error)
        try:
            self.publish_avatar()
        except (ApiError, requests.RequestException, IOError) as error:
            kodi.log_warning('Could not read the profile\'s avatar: %s' % error)

    def publish_avatar(self):
        """The profile's name, initial and picture for a Silo-style skin's menu
        bar (Home window silo.profile_name, silo.profile_initial, silo.avatar).
        The picture is saved in the add-on's folder: Silo's link to an uploaded
        one expires within the hour. Without one the skin draws the initial."""
        profile_id = self.store.get('profile_id')
        profile = next((p for p in self.client.profiles() if p.get('id') == profile_id), {})
        name = profile.get('name') or self.store.get('profile_name') or ''
        home = xbmcgui.Window(10000)
        home.setProperty('silo.profile_name', name)
        home.setProperty('silo.profile_initial', name[:1].upper())
        url = profile.get('avatar_url') or ''
        home.setProperty('silo.avatar', self.save_avatar(url, profile.get('avatar') or url) if url else '')

    def save_avatar(self, url, ref):
        """A local copy of the avatar at url, named after its stored ref so it is
        fetched again only when the profile's picture changes; '' when the
        picture isn't a format Kodi draws."""
        folder = kodi.profile_dir()
        stem = 'avatar-%s' % hashlib.sha1(ref.encode('utf-8')).hexdigest()[:12]
        for name in os.listdir(folder):
            if name.startswith(stem + '.') and not name.endswith('.part'):
                return os.path.join(folder, name)
        server = self.client.server_url.rstrip('/')
        # Silo links DiceBear pictures as SVG, which Kodi can't draw; DiceBear
        # serves the same picture as PNG (Silo's Android app asks for that too).
        if url.startswith(DICEBEAR) and '/svg?' in url:
            url = url.replace('/svg?', '/png?', 1).replace('size=128', 'size=256')
        if url.startswith('/'):
            url = server + url
        # The sign-in token goes to the Silo server only, never to an image host.
        headers = self.client.auth_headers(profile=False) if url.startswith(server + '/') else {}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        extension = AVATAR_TYPES.get(response.headers.get('Content-Type', '').split(';')[0].strip().lower())
        if not extension:
            return ''
        for name in os.listdir(folder):
            if name.startswith('avatar-'):
                os.remove(os.path.join(folder, name))
        path = os.path.join(folder, stem + extension)
        with open(path + '.part', 'wb') as handle:
            handle.write(response.content)
        os.rename(path + '.part', path)
        return path

    def publish_layout(self):
        """Tell a Silo-style skin which pages and rows to show (Home window
        properties silo.*), following the profile's Silo home and libraries."""
        home = self.client.get('/home/sections').get('sections') or []
        self.home_rows = home
        libraries = self.syncer.libraries(self.excluded())
        library_sections = {}
        for library in libraries:
            try:
                library_sections[str(library['id'])] = (
                    self.client.get('/library/%s/sections' % library['id']).get('sections') or [])
            except ApiError as error:
                kodi.log_warning('No rows for library %s: %s' % (library.get('name'), error))
        request_rows = self.request_rows()
        self.layout = skin_layout(home, libraries, library_sections, request_rows)
        # Where each page's rows come from, in the layout's order, for its cards.
        self.page_sources = {'home': [(section['id'], None) for section in home if section.get('id')],
                             'requests': [(key, None) for key, _ in request_rows or []]}
        for page, types in (('movies', MOVIE_LIBRARY_TYPES), ('series', SHOW_LIBRARY_TYPES)):
            self.page_sources[page] = [(section['id'], str(library['id'])) for library in libraries
                                       if (library.get('type') or '').lower() in types
                                       for section in library_sections.get(str(library['id'])) or []]
        self.apply_layout()
        self.db.set_meta('layout', self.layout)
        kodi.set_property('layout_updated', str(int(time.time())))

    def apply_layout(self, page=None):
        """Set the layout's Home window properties: all of them, or (page) only
        that page's row paths. A row's path under its style is held back until
        its page has been viewed, so Kodi doesn't load the rows of pages the
        viewer hasn't opened (each row costs about a second on a SHIELD)."""
        home = xbmcgui.Window(10000)
        self.refresh_token = kodi.get_property('library_updated')
        for key, value in self.layout.items():
            row_page = page_of(key)
            if page is not None and row_page != page:
                continue
            if row_page is not None:
                # A row's path is empty until its page is viewed, so Kodi
                # waits instead of failing (a list that failed to load never
                # retries), and carries the refresh token so rows reload when
                # SiloKodiConnect writes the library.
                value = '%s&refresh=%s' % (value, self.refresh_token) \
                    if value and row_page in self.viewed_pages else ''
            if value.startswith('videodb://') and not self.native_has_items(value.split('&refresh=')[0]):
                # An empty Kodi library row (Continue Watching with nothing
                # part-watched) has no path, so the skin hides it at once
                # rather than showing its title while the page loads.
                value = ''
            home.setProperty(key, value)
        # A skin hides empty rows, and Kodi never loads a hidden list whose path
        # changed: silo.<page>.loading keeps a page's rows shown while they load.
        for viewed in ([page] if page is not None else list(self.viewed_pages)):
            home.setProperty('silo.%s.loading' % viewed, '1')
            self.loading_until[viewed] = time.time() + PAGE_LOADING

    def native_has_items(self, path):
        """Whether a Kodi library row's path lists anything; asked once per
        path until the library changes."""
        key = (path, self.refresh_token)
        if key not in self.native_counts:
            try:
                result = kodi.jsonrpc('Files.GetDirectory', directory=path, media='video',
                                      limits={'start': 0, 'end': 1}) or {}
                self.native_counts[key] = bool((result.get('limits') or {}).get('total'))
            except Exception:  # an unanswered row stays: Kodi loads it as before
                self.native_counts[key] = True
        return self.native_counts[key]

    def end_loading(self):
        now = time.time()
        # A skin's own pages (custom menu items) set their flag themselves.
        if 'custom' not in self.loading_until and xbmcgui.Window(10000).getProperty('silo.custom.loading') == '1':
            self.loading_until['custom'] = now + PAGE_LOADING
        for page, until in list(self.loading_until.items()):
            if now >= until:
                xbmcgui.Window(10000).clearProperty('silo.%s.loading' % page)
                del self.loading_until[page]

    def note_refresh(self):
        """When silokodi.library_updated changes (the library was written, or a
        request made), give the rows their paths with the new token, and
        publish Home's cards again."""
        if self.layout and kodi.get_property('library_updated') != self.refresh_token:
            self.apply_layout()
            self.home_cards_soon()

    def home_cards_soon(self, delay=2):
        """Publish Home's cards in a moment: changes come in bursts."""
        if not self.home_cards_due:
            self.home_cards_due = time.time() + delay

    def publish_home_cards(self):
        """Home's and Requests' rows, in Silo's order, as Home window
        properties a skin draws without a plugin (rows.py). Only changed
        values are set."""
        if not self.page_sources:
            return
        props = {}
        # Not Movies and Series: the rows Kodi can order load natively, and
        # every published slot costs the skin load time (see the skin).
        for page, count in PUBLISHED_PAGES.items():
            for row, (row_id, library_id) in enumerate(self.page_sources.get(page, [])[:MAX_ROWS], 1):
                try:
                    cards = self.page_cards(page, row_id, library_id)
                except (ApiError, requests.RequestException) as error:
                    kodi.log_warning('%s row %s: %s' % (page, row_id, error))
                    cards = None
                if cards is None:  # keep what the row showed
                    prefix = 'silo.%s.%d.' % (page, row)
                    props.update({key: value for key, value in self.home_cards.items() if key.startswith(prefix)})
                    continue
                props.update(home_cards.row_properties(row, cards[:count], page, count))
        home = xbmcgui.Window(10000)
        changed = 0
        for key, value in props.items():
            if self.home_cards.get(key) != value:
                home.setProperty(key, value)
                changed += 1
        for key in set(self.home_cards) - set(props):
            home.clearProperty(key)
        self.home_cards = props
        self.db.set_meta('home_cards', props)
        kodi.log('Rows published (%d values changed)' % changed)

    def page_cards(self, page, row_id, library_id):
        """One row's cards: library titles with their cached details; on the
        Requests page, titles Silo can request too."""
        if page == 'requests':
            entries = [(card.get('kind'), card.get('kodi_id'), card) for card in self.request_cards(row_id)]
        else:
            entries = [(item['type'], item['kodi_id'], item) for item in self.widget_items(row_id, library_id)]
        cards = []
        for kind, kodi_id, source in entries[:home_cards.CARDS]:
            if not kodi_id:
                cards.append(home_cards.request_card(source))
                continue
            cached = self.details.get(kind, kodi_id)
            if cached:
                card = home_cards.card(kind, cached['details'], cached.get('show'), source.get('tint'))
                if page == 'requests' and source.get('label'):
                    card['card.sub'] = card['card.wide_sub'] = source['label']
                cards.append(card)
        return cards

    def note_viewed_page(self):
        """When the skin shows a page for the first time, give its rows their paths."""
        page = xbmcgui.Window(10000).getProperty('silo.page') or 'home'
        if page not in self.viewed_pages:
            self.viewed_pages.add(page)
            if self.layout:
                self.apply_layout(page)


    def request_rows(self):
        """The Requests page's rows, or None when the profile can't request."""
        try:
            sections = self.client.discover_sections()
            mine = self.client.my_requests()
        except (ApiError, requests.RequestException) as error:
            kodi.log('No Requests page: %s' % error)
            return None
        rows = [('mine', 'Your requests')] if mine else []
        return rows + [(section['key'], section.get('title') or section['key'])
                       for section in sections if section.get('key') and section.get('results')]

    def request_cards(self, row):
        """A Requests row as cards (row: mine, a discovery key, search:<words>
        or similar:<type>:<tmdb id>);
        a title already in the library carries its Kodi item, so it opens and
        plays as usual."""
        if row.startswith('search:'):
            # Search: only what isn't in the library, which the skin lists itself.
            items = [item for item in self.client.request_search(row[len('search:'):])
                     if item.get('availability') != 'available']
        elif row.startswith('similar:'):
            # A request title's More Like This: Silo's recommendations for it.
            media_type, _, tmdb_id = row[len('similar:'):].partition(':')
            items = self.client.request_detail(media_type, tmdb_id).get('recommendations') or []
        else:
            items = self.client.my_requests() if row == 'mine' else self.client.discover_section(row)
        cards = []
        for item in items:
            if item.get('media_type') not in ('movie', 'series'):
                continue
            card = mapping.request_card(item)
            library = self.db.get(card['content_id']) if card['content_id'] else None
            if library and library.get('kodi_id') and library['kind'] in ('movie', 'tvshow'):
                card['kind'], card['kodi_id'] = library['kind'], library['kodi_id']
            cards.append(card)
        return cards

    def make_request(self, media_type, tmdb_id, seasons=None):
        """Request a title: for a series the given seasons, or every aired
        season not yet in the library (Silo's default)."""
        detail = self.client.request_detail(media_type, tmdb_id)
        state = detail.get('request') or {}
        if not state.get('requestable'):
            return {'error': mapping.request_summary(detail)['reason']}
        body = mapping.request_body(detail)
        if seasons and media_type == 'series':
            body['seasons'] = seasons
        answer = self.client.create_request(body)
        return {'state': mapping.REQUEST_STATES.get(answer.get('state'), 'Requested'), 'title': detail.get('title')}

    def media_allowed(self, path):
        return any(session_id in path for session_id in self.playback.session_ids())

    def refresh_art(self, content_id):
        if time.time() - self.art_attempts.get(content_id, 0) < ART_RETRY:
            return
        self.art_attempts[content_id] = time.time()
        if content_id.startswith('person:'):
            person = self.client.get('/catalog/people/%s' % content_id.split(':', 1)[1])
            if person.get('photo_url'):
                self.db.set_art(content_id, {'thumb': person['photo_url']})
            return
        row = self.db.get(content_id)
        if not row:
            return
        if row['kind'] == 'season':
            for season in self.client.seasons(row['parent_id']):
                key = season.get('content_id') or 'season:%s:%s' % (row['parent_id'], season.get('season_number'))
                self.db.set_art(key, mapping.artwork(season, mapping.SEASON_ART, self.port, key)[1])
        elif row['kind'] == 'episode':
            for episode in self.client.season_episodes(row['parent_id'], row['season_number']):
                self.db.set_art(episode['content_id'], mapping.artwork(episode, mapping.EPISODE_ART, self.port)[1])
        else:
            item = self.client.item(content_id)
            art_map = mapping.MOVIE_ART if row['kind'] == 'movie' else mapping.SHOW_ART
            self.db.set_art(content_id, mapping.artwork(item, art_map, self.port)[1])
            for key, url in mapping.person_art(item).items():
                self.db.set_art(key, {'thumb': url})


def run():
    Service().run()
