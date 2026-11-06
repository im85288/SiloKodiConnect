# -*- coding: utf-8 -*-
"""The add-on was SiloKodiConnect (plugin.video.silokodiconnect), and Kodi can't
rename an add-on: the new id installs beside the old one. On its first start
this one turns the old one off, takes a copy of its sign-in, settings and sync
records, and points the library's rows at the new id, so nothing needs signing
in or syncing again. The old add-on's own files stay until the user uninstalls
it. Each step can run again: a failure is retried at the next start."""
import os
import shutil
import sqlite3

import xbmc

from . import kodi, kodidb, mapping

OLD_ID = 'plugin.video.silokodiconnect'
OLD_ROOT = 'plugin://%s/' % OLD_ID
DONE = 'migrated_from_silokodiconnect'
SIGN_IN = 'account.json'
SYNC_DB = 'silokodi.db'
STOP_SECONDS = 30  # at most, for the old add-on's service to stop and close its database
# Where Kodi's library keeps a plugin path: (table, column).
LIBRARY_PATHS = (('path', 'strPath'), ('files', 'strFilename'), ('movie', 'c22'), ('episode', 'c18'))


def run():
    """Never stops the service from starting."""
    try:
        _migrate(os.path.join(kodi.addon_data_dir(), OLD_ID), kodi.profile_dir())
    except Exception as error:  # pylint: disable=broad-except
        kodi.log_error('Moving from %s failed, will retry: %s' % (OLD_ID, error))


def _migrate(old, new):
    marker = os.path.join(new, DONE)
    if os.path.exists(marker) or not os.path.isdir(old):
        return
    _stop_old(old)
    # The sign-in comes last: until it's there the service doesn't sync, so a
    # step that fails can't leave it writing a second copy of the library.
    copied = copy_data(old, new)
    rewrite_saved(new)
    path, version = kodidb.find_video_db(kodi.database_dir())
    if path:
        with kodidb.open_video_db(path, version) as db:
            rewrite_library(db.cursor)
    if copied:
        _copy_file(os.path.join(old, SIGN_IN), os.path.join(new, SIGN_IN))
    _move_screensaver()
    with open(marker, 'w') as handle:
        handle.write(OLD_ID)
    kodi.log('Moved from %s (data copied: %s)' % (OLD_ID, copied))
    kodi.notify(kodi.string(30217))


def _stop_old(old):
    try:
        kodi.jsonrpc('Addons.SetAddonEnabled', addonid=OLD_ID, enabled=False)
    except Exception as error:  # pylint: disable=broad-except
        kodi.log_warning('Could not turn off %s: %s' % (OLD_ID, error))  # not installed any more
        return
    # SQLite removes its write-ahead log when the last connection closes: the
    # old service's closing, a moment after it's told to stop.
    monitor = xbmc.Monitor()
    for _ in range(STOP_SECONDS * 2):
        if not os.path.exists(os.path.join(old, SYNC_DB + '-wal')):
            return
        if monitor.waitForAbort(0.5):
            return


def _copy_file(source, target):
    try:
        shutil.copy2(source, target)
    except FileNotFoundError:
        pass  # gone since it was listed: SQLite's own files, as the old service closes


def copy_data(old, new):
    """A copy of the old add-on's data, all but its sign-in, unless this add-on
    already has a sign-in of its own. Returns whether it copied."""
    if os.path.exists(os.path.join(new, SIGN_IN)):
        return False
    # Left by a try that failed: with a different database they'd corrupt it.
    for name in (SYNC_DB + '-wal', SYNC_DB + '-shm'):
        if os.path.exists(os.path.join(new, name)):
            os.remove(os.path.join(new, name))
    for folder, _, names in os.walk(old):
        target = os.path.normpath(os.path.join(new, os.path.relpath(folder, old)))
        if not os.path.isdir(target):
            os.makedirs(target)
        for name in names:
            if not (folder == old and name == SIGN_IN):
                _copy_file(os.path.join(folder, name), os.path.join(target, name))
    return True


def _move_screensaver():
    """Kodi's screensaver setting, if it names the old add-on's."""
    answer = kodi.jsonrpc('Settings.GetSettingValue', setting='screensaver.mode')
    if (answer or {}).get('value') == OLD_ID:
        kodi.jsonrpc('Settings.SetSettingValue', setting='screensaver.mode', value=mapping.ADDON_ID)


def rewrite_saved(folder):
    """The saved layout Home starts from holds the rows' plugin paths."""
    path = os.path.join(folder, SYNC_DB)
    if not os.path.exists(path):
        return
    connection = sqlite3.connect(path, timeout=60)
    try:
        with connection:
            connection.execute('UPDATE meta SET value = replace(value, ?, ?)', (OLD_ROOT, mapping.PLUGIN_ROOT))
    finally:
        connection.close()


def rewrite_library(cursor):
    """Library rows the old add-on wrote: the new id in their paths."""
    new_root = mapping.PLUGIN_ROOT
    for table, column in LIBRARY_PATHS:
        cursor.execute('UPDATE %s SET %s = ? || substr(%s, ?) WHERE %s LIKE ?' % (table, column, column, column),
                       (new_root, len(OLD_ROOT) + 1, OLD_ROOT + '%'))
