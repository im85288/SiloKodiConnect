# -*- coding: utf-8 -*-
"""Titles' details from Kodi's library, as rows and info pages show them,
and a cache of them for the service: the lookups are most of the time a
Home row takes on a SHIELD (about 60 ms a title)."""
import json
import os
import threading

from . import kodi

MOVIE_FIELDS = ['title', 'plot', 'year', 'rating', 'ratings', 'runtime', 'genre', 'mpaa', 'art', 'resume',
                'playcount', 'file', 'premiered', 'streamdetails', 'dateadded', 'lastplayed', 'tagline', 'studio',
                'cast', 'director']
EPISODE_FIELDS = ['title', 'plot', 'rating', 'ratings', 'runtime', 'art', 'resume', 'playcount', 'file',
                  'firstaired', 'showtitle', 'season', 'episode', 'tvshowid', 'streamdetails', 'dateadded',
                  'lastplayed', 'cast', 'director']
CAST_LIMIT = 20
SHOW_FIELDS = ['title', 'plot', 'year', 'rating', 'ratings', 'genre', 'mpaa', 'art', 'file', 'premiered',
               'studio', 'episode', 'watchedepisodes', 'cast']


def details_of(kind, kodi_id, shows=None):
    """(details, show) for a movie, episode or show in Kodi's library; show is
    an episode's show (mpaa, genre), read once per `shows` dict."""
    if kind == 'movie':
        details = (kodi.jsonrpc('VideoLibrary.GetMovieDetails', movieid=kodi_id, properties=MOVIE_FIELDS)
                   or {}).get('moviedetails')
    elif kind == 'episode':
        details = (kodi.jsonrpc('VideoLibrary.GetEpisodeDetails', episodeid=kodi_id, properties=EPISODE_FIELDS)
                   or {}).get('episodedetails')
    else:
        details = (kodi.jsonrpc('VideoLibrary.GetTVShowDetails', tvshowid=kodi_id, properties=SHOW_FIELDS)
                   or {}).get('tvshowdetails')
    if not details:
        return None, None
    if details.get('cast'):
        details['cast'] = details['cast'][:CAST_LIMIT]
    show = None
    if kind == 'episode' and details.get('tvshowid'):
        shows = {} if shows is None else shows
        if details['tvshowid'] not in shows:
            shows[details['tvshowid']] = (kodi.jsonrpc('VideoLibrary.GetTVShowDetails', tvshowid=details['tvshowid'],
                                                       properties=['mpaa', 'genre']) or {}).get('tvshowdetails')
        show = shows[details['tvshowid']]
    return details, show


class DetailsCache(object):
    """details_of, remembered until Kodi reports the title changed (forget)
    or SiloKodiConnect writes the library (clear)."""

    VERSION = 1

    def __init__(self, fetch=details_of):
        self.fetch = fetch
        self.lock = threading.Lock()
        self.titles = {}
        self.shows = {}
        self.dirty = False  # changed since last saved

    def get(self, kind, kodi_id):
        key = (kind, kodi_id)
        with self.lock:
            if key in self.titles:
                return self.titles[key]
        details, show = self.fetch(kind, kodi_id, self.shows)
        found = {'details': details, 'show': show} if details else None
        with self.lock:
            if found:
                self.titles[key] = found
                self.dirty = True
        return found

    def forget(self, kind, kodi_id):
        with self.lock:
            gone = self.titles.pop((kind, kodi_id), None)
            # A show's watched count follows its episodes: forget the episode's
            # show (any show, if the episode wasn't cached).
            show_id = ((gone or {}).get('details') or {}).get('tvshowid') if kind == 'episode' else None
            for key in [key for key in self.titles if key[0] == 'tvshow']:
                if kind == 'episode' and (show_id is None or key[1] == show_id):
                    del self.titles[key]
            self.dirty = True

    def clear(self):
        with self.lock:
            self.titles.clear()
            self.shows.clear()
            self.dirty = True

    # ----- across restarts -----
    # Saved so Home's first rows after Kodi starts come from it, before a
    # single lookup; recheck() then confirms every title in the background.

    def save(self, path):
        with self.lock:
            titles = [[kind, kodi_id, value] for (kind, kodi_id), value in self.titles.items()]
            self.dirty = False
        with open(path + '.part', 'w') as handle:
            json.dump({'version': self.VERSION, 'titles': titles}, handle)
        os.replace(path + '.part', path)

    def load(self, path):
        try:
            with open(path) as handle:
                saved = json.load(handle)
        except (IOError, OSError, ValueError):
            return 0
        if saved.get('version') != self.VERSION:
            return 0
        with self.lock:
            for kind, kodi_id, value in saved.get('titles') or []:
                self.titles[(kind, kodi_id)] = value
        return len(saved.get('titles') or [])

    def recheck(self, keep_going=lambda: True):
        """Look every cached title up again; True if any had changed (the
        rows showing them should reload). keep_going() can stop it early."""
        with self.lock:
            keys = list(self.titles)
            self.shows.clear()
        changed = False
        for kind, kodi_id in keys:
            if not keep_going():
                break
            details, show = self.fetch(kind, kodi_id, self.shows)
            fresh = {'details': details, 'show': show} if details else None
            with self.lock:
                if self.titles.get((kind, kodi_id)) != fresh:
                    changed = True
                    self.dirty = True
                    if fresh:
                        self.titles[(kind, kodi_id)] = fresh
                    else:
                        self.titles.pop((kind, kodi_id), None)
        return changed
