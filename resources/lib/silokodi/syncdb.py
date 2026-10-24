# -*- coding: utf-8 -*-
"""SiloKodiConnect's own bookkeeping: which Silo item became which Kodi row,
what each looked like when last written, and the current artwork URLs."""
import json
import os
import sqlite3
import threading

SCHEMA = '''
CREATE TABLE IF NOT EXISTS items (
    content_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    library_id TEXT,
    parent_id TEXT,
    season_number INTEGER,
    kodi_id INTEGER,
    kodi_file_id INTEGER,
    kodi_path_id INTEGER,
    fingerprint TEXT,
    state TEXT
);
CREATE INDEX IF NOT EXISTS ix_items_kind ON items (kind, library_id);
CREATE INDEX IF NOT EXISTS ix_items_parent ON items (parent_id);
CREATE INDEX IF NOT EXISTS ix_items_kodi ON items (kind, kodi_id);
CREATE TABLE IF NOT EXISTS art (
    content_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (content_id, kind)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
'''

COLUMNS = ('content_id', 'kind', 'library_id', 'parent_id', 'season_number', 'kodi_id', 'kodi_file_id',
           'kodi_path_id', 'fingerprint', 'state')


class SyncDB(object):
    """Thread-safe: one connection, one lock. Writes commit immediately."""

    def __init__(self, folder):
        self.path = os.path.join(folder, 'silokodi.db')
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # A sync commits tens of thousands of small writes. With the default
        # journal each one waits for the disk, which on a SHIELD's flash
        # storage made a first sync crawl at about one movie a second. In WAL
        # mode with synchronous=NORMAL a commit doesn't wait for the disk;
        # a power cut can lose the last few commits, which the next sync
        # rewrites without duplicating Kodi rows.
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def _query(self, sql, args=()):
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, args).fetchall()]

    def _write(self, sql, args=()):
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

    # ----- items -----

    def get(self, content_id):
        rows = self._query('SELECT * FROM items WHERE content_id = ?', (content_id,))
        return rows[0] if rows else None

    def by_kodi(self, kind, kodi_id):
        rows = self._query('SELECT * FROM items WHERE kind = ? AND kodi_id = ?', (kind, kodi_id))
        return rows[0] if rows else None

    def items(self, kind, library_id=None):
        if library_id is None:
            return self._query('SELECT * FROM items WHERE kind = ?', (kind,))
        return self._query('SELECT * FROM items WHERE kind = ? AND library_id = ?', (kind, library_id))

    def children(self, parent_id, kind=None):
        if kind:
            return self._query('SELECT * FROM items WHERE parent_id = ? AND kind = ?', (parent_id, kind))
        return self._query('SELECT * FROM items WHERE parent_id = ?', (parent_id,))

    def libraries(self):
        return [row['library_id'] for row in
                self._query("SELECT DISTINCT library_id FROM items WHERE kind IN ('movie', 'tvshow')")]

    def put(self, **row):
        values = [row.get(column) for column in COLUMNS]
        self._write('INSERT OR REPLACE INTO items(%s) VALUES (%s)' % (', '.join(COLUMNS), ', '.join('?' * len(COLUMNS))),
                    values)

    def set_state(self, content_id, state):
        self._write('UPDATE items SET state = ? WHERE content_id = ?', (state, content_id))

    def forget_fingerprints(self):
        """Make the next sync rewrite every item."""
        self._write('UPDATE items SET fingerprint = NULL')

    def remove(self, content_id):
        with self._lock:
            self._conn.execute('DELETE FROM items WHERE content_id = ?', (content_id,))
            self._conn.execute('DELETE FROM art WHERE content_id = ?', (content_id,))
            self._conn.commit()

    def clear(self):
        with self._lock:
            self._conn.execute('DELETE FROM items')
            self._conn.execute('DELETE FROM art')
            self._conn.execute('DELETE FROM meta')
            self._conn.commit()

    # ----- artwork -----

    def set_art(self, content_id, urls):
        """urls: {kind: Silo URL}; replaces the item's whole set."""
        with self._lock:
            self._conn.execute('DELETE FROM art WHERE content_id = ?', (content_id,))
            self._conn.executemany('INSERT INTO art(content_id, kind, url) VALUES (?, ?, ?)',
                                   [(content_id, kind, url) for kind, url in urls.items()])
            self._conn.commit()

    def art(self, content_id, kind):
        rows = self._query('SELECT url FROM art WHERE content_id = ? AND kind = ?', (content_id, kind))
        return rows[0]['url'] if rows else None

    # ----- small values -----

    def meta(self, key, default=None):
        rows = self._query('SELECT value FROM meta WHERE key = ?', (key,))
        return json.loads(rows[0]['value']) if rows else default

    def set_meta(self, key, value):
        self._write('INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)', (key, json.dumps(value)))
