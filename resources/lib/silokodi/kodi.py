# -*- coding: utf-8 -*-
"""Thin wrappers around Kodi's Python modules.

Everything else imports Kodi through here, so the tests can replace these
modules with stand-ins.
"""
import json
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'plugin.video.silokodiconnect'
HOME = xbmcgui.Window(10000)


def addon():
    # A fresh Addon object reads the current settings; cached ones go stale.
    return xbmcaddon.Addon(ADDON_ID)


def version():
    return addon().getAddonInfo('version')


def profile_dir():
    path = xbmcvfs.translatePath(addon().getAddonInfo('profile'))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def database_dir():
    return xbmcvfs.translatePath('special://database/')


def string(string_id):
    return addon().getLocalizedString(string_id)


def setting(key):
    return addon().getSetting(key)


def setting_bool(key):
    return addon().getSettingBool(key)


def setting_int(key):
    return addon().getSettingInt(key)


def set_setting(key, value):
    addon().setSetting(key, value)


def log(message, level=xbmc.LOGINFO):
    xbmc.log('[%s] %s' % (ADDON_ID, message), level)


def log_debug(message):
    log(message, xbmc.LOGDEBUG)


def log_warning(message):
    log(message, xbmc.LOGWARNING)


def log_error(message):
    log(message, xbmc.LOGERROR)


def notify(message, heading='SiloKodiConnect', time=5000):
    xbmcgui.Dialog().notification(heading, message, addon().getAddonInfo('icon'), time)


def get_property(key):
    return HOME.getProperty('silokodi.%s' % key)


def set_property(key, value):
    HOME.setProperty('silokodi.%s' % key, value)


def clear_property(key):
    HOME.clearProperty('silokodi.%s' % key)


def jsonrpc(method, **params):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    return response.get('result')


def refresh_library_views(refresh_list=True, update_library=True):
    """Make Kodi show what was just written to its database. Returns False when
    a library update has to wait (in a library list or during playback).

    Kodi caches whether the library has movies and TV shows (the home menu
    hides TV shows until then) and only rechecks after a library update, so
    like PlexKodiConnect this runs UpdateLibrary(video) when titles were added
    or removed. Not for every change: skin helpers answer each library update
    with a rescan of the whole library (Aeon Tajo's took minutes). Silo's
    add-on paths are noUpdate, so Kodi's own scan doesn't touch them. In a
    library list it would move the cursor, so there it refreshes the list.
    """
    if refresh_list:
        set_property('library_updated', str(int(time.time())))
    if xbmc.Player().isPlaying():
        return not update_library
    if xbmc.getCondVisibility('Window.IsMedia'):
        if refresh_list:
            xbmc.executebuiltin('Container.Refresh')
        return not update_library
    if update_library:
        xbmc.executebuiltin('UpdateLibrary(video)')
    return True
