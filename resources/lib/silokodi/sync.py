# -*- coding: utf-8 -*-
"""Keeps Kodi's video library in step with Silo's movie and TV libraries.

A sync pages each library's catalog cards (cheap: up to 200 per request) and
compares each card's fingerprint with the one stored when it was last written.
Only new or changed titles are fetched in full; a card whose title is
unchanged but whose watch state moved only updates Kodi's playcount and
resume point. Titles that left Silo are removed from Kodi.
"""
from collections import OrderedDict
from urllib.parse import quote, urlencode
from concurrent.futures import ThreadPoolExecutor

from . import mapping
from .api import ApiError

MOVIE_LIBRARY_TYPES = ('movies', 'mixed')
SHOW_LIBRARY_TYPES = ('series', 'tv', 'tvshows', 'mixed')
BATCH = 25
FETCH_WORKERS = 4


# Rows about what to watch next: a show card there means its next episode.
NEXT_EPISODE_SECTIONS = ('continue_watching', 'next_up', 'next_in_series')


def collection_cards(tab):
    """A library's Collections tab as [{'id', 'title', 'poster', 'count'}], in
    Silo's order: grouped collections first (by group), then the ungrouped."""
    by_id = {c.get('id'): c for c in tab.get('collections') or []}
    ordered = []
    for group in tab.get('groups') or []:
        ordered += group.get('collections') or []
    ordered += (tab.get('ungrouped') or {}).get('collections') or []
    if not ordered:
        ordered = tab.get('collections') or []
    cards, seen = [], set()
    for card in ordered:
        if card.get('id') in seen or not card.get('item_count'):
            continue
        seen.add(card.get('id'))
        full = by_id.get(card.get('id'), card)
        cards.append({'id': card['id'], 'title': card.get('title') or full.get('title') or '',
                      'poster': card.get('poster_url') or full.get('poster_url') or '',
                      'count': card.get('item_count') or 0})
    return cards


def home_row_items(page, section, db):
    """A Silo home row's cards as [{'type', 'kodi_id', 'tint'}] in Silo's order.
    Titles not synced to Kodi (other media types, excluded libraries) are left
    out. tint is the card's ambient page tint for a Silo-style skin."""
    next_episode = (page.get('section_type') or section) in NEXT_EPISODE_SECTIONS
    items = []
    for card in page.get('items') or []:
        row = None
        if card.get('type') == 'series' and next_episode and card.get('play_content_id'):
            row = db.get(card['play_content_id'])
        row = row or db.get(card.get('content_id'))
        if row and row['kind'] in ('movie', 'episode', 'tvshow') and row.get('kodi_id'):
            items.append({'type': row['kind'], 'kodi_id': row['kodi_id'],
                          'tint': mapping.ambient_tint(card.get('poster_thumbhash') or '')})
    return items


# The skin's pages and row slots. Rows beyond MAX_ROWS are left out.
MAX_ROWS = 12
WIDE_SECTIONS = ('continue_watching', 'next_up', 'next_in_series')
PLUGIN = 'plugin://plugin.video.silokodiconnect/'


def row_entry(section, library_id=None):
    query = {'mode': 'widget', 'section': section['id']}
    if library_id:
        query['library'] = library_id
    return {'title': section.get('title') or section['id'],
            'path': PLUGIN + '?' + '&'.join('%s=%s' % (k, quote(str(v), safe='')) for k, v in sorted(query.items())),
            'style': 'landscape' if section.get('section_type') in WIDE_SECTIONS else 'poster',
            # Silo's row type: a skin marks some rows (Continue Watching's play icon).
            'type': section.get('section_type') or ''}


def skin_layout(home_sections, libraries, library_sections, request_rows=None):
    """The pages and rows a Silo-style skin shows, as Home window properties.

    home_sections:     Silo's home rows, in order
    libraries:         the synced movie and series libraries, in Silo's order
    library_sections:  {library id: that library's rows, in order}
    request_rows:      [(row key, title)] for the Requests page, or None

    Pages are Home plus one per library type present, as in Silo's top bar;
    a type with several libraries shows their rows one library after another.
    """
    pages = OrderedDict([('home', [row_entry(s) for s in home_sections])])
    collections = {}
    for page, types in (('movies', MOVIE_LIBRARY_TYPES), ('series', SHOW_LIBRARY_TYPES)):
        rows, ids = [], []
        for library in libraries:
            if (library.get('type') or '').lower() in types:
                ids.append(str(library['id']))
                rows += [row_entry(s, str(library['id'])) for s in library_sections.get(str(library['id'])) or []]
        if rows:
            pages[page] = rows
        # The page's libraries' collections, as Silo's Collections section.
        collections[page] = '%s?%s' % (PLUGIN, urlencode({'mode': 'collections', 'libraries': ','.join(ids)})) \
            if ids else ''
    if request_rows:
        # Silo's Requests: the profile's requests, then the discovery rows.
        pages['requests'] = [{'title': title, 'style': 'poster',
                              'path': '%s?%s' % (PLUGIN, urlencode({'mode': 'requests', 'row': key}))}
                             for key, title in request_rows]
    properties = {'silo.pages': ','.join(pages)}
    for page, path in collections.items():
        properties['silo.%s.collections' % page] = path
    for page in ('home', 'movies', 'series', 'requests'):
        rows = pages.get(page, [])[:MAX_ROWS]
        properties['silo.%s.count' % page] = str(len(rows))
        for index in range(1, MAX_ROWS + 1):
            row = rows[index - 1] if index <= len(rows) else {}
            for key in ('title', 'path', 'style', 'type'):
                properties['silo.%s.%d.%s' % (page, index, key)] = row.get(key, '')
            # The path again under the row's style, which is the only list
            # a skin should load from (it has one list per style per slot).
            for style in ROW_STYLES:
                properties['silo.%s.%d.%s' % (page, index, style)] = \
                    row.get('path', '') if row.get('style') == style else ''
    return properties


ROW_STYLES = ('poster', 'landscape')


def page_of(key):
    """The page a row's style-keyed path belongs to ('silo.movies.3.poster'
    -> 'movies'), or None for any other layout property."""
    parts = key.split('.')
    if len(parts) == 4 and parts[0] == 'silo' and parts[3] in ROW_STYLES:
        return parts[1]
    return None


class Stopped(Exception):
    pass


def season_key(series_id, number):
    return 'season:%s:%s' % (series_id, number)


def chunks(values, size):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


class Syncer(object):
    """
    client:     SiloClient
    db:         SyncDB
    open_kodi:  callable returning a context manager that yields a KodiVideoDB
    port:       the local artwork relay's port, baked into Kodi's art URLs
    """

    def __init__(self, client, db, open_kodi, port, log=None, should_stop=None, progress=None, wait_while_busy=None):
        self.client = client
        self.db = db
        self.open_kodi = open_kodi
        self.port = port
        self.log = log or (lambda message: None)
        self.should_stop = should_stop or (lambda: False)
        self.progress = progress or (lambda percent, message: None)
        # Called between batches; blocks while the device needs its memory
        # (a video playing), so a sync that started first doesn't compete.
        self.wait_while_busy = wait_while_busy or (lambda: None)
        self.changed = False
        # Titles were added or removed (not just updated): Kodi and skins need
        # a library update then, which skin helpers answer with a full rescan.
        self.added_or_removed = False
        # For the service's cache of library details: whether titles were
        # removed (rewrote: forget them all), and the (kind, kodi id) titles
        # written or whose play state moved (forget just those).
        self.rewrote = False
        self.touched = set()
        self._base, self._span = 0.0, 100.0

    def step(self, fraction, message):
        """Progress within the library being synced, fraction 0..1."""
        self.progress(int(self._base + self._span * min(max(fraction, 0.0), 1.0)), message)

    def check_stop(self):
        self.wait_while_busy()
        if self.should_stop():
            raise Stopped()

    def fetch_all(self, function, args):
        """Fetch in parallel, keeping order; a failed fetch yields None."""
        def guarded(arg):
            try:
                return function(arg)
            except ApiError as error:
                self.log('Could not fetch %s: %s' % (arg, error))
                return None
        with ThreadPoolExecutor(FETCH_WORKERS) as pool:
            return list(pool.map(guarded, args))

    # ----- whole sync -----

    def libraries(self, excluded=()):
        wanted = []
        for library in self.client.libraries():
            kind = (library.get('type') or '').lower()
            if not library.get('enabled', True) or str(library['id']) in excluded:
                continue
            if kind in MOVIE_LIBRARY_TYPES or kind in SHOW_LIBRARY_TYPES:
                wanted.append(library)
        return wanted

    def sync(self, excluded=()):
        """Sync every chosen library. Returns True if Kodi's library changed."""
        self.changed = False
        with self.open_kodi() as kodi:
            kodi.setup_root_paths()
        libraries = self.libraries(excluded)
        wanted = set(str(library['id']) for library in libraries)
        for library_id in self.db.libraries():
            if library_id not in wanted:
                self.log('Removing library %s from Kodi' % library_id)
                self.remove_library(library_id)
        for index, library in enumerate(libraries):
            self.check_stop()
            self._span = 100.0 / max(len(libraries), 1)
            self._base = index * self._span
            self.progress(int(self._base), library.get('name') or '')
            self.sync_library(library)
        self.progress(100, '')
        return self.changed

    def sync_library(self, library):
        kind = (library.get('type') or '').lower()
        if kind in MOVIE_LIBRARY_TYPES:
            self.sync_movies(library)
        if kind in SHOW_LIBRARY_TYPES:
            self.sync_shows(library)

    def library_by_id(self, library_id):
        for library in self.client.libraries():
            if str(library['id']) == str(library_id):
                return library
        return None

    def remove_library(self, library_id):
        movies = self.db.items('movie', library_id)
        shows = self.db.items('tvshow', library_id)
        for batch in chunks(movies, BATCH):
            with self.open_kodi() as kodi:
                for row in batch:
                    self._remove_movie(kodi, row)
        for show in shows:
            with self.open_kodi() as kodi:
                self._remove_show(kodi, show)
        self.changed = self.rewrote = True

    # ----- movies -----

    def sync_movies(self, library):
        library_id = str(library['id'])
        # Read a page at a time and keep only ids and fingerprints: on a 2 GB
        # SHIELD every MB the sync holds is one Kodi lacks when a film starts.
        seen, fetch, states = set(), [], []
        for card in self.client.catalog(library_id, 'movie'):
            content_id = card['content_id']
            seen.add(content_id)
            row = self.db.get(content_id)
            fingerprint = mapping.fingerprint(card)
            if not row or row['fingerprint'] != fingerprint or row['library_id'] != library_id:
                fetch.append((content_id, fingerprint))
            elif row['state'] != mapping.state_fingerprint(card):
                states.append((row, {'user_state': card.get('user_state'), 'runtime': card.get('runtime'),
                                     'duration_seconds': card.get('duration_seconds')}))
        self.log('Library %s: %d movies, %d to write, %d watch-state updates'
                 % (library.get('name'), len(seen), len(fetch), len(states)))
        for number, batch in enumerate(chunks(fetch, BATCH)):
            self.check_stop()
            done = number * BATCH
            self.step(float(done) / len(fetch), '%s: %d / %d' % (library.get('name') or '', done, len(fetch)))
            items = self.fetch_all(lambda pair: self.client.item(pair[0]), batch)
            with self.open_kodi() as kodi:
                for (content_id, fingerprint), item in zip(batch, items):
                    if item:
                        self.write_movie(kodi, item, library, fingerprint)
        self.apply_states(states)
        gone = [row for row in self.db.items('movie', library_id) if row['content_id'] not in seen]
        for batch in chunks(gone, BATCH):
            with self.open_kodi() as kodi:
                for row in batch:
                    self._remove_movie(kodi, row)

    def write_movie(self, kodi, item, library, fingerprint=None):
        content_id = item['content_id']
        row = self.db.get(content_id) or {}
        fields = mapping.movie_fields(item)
        path_id = kodi.add_path(mapping.MOVIES_PATH, content='movies', scraper='metadata.local')
        filename = mapping.play_url(mapping.MOVIES_PATH, content_id, 'movie')
        file_id = kodi.add_file(filename, path_id, fields['date_added'])
        if row.get('kodi_file_id') and row['kodi_file_id'] != file_id:
            kodi.remove_file(row['kodi_file_id'])
        kodi_id = row.get('kodi_id') or self._existing(kodi, 'movie', 'idMovie', file_id) \
            or kodi.new_id('movie', 'idMovie')
        self.added_or_removed |= not row.get('kodi_id')
        people = mapping.people(item, self.port)
        rating_id = kodi.set_rating(kodi_id, 'movie', mapping.ratings(item))
        unique_id = kodi.set_unique_ids(kodi_id, 'movie', mapping.unique_ids(item))
        kodi.write_movie(kodi_id, file_id, fields, rating_id, unique_id, people, path_id)
        kodi.set_people(kodi_id, 'movie', people)
        kodi.set_genres(kodi_id, 'movie', fields['genres'])
        kodi.set_studios(kodi_id, 'movie', fields['studios'])
        kodi.set_countries(kodi_id, 'movie', fields['countries'])
        kodi.set_tags(kodi_id, 'movie', [library.get('name')])
        local_art, remote_art = mapping.artwork(item, mapping.MOVIE_ART, self.port)
        kodi.set_art(kodi_id, 'movie', local_art)
        version = mapping.default_version(item)
        kodi.set_streams(file_id, mapping.stream_details(version), fields['runtime'])
        # The item's details carry the resume position; catalog cards don't.
        count, resume, total, last = mapping.play_state(item)
        kodi.set_play_state(file_id, count, resume, total or fields['runtime'] or 0, last)
        self.db.put(content_id=content_id, kind='movie', library_id=str(library['id']), kodi_id=kodi_id,
                    kodi_file_id=file_id, kodi_path_id=path_id, fingerprint=fingerprint,
                    state=mapping.state_fingerprint(item))
        self.db.set_art(content_id, remote_art)
        self._save_person_art(item)
        self.changed = True
        self.touched.add(('movie', kodi_id))

    def _remove_movie(self, kodi, row):
        if row.get('kodi_id'):
            kodi.remove_movie(row['kodi_id'], row['kodi_file_id'])
        self.db.remove(row['content_id'])
        self.changed = self.added_or_removed = self.rewrote = True

    # ----- TV -----

    def sync_shows(self, library):
        library_id = str(library['id'])
        series_cards = list(self.client.catalog(library_id, 'series'))
        seen_series = set()
        changed_series = []
        for card in series_cards:
            series_id = card['content_id']
            seen_series.add(series_id)
            row = self.db.get(series_id)
            if not row or row['fingerprint'] != mapping.fingerprint(card) or row['library_id'] != library_id:
                changed_series.append(card)
        # A big library has tens of thousands of episode cards, over 100 MB
        # as Python objects, which got Kodi killed for memory on a 2 GB
        # SHIELD. Read them a page at a time and keep only what's needed.
        stale_seasons = OrderedDict()
        seen_episodes = set()
        states = []
        episodes = 0
        for card in self.client.catalog(library_id, 'episode'):
            episodes += 1
            if card.get('series_id') not in seen_series:
                continue
            content_id = card['content_id']
            seen_episodes.add(content_id)
            row = self.db.get(content_id)
            fingerprint = mapping.fingerprint(card)
            if not row or row['fingerprint'] != fingerprint:
                stale_seasons.setdefault((card['series_id'], card.get('season_number')), {})[content_id] = {
                    'added_at': card.get('added_at'), 'fingerprint': fingerprint}
            elif row['state'] != mapping.state_fingerprint(card):
                states.append((row, {'user_state': card.get('user_state'), 'runtime': card.get('runtime'),
                                     'duration_seconds': card.get('duration_seconds')}))
        self.log('Library %s: %d shows (%d changed), %d episodes'
                 % (library.get('name'), len(series_cards), len(changed_series), episodes))
        # Shows first: episodes need their show and season rows.
        # Shows are a fifth of the work, their seasons of episodes the rest.
        for number, batch in enumerate(chunks(changed_series, BATCH)):
            self.check_stop()
            done = number * BATCH
            self.step(0.2 * done / len(changed_series),
                      '%s: %d / %d' % (library.get('name') or '', done, len(changed_series)))
            details = self.fetch_all(self._fetch_show, batch)
            with self.open_kodi() as kodi:
                for card, detail in zip(batch, details):
                    if detail:
                        self.write_show(kodi, detail[0], detail[1], library, card)
        # Then the seasons whose episodes are new or changed.
        self.log('Library %s: %d seasons to write, %d watch-state updates'
                 % (library.get('name'), len(stale_seasons), len(states)))
        keys = list(stale_seasons)
        for number, batch in enumerate(chunks(keys, 10)):
            self.check_stop()
            done = number * 10
            self.step(0.2 + 0.8 * done / len(keys),
                      '%s: %d / %d seasons' % (library.get('name') or '', done, len(keys)))
            listings = self.fetch_all(lambda key: self.client.season_episodes(key[0], key[1]), batch)
            with self.open_kodi() as kodi:
                for key, episodes in zip(batch, listings):
                    if episodes is not None:
                        self.write_episodes(kodi, key[0], key[1], episodes, stale_seasons[key])
        self.apply_states(states)
        # Remove what left Silo.
        with self.open_kodi() as kodi:
            for row in self.db.items('episode', library_id):
                if row['content_id'] not in seen_episodes:
                    self._remove_episode(kodi, row)
        for row in self.db.items('tvshow', library_id):
            if row['content_id'] not in seen_series:
                with self.open_kodi() as kodi:
                    self._remove_show(kodi, row)

    def _fetch_show(self, card):
        series_id = card['content_id']
        return self.client.item(series_id), self.client.seasons(series_id)

    def write_show(self, kodi, item, seasons, library, card=None):
        series_id = item['content_id']
        library_id = str(library['id'])
        row = self.db.get(series_id) or {}
        fields = mapping.show_fields(item)
        path = mapping.show_path(series_id)
        path_id = kodi.add_path(path, date_added=fields['date_added'])
        kodi_id = row.get('kodi_id') or self._show_for_path(kodi, path_id) or kodi.new_id('tvshow', 'idShow')
        self.added_or_removed |= not row.get('kodi_id')
        people = mapping.people(item, self.port)
        rating_id = kodi.set_rating(kodi_id, 'tvshow', mapping.ratings(item))
        unique_id = kodi.set_unique_ids(kodi_id, 'tvshow', mapping.unique_ids(item))
        kodi.write_show(kodi_id, fields, rating_id, unique_id, path_id)
        kodi.set_people(kodi_id, 'tvshow', people)
        kodi.set_genres(kodi_id, 'tvshow', fields['genres'])
        kodi.set_studios(kodi_id, 'tvshow', fields['studios'])
        kodi.set_tags(kodi_id, 'tvshow', [library.get('name')])
        local_art, remote_art = mapping.artwork(item, mapping.SHOW_ART, self.port)
        kodi.set_art(kodi_id, 'tvshow', local_art)
        self.db.put(content_id=series_id, kind='tvshow', library_id=library_id, kodi_id=kodi_id,
                    kodi_path_id=path_id, fingerprint=mapping.fingerprint(card) if card else None,
                    state=None)
        self.db.set_art(series_id, remote_art)
        self._save_person_art(item)
        wanted = set()
        for season in seasons:
            number = season.get('season_number')
            if number is None:
                continue
            key = season.get('content_id') or season_key(series_id, number)
            wanted.add(key)
            season_row = self.db.get(key) or {}
            season_id = kodi.write_season(season_row.get('kodi_id'), kodi_id, number, mapping.season_name(season))
            local_art, remote_art = mapping.artwork(season, mapping.SEASON_ART, self.port, key)
            kodi.set_art(season_id, 'season', local_art)
            self.db.put(content_id=key, kind='season', library_id=library_id, parent_id=series_id,
                        season_number=number, kodi_id=season_id)
            self.db.set_art(key, remote_art)
        for season_row in self.db.children(series_id, 'season'):
            if season_row['content_id'] not in wanted and not self._season_has_episodes(season_row):
                kodi.remove_season(season_row['kodi_id'])
                self.db.remove(season_row['content_id'])
        self.changed = True
        self.touched.add(('tvshow', kodi_id))

    def _season_has_episodes(self, season_row):
        return any(row['season_number'] == season_row['season_number']
                   for row in self.db.children(season_row['parent_id'], 'episode'))

    def _season_id(self, kodi, series_id, number):
        for row in self.db.children(series_id, 'season'):
            if row['season_number'] == number:
                return row['kodi_id']
        show = self.db.get(series_id)
        if not show:
            return None
        key = season_key(series_id, number)
        season_id = kodi.write_season(None, show['kodi_id'], number,
                                      mapping.season_name({'season_number': number}))
        self.db.put(content_id=key, kind='season', library_id=show['library_id'], parent_id=series_id,
                    season_number=number, kodi_id=season_id)
        return season_id

    def write_episodes(self, kodi, series_id, season_number, episodes, cards=None):
        """Write the episodes of one season listing. cards ({content_id: {'added_at',
        'fingerprint'}}) limits it to those changed."""
        show = self.db.get(series_id)
        if not show:
            return
        path = mapping.show_path(series_id)
        season_id = self._season_id(kodi, series_id, season_number)
        for episode in episodes:
            content_id = episode['content_id']
            if cards is not None and content_id not in cards:
                continue
            card = (cards or {}).get(content_id)
            row = self.db.get(content_id) or {}
            fields = mapping.episode_fields(episode, card.get('added_at') if card else None)
            filename = mapping.play_url(path, content_id, 'episode')
            file_id = kodi.add_file(filename, show['kodi_path_id'], fields['date_added'])
            if row.get('kodi_file_id') and row['kodi_file_id'] != file_id:
                kodi.remove_file(row['kodi_file_id'])
            kodi_id = row.get('kodi_id') or self._existing(kodi, 'episode', 'idEpisode', file_id) \
                or kodi.new_id('episode', 'idEpisode')
            self.added_or_removed |= not row.get('kodi_id')
            people = mapping.people(episode, self.port)
            rating_id = kodi.set_rating(kodi_id, 'episode', mapping.ratings(episode))
            unique_id = kodi.set_unique_ids(kodi_id, 'episode', mapping.unique_ids(episode))
            kodi.write_episode(kodi_id, file_id, fields, rating_id, unique_id, people, show['kodi_id'],
                               season_id, filename, show['kodi_path_id'])
            kodi.set_people(kodi_id, 'episode', people)
            local_art, remote_art = mapping.artwork(episode, mapping.EPISODE_ART, self.port)
            kodi.set_art(kodi_id, 'episode', local_art)
            kodi.set_streams(file_id, mapping.episode_stream_details(episode), fields['runtime'])
            # The season listing carries the resume position; catalog cards don't.
            count, resume, total, last = mapping.play_state(episode)
            kodi.set_play_state(file_id, count, resume, total or fields['runtime'] or 0, last)
            self.db.put(content_id=content_id, kind='episode', library_id=show['library_id'], parent_id=series_id,
                        season_number=season_number, kodi_id=kodi_id, kodi_file_id=file_id,
                        kodi_path_id=show['kodi_path_id'],
                        fingerprint=card['fingerprint'] if card else None,
                        state=mapping.state_fingerprint(episode))
            self.db.set_art(content_id, remote_art)
            self.touched.add(('episode', kodi_id))
        self.changed = True

    def _remove_episode(self, kodi, row):
        if row.get('kodi_id'):
            kodi.remove_episode(row['kodi_id'], row['kodi_file_id'])
        self.db.remove(row['content_id'])
        self.changed = self.added_or_removed = self.rewrote = True

    def _remove_show(self, kodi, row):
        for child in self.db.children(row['content_id']):
            if child['kind'] == 'episode' and child.get('kodi_id'):
                kodi.remove_episode(child['kodi_id'], child['kodi_file_id'])
            self.db.remove(child['content_id'])
        if row.get('kodi_id'):
            kodi.remove_show(row['kodi_id'], row.get('kodi_path_id'))
        self.db.remove(row['content_id'])
        self.changed = self.added_or_removed = self.rewrote = True

    # ----- single items, from realtime events -----

    def resync_item(self, content_id, library_id=None):
        """Rewrite one title after Silo reports it changed."""
        try:
            item = self.client.item(content_id)
        except ApiError as error:
            if error.status == 404:
                self.remove_item(content_id)
                return
            raise
        kind = item.get('type')
        row = self.db.get(content_id) or {}
        if kind == 'movie':
            library = self.library_by_id(library_id or row.get('library_id'))
            if library:
                with self.open_kodi() as kodi:
                    self.write_movie(kodi, item, library)
        elif kind == 'series':
            library = self.library_by_id(library_id or row.get('library_id'))
            if library:
                seasons = self.client.seasons(content_id)
                with self.open_kodi() as kodi:
                    self.write_show(kodi, item, seasons, library)
                for season in seasons:
                    if season.get('season_number') is not None:
                        self.resync_season(content_id, season['season_number'])
        elif kind == 'episode' and item.get('series_id'):
            if not self.db.get(item['series_id']):
                self.resync_item(item['series_id'], library_id)
            else:
                self.resync_season(item['series_id'], item.get('season_number'))

    def resync_season(self, series_id, season_number):
        episodes = self.client.season_episodes(series_id, season_number)
        with self.open_kodi() as kodi:
            self.write_episodes(kodi, series_id, season_number, episodes)

    def remove_item(self, content_id):
        row = self.db.get(content_id)
        if not row:
            return
        with self.open_kodi() as kodi:
            if row['kind'] == 'movie':
                self._remove_movie(kodi, row)
            elif row['kind'] == 'tvshow':
                self._remove_show(kodi, row)
            elif row['kind'] == 'episode':
                self._remove_episode(kodi, row)

    # ----- watch state -----

    def apply_states(self, pairs):
        """pairs: [(sync row, card or item)] whose watch state changed."""
        for batch in chunks(pairs, 100):
            with self.open_kodi() as kodi:
                for row, source in batch:
                    count, resume, total, last = mapping.play_state(source)
                    if row.get('kodi_file_id'):
                        kodi.set_play_state(row['kodi_file_id'], count, resume, total, last)
                    self.db.set_state(row['content_id'], mapping.state_fingerprint(source))
                    self.changed = True
                    self.touched.add((row['kind'], row.get('kodi_id')))

    def refresh_state(self, content_id):
        """Re-read one title's watch state after Silo reports a change."""
        row = self.db.get(content_id)
        if not row:
            return
        if row['kind'] in ('movie', 'episode'):
            self.apply_states([(row, self.client.item(content_id))])
        elif row['kind'] == 'tvshow':
            pairs = []
            for season in set(child['season_number'] for child in self.db.children(content_id, 'episode')):
                for episode in self.client.season_episodes(content_id, season):
                    episode_row = self.db.get(episode['content_id'])
                    if episode_row and episode_row['state'] != mapping.state_fingerprint(episode):
                        pairs.append((episode_row, episode))
            self.apply_states(pairs)

    def apply_progress(self, entries):
        """Progress list entries (from /progress) -> Kodi resume points."""
        pairs = []
        for entry in entries:
            row = self.db.get(entry.get('media_item_id'))
            if not row or row['kind'] not in ('movie', 'episode'):
                continue
            source = {'duration_seconds': entry.get('duration_seconds'),
                      'position_seconds': entry.get('position_seconds'),
                      'user_state': {'played': entry.get('completed')},
                      'progress_updated_at': entry.get('updated_at')}
            # The feed only holds changes since the last check, and positions
            # aren't in the state comparison, so apply every entry.
            pairs.append((row, source))
        self.apply_states(pairs)

    def sync_history(self, since=None):
        """Kodi's "last played" dates from Silo's watch history, which comes
        newest first: read back to `since` (all of it the first time) and
        return the newest watch time seen, to pass as `since` next time."""
        latest = {}
        newest = None
        seen = undated = 0
        sources = {}
        for entry in self.client.history():
            seen += 1
            watch = entry.get('watch') or {}
            stamp = watch.get('watched_at')
            if not stamp:
                undated += 1
                continue
            sources[watch.get('source') or '?'] = sources.get(watch.get('source') or '?', 0) + 1
            if since and stamp <= since:
                break
            newest = newest or stamp
            content_id = watch.get('media_item_id') or entry.get('content_id')
            latest.setdefault(content_id, stamp)  # the first one seen is the latest
        pairs = []
        for content_id, stamp in latest.items():
            row = self.db.get(content_id)
            if row and row['kind'] in ('movie', 'episode') and row.get('kodi_file_id'):
                pairs.append((row['kodi_file_id'], mapping.kodi_datetime(stamp), (row['kind'], row.get('kodi_id'))))
        updated = 0
        for batch in chunks(pairs, 200):
            with self.open_kodi() as kodi:
                for file_id, last_played, title in batch:
                    if kodi.set_last_played(file_id, last_played):
                        updated += 1
                        self.touched.add(title)
        if updated:
            self.changed = True
        self.log('Watch history: %d entries read (%d undated, sources %s), %d titles, %d last-played dates '
                 'updated%s' % (seen, undated, sources, len(latest), updated, ' (full read)' if not since else ''))
        return newest or since

    # ----- helpers -----

    def _existing(self, kodi, table, column, file_id):
        kodi.cursor.execute('SELECT %s FROM %s WHERE idFile = ?' % (column, table), (file_id,))
        found = kodi.cursor.fetchone()
        return found[0] if found else None

    def _show_for_path(self, kodi, path_id):
        kodi.cursor.execute('SELECT idShow FROM tvshowlinkpath WHERE idPath = ?', (path_id,))
        found = kodi.cursor.fetchone()
        return found[0] if found else None

    def _save_person_art(self, item):
        for key, url in mapping.person_art(item).items():
            self.db.set_art(key, {'thumb': url})
