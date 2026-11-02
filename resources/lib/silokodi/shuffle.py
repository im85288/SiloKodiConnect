# -*- coding: utf-8 -*-
"""Shuffle, as Netflix's "Play something": a random title to play from a
scope a skin names (mode=shuffle&scope=...&id=...).

    movies   a random film not yet watched (any film once all are)
    tvshows  a random show with episodes left, at its next one
    tvshow   a random episode of one show (id: its library id), unwatched
             first: for the sitcoms one dips into
    set      a random film of a box set (id: the movie set), unwatched first

Kodi plays what's chosen as any library item, through the add-on's own
play route, so a Silo session, resume and Up Next work as usual.
"""
import random

from . import kodi

FIELDS = ['playcount', 'title']


def pick(items, rand=random):
    """A random item, from the unwatched ones while there are any."""
    unwatched = [i for i in items if not i.get('playcount')]
    pool = unwatched or items
    return rand.choice(pool) if pool else None


def next_episode(episodes):
    """A show's next episode: the first unwatched in order, specials aside."""
    ordered = sorted((e for e in episodes if e.get('season')), key=lambda e: (e['season'], e['episode']))
    return next((e for e in ordered if not e.get('playcount')), None)


def choose(scope, item_id=None, rand=random):
    """{'movieid': n} or {'episodeid': n} to play, or None."""
    if scope == 'movies':
        films = (kodi.jsonrpc('VideoLibrary.GetMovies', properties=FIELDS) or {}).get('movies') or []
        film = pick(films, rand)
        return {'movieid': film['movieid']} if film else None
    if scope == 'set':
        found = (kodi.jsonrpc('VideoLibrary.GetMovieSetDetails', setid=int(item_id),
                              movies={'properties': FIELDS}) or {}).get('setdetails') or {}
        film = pick(found.get('movies') or [], rand)
        return {'movieid': film['movieid']} if film else None
    if scope == 'tvshow':
        episodes = (kodi.jsonrpc('VideoLibrary.GetEpisodes', tvshowid=int(item_id),
                                 properties=FIELDS + ['season', 'episode']) or {}).get('episodes') or []
        episode = pick([e for e in episodes if e.get('season')], rand)
        return {'episodeid': episode['episodeid']} if episode else None
    if scope == 'tvshows':
        shows = (kodi.jsonrpc('VideoLibrary.GetTVShows', properties=['episode', 'watchedepisodes']) or {}).get(
            'tvshows') or []
        started = [s for s in shows if (s.get('episode') or 0) > (s.get('watchedepisodes') or 0)]
        rand.shuffle(started)
        for show in started[:5]:  # a show whose unwatched episodes are all specials: the next one
            episodes = (kodi.jsonrpc('VideoLibrary.GetEpisodes', tvshowid=show['tvshowid'],
                                     properties=FIELDS + ['season', 'episode']) or {}).get('episodes') or []
            episode = next_episode(episodes)
            if episode:
                return {'episodeid': episode['episodeid']}
        return None
    return None
