# -*- coding: utf-8 -*-
"""The service's loopback HTTP server.

/art/<content_id>/<kind>   Kodi's art URLs point here. Silo's artwork URLs are
                           signed and change daily, so Kodi stores this stable
                           address and the relay fetches Silo's current one.
/play?id=..&resume=1       The plugin asks the service to start a Silo
                           session, because only the service holds the tokens.
/command/<name>            The plugin's menu actions (sync now, sign-in done).
/media/<Silo path>?<query> Silo's media routes need the sign-in header even on
                           signed URLs. Kodi plays through here and the relay
                           adds a fresh token to every request, so a long film
                           outlives the hourly token and no token reaches
                           Kodi's log. Only sessions this add-on started pass.

It listens on 127.0.0.1 only and never logs URLs, which carry signatures.
"""
import json
import socket
import threading
import time

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, unquote, urlsplit
except ImportError:  # pragma: no cover
    raise

import requests

from .mapping import RELAY_PORT as DEFAULT_PORT
CHUNK = 64 * 1024
MEDIA_CHUNK = 1024 * 1024
STATS_EVERY = 30
# Request headers Kodi's player sends that the media server needs to see.
FORWARD_REQUEST = ('Range', 'If-Range', 'Accept', 'Accept-Encoding')
FORWARD_RESPONSE = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag',
                    'Last-Modified', 'Cache-Control')


class ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Players drop connections whenever they seek; that is not an error.
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):  # noqa: A002 - silence the default stderr log
        pass

    def do_HEAD(self):
        self.route(head=True)

    def do_GET(self):
        self.route(head=False)

    def route(self, head):
        relay = self.server.relay
        parts = urlsplit(self.path)
        segments = [unquote(s) for s in parts.path.strip('/').split('/')]
        try:
            if len(segments) == 3 and segments[0] == 'art':
                return self.art(relay, segments[1], segments[2], head)
            if segments[0] == 'media':
                return self.media(relay, parts, head)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            if segments == ['play']:
                return self.json(200, relay.play(query))
            if len(segments) == 2 and segments[0] == 'command':
                return self.json(200, relay.command(segments[1], query))
            if segments == ['status']:
                return self.json(200, {'ok': True})
        except Exception as error:  # report, never crash the server thread
            relay.log('Relay error on /%s: %s' % (segments[0] if segments else '', error))
            return self.json(500, {'error': str(error)})
        self.send_error(404)

    def json(self, status, body):
        data = json.dumps(body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def art(self, relay, content_id, kind, head):
        response = relay.fetch_art(content_id, kind)
        if response is None:
            self.send_error(404)
            return
        try:
            self.send_response(200)
            self.send_header('Content-Type', response.headers.get('Content-Type', 'image/jpeg'))
            if response.headers.get('Content-Length'):
                self.send_header('Content-Length', response.headers['Content-Length'])
            else:
                self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'max-age=86400')
            self.end_headers()
            if not head:
                for chunk in response.iter_content(CHUNK):
                    self.wfile.write(chunk)
        except (socket.error, ConnectionError):
            pass
        finally:
            response.close()


    def media(self, relay, parts, head):
        path = parts.path[len('/media'):]
        if not relay.media_allowed(path):
            self.send_error(403)
            return
        headers = {key: self.headers[key] for key in FORWARD_REQUEST if self.headers.get(key)}
        response = relay.fetch_media('HEAD' if head else 'GET', path, parts.query, headers)
        try:
            self.send_response(response.status_code)
            for key in FORWARD_RESPONSE:
                if response.headers.get(key):
                    self.send_header(key, response.headers[key])
            if not response.headers.get('Content-Length'):
                self.send_header('Connection', 'close')
                self.close_connection = True
            self.end_headers()
            if not head:
                self.copy(relay, response)
        except (socket.error, ConnectionError):
            pass  # Kodi closes connections when it seeks
        finally:
            response.close()

    def copy(self, relay, response):
        """Copy the media body, timing where the waits are.

        Raw reads skip requests' chunk iterator; the media is never
        content-encoded. Every STATS_EVERY seconds the log says how fast the
        relay sends and what share of the time it waited on Silo (network
        or server slow), on Kodi (its buffer is full: the relay keeps up)
        or on neither (Python itself is the limit).
        """
        raw = response.raw
        stats = Stats(relay.log)
        while True:
            started = time.time()
            data = raw.read(MEDIA_CHUNK, decode_content=False)
            read_done = time.time()
            if not data:
                break
            self.wfile.write(data)
            stats.add(len(data), read_done - started, time.time() - read_done)
        stats.report(final=True)


class Stats(object):
    def __init__(self, log):
        self.log = log
        self.start = self.window = time.time()
        self.bytes = self.total = 0
        self.upstream = self.downstream = 0.0

    def add(self, size, upstream, downstream):
        self.bytes += size
        self.total += size
        self.upstream += upstream
        self.downstream += downstream
        if time.time() - self.window >= STATS_EVERY:
            self.report()

    def report(self, final=False):
        elapsed = max(time.time() - self.window, 0.001)
        if self.bytes and (elapsed >= 5 or not final):
            self.log('Relay: %.0f Mbit/s to Kodi over %.0f s; waited on Silo %.0f%%, on Kodi %.0f%%'
                     % (self.bytes * 8 / elapsed / 1e6, elapsed, 100 * self.upstream / elapsed,
                        100 * self.downstream / elapsed))
        self.window = time.time()
        self.bytes = 0
        self.upstream = self.downstream = 0.0


class Relay(object):
    """
    client:       SiloClient (for the server URL and auth headers)
    db:           SyncDB (current artwork URLs)
    refresh_art:  callable(content_id) that re-reads the item's artwork from Silo
    play:         callable(query dict) -> dict for the plugin
    command:      callable(name, query dict) -> dict for the plugin
    media_allowed: callable(Silo path) -> True for a session this add-on started
    """

    def __init__(self, client, db, refresh_art, play, command, port=DEFAULT_PORT, log=None,
                 media_allowed=lambda path: False):
        self.client = client
        self.db = db
        self.refresh_art = refresh_art
        self.play = play
        self.command = command
        self.media_allowed = media_allowed
        self.port = port
        self.log = log or (lambda message: None)
        self.session = requests.Session()
        self._server = None
        self._thread = None

    def start(self):
        self._server = ThreadingServer(('127.0.0.1', self.port), Handler)
        self._server.relay = self
        self._thread = threading.Thread(target=self._server.serve_forever, name='silokodi-relay')
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _get(self, url):
        headers = {'User-Agent': self.client.base_headers()['User-Agent']}
        if url.startswith('/') or url.startswith(self.client.server_url or '\0'):
            # Silo's own artwork route: signed, but sending our sign-in does no harm.
            headers.update(self.client.auth_headers(profile=False))
        return self.session.get(self.client.url(url) if url.startswith('/') else url, headers=headers,
                                stream=True, timeout=20)

    def fetch_art(self, content_id, kind):
        url = self.db.art(content_id, kind)
        if url:
            response = self._get(url)
            if response.status_code == 200:
                return response
            response.close()
        # Signature expired (or never known): ask Silo for the current URL once.
        try:
            self.refresh_art(content_id)
        except Exception as error:
            self.log('Could not refresh artwork for %s: %s' % (content_id, error))
            return None
        url = self.db.art(content_id, kind)
        if not url:
            return None
        response = self._get(url)
        if response.status_code == 200:
            return response
        response.close()
        return None

    # ----- media -----

    def media_url(self, url):
        """Kodi's address for a Silo media URL on the API origin; others pass through."""
        server = urlsplit(self.client.server_url)
        target = urlsplit(url)
        if (target.scheme, target.netloc) != (server.scheme, server.netloc):
            return url
        return 'http://127.0.0.1:%d/media%s%s' % (self.port, target.path, '?' + target.query if target.query else '')

    def fetch_media(self, method, path, query, headers):
        url = self.client.server_url + path + ('?' + query if query else '')
        for attempt in (1, 2):
            if attempt == 2:
                self.client.refresh(force=True)
            else:
                self.client.ensure_fresh_token()
            response = self.session.request(method, url, headers=dict(self.client.auth_headers(), **headers),
                                            stream=True, timeout=(10, 60))
            if response.status_code != 401 or attempt == 2:
                return response
            response.close()
