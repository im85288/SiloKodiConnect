# -*- coding: utf-8 -*-
"""Skyline's YouTube page: focus a section's first row once it has loaded."""
import xbmc
import xbmcgui

from resources.lib.silokodi import context


class FakePage(object):
    """The page as Kodi reports it: rows that fill after some polls."""

    def __init__(self, rows, section='library', active=True):
        self.rows = rows  # list id -> (polls until filled, items), or None while it stays loading
        self.section = section
        self.active = active
        self.polls = 0
        self.focused = None

    def cond(self, condition):
        if condition == 'Window.IsActive(1198)':
            return self.active
        kind, list_id = condition.split('(')[0], int(''.join(c for c in condition if c.isdigit())[:3])
        row = self.rows.get(list_id)
        if kind == 'Control.HasFocus':
            return self.focused == list_id
        if kind == 'Container':  # Container(n).IsUpdating
            return row is None or self.polls < row[0]
        return row is not None and self.polls >= row[0] and row[1] > 0  # Integer.IsGreater(...NumItems,0)

    def builtin(self, command):
        self.focused = int(command[len('SetFocus('):-1])


def run(monkeypatch, page):
    class Home(object):
        def __init__(self, *args):
            pass

        def getProperty(self, key):
            return page.section if key == 'silo.youtube.section' else ''

    class Monitor(object):
        def abortRequested(self):
            return False

        def waitForAbort(self, seconds):
            page.polls += 1

    monkeypatch.setattr(xbmcgui, 'Window', Home)
    monkeypatch.setattr(xbmc, 'Monitor', Monitor)
    monkeypatch.setattr(xbmc, 'getCondVisibility', page.cond)
    monkeypatch.setattr(xbmc, 'executebuiltin', page.builtin)
    context.youtube_focus('library', [511, 512, 513])


def test_focuses_the_first_row_once_it_has_loaded(monkeypatch):
    page = FakePage({511: (5, 15), 512: (1, 15), 513: (1, 3)})
    run(monkeypatch, page)
    assert page.focused == 511
    assert page.polls == 5


def test_passes_over_a_row_that_loaded_empty(monkeypatch):
    page = FakePage({511: (1, 0), 512: (3, 15), 513: (1, 3)})
    run(monkeypatch, page)
    assert page.focused == 512


def test_waits_for_a_row_still_loading_before_the_rows_after_it(monkeypatch):
    page = FakePage({511: (40, 15), 512: (1, 15), 513: (1, 3)})
    run(monkeypatch, page)
    assert page.focused == 511


def test_gives_up_when_the_section_changes(monkeypatch):
    page = FakePage({511: (5, 15)}, section='home')
    run(monkeypatch, page)
    assert page.focused is None


def test_focuses_a_control_once_it_shows(monkeypatch):
    page = FakePage({}, section='subscriptions')
    run(monkeypatch, page)  # installs the fakes (it waits for the library section, not shown)
    page.polls, page.focused = 0, None
    plain = page.cond
    monkeypatch.setattr(xbmc, 'getCondVisibility', lambda condition: (
        page.polls >= 3 if condition == 'Control.IsVisible(401)' else plain(condition)))
    context.youtube_focus('subscriptions', [], [401])
    assert page.focused == 401
    assert page.polls == 3


def test_home_is_the_section_while_none_is_chosen(monkeypatch):
    page = FakePage({301: (2, 15)}, section='')
    run(monkeypatch, page)  # installs the fakes
    page.polls, page.focused = 0, None
    context.youtube_focus('home', [301])
    assert page.focused == 301


def test_gives_up_when_nothing_loads(monkeypatch):
    page = FakePage({511: None, 512: None, 513: None})
    run(monkeypatch, page)
    assert page.focused is None
    assert page.polls == context.YOUTUBE_FOCUS_WAIT * 10
