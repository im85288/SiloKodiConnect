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


def _schedule():
    import datetime
    day = lambda d, h: datetime.datetime(2026, 10, d, h).timestamp()
    broadcasts = [{'broadcastid': 1, 'title': 'Late', 'start': day(27, 22), 'end': day(27, 23)},
                  {'broadcastid': 2, 'title': 'Breakfast', 'start': day(28, 7), 'end': day(28, 8)},
                  {'broadcastid': 3, 'title': 'Cricket', 'start': day(28, 8), 'end': day(28, 16)},
                  {'broadcastid': 4, 'title': 'Desk', 'start': day(28, 16), 'end': day(28, 19)},
                  {'broadcastid': 5, 'title': 'Football', 'start': day(28, 19), 'end': day(28, 22)},
                  {'broadcastid': 6, 'title': 'Old', 'start': day(10, 9), 'end': day(10, 10)},
                  {'broadcastid': 7, 'title': 'Tomorrow', 'start': day(29, 9), 'end': day(29, 10)}]
    return channels.schedule_rows(broadcasts, day(28, 20), archive=True), day


def test_a_schedule_has_day_headers_and_marks_now_and_catch_up():
    rows, _ = _schedule()
    assert [r.get('header') or r['title'] for r in rows] == [
        'Yesterday', 'Late', 'Today', 'Breakfast', 'Cricket', 'Desk', 'Football', 'Tomorrow', 'Tomorrow']
    football = next(r for r in rows if r.get('title') == 'Football')
    assert football['when'] == 'now' and not football['archive']
    assert next(r for r in rows if r.get('title') == 'Late')['archive']   # yesterday's, playable
    assert 'Old' not in [r.get('title') for r in rows]                    # beyond the catch-up days


def test_before_moving_in_the_schedule_starts_just_before_now():
    rows, _ = _schedule()
    assert [r.get('header') or r['title'] for r in channels.around_now(rows)] == [
        'Today', 'Cricket', 'Desk', 'Football', 'Tomorrow', 'Tomorrow']


def test_day_headers_read_like_tivimates():
    import datetime
    today = datetime.date(2026, 10, 28)
    assert [channels.day_header(today + datetime.timedelta(days=n), today) for n in (-2, -1, 0, 1, 2)] == [
        'Mon, 26 Oct', 'Yesterday', 'Today', 'Tomorrow', 'Fri, 30 Oct']


def test_every_row_knows_its_day_for_the_day_column():
    import datetime
    rows, _ = _schedule()
    assert [r['day'] for r in rows] == [0, 0, 1, 1, 1, 1, 1, 2, 2]
    assert [r['today'] for r in rows if r.get('header')] == [False, True, False]
    assert channels.day_label(datetime.date(2026, 10, 27)) == 'Tue, 27 Oct'
