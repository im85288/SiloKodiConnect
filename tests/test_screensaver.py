# -*- coding: utf-8 -*-
import random

from resources.lib.silokodi import screensaver


def test_titles_with_a_backdrop_only(monkeypatch):
    answers = {'VideoLibrary.GetMovies': {'movies': [
        {'title': 'Heat', 'art': {'fanart': 'f1', 'clearlogo': 'l1'}},
        {'title': 'No art', 'art': {}}]},
        'VideoLibrary.GetTVShows': {'tvshows': [{'title': 'Severance', 'art': {'fanart': 'f2'}}]}}
    monkeypatch.setattr(screensaver.kodi, 'jsonrpc', lambda method, **params: answers[method])
    found = screensaver.titles(random.Random(0))
    assert sorted(t['name'] for t in found) == ['Heat', 'Severance']
    assert {t['name']: t['logo'] for t in found} == {'Heat': 'l1', 'Severance': ''}
