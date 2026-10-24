# -*- coding: utf-8 -*-
import base64
import json

from resources.lib.silokodi import upnext

VERSION = {'marker_segments': [
    {'kind': 'credits', 'start_seconds': 2500.5, 'end_seconds': 2600},
    {'kind': 'recap', 'start_seconds': 0, 'end_seconds': 45},
    {'kind': 'intro', 'start_seconds': 60, 'end_seconds': 120},
]}


def episode(season, number, episodeid):
    return {'season': season, 'episode': number, 'episodeid': episodeid, 'tvshowid': 7, 'title': 'E%d' % number}


def test_markers_from_segments_or_older_fields():
    assert upnext.markers(VERSION) == [('recap', 0.0, 45.0), ('intro', 60.0, 120.0), ('credits', 2500.5, 2600.0)]
    older = {'intro': {'start': 30, 'end': 90}, 'credits': {'start': 2400, 'end': 2500}}
    assert upnext.markers({}, older) == [('intro', 30.0, 90.0), ('credits', 2400.0, 2500.0)]
    assert upnext.markers({'intro': {}}, None) == []


def test_next_episode_crosses_seasons_and_skips_specials():
    episodes = [episode(0, 1, 100), episode(1, 1, 1), episode(1, 2, 2), episode(2, 1, 3)]
    assert upnext.next_episode(episodes, episode(1, 1, 1))['episodeid'] == 2
    assert upnext.next_episode(episodes, episode(1, 2, 2))['episodeid'] == 3
    assert upnext.next_episode(episodes, episode(2, 1, 3)) is None
    assert upnext.next_episode(episodes, episode(0, 1, 100))['episodeid'] == 1


def test_payload_carries_credits_time_and_skip_ranges():
    data = upnext.payload(episode(1, 1, 1), episode(1, 2, 2), upnext.markers(VERSION))
    assert data['play_info'] == {'episodeid': 2}
    assert data['notification_offset'] == 2500
    assert data['skip_segments'] == [{'kind': 'recap', 'start': 0.0, 'end': 45.0},
                                     {'kind': 'intro', 'start': 60.0, 'end': 120.0}]
    assert 'play_url' not in data  # Up Next hands the choice back, so it plays as a library item


def test_payload_on_a_stream_starting_part_way_in():
    data = upnext.payload(episode(1, 1, 1), episode(1, 2, 2), upnext.markers(VERSION), origin=50)
    assert data['notification_offset'] == 2450
    assert data['skip_segments'] == [{'kind': 'intro', 'start': 10.0, 'end': 70.0}]


def test_without_markers_up_next_keeps_its_own_timing():
    data = upnext.payload(episode(1, 1, 1), episode(1, 2, 2), [])
    assert 'notification_offset' not in data and 'skip_segments' not in data


def test_encoding_matches_up_next():
    sent = upnext.encode({'a': 1})
    assert json.loads(base64.b64decode(sent[0])) == {'a': 1}
    # Up Next's play_action comes back the same way, as a JSON list in the notification.
    assert upnext.decode(json.dumps(upnext.encode({'episodeid': 2}))) == {'episodeid': 2}
    assert upnext.decode('nonsense') is None
