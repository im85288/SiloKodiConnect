# -*- coding: utf-8 -*-
"""Stand-ins for Kodi's modules, and helpers shared by the tests."""
import os
import sqlite3
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


class _Anything(object):
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: ''


_module('xbmc', LOGDEBUG=0, LOGINFO=1, LOGWARNING=2, LOGERROR=3, log=lambda *a: None,
        Player=_Anything, Monitor=_Anything, executebuiltin=lambda *a: None,
        getCondVisibility=lambda *a: False, getInfoLabel=lambda *a: 'Test Kodi',
        executeJSONRPC=lambda *a: '{}', getGlobalIdleTime=lambda: 0)
_module('xbmcaddon', Addon=_Anything)
_module('xbmcgui', Window=_Anything, Dialog=_Anything, DialogProgress=_Anything,
        DialogProgressBG=_Anything, ListItem=_Anything)
_module('xbmcvfs', translatePath=lambda path: path, exists=lambda path: True, mkdirs=lambda path: True)
_module('xbmcplugin', setResolvedUrl=lambda *a: None, addDirectoryItem=lambda *a, **k: None,
        endOfDirectory=lambda *a, **k: None)

SCHEMA = os.path.join(os.path.dirname(__file__), 'fixtures', 'MyVideos131.sql')


@pytest.fixture
def kodi_db_path(tmp_path):
    path = str(tmp_path / 'MyVideos131.db')
    connection = sqlite3.connect(path)
    with open(SCHEMA) as handle:
        connection.executescript(handle.read())
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def open_kodi(kodi_db_path):
    from resources.lib.silokodi import kodidb

    def opener():
        return kodidb.open_video_db(kodi_db_path, 131)
    return opener


@pytest.fixture
def query(kodi_db_path):
    def run(sql, args=()):
        connection = sqlite3.connect(kodi_db_path)
        try:
            return connection.execute(sql, args).fetchall()
        finally:
            connection.close()
    return run
