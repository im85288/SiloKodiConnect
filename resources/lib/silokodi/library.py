# -*- coding: utf-8 -*-
"""Titles' details from Kodi's library, as rows and info pages show them,
and a cache of them for the service: the lookups are most of the time a
Home row takes on a SHIELD (about 60 ms a title)."""
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

    def __init__(self, fetch=details_of):
        self.fetch = fetch
        self.lock = threading.Lock()
        self.titles = {}
        self.shows = {}

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
        return found

    def forget(self, kind, kodi_id):
        with self.lock:
            self.titles.pop((kind, kodi_id), None)
            # A show's watched count follows its episodes.
            for key in [key for key in self.titles if key[0] == 'tvshow']:
                del self.titles[key]

    def clear(self):
        with self.lock:
            self.titles.clear()
            self.shows.clear()
