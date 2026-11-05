# -*- coding: utf-8 -*-
"""Skyline's YouTube page: Search's history list, and Back."""
import xbmc
import xbmcgui

from resources.lib.silokodi import context


class Page(object):
    """Window(Home)'s properties, the focused control and the history list's item."""

    def __init__(self, focus=None, section='', path='', label=''):
        self.props = {'silo.youtube.section': section} if section else {}
        self.focus = focus
        self.labels = {'Container(620).ListItem.FolderPath': path, 'Container(620).ListItem.Label': label}
        self.builtins = []
        self.focused_on = []

    def install(self, monkeypatch):
        page = self

        class Home(object):
            def __init__(self, *args):
                pass

            def getProperty(self, key):
                return page.props.get(key, '')

            def setProperty(self, key, value):
                page.props[key] = value

            def clearProperty(self, key):
                page.props.pop(key, None)

        def cond(condition):
            return condition == 'Control.HasFocus(%s)' % page.focus

        monkeypatch.setattr(xbmcgui, 'Window', Home)
        monkeypatch.setattr(xbmc, 'getCondVisibility', cond)
        monkeypatch.setattr(xbmc, 'getInfoLabel', lambda label: page.labels.get(label, ''))
        monkeypatch.setattr(xbmc, 'executebuiltin', page.builtins.append)
        monkeypatch.setattr(context, 'youtube_focus', lambda section, ids: page.focused_on.append((section, ids)))
        return page


QUERY = 'plugin://plugin.video.youtube/kodion/search/query/?q=top+gear'


def test_a_past_search_shows_its_results(monkeypatch):
    page = Page(path=QUERY, label='top gear').install(monkeypatch)
    context.youtube_pick()
    assert page.props['silo.youtube.query'] == 'top gear'
    assert page.props['silo.youtube.search'] == QUERY + '&hide_folders=true&hide_next_page=true'
    assert page.props['silo.youtube.section'] == 'search'
    assert page.focused_on == [('search', [610])]
    assert page.props['silo.youtube.history']  # the history list reloads


def test_new_search_opens_the_keyboard(monkeypatch):
    page = Page(path='plugin://plugin.video.youtube/kodion/search/input/').install(monkeypatch)
    opened = []
    monkeypatch.setattr(context, 'youtube_search', lambda: opened.append(True))
    context.youtube_pick()
    assert opened == [True]
    assert 'silo.youtube.search' not in page.props


def test_back_from_results_goes_to_the_history(monkeypatch):
    page = Page(focus=610, section='search').install(monkeypatch)
    page.props['silo.youtube.query'] = 'top gear'
    context.youtube_back()
    assert page.builtins == ['SetFocus(620)']
    assert page.props['silo.youtube.query'] == 'top gear'


def test_back_from_the_history_goes_home(monkeypatch):
    page = Page(focus=620, section='search').install(monkeypatch)
    page.props['silo.youtube.query'] = 'top gear'
    context.youtube_back()
    assert page.builtins == ['SetFocus(301)']
    assert 'silo.youtube.section' not in page.props and 'silo.youtube.query' not in page.props


def test_back_elsewhere_is_kodis(monkeypatch):
    page = Page(focus=410, section='subscriptions').install(monkeypatch)
    context.youtube_back()
    assert page.builtins == ['Action(Back)']
