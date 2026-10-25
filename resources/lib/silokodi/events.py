# -*- coding: utf-8 -*-
"""Silo's realtime event socket: library and watch-state changes as they happen.

Each connection needs a fresh single-use ticket and lasts at most five
minutes, so the listener reconnects in a loop.
"""
import json
import threading
import time

try:
    from urllib.parse import urlsplit, urlunsplit
except ImportError:  # pragma: no cover
    raise

import websocket

CHANNELS = 'catalog,user_state'


def socket_url(server_url):
    parts = urlsplit(server_url)
    scheme = 'wss' if parts.scheme == 'https' else 'ws'
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip('/') + '/api/v2/events/ws',
                       'channels=' + CHANNELS, ''))


class EventListener(threading.Thread):
    """
    client:    SiloClient
    on_event:  callable(channel, event, data), called on this thread
    """

    def __init__(self, client, on_event, should_stop, log=None):
        threading.Thread.__init__(self, name='silokodi-events')
        self.daemon = True
        self.client = client
        self.on_event = on_event
        self.should_stop = should_stop
        self.log = log or (lambda message: None)
        self.connected = False
        self._socket = None
        self._closing = False

    def close(self):
        self._closing = True
        sock = self._socket
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    def stopping(self):
        return self._closing or self.should_stop()

    def run(self):
        failures = 0
        while not self.stopping():
            started = time.time()
            try:
                self.connect_once()
            except Exception as error:
                if not self.stopping():
                    self.log('Event socket: %s' % error)
            self.connected = False
            # A normal five-minute rollover reconnects at once; failures back off.
            failures = 0 if time.time() - started > 60 else failures + 1
            delay = min(300, 5 * (2 ** min(failures, 6))) if failures else 1
            for _ in range(int(delay * 2)):
                if self.stopping():
                    return
                time.sleep(0.5)

    def connect_once(self):
        ticket = self.client.events_ticket()
        protocol = ticket.get('protocol') or 'silo.events.v2'
        sock = websocket.create_connection(
            socket_url(self.client.server_url),
            subprotocols=[protocol, 'silo.ticket.%s' % ticket['ticket']],
            header=['User-Agent: %s' % self.client.base_headers()['User-Agent']],
            suppress_origin=True, timeout=30)
        self._socket = sock
        try:
            sock.settimeout(60)
            while not self.stopping():
                try:
                    message = sock.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not message:
                    return
                self.handle(message)
        finally:
            self._socket = None
            try:
                sock.close()
            except Exception:
                pass

    def handle(self, message):
        try:
            frame = json.loads(message)
        except ValueError:
            return
        kind = frame.get('type')
        if kind == 'hello':
            self.connected = True
            self.log('Event socket connected (%s)' % ', '.join(frame.get('available_channels') or []))
        elif kind == 'event':
            try:
                self.on_event(frame.get('channel'), frame.get('event'), frame.get('data') or {})
            except Exception as error:
                self.log('Handling %s failed: %s' % (frame.get('event'), error))
        elif kind == 'access_changed':
            raise RuntimeError('access changed; reconnecting')
        elif kind == 'error':
            self.log('Event socket error frame: %s' % (frame.get('message') or frame.get('code')))


class Debouncer(object):
    """Hold back repeated work until things go quiet.

    During a library scan Silo sends a library-changed event after each step,
    and one full TV sync per event meant ten in 25 minutes. A key is released
    once `quiet` seconds pass with no new event for it, or `limit` seconds
    after its first event while events keep coming.
    """

    def __init__(self, quiet, limit, clock=time.time):
        self.quiet = quiet
        self.limit = limit
        self.clock = clock
        self._lock = threading.Lock()
        self._pending = {}  # key -> (first seen, last seen)

    def add(self, key):
        now = self.clock()
        with self._lock:
            first = self._pending.get(key, (now, now))[0]
            self._pending[key] = (first, now)

    def due(self):
        """Keys ready to act on, removed from the pending set."""
        now = self.clock()
        ready = []
        with self._lock:
            for key, (first, last) in list(self._pending.items()):
                if now - last >= self.quiet or now - first >= self.limit:
                    ready.append(key)
                    del self._pending[key]
        return ready
