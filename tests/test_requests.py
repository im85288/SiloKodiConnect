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


def test_marker_ranges_are_percent_of_the_player_timeline():
    from resources.lib.silokodi import upnext
    found = [('intro', 60.0, 120.0), ('credits', 2700.0, 3000.0), ('recap', 0.0, 30.0), ('intro', 3500.0, 3600.0)]
    assert upnext.marker_ranges(found, 3000.0) == {'intro': '2.00,4.00', 'credits': '90.00,100.00',
                                                    'recap': '0.00,1.00'}
    # A converted stream that starts 30 s in: the recap is gone, the rest shift.
    assert upnext.marker_ranges(found, 2970.0, origin=30.0)['intro'] == '1.01,3.03'
    assert 'recap' not in upnext.marker_ranges(found, 2970.0, origin=30.0)
    assert upnext.marker_ranges(found, 0) == {}


def test_a_title_passed_last_is_taken_as_written():
    argv = ['plugin://x/', '1', '?mode=widget&section=continue_watching&refresh=5&exclude_show=Law & Order: SVU']
    assert plugin.raw_tail(argv, 'exclude_show') == 'Law & Order: SVU'
    assert plugin.raw_tail(['plugin://x/', '1', '?mode=widget'], 'exclude_show') == ''
    assert plugin.raw_tail(['plugin://x/', '1', '?mode=widget&exclude_show=The%20Pitt'], 'exclude_show') == 'The Pitt'
