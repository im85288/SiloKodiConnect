# -*- coding: utf-8 -*-
"""A person's titles not on the server, from TMDb Helper."""
from resources.lib.silokodi import credits, kodi


def entry(title, tmdb, kind='movie', year=2001, genre=()):
    return {'type': kind, 'title': title, 'label': title, 'year': year, 'genre': list(genre), 'rating': 7.86,
            'plot': 'A plot', 'uniqueid': {'tmdb': str(tmdb)}, 'filetype': 'file', 'file': 'plugin://x/?play',
            'art': {'poster': 'image://poster/', 'fanart': 'image://fanart/'}}


NEXT = {'type': 'unknown', 'label': 'Next page', 'filetype': 'directory', 'file': 'plugin://x/?info=stars_in_both&page=2'}


def answers(monkeypatch, pages):
    asked = []

    def jsonrpc(method, **params):
        asked.append(params['directory'])
        return {'files': pages[len(asked) - 1]}
    monkeypatch.setattr(kodi, 'jsonrpc', jsonrpc)
    return asked


def add_title(open_kodi, media, kodi_id, tmdb):
    with open_kodi() as db:
        db.cursor.execute("INSERT INTO uniqueid(media_id, media_type, value, type) VALUES (?, ?, ?, 'tmdb')",
                          (kodi_id, media, str(tmdb)))


def test_leaves_out_what_kodis_library_has(monkeypatch, open_kodi, kodi_db_path):
    add_title(open_kodi, 'movie', 1, 13)
    add_title(open_kodi, 'tvshow', 2, 456)
    answers(monkeypatch, [[entry('Forrest Gump', 13), entry('Elvis', 614934), entry('The Simpsons', 456, 'tvshow'),
                           entry('Band of Brothers', 4613, 'tvshow')]])
    cards = credits.missing('31', kodi_db_path)
    assert [(c['type'], c['title']) for c in cards] == [('movie', 'Elvis'), ('series', 'Band of Brothers')]
    assert cards[0]['tmdb'] == '614934' and cards[0]['rating'] == 7.9 and cards[0]['requestable']
    assert cards[0]['poster'] == 'image://poster/' and cards[0]['backdrop'] == 'image://fanart/'


def test_a_film_and_a_show_with_the_same_id_are_different_titles(monkeypatch, open_kodi, kodi_db_path):
    add_title(open_kodi, 'movie', 1, 100)
    answers(monkeypatch, [[entry('A film', 100), entry('A show', 100, 'tvshow')]])
    assert [c['title'] for c in credits.missing('31', kodi_db_path)] == ['A show']


def test_skips_talk_shows_and_titles_with_no_year(monkeypatch, kodi_db_path):
    answers(monkeypatch, [[entry('Kelly', 1, 'tvshow', genre=['Talk']), entry('The News', 2, 'tvshow', genre=['News']),
                           entry('Announced', 3, year=0), entry('A film', 4, genre=['Drama']), NEXT]])
    monkeypatch.setattr(credits, 'MAX_PAGES', 1)
    assert [c['title'] for c in credits.missing('31', kodi_db_path)] == ['A film']


def test_asks_for_more_pages_until_there_are_enough(monkeypatch, kodi_db_path):
    monkeypatch.setattr(credits, 'ENOUGH', 2)
    asked = answers(monkeypatch, [[entry('One', 1), NEXT], [entry('Two', 2), entry('One', 1), NEXT], [entry('Three', 3)]])
    assert [c['title'] for c in credits.missing('31', kodi_db_path)] == ['One', 'Two']
    assert len(asked) == 2 and 'tmdb_id=31&page=2' in asked[1]


def test_stops_at_the_last_page(monkeypatch, kodi_db_path):
    asked = answers(monkeypatch, [[entry('One', 1)]])
    credits.missing('31', kodi_db_path)
    assert len(asked) == 1


def test_nothing_to_ask_without_a_tmdb_id_or_an_answer(monkeypatch, kodi_db_path):
    assert credits.missing('', kodi_db_path) == []
    monkeypatch.setattr(kodi, 'jsonrpc', lambda method, **params: None)  # TMDb Helper isn't installed
    assert credits.missing('31', kodi_db_path) == []
