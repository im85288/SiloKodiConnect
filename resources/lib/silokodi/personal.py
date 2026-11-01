# -*- coding: utf-8 -*-
"""The profile's own lists (watchlist, favourites, history), kept between
visits so a skin's page for one opens at once.

Silo pages history by watch, not by title: a page of 200 watches holds only
a handful of shows, so a long history takes many requests (14 for 103 titles,
about 2.5 s on a SHIELD). The lists are fetched in the background when the
service starts and after the profile's watch state changes, and a page shows
the kept copy; only a list never fetched is fetched while the page waits.
"""
import threading
import time

PAGE = 200       # Silo's largest page
LIMIT = 200      # titles kept from one list
FRESH = 600      # seconds a kept list is shown before it is fetched again

PATHS = ('/watchlist', '/favorites', '/history')


class PersonalLists(object):
    def __init__(self, client, profile_id, log=None, clock=time.time):
        self.client = client
        self.profile_id = profile_id  # a function: the profile signed in now
        self.log = log or (lambda message: None)
        self.clock = clock
        self._lock = threading.Lock()
        self._kept = {}  # path -> (profile id, fetched at, cards)
        self._busy = set()  # paths being fetched in the background

    def cards(self, path):
        """The list's cards: the kept copy for this profile, else fetched now.
        A copy older than FRESH is still shown, and fetched again behind it."""
        with self._lock:
            kept = self._kept.get(path)
        if not kept or kept[0] != self.profile_id():
            return self.fetch(path)
        if self.clock() - kept[1] > FRESH:
            self.refresh_soon([path])
        return kept[2]

    def fetch(self, path):
        profile = self.profile_id()
        started = self.clock()
        cards, requests = [], 0
        cursor = None
        while len(cards) < LIMIT:
            page = self.client.get(path, limit=PAGE, cursor=cursor)
            requests += 1
            cards.extend(page.get('items') or [])
            info = page.get('page') or {}
            cursor = info.get('next_cursor')
            if not info.get('has_more') or not cursor:
                break
        cards = cards[:LIMIT]
        with self._lock:
            self._kept[path] = (profile, self.clock(), cards)
        self.log('Fetched %s: %d titles in %d requests, %.2f s'
                 % (path, len(cards), requests, self.clock() - started))
        return cards

    def refresh_soon(self, paths=PATHS):
        """Fetch the lists again in a background thread (a list already being
        fetched is left to that fetch)."""
        with self._lock:
            paths = [path for path in paths if path not in self._busy]
            self._busy.update(paths)
        def work():
            for path in paths:
                try:
                    self.fetch(path)
                except Exception as error:  # a later visit fetches it instead
                    self.log('Could not fetch %s: %s' % (path, error))
                finally:
                    with self._lock:
                        self._busy.discard(path)
        thread = threading.Thread(target=work, name='silokodi-personal')
        thread.daemon = True
        thread.start()
        return thread

    def forget(self):
        with self._lock:
            self._kept.clear()
