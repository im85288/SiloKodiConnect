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
    # Python 3.8 (Kodi 20 on Android) takes only 3 or 6 fraction digits.
    for value, micro in (('2026-01-02T03:04:05.6Z', 600000), ('2026-01-02T03:04:05.123456789Z', 123456),
                         ('2026-01-02T03:04:05.5+01:00', 500000), ('2026-01-02T03:04:05Z', 0)):
        assert mapping.parse_time(value).microsecond == micro
    assert mapping.parse_time('nonsense') is None


def test_play_state_from_card():
    assert mapping.play_state({'user_state': {'played': True}, 'position_seconds': 50, 'runtime': 2})[:3] \
        == (1, 0.0, 120.0)
    assert mapping.play_state({'position_seconds': 50, 'duration_seconds': 3000})[:3] == (0, 50.0, 3000.0)


def test_media_is_relayed_with_ranges_and_stats(tmp_path):
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from resources.lib.silokodi import relay as relay_module

    media = tmp_path / 'api' / 'v2' / 'stream'
    media.mkdir(parents=True)
    payload = bytes(range(256)) * 20000  # 5 MB
    (media / 'sess-1').write_bytes(payload)

    class RangeHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_head(self):
            ranged = self.headers.get('Range')
            if not ranged:
                return SimpleHTTPRequestHandler.send_head(self)
            start, end = [int(v) for v in ranged.split('=')[1].split('-')]
            body = payload[start:end + 1]
            self.send_response(206)
            self.send_header('Content-Type', 'video/x-matroska')
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, len(payload)))
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            import io
            return io.BytesIO(body)

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(RangeHandler, directory=str(tmp_path)))
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    class LocalClient(Client):
        server_url = 'http://127.0.0.1:%d' % upstream.server_address[1]

        def ensure_fresh_token(self):
            pass

    logged = []
    old_every = relay_module.STATS_EVERY
    relay_module.STATS_EVERY = 0
    server = Relay(LocalClient(), SyncDB(str(tmp_path)), lambda c: None, lambda q: {}, lambda n, q: {},
                   port=free_port(), log=logged.append, media_allowed=lambda path: 'sess-1' in path)
    server.start()
    try:
        url = server.media_url(LocalClient.server_url + '/api/v2/stream/sess-1?st=signed')
        assert url == 'http://127.0.0.1:%d/media/api/v2/stream/sess-1?st=signed' % server.port
        with urlopen(url, timeout=10) as response:
            assert response.read() == payload
        from urllib.request import Request
        with urlopen(Request(url, headers={'Range': 'bytes=1000-1999'}), timeout=10) as response:
            assert response.status == 206
            assert response.headers['Content-Range'] == 'bytes 1000-1999/%d' % len(payload)
            assert response.read() == payload[1000:2000]
        with pytest.raises(Exception) as refused:
            urlopen('http://127.0.0.1:%d/media/api/v2/stream/other' % server.port, timeout=5)
        assert '403' in str(refused.value)
        assert any(line.startswith('Relay: ') and 'waited on Silo' in line for line in logged)
    finally:
        relay_module.STATS_EVERY = old_every
        server.stop()
        upstream.shutdown()


def test_scan_events_are_held_until_silo_goes_quiet():
    now = [0.0]
    debouncer = events.Debouncer(quiet=120, limit=900, clock=lambda: now[0])
    for second in range(0, 300, 27):  # a scan: an event every 27 s for 5 minutes
        now[0] = second
        debouncer.add(('library', '2'))
        debouncer.add(('library', '2'))  # Silo sends some changes twice
        assert debouncer.due() == []
    now[0] = 297 + 119
    assert debouncer.due() == []
    now[0] = 297 + 120
    assert debouncer.due() == [('library', '2')]  # one sync for the whole scan
    assert debouncer.due() == []


def test_a_long_scan_still_syncs_every_limit():
    now = [0.0]
    debouncer = events.Debouncer(quiet=120, limit=900, clock=lambda: now[0])
    released = []
    for second in range(0, 2000, 30):
        now[0] = second
        debouncer.add('library')
        released += [second for _ in debouncer.due()]
    assert released == [900, 1830]  # each window starts at the first event after the last sync


def test_skyline_properties_for_an_episode_and_a_show():
    episode = {'season': 2, 'episode': 2, 'firstaired': '2026-09-25', 'tvshowid': 41,
               'ratings': {'imdb': {'rating': 8.41, 'default': True}, 'themoviedb': {'rating': 7.0}},
               'cast': [{'name': 'Helen Mirren', 'order': 1}, {'name': 'Tom Hardy', 'order': 0},
                        {'name': 'Pierce Brosnan', 'order': 2}, {'name': 'Paddy Considine', 'order': 3}],
               'streamdetails': {'video': [{'width': 3840, 'height': 2160, 'hdrtype': 'dolbyvision'}],
                                 'audio': [{'codec': 'eac3', 'channels': 6}]}}
    props = mapping.skyline_properties('episode', episode, {'mpaa': 'TV-MA', 'genre': ['Crime', 'Drama']})
    assert props['silo.code'] == 'S02E02'
    assert props['silo.aired'] == 'Aired Sep 25, 2026'
    assert props['silo.show_path'] == 'videodb://tvshows/titles/41/'
    assert props['silo.season_path'] == 'videodb://tvshows/titles/41/2/'
    assert props['silo.mpaa'] == 'TV-MA' and props['silo.genre'] == 'Crime'
    assert props['silo.genres'] == 'Crime, Drama'
    assert props['silo.rating'] == 'IMDb 8.4'
    assert props['silo.cast'] == 'Tom Hardy, Helen Mirren, Pierce Brosnan'
    assert [props['silo.badge%d' % n] for n in (1, 2, 3)] == ['4K', 'DOLBY VISION', 'EAC3 5.1']
    show = mapping.skyline_properties('tvshow', {'mpaa': 'TV-14', 'genre': ['Horror'],
                                                 'ratings': {'themoviedb': {'rating': 8.3}}})
    assert show == {'silo.mpaa': 'TV-14', 'silo.genre': 'Horror', 'silo.genres': 'Horror', 'silo.rating': 'TMDB 8.3', 'silo.cast': '',
                    'silo.similar': mapping.similar_path('tvshow', 'Horror', ''),
                    'silo.rating.imdb': '', 'silo.rating.tmdb': '8.3',
                    'silo.version': '', 'silo.audio': '', 'silo.subtitles': 'None'}


def test_quality_badges_cover_common_files():
    assert mapping.quality_badges({'video': [{'width': 1920, 'height': 800}], 'audio': [{'codec': 'ac3', 'channels': 6}]}) \
        == ['1080P', 'AC3 5.1']
    assert mapping.quality_badges({'video': [{'width': 3840, 'height': 1600, 'hdrtype': 'hdr10'}],
                                   'audio': [{'codec': 'truehd', 'channels': 8}]}) == ['4K', 'HDR10', 'TRUEHD 7.1']
    assert mapping.quality_badges(None) == []
    assert mapping.quality_badges({'video': [{'height': 2160}], 'audio': [{'codec': '', 'channels': 6}]}) == ['4K', '5.1']
