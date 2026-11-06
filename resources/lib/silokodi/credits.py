# -*- coding: utf-8 -*-
"""A person's titles Silo doesn't have, for Skyline's person page to request.

Silo only lists a person's titles in the library, so their other credits come
from the TMDb Helper add-on when it's installed, as a skin's widget gets them:
through its plugin path, so no TMDB key of our own. Kodi's library says which of
them are here. Whether Silo can request one shows on its request page, when it's
opened: asking first would take a request per title.
"""
import sqlite3

from . import kodi, kodidb

HELPER = 'plugin.video.themoviedb.helper'
CREDITS = 'plugin://%s/?info=stars_in_both&tmdb_type=person&tmdb_id=%%s&page=%%d' % HELPER
PROPERTIES = ['title', 'year', 'uniqueid', 'art', 'rating', 'plot', 'genre']
MAX_PAGES = 3  # each is 20 films and 20 shows, most popular first
ENOUGH = 24  # titles to show before asking for another page
# A guest spot on one of these isn't the person's work.
PASSING_THROUGH = {'talk', 'news'}
REQUEST_TYPES = {'movie': 'movie', 'tvshow': 'series'}


def missing(tmdb_id, database=None):
    """Cards (mapping.request_card's shape) for a person's titles not in Kodi's
    library, most popular first."""
    if not tmdb_id:
        return []
    cards, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        answer = kodi.jsonrpc('Files.GetDirectory', directory=CREDITS % (tmdb_id, page), media='video',
                              properties=PROPERTIES) or {}
        files = answer.get('files') or []
        found = [card for card in (credit_card(entry) for entry in files) if card]
        found = [card for card in found if (card['type'], card['tmdb']) not in seen]
        seen.update((card['type'], card['tmdb']) for card in found)
        here = in_library(found, database)
        cards += [card for card in found if (card['type'], card['tmdb']) not in here]
        if len(cards) >= ENOUGH or not any(is_next_page(entry) for entry in files):
            break
    return cards


def credit_card(entry):
    """TMDb Helper's entry as a request card; None for what can't be one: its
    Next page entry, a title not out yet with no year, a talk show."""
    kind = REQUEST_TYPES.get(entry.get('type'))
    tmdb = (entry.get('uniqueid') or {}).get('tmdb')
    if not kind or not tmdb or not entry.get('year'):
        return None
    if PASSING_THROUGH & {genre.lower() for genre in entry.get('genre') or []}:
        return None
    art = entry.get('art') or {}
    return {'type': kind, 'tmdb': str(tmdb), 'title': entry.get('title') or entry.get('label') or '',
            'year': entry['year'], 'overview': entry.get('plot') or '', 'poster': art.get('poster') or '',
            'backdrop': art.get('fanart') or '', 'rating': round(float(entry.get('rating') or 0), 1),
            'state': '', 'label': '', 'tint': '', 'requestable': True}


def is_next_page(entry):
    return entry.get('filetype') == 'directory' and 'info=stars_in_both' in (entry.get('file') or '')


def in_library(cards, database=None):
    """Which cards' (type, tmdb id) Kodi's library has, by the ids the sync wrote."""
    if not cards:
        return set()
    path = database or kodidb.find_video_db(kodi.database_dir())[0]
    if not path:
        return set()
    connection = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=10)
    try:
        return {('movie' if media == 'movie' else 'series', value) for media, value in connection.execute(
            "SELECT media_type, value FROM uniqueid WHERE type = 'tmdb' AND media_type IN ('movie', 'tvshow') "
            'AND value IN (%s)' % ', '.join('?' * len(cards)), [card['tmdb'] for card in cards])}
    finally:
        connection.close()
