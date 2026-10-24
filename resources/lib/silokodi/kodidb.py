# -*- coding: utf-8 -*-
"""Writes Silo items straight into Kodi's video database (MyVideosNNN.db).

Adapted from PlexKodiConnect's kodi_db/video.py. Kodi's JSON-RPC cannot add
library items, so like PKC this writes the SQLite tables Kodi reads; Kodi's
own triggers remove an item's genre, cast, art, rating and id links when its
movie, tvshow or episode row is deleted.
"""
import glob
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import mapping

ROOT_PATHS = ((mapping.MOVIES_PATH, 'movies'), (mapping.SHOWS_PATH, 'tvshows'))


def find_video_db(folder):
    """Newest MyVideosNNN.db in Kodi's database folder."""
    best, best_version = None, -1
    for path in glob.glob(os.path.join(folder, 'MyVideos*.db')):
        match = re.search(r'MyVideos(\d+)\.db$', path)
        if match and int(match.group(1)) > best_version:
            best, best_version = path, int(match.group(1))
    return best, best_version


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@contextmanager
def open_video_db(path, version):
    """One write transaction against Kodi's video database."""
    connection = sqlite3.connect(path, timeout=60, isolation_level=None)
    try:
        connection.execute('BEGIN IMMEDIATE')
        db = KodiVideoDB(connection.cursor(), version)
        yield db
        connection.execute('COMMIT')
    except BaseException:
        if connection.in_transaction:
            connection.execute('ROLLBACK')
        raise
    finally:
        connection.close()


def _ignore_duplicate(exc):
    # Directly catching sqlite3.IntegrityError crashes some Android builds
    # (PKC issues 1777 and 1796), so inspect the message instead.
    return exc.args and 'UNIQUE constraint failed' in str(exc.args[0])


class KodiVideoDB(object):
    def __init__(self, cursor, version):
        self.cursor = cursor
        self.version = version
        self._has_video_version = None

    # ----- paths and files -----

    def setup_root_paths(self):
        for path, content in ROOT_PATHS:
            if self.get_path(path) is None:
                self.cursor.execute('''
                    INSERT INTO path(strPath, strContent, strScraper, noUpdate, exclude, allAudio)
                    VALUES (?, ?, 'metadata.local', 1, 0, 0)
                ''', (path, content))

    def get_path(self, path):
        self.cursor.execute('SELECT idPath FROM path WHERE strPath = ? LIMIT 1', (path,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    def add_path(self, path, date_added=None, parent_id=None, content=None, scraper=None):
        path_id = self.get_path(path)
        if path_id is None:
            self.cursor.execute('''
                INSERT INTO path(strPath, dateAdded, idParentPath, strContent, strScraper, noUpdate, exclude,
                                 allAudio)
                VALUES (?, ?, ?, ?, ?, 1, 0, 0)
            ''', (path, date_added, parent_id, content, scraper))
            path_id = self.cursor.lastrowid
        return path_id

    def add_file(self, filename, path_id, date_added):
        self.cursor.execute('SELECT idFile FROM files WHERE idPath = ? AND strFilename = ?', (path_id, filename))
        row = self.cursor.fetchone()
        if row:
            if date_added:
                self.cursor.execute('UPDATE files SET dateAdded = ? WHERE idFile = ?', (date_added, row[0]))
            return row[0]
        self.cursor.execute('INSERT INTO files(idPath, strFilename, dateAdded) VALUES (?, ?, ?)',
                            (path_id, filename, date_added or now()))
        return self.cursor.lastrowid

    def remove_file(self, file_id):
        self.cursor.execute('SELECT idPath FROM files WHERE idFile = ?', (file_id,))
        row = self.cursor.fetchone()
        for table in ('files', 'bookmark', 'settings', 'streamdetails', 'stacktimes'):
            self.cursor.execute('DELETE FROM %s WHERE idFile = ?' % table, (file_id,))
        if self.has_video_version:
            self.cursor.execute('DELETE FROM videoversion WHERE idFile = ?', (file_id,))
        if row:
            self.remove_path_if_unused(row[0])

    def remove_path_if_unused(self, path_id):
        self.cursor.execute('SELECT 1 FROM files WHERE idPath = ? LIMIT 1', (path_id,))
        if self.cursor.fetchone():
            return
        self.cursor.execute('SELECT 1 FROM tvshowlinkpath WHERE idPath = ? LIMIT 1', (path_id,))
        if self.cursor.fetchone():
            return
        self.cursor.execute('DELETE FROM path WHERE idPath = ? AND strPath NOT IN (?, ?)',
                            (path_id, mapping.MOVIES_PATH, mapping.SHOWS_PATH))

    # ----- link tables: genres, studios, countries, tags -----

    def _set_links(self, kodi_id, kodi_type, names, link_table, table, key):
        wanted = []
        for name in names or []:
            if not name:
                continue
            self.cursor.execute('SELECT %s FROM %s WHERE name = ? COLLATE NOCASE LIMIT 1' % (key, table), (name,))
            row = self.cursor.fetchone()
            if row:
                entry_id = row[0]
            else:
                self.cursor.execute('INSERT INTO %s(name) VALUES (?)' % table, (name,))
                entry_id = self.cursor.lastrowid
            if entry_id not in wanted:
                wanted.append(entry_id)
        self.cursor.execute('SELECT %s FROM %s WHERE media_id = ? AND media_type = ?' % (key, link_table),
                            (kodi_id, kodi_type))
        current = [row[0] for row in self.cursor.fetchall()]
        for entry_id in wanted:
            if entry_id not in current:
                self.cursor.execute('INSERT INTO %s(%s, media_id, media_type) VALUES (?, ?, ?)' % (link_table, key),
                                    (entry_id, kodi_id, kodi_type))
        for entry_id in current:
            if entry_id not in wanted:
                self.cursor.execute('DELETE FROM %s WHERE %s = ? AND media_id = ? AND media_type = ?'
                                    % (link_table, key), (entry_id, kodi_id, kodi_type))
                self.cursor.execute('SELECT 1 FROM %s WHERE %s = ? LIMIT 1' % (link_table, key), (entry_id,))
                if not self.cursor.fetchone():
                    self.cursor.execute('DELETE FROM %s WHERE %s = ?' % (table, key), (entry_id,))

    def set_genres(self, kodi_id, kodi_type, names):
        self._set_links(kodi_id, kodi_type, names, 'genre_link', 'genre', 'genre_id')

    def set_studios(self, kodi_id, kodi_type, names):
        self._set_links(kodi_id, kodi_type, names, 'studio_link', 'studio', 'studio_id')

    def set_countries(self, kodi_id, kodi_type, names):
        self._set_links(kodi_id, kodi_type, names, 'country_link', 'country', 'country_id')

    def set_tags(self, kodi_id, kodi_type, names):
        self._set_links(kodi_id, kodi_type, names, 'tag_link', 'tag', 'tag_id')

    # ----- people -----

    def _actor_id(self, name, thumb=None):
        self.cursor.execute('SELECT actor_id FROM actor WHERE name = ? LIMIT 1', (name,))
        row = self.cursor.fetchone()
        if row:
            actor_id = row[0]
        else:
            self.cursor.execute('INSERT INTO actor(name) VALUES (?)', (name,))
            actor_id = self.cursor.lastrowid
        if thumb:
            self.set_art(actor_id, 'actor', {'thumb': thumb})
        return actor_id

    def set_people(self, kodi_id, kodi_type, people):
        """people: {'actor': [(name, thumb, role, order)], 'director': [(name,)], 'writer': [(name,)]}"""
        touched = set()
        for kind in ('actor', 'director', 'writer'):
            self.cursor.execute('SELECT actor_id FROM %s_link WHERE media_id = ? AND media_type = ?' % kind,
                                (kodi_id, kodi_type))
            touched.update(row[0] for row in self.cursor.fetchall())
            self.cursor.execute('DELETE FROM %s_link WHERE media_id = ? AND media_type = ?' % kind,
                                (kodi_id, kodi_type))
            for person in people.get(kind) or []:
                actor_id = self._actor_id(person[0], person[1] if kind == 'actor' else None)
                try:
                    if kind == 'actor':
                        self.cursor.execute('''
                            INSERT INTO actor_link(actor_id, media_id, media_type, role, cast_order)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (actor_id, kodi_id, kodi_type, person[2], person[3]))
                    else:
                        self.cursor.execute('INSERT INTO %s_link(actor_id, media_id, media_type) VALUES (?, ?, ?)'
                                            % kind, (actor_id, kodi_id, kodi_type))
                except Exception as exc:
                    if not _ignore_duplicate(exc):
                        raise
        self.remove_orphaned_people(touched)

    def remove_orphaned_people(self, actor_ids):
        for actor_id in actor_ids:
            for kind in ('actor', 'director', 'writer'):
                self.cursor.execute('SELECT 1 FROM %s_link WHERE actor_id = ? LIMIT 1' % kind, (actor_id,))
                if self.cursor.fetchone():
                    break
            else:
                self.cursor.execute('DELETE FROM actor WHERE actor_id = ?', (actor_id,))
                self.cursor.execute("DELETE FROM art WHERE media_id = ? AND media_type = 'actor'", (actor_id,))

    def people_of(self, kodi_id, kodi_type):
        ids = set()
        for kind in ('actor', 'director', 'writer'):
            self.cursor.execute('SELECT actor_id FROM %s_link WHERE media_id = ? AND media_type = ?' % kind,
                                (kodi_id, kodi_type))
            ids.update(row[0] for row in self.cursor.fetchall())
        return ids

    # ----- art, ratings, unique ids, streams -----

    def set_art(self, kodi_id, kodi_type, art):
        self.cursor.execute('SELECT type FROM art WHERE media_id = ? AND media_type = ?', (kodi_id, kodi_type))
        current = set(row[0] for row in self.cursor.fetchall())
        for kind, url in art.items():
            if kind in current:
                self.cursor.execute('UPDATE art SET url = ? WHERE media_id = ? AND media_type = ? AND type = ?',
                                    (url, kodi_id, kodi_type, kind))
            else:
                self.cursor.execute('INSERT INTO art(media_id, media_type, type, url) VALUES (?, ?, ?, ?)',
                                    (kodi_id, kodi_type, kind, url))
        if kodi_type != 'actor':
            for kind in current - set(art):
                self.cursor.execute('DELETE FROM art WHERE media_id = ? AND media_type = ? AND type = ?',
                                    (kodi_id, kodi_type, kind))

    def set_rating(self, kodi_id, kodi_type, rating):
        """rating: (type, value, votes) or None. Returns the rating_id."""
        self.cursor.execute('DELETE FROM rating WHERE media_id = ? AND media_type = ?', (kodi_id, kodi_type))
        if not rating:
            return None
        self.cursor.execute('INSERT INTO rating(media_id, media_type, rating_type, rating, votes) VALUES (?, ?, ?, ?, ?)',
                            (kodi_id, kodi_type, rating[0], rating[1], rating[2]))
        return self.cursor.lastrowid

    def set_unique_ids(self, kodi_id, kodi_type, ids):
        """ids: [(type, value)], most preferred first. Returns the default uniqueid_id."""
        self.cursor.execute('DELETE FROM uniqueid WHERE media_id = ? AND media_type = ?', (kodi_id, kodi_type))
        first = None
        for kind, value in ids:
            self.cursor.execute('INSERT INTO uniqueid(media_id, media_type, value, type) VALUES (?, ?, ?, ?)',
                                (kodi_id, kodi_type, value, kind))
            first = first or self.cursor.lastrowid
        return first

    def set_streams(self, file_id, details, runtime):
        self.cursor.execute('DELETE FROM streamdetails WHERE idFile = ?', (file_id,))
        if not details:
            return
        for track in details['video']:
            self.cursor.execute('''
                INSERT INTO streamdetails(idFile, iStreamType, strVideoCodec, fVideoAspect, iVideoWidth,
                                          iVideoHeight, iVideoDuration, strStereoMode, strHdrType)
                VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?)
            ''', (file_id, track['codec'], track['aspect'], track['width'], track['height'], runtime,
                  track['stereo'], track['hdr']))
        for track in details['audio']:
            self.cursor.execute('''
                INSERT INTO streamdetails(idFile, iStreamType, strAudioCodec, iAudioChannels, strAudioLanguage)
                VALUES (?, 1, ?, ?, ?)
            ''', (file_id, track['codec'], track['channels'], track['language']))
        for language in details['subtitle']:
            self.cursor.execute('INSERT INTO streamdetails(idFile, iStreamType, strSubtitleLanguage) VALUES (?, 2, ?)',
                                (file_id, language))

    # ----- watch state -----

    def set_play_state(self, file_id, playcount, resume, total, last_played):
        self.cursor.execute('DELETE FROM bookmark WHERE idFile = ? AND type = 1', (file_id,))
        # Kodi expects NULL, not 0, for unwatched.
        self.cursor.execute('UPDATE files SET playCount = ?, lastPlayed = ? WHERE idFile = ?',
                            (playcount or None, last_played, file_id))
        if resume:
            self.cursor.execute('''
                INSERT INTO bookmark(idFile, timeInSeconds, totalTimeInSeconds, thumbNailImage, player,
                                     playerState, type)
                VALUES (?, ?, ?, '', 'VideoPlayer', '', 1)
            ''', (file_id, resume, total))

    def play_state(self, file_id):
        """(playcount, resume seconds) as Kodi has them."""
        self.cursor.execute('SELECT playCount FROM files WHERE idFile = ?', (file_id,))
        row = self.cursor.fetchone()
        count = (row[0] or 0) if row else 0
        self.cursor.execute('SELECT timeInSeconds FROM bookmark WHERE idFile = ? AND type = 1', (file_id,))
        row = self.cursor.fetchone()
        return count, (row[0] if row else 0)

    # ----- movies -----

    @property
    def has_video_version(self):
        if self._has_video_version is None:
            self.cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'videoversion'")
            self._has_video_version = self.cursor.fetchone() is not None
        return self._has_video_version

    def new_id(self, table, column):
        self.cursor.execute('SELECT COALESCE(MAX(%s), 0) + 1 FROM %s' % (column, table))
        return self.cursor.fetchone()[0]

    def write_movie(self, kodi_id, file_id, fields, rating_id, unique_id, people, path_id):
        self.cursor.execute('''
            INSERT OR REPLACE INTO movie(idMovie, idFile, c00, c01, c02, c03, c05, c06, c09, c10, c11, c12,
                                         c14, c15, c16, c18, c21, c22, c23, premiered, userrating)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (kodi_id, file_id, fields['title'], fields['plot'], fields['plot'][:300], fields['tagline'],
              rating_id, mapping.joined(p[0] for p in people['writer']), unique_id, fields['sorttitle'],
              fields['runtime'], fields['mpaa'], mapping.joined(fields['genres']),
              mapping.joined(p[0] for p in people['director']), fields['originaltitle'],
              mapping.joined(fields['studios']), mapping.joined(fields['countries']), mapping.MOVIES_PATH,
              path_id, fields['premiered'], fields['userrating']))
        if self.has_video_version:
            # Kodi 22 (MyVideos 134+) marks the default version with itemType 1.
            item_type = 1 if self.version >= 134 else 0
            self.cursor.execute('''
                INSERT OR REPLACE INTO videoversion(idFile, idMedia, media_type, itemType, idType)
                VALUES (?, ?, 'movie', ?, 40400)
            ''', (file_id, kodi_id, item_type))

    def remove_movie(self, kodi_id, file_id):
        actors = self.people_of(kodi_id, 'movie')
        self.cursor.execute('DELETE FROM movie WHERE idMovie = ?', (kodi_id,))
        self.remove_file(file_id)
        self.remove_orphaned_people(actors)

    # ----- TV -----

    def write_show(self, kodi_id, fields, rating_id, unique_id, path_id):
        self.cursor.execute('''
            INSERT OR REPLACE INTO tvshow(idShow, c00, c01, c02, c04, c05, c08, c09, c12, c13, c14, c15)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (kodi_id, fields['title'], fields['plot'], fields['status'], rating_id, fields['premiered'],
              mapping.joined(fields['genres']), fields['originaltitle'], unique_id, fields['mpaa'],
              mapping.joined(fields['studios']), fields['sorttitle']))
        self.cursor.execute('SELECT 1 FROM tvshowlinkpath WHERE idShow = ? AND idPath = ?', (kodi_id, path_id))
        if not self.cursor.fetchone():
            self.cursor.execute('INSERT INTO tvshowlinkpath(idShow, idPath) VALUES (?, ?)', (kodi_id, path_id))

    def remove_show(self, kodi_id, path_id):
        actors = self.people_of(kodi_id, 'tvshow')
        self.cursor.execute('SELECT idSeason FROM seasons WHERE idShow = ?', (kodi_id,))
        for (season_id,) in self.cursor.fetchall():
            self.remove_season(season_id)
        self.cursor.execute('DELETE FROM tvshow WHERE idShow = ?', (kodi_id,))
        self.remove_orphaned_people(actors)
        if path_id:
            self.remove_path_if_unused(path_id)

    def write_season(self, season_id, show_id, number, name):
        if season_id is None:
            self.cursor.execute('SELECT idSeason FROM seasons WHERE idShow = ? AND season = ?', (show_id, number))
            row = self.cursor.fetchone()
            season_id = row[0] if row else None
        if season_id is None:
            self.cursor.execute('INSERT INTO seasons(idShow, season, name) VALUES (?, ?, ?)', (show_id, number, name))
            return self.cursor.lastrowid
        self.cursor.execute('UPDATE seasons SET idShow = ?, season = ?, name = ? WHERE idSeason = ?',
                            (show_id, number, name, season_id))
        return season_id

    def remove_season(self, season_id):
        self.cursor.execute('DELETE FROM seasons WHERE idSeason = ?', (season_id,))
        self.cursor.execute("DELETE FROM art WHERE media_id = ? AND media_type = 'season'", (season_id,))

    def write_episode(self, kodi_id, file_id, fields, rating_id, unique_id, people, show_id, season_id,
                      filename, path_id):
        self.cursor.execute('''
            INSERT OR REPLACE INTO episode(idEpisode, idFile, c00, c01, c03, c04, c05, c09, c10, c12, c13, c14,
                                           c15, c16, c18, c19, c20, idShow, idSeason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, -1, -1, ?, ?, ?, ?, ?)
        ''', (kodi_id, file_id, fields['title'], fields['plot'], rating_id,
              mapping.joined(p[0] for p in people['writer']), fields['aired'], fields['runtime'],
              mapping.joined(p[0] for p in people['director']), fields['season'], fields['episode'],
              fields['title'], filename, path_id, unique_id, show_id, season_id))

    def remove_episode(self, kodi_id, file_id):
        actors = self.people_of(kodi_id, 'episode')
        self.cursor.execute('DELETE FROM episode WHERE idEpisode = ?', (kodi_id,))
        self.remove_file(file_id)
        self.remove_orphaned_people(actors)

    # ----- lookups used when Kodi reports a change -----

    def item_for_file(self, kodi_type, kodi_id):
        """idFile for a movie or episode."""
        table, column = ('movie', 'idMovie') if kodi_type == 'movie' else ('episode', 'idEpisode')
        self.cursor.execute('SELECT idFile FROM %s WHERE %s = ?' % (table, column), (kodi_id,))
        row = self.cursor.fetchone()
        return row[0] if row else None
