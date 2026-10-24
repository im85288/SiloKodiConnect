# -*- coding: utf-8 -*-
"""The service's loopback HTTP server.

/art/<content_id>/<kind>   Kodi's art URLs point here. Silo's artwork URLs are
                           signed and change daily, so Kodi stores this stable
                           address and the relay fetches Silo's current one.
/play?id=..&resume=1       The plugin asks the service to start a Silo
                           session, because only the service holds the tokens.
/command/<name>            The plugin's menu actions (sync now, sign-in done).

It listens on 127.0.0.1 only and never logs URLs, which carry signatures.
"""
import json
import socket
import threading

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, unquote, urlsplit
except ImportError:  # pragma: no cover
    raise

import requests

DEFAULT_PORT = 57578
CHUNK = 64 * 1024


class ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


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


class Relay(object):
    """
    client:       SiloClient (for the server URL and auth headers)
    db:           SyncDB (current artwork URLs)
    refresh_art:  callable(content_id) that re-reads the item's artwork from Silo
    play:         callable(query dict) -> dict for the plugin
    command:      callable(name, query dict) -> dict for the plugin
    """

    def __init__(self, client, db, refresh_art, play, command, port=DEFAULT_PORT, log=None):
        self.client = client
        self.db = db
        self.refresh_art = refresh_art
        self.play = play
        self.command = command
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
