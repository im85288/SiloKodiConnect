# -*- coding: utf-8 -*-
"""A show's theme tune while its page is open, as Plex and Infuse play one.

Silo's own theme comes first (a theme.mp3 kept beside the media); else the
tune comes from Plex's public TV theme service, by the show's TVDB id (about
half of a library's shows have one there). A theme never interrupts what the viewer
is playing, and stops when the show's page closes (a video started from the
page replaces it anyway).

Kodi opens what it plays on its main thread, which also draws the page: a
web address held the page up until Plex answered. So a theme is fetched
into a small cache first and played from there, once the page has drawn its
episodes."""
import os
import threading
import time

import requests
import xbmc
import xbmcgui

URL = 'https://tvthemes.plexapp.com/%s.mp3'
PAGE = 'Window.IsVisible(movieinformation)'
PAGE_WAIT = 3.0  # how long a theme waits for the page it was asked for to open
# The page has drawn this show's episodes (Skyline's rail: a show page's list 61,
# an episode page's 64), or long enough has passed. Starting audio re-opens the
# SHIELD's sound output, which stalls the screen a moment: on a page still
# loading that held it up. The page is reused, so the rail must hold this
# show's episodes, not the last show's.
SHOW_RAIL, EPISODE_RAIL = 61, 64
DRAWN_WAIT = 6.0
KEEP = 40  # themes kept (about 1.2 MB each), the most recently played
AUDIO = ('.mp3', '.m4a')


class ThemePlayer(object):
    def __init__(self, enabled, folder, log=None, session=None):
        self.enabled = enabled
        self.folder = folder
        self.log = log or (lambda message: None)
        self.session = session or requests.Session()
        self.missing = set()  # TVDB ids Plex has no theme for
        self.url = None
        self.player = xbmc.Player()
        self.lock = threading.Lock()

    def offer(self, tvdb_id, silo=None, show='', rail=SHOW_RAIL):
        """A show's page (or one of its episodes') is opening: play the show's
        theme, if it has one, in the background. silo: (cache key, a function
        giving Silo's own tune as (bytes, extension) or None), preferred to
        Plex's. show: the show's title, rail: the page's episode list, which
        the theme waits for."""
        if not (tvdb_id or silo) or not self.enabled():
            return
        thread = threading.Thread(target=self._start, args=(str(tvdb_id or ''), silo, show, rail),
                                  name='silokodi-theme')
        thread.daemon = True
        thread.start()

    def fetch(self, tvdb_id, silo=None):
        """The theme as a local file (fetched once): Silo's own, else Plex's;
        None when neither has one."""
        if silo:
            path = self.cached('silo-%s' % silo[0]) or self.save('silo-%s' % silo[0], self.from_silo(silo[1]))
            if path:
                return path
        if not tvdb_id or tvdb_id in self.missing:
            return None
        return self.cached(tvdb_id) or self.save(tvdb_id, self.from_plex(tvdb_id))

    def cached(self, key):
        for extension in AUDIO:
            path = os.path.join(self.folder, key + extension)
            if os.path.exists(path):
                os.utime(path, None)  # recently played: kept longest
                return path
        return None

    def from_silo(self, download):
        try:
            return download()
        except Exception as error:  # Silo's grant or audio failed: Plex's instead
            self.log('Silo theme unavailable: %s' % error)
            return None

    def from_plex(self, tvdb_id):
        try:
            response = self.session.get(URL % tvdb_id, timeout=8)
        except requests.RequestException:
            return None  # unknown, asked again next time
        if response.status_code != 200 or not response.content:
            self.missing.add(tvdb_id)
            return None
        return response.content, '.mp3'

    def save(self, key, tune):
        if not tune:
            return None
        content, extension = tune
        if not os.path.isdir(self.folder):
            os.makedirs(self.folder)
        path = os.path.join(self.folder, key + extension)
        with open(path + '.part', 'wb') as handle:
            handle.write(content)
        os.rename(path + '.part', path)
        self.prune()
        return path

    def prune(self):
        files = sorted((os.path.getmtime(os.path.join(self.folder, name)), name)
                       for name in os.listdir(self.folder) if name.endswith(AUDIO))
        for _, name in files[:-KEEP]:
            os.remove(os.path.join(self.folder, name))

    def _start(self, tvdb_id, silo=None, show='', rail=SHOW_RAIL):
        path = self.fetch(tvdb_id, silo)
        if path and path == self.url and self.playing_ours():
            return  # still playing (back on the same show's page)
        if not path:
            self.stop()  # another show's theme shouldn't run on under this one
            return
        if not self.wait_for(PAGE, PAGE_WAIT):
            return
        self.wait_for_rail(show, rail)
        with self.lock:
            if not xbmc.getCondVisibility(PAGE):
                return
            if self.player.isPlaying() and not self.playing_ours():
                return  # the viewer's own playback
            self.player.play(path, xbmcgui.ListItem('Theme'), True)
            self.url = path

    @staticmethod
    def wait_for_rail(show, rail):
        """Until the page's rail lists this show's episodes (DRAWN_WAIT at most)."""
        deadline = time.time() + DRAWN_WAIT
        label = 'Container(%d).ListItem.TVShowTitle' % rail
        while time.time() < deadline:
            shown = xbmc.getInfoLabel(label)
            if shown and (not show or shown.lower() == show.lower()):
                time.sleep(0.4)  # drawn, then the tune
                return True
            time.sleep(0.2)
        return False

    @staticmethod
    def wait_for(condition, seconds):
        deadline = time.time() + seconds
        while not xbmc.getCondVisibility(condition):
            if time.time() > deadline:
                return False
            time.sleep(0.2)
        return True

    def playing_ours(self):
        try:
            return bool(self.url) and self.player.isPlayingAudio() and self.player.getPlayingFile() == self.url
        except RuntimeError:
            return False

    def tick(self):
        """Called by the service about every second: the page has closed."""
        if self.url and not xbmc.getCondVisibility(PAGE):
            self.stop()

    def stop(self):
        with self.lock:
            if self.playing_ours():
                self.player.stop()
            self.url = None
