# -*- coding: utf-8 -*-
import sqlite3

from resources.lib.silokodi import channels

CHANNELS = [{'channelid': 1, 'label': 'BBC News'}, {'channelid': 2, 'label': 'Sky Sports'},
            {'channelid': 3, 'label': 'Never watched'}, {'channelid': 4, 'label': 'Hidden', 'hidden': True},
            {'channelid': 5, 'label': 'Premier Sports'}]
WATCHED = {1: 1000, 2: 3000, 4: 4000, 5: 2000}


def test_recent_channels_are_newest_first_without_the_playing_one():
    assert [c['channelid'] for c in channels.recent(CHANNELS, WATCHED)] == [2, 5, 1]
    assert [c['channelid'] for c in channels.recent(CHANNELS, WATCHED, playing=2)] == [5, 1]


def test_a_removed_channel_comes_back_once_watched_again():
    removed = {'5': 2000}
    assert [c['channelid'] for c in channels.recent(CHANNELS, WATCHED, removed=removed)] == [2, 1]
    assert [c['channelid'] for c in channels.recent(CHANNELS, {**WATCHED, 5: 5000}, removed=removed)] == [5, 2, 1]


def test_previous_is_the_last_channel_before_the_playing_one():
    assert channels.previous(CHANNELS, WATCHED, playing=2)['channelid'] == 5
    assert channels.previous([], WATCHED, playing=2) is None


def test_last_watched_times_come_from_the_newest_tv_database(tmp_path, monkeypatch):
    for name, rows in (('TV9.db', [(1, 50)]), ('TV46.db', [(1, 1000), (2, 0), (5, 2000)])):
        db = sqlite3.connect(str(tmp_path / name))
        db.execute('CREATE TABLE channels (idChannel INTEGER, iLastWatched INTEGER)')
        db.executemany('INSERT INTO channels VALUES (?, ?)', rows)
        db.commit()
        db.close()
    monkeypatch.setattr(channels.kodi, 'database_dir', lambda: str(tmp_path))
    assert channels.last_watched() == {1: 1000, 5: 2000}
