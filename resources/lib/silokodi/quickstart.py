# -*- coding: utf-8 -*-
"""The first thing the service does: give a Silo-style skin's Home what it
showed last time, before loading the rest of the add-on.

Loading the add-on takes seconds on a small device (the web library alone
took 3 s on a SHIELD), and Home waits for its rows. This module uses only
Kodi's own modules and SQLite: it reads the layout and Home cards the
service saved, and sets them as Home window properties at once. The service
then sets them again with its full logic, and keeps them current.
"""
import json
import os
import sqlite3

import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'plugin.video.silokodiconnect'
ROW_STYLES = ('poster', 'landscape')


def saved(folder):
    """The saved layout and Home cards: ({}, {}) when there are none yet."""
    path = os.path.join(folder, 'silokodi.db')
    if not os.path.exists(path):
        return {}, {}
    connection = sqlite3.connect(path, timeout=5)
    try:
        values = dict(connection.execute("SELECT key, value FROM meta WHERE key IN ('layout', 'home_cards')"))
    except sqlite3.Error:
        return {}, {}
    finally:
        connection.close()
    return tuple(json.loads(values[key]) if values.get(key) else {} for key in ('layout', 'home_cards'))


def home_properties(layout, cards):
    """What to set: the layout, with only Home's row paths (other pages get
    theirs when first opened), and Home's cards."""
    props = {}
    for key, value in layout.items():
        parts = key.split('.')
        if len(parts) == 4 and parts[0] == 'silo' and parts[3] in ROW_STYLES and parts[1] != 'home':
            value = ''
        props[key] = value
    props.update(cards)
    return props


def apply():
    try:
        folder = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo('profile'))
        props = home_properties(*saved(folder))
    except Exception:  # never stop the service starting
        return 0
    home = xbmcgui.Window(10000)
    for key, value in props.items():
        home.setProperty(key, value)
    return len(props)
