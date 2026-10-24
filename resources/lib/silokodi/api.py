# -*- coding: utf-8 -*-
"""Client for Silo's native /api/v2.

Only the service refreshes tokens. Refresh tokens rotate, so two processes
refreshing the same session would sign each other out.
"""
import platform
import threading
import time

import requests

API_PREFIX = '/api/v2'
CLIENT_NAME = 'SiloKodiConnect'
TIMEOUT = 30
PAGE_SIZE = 200


class ApiError(Exception):
    def __init__(self, status, code, message):
        Exception.__init__(self, message)
        self.status = status
        self.code = code
        self.message = message

    def __str__(self):
        return 'HTTP %s %s: %s' % (self.status, self.code, self.message)


class SignedOut(ApiError):
    """The session is gone; someone has to sign in again."""


def problem(response):
    """ApiError from an RFC 9457 problem response."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    code = (body.get('type') or '').rstrip('/').rsplit('/', 1)[-1] or body.get('code') or 'http_%s' % response.status_code
    message = body.get('detail') or body.get('title') or response.reason or ''
    for error in body.get('errors') or []:
        if isinstance(error, dict) and error.get('detail'):
            message += ' (%s: %s)' % (error.get('location', ''), error['detail'])
    return ApiError(response.status_code, code, message)


def normalize_server_url(url):
    url = (url or '').strip().rstrip('/')
    if url and '://' not in url:
        url = 'https://' + url
    if url.endswith(API_PREFIX):
        url = url[:-len(API_PREFIX)]
    return url


class SiloClient(object):
    def __init__(self, store, version='0.0.0', device_name=None, can_refresh=True):
        self.store = store
        self.version = version
        self.device_name = device_name or platform.node() or 'Kodi'
        self.can_refresh = can_refresh
        self.session = requests.Session()
        self._refresh_lock = threading.Lock()

    # ----- plumbing -----

    @property
    def server_url(self):
        return self.store.get('server_url')

    def url(self, path):
        if path.startswith('http://') or path.startswith('https://'):
            return path
        if not path.startswith('/api/'):
            path = API_PREFIX + path
        return self.server_url + path

    def base_headers(self):
        return {
            'User-Agent': '%s/%s (Kodi; %s)' % (CLIENT_NAME, self.version, platform.system()),
            'X-Silo-Client': CLIENT_NAME,
            'X-Silo-Client-Version': self.version,
            'X-Silo-Device-Id': self.store.get('device_id'),
            'X-Silo-Device-Name': self.device_name[:120],
            'X-Silo-Device-Platform': 'Kodi',
            'X-Device-ID': self.store.get('device_id'),
        }

    def auth_headers(self, profile=True):
        headers = self.base_headers()
        token = self.store.get('access_token')
        if token:
            headers['Authorization'] = 'Bearer %s' % token
        if profile and self.store.get('profile_id'):
            headers['X-Profile-Id'] = self.store.get('profile_id')
            if self.store.get('profile_token'):
                headers['X-Profile-Token'] = self.store.get('profile_token')
        return headers

    def request(self, method, path, body=None, params=None, auth=True, profile=True, timeout=TIMEOUT):
        if auth:
            self.ensure_fresh_token()
        for attempt in (1, 2):
            headers = self.auth_headers(profile) if auth else self.base_headers()
            response = self.session.request(method, self.url(path), json=body, params=params,
                                            headers=headers, timeout=timeout)
            if response.status_code == 401 and auth and attempt == 1 and self.can_refresh:
                self.refresh(force=True)
                continue
            break
        if response.status_code == 401 and auth:
            raise SignedOut(401, problem(response).code, 'Signed out of Silo')
        if response.status_code >= 400:
            raise problem(response)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def get(self, path, **params):
        return self.request('GET', path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path, body=None, **kwargs):
        return self.request('POST', path, body=body, **kwargs)

    def pages(self, path, limit=PAGE_SIZE, **params):
        """Yield every item of a cursor-paged list."""
        cursor = None
        while True:
            page = self.get(path, limit=limit, cursor=cursor, **params)
            for item in page.get('items') or []:
                yield item
            info = page.get('page') or {}
            cursor = info.get('next_cursor')
            if not info.get('has_more') or not cursor:
                return

    # ----- tokens -----

    def save_tokens(self, tokens):
        expires_in = tokens.get('expires_in') or 900
        self.store.update(access_token=tokens['access_token'],
                          refresh_token=tokens.get('refresh_token') or self.store.get('refresh_token'),
                          access_expires_at=time.time() + expires_in)

    def ensure_fresh_token(self):
        if not self.can_refresh:
            return
        if self.store.get('access_expires_at', 0) - time.time() < 120:
            self.refresh()

    def refresh(self, force=False):
        with self._refresh_lock:
            if not force and self.store.get('access_expires_at', 0) - time.time() >= 120:
                return  # another thread refreshed while we waited
            refresh_token = self.store.get('refresh_token')
            if not refresh_token:
                raise SignedOut(401, 'signed_out', 'Not signed in')
            response = self.session.post(self.url('/auth/refresh'), json={'refresh_token': refresh_token},
                                         headers=self.base_headers(), timeout=TIMEOUT)
            if response.status_code in (400, 401, 403):
                error = problem(response)
                raise SignedOut(error.status, error.code, error.message)
            if response.status_code >= 400:
                raise problem(response)
            self.save_tokens(response.json())

    # ----- sign-in -----

    def device_start(self):
        return self.request('POST', '/auth/device/start', auth=False, body={
            'client_purpose': 'device_login',
            'device_name': self.device_name[:120],
            'device_platform': 'Kodi',
        })

    def device_poll(self, device_code):
        return self.request('POST', '/auth/device/poll', auth=False, body={'device_code': device_code})

    def device_cancel(self, device_code):
        return self.request('POST', '/auth/device/cancel', auth=False, body={'device_code': device_code})

    def logout(self):
        try:
            self.request('POST', '/auth/logout', profile=False)
        except (ApiError, requests.RequestException):
            pass

    def me(self):
        return self.request('GET', '/account/me', profile=False)

    def profiles(self):
        return self.request('GET', '/profiles', profile=False).get('items') or []

    def verify_pin(self, profile_id, pin):
        return self.request('POST', '/profiles/%s/verify-pin' % profile_id, body={'pin': pin}, profile=False)

    # ----- catalog -----

    def libraries(self):
        return self.get('/libraries').get('items') or []

    def catalog(self, library_id, media_type):
        """Every card of one type in a library, in a stable order."""
        return self.pages('/catalog', library_id=library_id, type=media_type, sort='added_at',
                          skip_total='true', image_size='large')

    def item(self, content_id):
        return self.get('/catalog/items/%s' % content_id, image_size='large')

    def seasons(self, series_id):
        # Not paged: one answer holds every season.
        return self.get('/catalog/series/%s/seasons' % series_id, image_size='large').get('items') or []

    def season_episodes(self, series_id, season_number):
        return self.get('/catalog/series/%s/seasons/%s/episodes' % (series_id, season_number),
                        image_size='large').get('items') or []

    def progress(self):
        """Watch progress, newest change first."""
        return self.pages('/progress')

    def history(self):
        """Watch events, newest first."""
        return self.pages('/history')

    def mark_watched(self, content_id):
        return self.post('/watched/%s' % content_id)

    def mark_unwatched(self, content_id):
        return self.request('DELETE', '/watched/%s' % content_id)

    def clear_resume(self, content_id):
        return self.request('DELETE', '/progress/%s/position' % content_id)

    def events_ticket(self):
        return self.post('/events/ws-ticket')

    # ----- playback -----

    def playback_capabilities(self):
        return self.get('/playback/capabilities')

    def playback_start(self, body):
        return self.post('/playback/start', body, timeout=60)

    def playback_progress(self, session_id, body):
        # Called from Kodi's main loop: keep it short.
        return self.post('/playback/%s/progress' % session_id, body, timeout=8)

    def playback_stop(self, session_id, body):
        return self.request('DELETE', '/playback/%s' % session_id, body=body)
