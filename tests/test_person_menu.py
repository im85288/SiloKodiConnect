# -*- coding: utf-8 -*-
"""The context menu key on Skyline's person page."""
import pytest
import xbmc
import xbmcgui

from resources.lib.silokodi import context, kodi


@pytest.fixture
def page(monkeypatch):
    state = {'tmdb': '123', 'type': 'series', 'focused': True, 'choice': -1, 'ran': [], 'offered': None}

    class Dialog(object):
        def contextmenu(self, entries):
            state['offered'] = entries
            return state['choice']
    monkeypatch.setattr(xbmcgui, 'Dialog', Dialog)
    monkeypatch.setattr(kodi, 'string', lambda number: {30219: 'Request', 30220: 'Details'}[number])
    monkeypatch.setattr(xbmc, 'getInfoLabel', lambda label: state['tmdb'] if label.endswith('(silo.request.tmdb)') else state['type'])
    monkeypatch.setattr(xbmc, 'getCondVisibility', lambda condition: state['focused'])
    monkeypatch.setattr(xbmc, 'executebuiltin', state['ran'].append)
    return state


def test_request_goes_through_the_asking_request_action(page):
    page['choice'] = 0
    context.person_menu()
    assert page['offered'] == ['Request', 'Details']
    assert page['ran'] == ['RunPlugin(plugin://plugin.video.kodiconnectforsilo/?mode=request&type=series&tmdb=123)']


def test_details_opens_as_ok_does(page):
    page['choice'] = 1
    context.person_menu()
    assert page['ran'] == ['Action(Select)']


def test_backing_out_does_nothing(page):
    context.person_menu()
    assert page['ran'] == []


def test_a_library_title_gets_kodis_own_menu(page):
    page['tmdb'] = ''
    context.person_menu()
    assert page['offered'] is None and page['ran'] == ['Action(ContextMenu)']


def test_focus_off_the_grid_gets_kodis_own_menu(page):
    page['focused'] = False
    context.person_menu()
    assert page['offered'] is None and page['ran'] == ['Action(ContextMenu)']
