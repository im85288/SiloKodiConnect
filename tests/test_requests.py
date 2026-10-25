# -*- coding: utf-8 -*-
from resources.lib.silokodi import mapping, plugin

SERIES = {
    'media_type': 'series', 'tmdb_id': 95396, 'title': 'Severance', 'year': 2022, 'vote_average': 8.39,
    'genres': ['Drama', 'Mystery'], 'networks': ['Apple TV'], 'status': 'Returning Series', 'number_of_seasons': 3,
    'cast': [{'name': 'Adam Scott', 'character': 'Mark Scout', 'profile_path': '/adam.jpg'}, {'name': ''}],
    'request': {'requestable': True},
    'seasons': [{'season_number': 0, 'name': 'Specials', 'episode_count': 4, 'availability': 'missing'},
                {'season_number': 1, 'episode_count': 9, 'availability': 'available'},
                {'season_number': 2, 'name': 'Season 2', 'episode_count': 10, 'availability': 'missing'},
                {'season_number': 3, 'name': 'Season 3', 'episode_count': 10, 'availability': 'missing',
                 'requested': True},
                {'season_number': 4, 'name': 'Season 4', 'episode_count': 0, 'availability': 'missing'}],
}


def test_only_aired_seasons_still_to_get_are_offered():
    summary = mapping.request_summary(SERIES)
    assert summary['seasons'] == [{'number': 2, 'label': 'Season 2'}]
    assert summary['requestable'] and summary['reason'] == ''


def test_request_summary_carries_the_info_page_facts():
    summary = mapping.request_summary(SERIES)
    assert summary['cast'] == [{'name': 'Adam Scott', 'role': 'Mark Scout',
                                'thumbnail': mapping.TMDB_IMAGES + 'w185/adam.jpg'}]
    assert summary['studios'] == ['Apple TV'] and summary['genres'] == ['Drama', 'Mystery']
    assert summary['card']['title'] == 'Severance' and summary['season_count'] == 3
    movie = mapping.request_summary({'media_type': 'movie', 'tmdb_id': 1, 'title': 'Dune', 'runtime': 155,
                                     'director': 'Denis Villeneuve', 'production_companies': ['Legendary'],
                                     'request': {'requestable': False, 'reason': 'already_available'}})
    assert movie['runtime'] == 155 and movie['directors'] == ['Denis Villeneuve']
    assert movie['studios'] == ['Legendary'] and movie['reason'] == 'Already available'


def test_search_words_may_hold_ampersands(monkeypatch):
    rows = []
    monkeypatch.setattr(plugin, 'request_row', lambda base, handle, row: rows.append(row))
    plugin.run(['plugin://x/', '1', '?mode=requests&search=tom & jerry=1&refresh=123'])
    plugin.run(['plugin://x/', '1', '?mode=requests&search=dune%20part&refresh=5'])
    plugin.run(['plugin://x/', '1', '?mode=requests&row=trending'])
    assert rows == ['search:tom & jerry=1', 'search:dune part', 'trending']
