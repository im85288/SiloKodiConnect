# -*- coding: utf-8 -*-
"""A screensaver of the library's artwork, as Apple TV's and Infuse's: a
title's backdrop, slowly drifting, with its clear logo (its name without
one), then the next, cross-fading. Silo's films and shows only: their art
comes through the add-on's relay, so it is already on the device's network
and costs no disk. Any skin can use it (Settings > Interface > Screensaver).
"""
import random

import xbmc
import xbmcgui

from . import kodi

SLIDE = 12  # seconds a title stays
FADE = 1500  # ms of cross-fade between titles
BATCH = 60  # titles fetched at a time, at random
W, H = 1920, 1080


def titles(rand=random):
    """Films and shows with a backdrop, in random order: [{'fanart', 'logo', 'name'}]."""
    found = []
    for method, key in (('VideoLibrary.GetMovies', 'movies'), ('VideoLibrary.GetTVShows', 'tvshows')):
        answer = kodi.jsonrpc(method, properties=['art', 'title'], sort={'method': 'random'},
                              limits={'start': 0, 'end': BATCH}) or {}
        found += answer.get(key) or []
    out = [{'fanart': (t.get('art') or {}).get('fanart'), 'logo': (t.get('art') or {}).get('clearlogo') or '',
            'name': t.get('title') or ''} for t in found if (t.get('art') or {}).get('fanart')]
    rand.shuffle(out)
    return out


class Screensaver(xbmcgui.WindowDialog):
    """Two backdrop layers taking turns (one fades in over the other), a logo
    and a name; closes when Kodi ends the screensaver."""

    def __init__(self):
        xbmcgui.WindowDialog.__init__(self)
        self.layers = []
        drift = [('conditional', 'effect=zoom start=100 end=108 center=auto time=%d condition=true' % (SLIDE * 1000 + FADE)),
                 ('visiblechange', 'effect=fade time=%d' % FADE)]
        for _ in range(2):
            layer = xbmcgui.ControlImage(0, 0, W, H, '', aspectRatio=2, colorDiffuse='FFBEBEBE')
            self.addControl(layer)
            layer.setAnimations(drift)
            layer.setVisible(False)
            self.layers.append(layer)
        self.logo = xbmcgui.ControlImage(120, H - 340, 640, 220, '', aspectRatio=2)
        self.name = xbmcgui.ControlLabel(120, H - 200, 1200, 90, '', font='font_MainMenu', textColor='FFEDEDED')
        for control in (self.logo, self.name):
            self.addControl(control)
            control.setAnimations([('visiblechange', 'effect=fade time=%d' % FADE)])
        self.ended = False

    def onAction(self, action):
        # Any key ends it (Kodi also says so to Monitor when it's the screensaver).
        self.ended = True
        self.close()

    def show_title(self, title):
        """The next backdrop, never through a see-through moment: on the top
        layer it fades in over the bottom one; on the bottom layer (already
        showing, covered by the top) it's revealed as the top fades out."""
        bottom, top = self.layers
        self.top_shown = not getattr(self, 'top_shown', False)
        if not self.top_shown:
            bottom.setImage(title['fanart'], False)
            bottom.setVisible(True)
            top.setVisible(False)
        else:
            top.setImage(title['fanart'], False)
            top.setVisible(True)
        self.logo.setImage(title['logo'], False)
        self.logo.setVisible(bool(title['logo']))
        self.name.setLabel(title['name'] if not title['logo'] else '')


class Monitor(xbmc.Monitor):
    def __init__(self, on_end):
        xbmc.Monitor.__init__(self)
        self.on_end = on_end

    def onScreensaverDeactivated(self):
        self.on_end()


def run():
    state = {'running': True}
    window = Screensaver()

    def end():
        state['running'] = False
    monitor = Monitor(end)
    window.show()
    queue = []
    while state['running'] and not window.ended and not monitor.abortRequested():
        if not queue:
            queue = titles()
            if not queue:
                break  # no artwork (not signed in, or nothing synced): Kodi's black screen
        window.show_title(queue.pop())
        for _ in range(SLIDE * 4):
            if monitor.waitForAbort(0.25) or not state['running'] or window.ended:
                break
        else:
            continue
        break
    window.close()
    del window, monitor
