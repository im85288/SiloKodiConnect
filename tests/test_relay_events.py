# -*- coding: utf-8 -*-
import json
import socket
from urllib.request import urlopen

import pytest

from resources.lib.silokodi import events, mapping
from resources.lib.silokodi.relay import Relay
from resources.lib.silokodi.syncdb import SyncDB


def free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class Image(object):
    def __init__(self, status, data=b'img'):
        self.status_code = status
        self.headers = {'Content-Type': 'image/webp', 'Content-Length': str(len(data))}
        self.data = data

    def iter_content(self, size):
        yield self.data

    def close(self):
        pass


class Client(object):
    server_url = 'https://silo.test'

    def base_headers(self):
        return {'User-Agent': 'test'}

    def auth_headers(self, profile=True):
        return {'User-Agent': 'test', 'Authorization': 'Bearer t'}

    def url(self, path):
        return self.server_url + path


@pytest.fixture
def relay(tmp_path):
    db = SyncDB(str(tmp_path))
    fetched = []
    refreshed = []

    def refresh(content_id):
        refreshed.append(content_id)
        db.set_art(content_id, {'poster': '/api/v2/artwork/new.webp?sig=fresh'})

    server = Relay(Client(), db, refresh, play=lambda q: {'url': 'u', 'id': q.get('id')},
                   command=lambda name, q: {'ok': name}, port=free_port())

    class FakeSession(object):
        def get(self, url, headers=None, stream=None, timeout=None):
            fetched.append((url, headers))
            return Image(200, b'fresh') if 'fresh' in url else Image(404)
    server.session = FakeSession()
    server.start()
    yield server, db, fetched, refreshed
    server.stop()


def get(relay, path):
    with urlopen('http://127.0.0.1:%d%s' % (relay.port, path), timeout=5) as response:
        return response.status, response.read()


def test_expired_artwork_is_refreshed_once(relay):
    server, db, fetched, refreshed = relay
    db.set_art('movie-1', {'poster': '/api/v2/artwork/old.webp?sig=yesterday'})
    status, body = get(server, '/art/movie-1/poster')
    assert (status, body) == (200, b'fresh')
    assert refreshed == ['movie-1']
    assert [url for url, _ in fetched] == ['https://silo.test/api/v2/artwork/old.webp?sig=yesterday',
                                           'https://silo.test/api/v2/artwork/new.webp?sig=fresh']


def test_unknown_artwork_is_404(relay):
    server, db, fetched, refreshed = relay
    server.refresh_art = lambda content_id: None
    with pytest.raises(Exception) as raised:
        get(server, '/art/nothing/poster')
    assert '404' in str(raised.value)


def test_play_and_command_routes(relay):
    server = relay[0]
    assert json.loads(get(server, '/play?id=movie-1&resume=1')[1]) == {'url': 'u', 'id': 'movie-1'}
    assert json.loads(get(server, '/command/sync')[1]) == {'ok': 'sync'}


def test_art_url_quotes_ids():
    assert mapping.art_url(1, 'person:7', 'thumb') == 'http://127.0.0.1:1/art/person%3A7/thumb'


def test_socket_url():
    assert events.socket_url('https://silo.test') == 'wss://silo.test/api/v2/events/ws?channels=catalog,user_state'
    assert events.socket_url('http://192.168.1.49:8090/') == \
        'ws://192.168.1.49:8090/api/v2/events/ws?channels=catalog,user_state'


def test_event_frames_reach_the_handler():
    seen = []
    listener = events.EventListener(None, lambda *args: seen.append(args), lambda: False)
    listener.handle(json.dumps({'type': 'hello', 'available_channels': ['catalog']}))
    listener.handle(json.dumps({'type': 'event', 'channel': 'catalog', 'event': 'catalog.item.changed',
                                'data': {'content_id': 'movie-1', 'library_id': '1'}}))
    listener.handle('not json')
    assert listener.connected
    assert seen == [('catalog', 'catalog.item.changed', {'content_id': 'movie-1', 'library_id': '1'})]
    with pytest.raises(RuntimeError):
        listener.handle(json.dumps({'type': 'access_changed'}))


def test_times_and_dates():
    assert mapping.kodi_date('2026-01-02T03:04:05.678Z') == '2026-01-02'
    assert mapping.parse_time('2026-01-02T03:04:05.6789Z').microsecond == 678900
    assert mapping.parse_time('nonsense') is None


def test_play_state_from_card():
    assert mapping.play_state({'user_state': {'played': True}, 'position_seconds': 50, 'runtime': 2})[:3] \
        == (1, 0.0, 120.0)
    assert mapping.play_state({'position_seconds': 50, 'duration_seconds': 3000})[:3] == (0, 50.0, 3000.0)
