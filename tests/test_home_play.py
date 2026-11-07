# -*- coding: utf-8 -*-
"""The Play/Pause key on Skyline's Home, Movies and Series."""
import pytest
import xbmc

from resources.lib.silokodi import context, plugin


@pytest.fixture
def home(monkeypatch):
    """Row 5101's focused card; labels maps 'Container(5101).ListItem.<x>' tails to values."""
    state = {'row': '5101', 'focus': '5101', 'labels': {}, 'ran': [], 'played': []}

    def info(label):
        if label == 'Window(Home).Property(silo.row)':
            return state['row']
        return state['labels'].get(label.replace('Container(%s).ListItem.' % state['row'], ''), '')
    monkeypatch.setattr(xbmc, 'getInfoLabel', info)
    monkeypatch.setattr(xbmc, 'getCondVisibility', lambda condition: condition == 'Control.HasFocus(%s)' % state['focus'])
    monkeypatch.setattr(xbmc, 'executebuiltin', state['ran'].append)
    monkeypatch.setattr(plugin, 'play_item', lambda kind, dbid: state['played'].append((kind, dbid)))
    return state


def test_a_published_card_plays_its_title(home):
    home['labels'] = {'Property(home.type)': 'tvshow', 'Property(home.dbid)': '42'}
    context.home_play()
    assert home['played'] == [('tvshow', '42')] and home['ran'] == []


def test_a_card_from_kodis_own_list_plays_too(home):
    home['labels'] = {'DBType': 'movie', 'DBID': '7'}
    context.home_play()
    assert home['played'] == [('movie', '7')]


def test_the_heros_buttons_play_the_last_rows_card(home):
    home['focus'] = '4802'  # More info has focus: the hero shows row 5101's card
    home['labels'] = {'Property(home.type)': 'movie', 'Property(home.dbid)': '3'}
    context.home_play()
    assert home['played'] == [('movie', '3')]


@pytest.mark.parametrize('focus, labels', [
    ('9002', {'Property(home.type)': 'movie', 'Property(home.dbid)': '3'}),  # the tab bar
    ('5101', {'Property(home.request.tmdb)': '550', 'Property(home.type)': 'movie'}),  # a title to request
    ('5101', {'Label': 'A studio'}),  # no library title
])
def test_anywhere_else_the_key_does_as_usual(home, focus, labels):
    home['focus'], home['labels'] = focus, labels
    context.home_play()
    assert home['played'] == [] and home['ran'] == ['Action(PlayPause)']
