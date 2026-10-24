# -*- coding: utf-8 -*-
"""The background service: owns the Silo session, keeps the library synced,
listens for Silo's realtime events and reports playback."""
import json
import queue
import threading
import time

import requests
import xbmc
import xbmcgui

from . import kodi, kodidb, mapping
from .api import ApiError, SignedOut, SiloClient
from .events import EventListener
from .playback import PlaybackError, PlaybackManager
from .relay import DEFAULT_PORT, Relay
from .store import Store
from .sync import Stopped, Syncer
from .syncdb import SyncDB

PROGRESS_POLL = 300          # seconds between resume-point checks while the socket is up
PROGRESS_POLL_OFFLINE = 120  # ... and while it is down
ART_RETRY = 3600             # don't ask Silo for the same missing artwork more often
ECHO_WINDOW = 90             # Kodi's own post-playback playcount update is not a user action


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

    def onPlayBackStopped(self):
        self._ended()

    def onPlayBackEnded(self):
        self._ended()

    def onPlayBackError(self):
        self._ended()

    def _ended(self):
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


class Monitor(xbmc.Monitor):
    def __init__(self, service):
        xbmc.Monitor.__init__(self)
        self.service = service

    def onNotification(self, sender, method, data):
        if method == 'VideoLibrary.OnUpdate':
            try:
                self.service.kodi_updated(json.loads(data))
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
                             should_stop=self.monitor.abortRequested, progress=self.sync_progress)
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
        self.library_update_pending = False
        self.deferred = []

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
        worker = threading.Thread(target=self.work, name='silokodi-worker')
        worker.daemon = True
        worker.start()
        while not self.monitor.waitForAbort(1):
            if self.store.changed_on_disk():
                self.store.reload()
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
            if self.deferred and not self.playing():
                for task in self.deferred:
                    self.enqueue(task)
                self.deferred = []
            if self.library_update_pending and self.tasks.empty():
                self.library_update_pending = not kodi.refresh_library_views(refresh_list=False)
        kodi.log('Stopping SiloKodiConnect')
        self.playback.on_stopped(self.player.last_time)
        self.stop_listener()
        self.relay.stop()
        self.tasks.put(None)
        worker.join(10)
        self.db.close()

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
            if self.tasks.empty() and self.syncer.changed:
                self.syncer.changed = False
                self.library_update_pending = not kodi.refresh_library_views()

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
        try:
            self.syncer.sync(self.excluded())
        finally:
            if self.progress_dialog:
                self.progress_dialog.close()
                self.progress_dialog = None
        kodi.log('Sync finished in %.0f s' % (time.time() - started))
        if first:
            self.db.set_meta('first_sync_done', True)
            kodi.notify(kodi.string(30141))

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

    def signed_out(self):
        if not self.signed_out_notice:
            self.signed_out_notice = True
            kodi.notify(kodi.string(30122))
        self.store.update(access_token=None, refresh_token=None)

    # ----- realtime events -----

    def on_event(self, channel, event, data):
        if channel == 'catalog' and event == 'catalog.item.changed' and data.get('content_id'):
            self.enqueue(('item', data['content_id'], data.get('library_id')))
        elif channel == 'catalog' and event in ('catalog.library.changed', 'library.changed'):
            if data.get('library_id'):
                self.enqueue(('library', str(data['library_id'])))
        elif channel == 'user_state' and event == 'user_state.changed':
            if data.get('profile_id') and data['profile_id'] != self.store.get('profile_id'):
                return
            content_id = data.get('content_id') or data.get('series_id')
            if content_id:
                self.enqueue(('state', content_id))

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
