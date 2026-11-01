# -*- coding: utf-8 -*-
import json
import time

import pytest

from resources.lib.silokodi import playback
from resources.lib.silokodi.api import ApiError, SignedOut, SiloClient, normalize_server_url
from resources.lib.silokodi.store import Store

from silo_fakes import movie


class Response(object):
    def __init__(self, status, body=None, reason=''):
        self.status_code = status
        self._body = body
        self.content = b'' if body is None else json.dumps(body).encode()
        self.reason = reason

    def json(self):
        if self._body is None:
            raise ValueError('no body')
        return self._body


class Session(object):
    """Records requests; answers from a queue of (method, path-suffix, Response)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.sent = []

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        self.sent.append((method, url, json, params, dict(headers or {})))
        for index, (want_method, suffix, response) in enumerate(self.answers):
            if want_method == method and url.split('?')[0].endswith(suffix):
                del self.answers[index]
                return response
        raise AssertionError('unexpected %s %s' % (method, url))

    def post(self, url, json=None, headers=None, timeout=None):
        return self.request('POST', url, json=json, headers=headers, timeout=timeout)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path))
    s.update(server_url='https://silo.test', access_token='old', refresh_token='r1',
             access_expires_at=time.time() + 3600, profile_id='p1')
    return s


def client_with(store, answers):
    client = SiloClient(store, '0.1.0', 'Living room')
    client.session = Session(answers)
    return client


def test_server_url_is_normalized():
    assert normalize_server_url(' silo.example.org/ ') == 'https://silo.example.org'
    assert normalize_server_url('http://192.168.1.49:8090/api/v2') == 'http://192.168.1.49:8090'


def test_requests_carry_profile_and_device(store):
    client = client_with(store, [('GET', '/api/v2/libraries', Response(200, {'items': []}))])
    client.libraries()
    headers = client.session.sent[0][4]
    assert headers['Authorization'] == 'Bearer old'
    assert headers['X-Profile-Id'] == 'p1'
    assert headers['X-Silo-Device-Id'] == store.get('device_id')
    assert headers['X-Silo-Device-Name'] == 'Living room'


def test_expired_access_token_is_refreshed_and_rotated(store):
    store.update(access_expires_at=time.time() - 1)
    client = client_with(store, [
        ('POST', '/api/v2/auth/refresh', Response(200, {'access_token': 'new', 'refresh_token': 'r2', 'expires_in': 3600})),
        ('GET', '/api/v2/libraries', Response(200, {'items': [{'id': '1'}]})),
    ])
    assert client.libraries() == [{'id': '1'}]
    assert client.session.sent[0][2] == {'refresh_token': 'r1'}
    assert client.session.sent[1][4]['Authorization'] == 'Bearer new'
    assert store.get('refresh_token') == 'r2'


def test_401_refreshes_once_then_retries(store):
    client = client_with(store, [
        ('GET', '/api/v2/libraries', Response(401, {'type': 'x/problems/invalid_token', 'status': 401})),
        ('POST', '/api/v2/auth/refresh', Response(200, {'access_token': 'new', 'refresh_token': 'r2', 'expires_in': 60})),
        ('GET', '/api/v2/libraries', Response(200, {'items': []})),
    ])
    assert client.libraries() == []


def test_revoked_session_signs_out(store):
    store.update(access_expires_at=0)
    client = client_with(store, [('POST', '/api/v2/auth/refresh', Response(401, {
        'type': 'https://siloserver.org/docs/api/v2/problems/session_expired', 'status': 401,
        'detail': 'Session has been revoked.'}))])
    with pytest.raises(SignedOut) as raised:
        client.libraries()
    assert raised.value.code == 'session_expired'


def test_problem_details_become_api_errors(store):
    client = client_with(store, [('GET', '/api/v2/progress', Response(422, {
        'type': 'https://siloserver.org/docs/api/v2/problems/validation_failed', 'status': 422,
        'detail': 'The request did not pass validation; see errors.',
        'errors': [{'location': 'header.x-profile-id', 'detail': 'The X-Profile-Id header is required.'}]}))])
    with pytest.raises(ApiError) as raised:
        list(client.progress())
    assert raised.value.code == 'validation_failed'
    assert 'X-Profile-Id' in raised.value.message


def test_pages_follow_cursors(store):
    client = client_with(store, [
        ('GET', '/api/v2/catalog', Response(200, {'items': [{'content_id': 'a'}], 'page': {'has_more': True, 'next_cursor': 'c1'}})),
        ('GET', '/api/v2/catalog', Response(200, {'items': [{'content_id': 'b'}], 'page': {'has_more': False}})),
    ])
    assert [i['content_id'] for i in client.catalog('1', 'movie')] == ['a', 'b']
    assert client.session.sent[1][3]['cursor'] == 'c1'
    assert client.session.sent[0][3]['type'] == 'movie' and client.session.sent[0][3]['limit'] == 100


# ----- playback -----

CAPABILITY = {'state': 'available', 'allowed': True, 'protocol_versions': [3], 'installation_id': 'inst-1',
              'deliveries': ['original_http', 'server_remux_hls', 'server_transcode_hls']}


def plan(delivery='original_http', url='/api/v2/stream/s1?st=signed', **timeline):
    return {'protocol_version': 3, 'outcome': 'playable', 'session_id': 's1',
            'playback_plan': {'delivery': delivery, 'session_id': 's1',
                              'stream': {'url': url, 'mime_type': 'video/x-matroska', 'headers': {}},
                              'timeline': dict({'stream_origin_seconds': 0, 'player_start_seconds': 0}, **timeline),
                              'subtitle': {'inventory': [
                                  {'source': 'external', 'codec': 'srt', 'url': '/api/v2/stream/s1/subtitles/0.srt?st=x'},
                                  {'source': 'embedded', 'codec': 'pgs', 'url': '/api/v2/stream/s1/subtitles/1.sup'}]}}}


def manager(store, answers, settings=None):
    client = client_with(store, answers)
    return playback.PlaybackManager(client, '0.1.0', lambda: settings or {'max_resolution': '2160p',
                                                                          'dynamic_range': 'dv'}), client


def test_start_builds_a_v3_request_and_returns_a_signed_url(store):
    item = movie(position_seconds=1325.0)
    mgr, client = manager(store, [('GET', '/api/v2/catalog/items/movie-1', Response(200, item)),
                                  ('GET', '/api/v2/playback/capabilities', Response(200, CAPABILITY)),
                                  ('POST', '/api/v2/playback/start', Response(201, plan()))])
    answer = mgr.start('movie-1', resume=True)
    body = client.session.sent[2][2]
    for key in ('installation_id', 'protocol_version', 'client_features', 'file_id', 'profile_id',
                'playback_attempt_id', 'quality_preference', 'subtitle_fidelity_preference', 'metered',
                'client_capabilities', 'client_playback_context'):
        assert key in body
    assert body['installation_id'] == 'inst-1'
    assert body['file_id'] == '120' and body['profile_id'] == 'p1'
    assert body['start_position'] == 1325.0
    # No header-authenticated media: the URL is signed, so Kodi needs no token.
    assert 'header_authenticated_media_v1' not in body['client_features']
    caps = body['client_capabilities']
    assert caps['max_resolution'] == '2160p' and caps['hdr_details']['dolby_vision_profiles'] == [5, 8]
    assert sorted(body['client_playback_context']['deliveries']) == ['hls', 'original_http']
    assert answer['url'] == 'https://silo.test/api/v2/stream/s1?st=signed'
    assert answer['subtitles'] == ['https://silo.test/api/v2/stream/s1/subtitles/0.srt?st=x']
    assert answer['hls'] is False and answer['start_offset'] == 0
    assert 'Authorization' not in json.dumps(answer)


def test_start_retries_once_after_installation_changed(store):
    mgr, client = manager(store, [
        ('GET', '/api/v2/catalog/items/movie-1', Response(200, movie())),
        ('GET', '/api/v2/playback/capabilities', Response(200, dict(CAPABILITY, installation_id='old'))),
        ('POST', '/api/v2/playback/start', Response(409, {'type': 'x/problems/installation_changed', 'status': 409})),
        ('GET', '/api/v2/playback/capabilities', Response(200, CAPABILITY)),
        ('POST', '/api/v2/playback/start', Response(201, plan()))])
    mgr.start('movie-1')
    assert client.session.sent[-1][2]['installation_id'] == 'inst-1'


def test_terminal_decision_explains_itself(store):
    mgr, _ = manager(store, [('GET', '/api/v2/catalog/items/movie-1', Response(200, movie())),
                             ('GET', '/api/v2/playback/capabilities', Response(200, CAPABILITY)),
                             ('POST', '/api/v2/playback/start', Response(201, {
                                 'outcome': 'terminal', 'terminal': {'reason': 'transcoding_disabled',
                                                                     'message': 'Transcoding is off'}}))])
    with pytest.raises(playback.PlaybackError, match='Transcoding is off'):
        mgr.start('movie-1')


def test_converted_stream_reports_positions_on_the_source_timeline(store):
    mgr, client = manager(store, [
        ('GET', '/api/v2/catalog/items/movie-1', Response(200, movie())),
        ('GET', '/api/v2/playback/capabilities', Response(200, CAPABILITY)),
        ('POST', '/api/v2/playback/start', Response(201, plan('server_transcode_hls', '/api/v2/playback/transcode/s1/master.m3u8?st=x',
                                                             stream_origin_seconds=600, player_start_seconds=12))),
        ('POST', '/api/v2/playback/s1/progress', Response(200, {'outcome': 'applied'})),
        ('POST', '/api/v2/playback/s1/progress', Response(200, {'outcome': 'applied'})),
        ('DELETE', '/api/v2/playback/s1', Response(200, {'outcome': 'stopped'}))])
    answer = mgr.start('movie-1')
    assert answer['hls'] is True and answer['start_offset'] == 12
    session = mgr.on_started()
    mgr.on_tick(30, False)
    mgr.on_tick(31, True)  # pausing reports at once
    mgr.on_stopped(40)
    sent = [s for s in client.session.sent if '/playback/s1' in s[1]]
    assert [s[2]['position'] for s in sent] == [630, 631, 640]
    assert [s[2]['sequence'] for s in sent] == [1, 2, 3]
    assert sent[1][2]['is_paused'] is True
    assert sent[2][0] == 'DELETE' and sent[2][2]['stop_id'] == session.stop_id
    assert session.stopped


def test_session_ended_by_admin_stops_reporting(store):
    mgr, client = manager(store, [
        ('GET', '/api/v2/catalog/items/movie-1', Response(200, movie())),
        ('GET', '/api/v2/playback/capabilities', Response(200, CAPABILITY)),
        ('POST', '/api/v2/playback/start', Response(201, plan())),
        ('POST', '/api/v2/playback/s1/progress', Response(410, {'type': 'x/playback_session_ended', 'status': 410}))])
    mgr.start('movie-1')
    mgr.on_started()
    mgr.on_tick(5, False)
    assert mgr.active.stopped
    mgr.on_stopped(6)  # no DELETE is sent for an ended session
    assert not [s for s in client.session.sent if s[0] == 'DELETE']


def test_dolby_vision_profile_7_option_declares_it():
    client, context = playback.capabilities('0.1.0', '2160p', 'dv7')
    assert client['hdr_details']['dolby_vision_profiles'] == [5, 7, 8]
    assert 'client_managed_dynamic_range_v1' in context['deliveries']['original_http']['validated_claims']
    client, context = playback.capabilities('0.1.0', '2160p', 'dv')
    assert 7 not in client['hdr_details']['dolby_vision_profiles']
    assert 'client_managed_dynamic_range_v1' not in context['deliveries']['original_http']['validated_claims']


def test_a_4k_hls_plan_is_refused_and_released(store):
    remux = plan('server_remux_hls', '/api/v2/playback/transcode/s1/master.m3u8?st=x')
    remux['playback_plan']['effective_recipe'] = {'height': 2160, 'video_codec': 'hevc'}
    remux['playback_plan']['transformations'] = [{'name': 'server_dv7_to_hdr10'}]
    mgr, client = manager(store, [('GET', '/api/v2/catalog/items/movie-1', Response(200, movie())),
                                  ('GET', '/api/v2/playback/capabilities', Response(200, CAPABILITY)),
                                  ('POST', '/api/v2/playback/start', Response(201, remux)),
                                  ('DELETE', '/api/v2/playback/s1', Response(200, {'outcome': 'stopped'}))])
    with pytest.raises(playback.PlaybackError, match='profile 7'):
        mgr.start('movie-1')
    assert client.session.sent[-1][0] == 'DELETE'
    assert mgr.pending is None


def test_watchlist_membership_add_and_remove(store):
    client = client_with(store, [
        ('GET', '/api/v2/watchlist/movie-1', Response(200, {'item_id': 'movie-1'})),
        ('GET', '/api/v2/watchlist/movie-2', Response(404, {'code': 'not_found', 'detail': 'not found'})),
        ('PUT', '/api/v2/watchlist/movie-2', Response(204)),
        ('DELETE', '/api/v2/watchlist/movie-1', Response(204)),
    ])
    assert client.in_watchlist('movie-1') is True
    assert client.in_watchlist('movie-2') is False
    client.add_to_watchlist('movie-2')
    client.remove_from_watchlist('movie-1')
    assert [(method, url.rsplit('/', 1)[1]) for method, url, *_ in client.session.sent] == [
        ('GET', 'movie-1'), ('GET', 'movie-2'), ('PUT', 'movie-2'), ('DELETE', 'movie-1')]
