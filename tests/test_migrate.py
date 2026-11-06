# -*- coding: utf-8 -*-
"""The move from the add-on's old id: its data and the library's paths."""
import json
import os
import sqlite3

import pytest
import xbmc

from resources.lib.silokodi import kodi, kodidb, mapping, migrate

OLD = migrate.OLD_ROOT
NEW = mapping.PLUGIN_ROOT


@pytest.fixture
def folders(tmp_path, monkeypatch, kodi_db_path):
    old = tmp_path / 'addon_data' / migrate.OLD_ID
    new = tmp_path / 'addon_data' / 'new'
    (old / 'themes').mkdir(parents=True)
    new.mkdir()
    (old / 'account.json').write_text('{"server": "old"}')
    records = sqlite3.connect(str(old / 'silokodi.db'))
    records.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
    records.execute('INSERT INTO meta VALUES (?, ?)', ('layout', json.dumps({'silo.home.1.path': OLD + '?mode=widget'})))
    records.commit()
    records.close()
    (old / 'themes' / 'a.mp3').write_text('tune')
    monkeypatch.setattr(kodi, 'addon_data_dir', lambda: str(tmp_path / 'addon_data'))
    monkeypatch.setattr(kodi, 'profile_dir', lambda: str(new))
    monkeypatch.setattr(kodi, 'database_dir', lambda: os.path.dirname(kodi_db_path))
    monkeypatch.setattr(kodi, 'string', lambda number: 'moved')
    monkeypatch.setattr(xbmc, 'Monitor', lambda: type('M', (), {'waitForAbort': lambda self, seconds: False})())
    return old, new


def add_rows(open_kodi):
    with open_kodi() as db:
        db.cursor.execute('INSERT INTO path(idPath, strPath) VALUES (1, ?), (2, ?), (3, ?)',
                          (OLD + 'movies/', OLD + 'tvshows/abc/', 'plugin://plugin.video.youtube/'))
        db.cursor.execute('INSERT INTO files(idFile, idPath, strFilename) VALUES (1, 1, ?), (2, 3, ?)',
                          (OLD + 'movies/?mode=play&id=1&type=movie', 'video.mp4'))
        db.cursor.execute('INSERT INTO movie(idMovie, idFile, c00, c22) VALUES (1, 1, ?, ?)', ('A film', OLD + 'movies/'))
        db.cursor.execute('INSERT INTO episode(idEpisode, idFile, c00, c18, idShow, idSeason) VALUES (1, 1, ?, ?, 1, 1)',
                          ('An episode', OLD + 'tvshows/abc/?mode=play&id=2&type=episode'))


def test_copies_the_data_and_rewrites_the_library(folders, open_kodi, query, monkeypatch):
    calls = []
    monkeypatch.setattr(kodi, 'jsonrpc', lambda method, **params: calls.append((method, params)))
    old, new = folders
    add_rows(open_kodi)
    migrate.run()
    assert calls == [('Addons.SetAddonEnabled', {'addonid': migrate.OLD_ID, 'enabled': False})]
    assert json.loads((new / 'account.json').read_text()) == {'server': 'old'}
    assert (new / 'themes' / 'a.mp3').read_text() == 'tune'
    assert (old / 'account.json').exists()  # the old add-on's data stays
    records = sqlite3.connect(str(new / 'silokodi.db'))
    assert json.loads(records.execute("SELECT value FROM meta WHERE key = 'layout'").fetchone()[0]) == {
        'silo.home.1.path': NEW + '?mode=widget'}
    records.close()
    assert query('SELECT strPath FROM path ORDER BY idPath') == [
        (NEW + 'movies/',), (NEW + 'tvshows/abc/',), ('plugin://plugin.video.youtube/',)]
    assert query('SELECT strFilename FROM files ORDER BY idFile') == [
        (NEW + 'movies/?mode=play&id=1&type=movie',), ('video.mp4',)]
    assert query('SELECT c22 FROM movie') == [(NEW + 'movies/',)]
    assert query('SELECT c18 FROM episode') == [(NEW + 'tvshows/abc/?mode=play&id=2&type=episode',)]
    assert mapping.content_id_from_path(query('SELECT strFilename FROM files WHERE idFile = 1')[0][0]) == '1'


def test_runs_once(folders, open_kodi, query, monkeypatch):
    calls = []
    monkeypatch.setattr(kodi, 'jsonrpc', lambda method, **params: calls.append(method))
    migrate.run()
    add_rows(open_kodi)  # rows only the old add-on could have written, after the move
    migrate.run()
    assert len(calls) == 1
    assert query('SELECT strPath FROM path WHERE idPath = 1') == [(OLD + 'movies/',)]


def test_keeps_a_sign_in_this_add_on_already_has(folders, monkeypatch):
    monkeypatch.setattr(kodi, 'jsonrpc', lambda method, **params: None)
    _, new = folders
    (new / 'account.json').write_text('{"server": "new"}')
    migrate.run()
    assert json.loads((new / 'account.json').read_text()) == {'server': 'new'}
    assert not (new / 'silokodi.db').exists()


def test_a_fresh_install_has_nothing_to_move(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(kodi, 'addon_data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(kodi, 'profile_dir', lambda: str(tmp_path / 'new'))
    monkeypatch.setattr(kodi, 'jsonrpc', lambda method, **params: calls.append(method))
    migrate.run()
    assert calls == []


def test_a_failure_is_retried_at_the_next_start(folders, open_kodi, query, monkeypatch):
    monkeypatch.setattr(kodi, 'jsonrpc', lambda method, **params: None)
    _, new = folders
    add_rows(open_kodi)
    real = kodidb.open_video_db

    def locked(path, version):
        raise OSError('database is locked')
    monkeypatch.setattr(kodidb, 'open_video_db', locked)
    migrate.run()  # doesn't raise: the service still starts
    assert not (new / migrate.DONE).exists()
    monkeypatch.setattr(kodidb, 'open_video_db', real)
    migrate.run()
    assert (new / migrate.DONE).exists()
    assert query('SELECT strPath FROM path WHERE idPath = 1') == [(NEW + 'movies/',)]
