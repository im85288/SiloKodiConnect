# -*- coding: utf-8 -*-
"""The first thing the service does: give a Silo-style skin's Home what it
showed last time, before loading the rest of the add-on.

Loading the add-on takes seconds on a small device (the web library alone
took 3 s on a SHIELD), and Home waits for its rows. This module uses only
Kodi's own modules and SQLite: it reads the layout and Home cards the
service saved, and sets them as Home window properties at once. The service
then sets them again with its full logic, and keeps them current.

It also opens the artwork relay's port first: Home's images point at the
relay, and an image Kodi asks for before anything listens fails, and isn't
asked for again while Kodi runs (new titles stayed blank). Kodi's requests
wait on the open port until the relay, seconds later, takes it over.
"""
import json
import os
import socket
import sqlite3

import xbmcaddon
import xbmcgui
import xbmcvfs

from .mapping import RELAY_PORT

ADDON_ID = 'plugin.video.kodiconnectforsilo'
ROW_STYLES = ('poster', 'landscape')
LISTENING = {}  # port: the socket opened here, for the relay to take over


def listen_early(port):
    """Open the relay's port before the relay can serve it; None if taken."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
        sock.listen(128)  # Home asks for dozens of images at once
    except OSError:
        sock.close()
        return None
    LISTENING[port] = sock
    return sock


def relay_port(addon):
    try:
        return int(addon.getSetting('relay_port') or 0) or RELAY_PORT
    except ValueError:
        return RELAY_PORT


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


def facts(folder):
    """The facts line the service saved (a skin's header shows it at once)."""
    try:
        connection = sqlite3.connect(os.path.join(folder, 'silokodi.db'), timeout=5)
        try:
            row = connection.execute("SELECT value FROM meta WHERE key = 'library_facts'").fetchone()
        finally:
            connection.close()
        return json.loads(row[0]) if row and row[0] else ''
    except (sqlite3.Error, ValueError):
        return ''


def apply():
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        listen_early(relay_port(addon))
        folder = xbmcvfs.translatePath(addon.getAddonInfo('profile'))
        props = home_properties(*saved(folder))
    except Exception:  # never stop the service starting
        return 0
    home = xbmcgui.Window(10000)
    props['silo.library.facts'] = facts(folder)
    for key, value in props.items():
        home.setProperty(key, value)
    return len(props)
