# -*- coding: utf-8 -*-
"""The sign-in record: server address, tokens, device id and chosen profile.

It lives in one JSON file in the add-on's profile folder. The service owns the
tokens once signed in; the plugin process writes the file only when someone
signs in or out, and the service notices the change by the file's mtime.
"""
import json
import os
import threading
import uuid

FILENAME = 'account.json'


class Store(object):
    def __init__(self, folder):
        self.path = os.path.join(folder, FILENAME)
        self._lock = threading.RLock()
        self._data = {}
        self._mtime = None
        self.reload()

    def reload(self):
        with self._lock:
            try:
                with open(self.path, 'r') as handle:
                    self._data = json.load(handle)
                self._mtime = os.path.getmtime(self.path)
            except (IOError, OSError, ValueError):
                self._data = {}
                self._mtime = None
            if not self._data.get('device_id'):
                self._data['device_id'] = str(uuid.uuid4())

    def changed_on_disk(self):
        try:
            return os.path.getmtime(self.path) != self._mtime
        except OSError:
            return self._mtime is not None

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def update(self, **values):
        with self._lock:
            for key, value in values.items():
                if value is None:
                    self._data.pop(key, None)
                else:
                    self._data[key] = value
            self._write()

    def sign_out(self):
        """Forget the account but keep this device's id."""
        with self._lock:
            self._data = {'device_id': self._data['device_id'],
                          'server_url': self._data.get('server_url')}
            self._write()

    @property
    def signed_in(self):
        return bool(self.get('server_url') and self.get('refresh_token'))

    def _write(self):
        temp = self.path + '.tmp'
        with open(temp, 'w') as handle:
            json.dump(self._data, handle)
        try:
            os.chmod(temp, 0o600)  # holds sign-in tokens
        except OSError:
            pass
        os.replace(temp, self.path)
        self._mtime = os.path.getmtime(self.path)
