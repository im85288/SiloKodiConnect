# -*- coding: utf-8 -*-
"""The audio track Silo picks, put on as a title starts."""
import time

import pytest

from resources.lib.silokodi import kodi, playeraudio
from resources.lib.silokodi.playback import Session

from resources.lib.silokodi.store import Store
from test_api_playback import CAPABILITY, Response, manager, plan
from silo_fakes import movie


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path))
    s.update(server_url='https://silo.test', access_token='old', refresh_token='r1',
             access_expires_at=time.time() + 3600, profile_id='p1')
    return s


def multi(**version):
    """A MULTi release: French first (the file's default), English second; Silo picks English."""
    item = movie()
    item['versions'][0].update(audio_tracks=[{'codec': 'eac3', 'language': 'fr', 'default': True},
                                             {'codec': 'eac3', 'language': 'en', 'default': False}],
                               effective_audio_language='en', effective_audio_track_index=1, **version)
    return item


def started(store, delivery):
    answers = [('GET', '/api/v2/catalog/items/movie-1', Response(200, multi())),
               ('GET', '/api/v2/playback/capabilities', Response(200, CAPABILITY)),
               ('POST', '/api/v2/playback/start', Response(201, plan(delivery)))]
    mgr, _ = manager(store, answers)
    mgr.start('movie-1')
    return mgr.pending


def test_direct_play_keeps_silos_track(store):
    session = started(store, 'original_http')
    assert (session.audio_track, session.audio_count, session.audio_language) == (1, 2, 'en')


def test_a_converted_stream_carries_it_already(store):
    assert started(store, 'server_transcode_hls').audio_track is None


@pytest.fixture
def player(monkeypatch):
    state = {'current': 0, 'count': 2, 'set': []}

    def jsonrpc(method, **params):
        if method == 'Player.GetProperties':
            return {'currentaudiostream': {'index': state['current']},
                    'audiostreams': [{'index': i} for i in range(state['count'])]}
        state['set'].append((method, params))
    monkeypatch.setattr(kodi, 'jsonrpc', jsonrpc)
    monkeypatch.setattr(playeraudio.time, 'sleep', lambda seconds: None)
    return state


def session(track=1, count=2):
    s = Session(None, 'inst-1', 's1', 'movie-1', {})
    s.audio_track, s.audio_count, s.audio_language = track, count, 'en'
    return s


def test_kodi_switches_to_silos_track(player):
    playeraudio.apply(session()).join()
    assert player['set'] == [('Player.SetAudioStream', {'playerid': 1, 'stream': 1})]


def test_nothing_when_it_already_plays(player):
    player['current'] = 1
    playeraudio.apply(session()).join()
    assert player['set'] == []


def test_nothing_when_kodi_lists_other_tracks(player):
    player['count'] = 3  # not the file Silo described: its ordinal could name another track
    playeraudio.apply(session()).join()
    assert player['set'] == []


def test_nothing_without_a_choice_from_silo(player):
    assert playeraudio.apply(session(track=None)) is None
    assert player['set'] == []
