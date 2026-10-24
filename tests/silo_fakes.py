# -*- coding: utf-8 -*-
"""A fake Silo server: catalog data shaped like /api/v2 responses."""
import copy

from resources.lib.silokodi.api import ApiError


def movie(content_id='movie-1', title='Heat', **extra):
    item = {
        'content_id': content_id, 'type': 'movie', 'title': title, 'year': 1995, 'sort_title': title,
        'overview': 'A Los Angeles crime saga.', 'tagline': 'A crime saga', 'genres': ['Crime', 'Drama'],
        'studios': ['Warner Bros.'], 'countries': ['US'], 'content_rating': 'R', 'runtime': 170,
        'release_date': '1995-12-15', 'added_at': '2026-01-02T03:04:05.678Z', 'imdb_id': 'tt0113277',
        'tmdb_id': '949', 'rating_imdb': 8.3, 'rating_sources': [{'source': 'imdb', 'score': 83, 'votes': 673852}],
        'poster_url': '/api/v2/artwork/abc/w500.r1.webp?sig=1', 'backdrop_url': 'https://cdn.test/b.webp?sig=2',
        'cast': [{'name': 'Al Pacino', 'character': 'Vincent Hanna', 'order': 0, 'person_id': '7',
                  'photo_url': '/api/v2/artwork/p7.webp?sig=3'},
                 {'name': 'Robert De Niro', 'character': 'Neil McCauley', 'order': 1, 'person_id': '8'}],
        'crew': [{'name': 'Michael Mann', 'job': 'Director'}, {'name': 'Michael Mann', 'job': 'Writer'}],
        'user_state': {'played': False}, 'user_data': {'played': False, 'last_file_id': '120'},
        'versions': [{'file_id': '120', 'resolution': '2160p', 'codec_video': 'hevc', 'hdr': True,
                      'video_tracks': [{'codec': 'hevc', 'width': 3840, 'height': 1600, 'aspect_ratio': '12:5',
                                        'dv_profile': 8}],
                      'audio_tracks': [{'codec': 'truehd', 'channels': 8, 'language': 'eng'}],
                      'subtitle_tracks': [{'language': 'eng'}]}],
    }
    item.update(extra)
    return item


def card_of(item):
    """The catalog card for an item: the list form, without detail-only fields.
    Like Silo's, cards carry watched state but no resume position."""
    keep = ('content_id', 'type', 'title', 'year', 'overview', 'genres', 'added_at', 'runtime',
            'duration_seconds', 'poster_url', 'backdrop_url', 'user_state', 'series_id', 'season_number',
            'episode_number', 'rating_imdb', 'studios')
    return {key: copy.deepcopy(item[key]) for key in keep if key in item}


def series(content_id='series-1', title='Severance', **extra):
    item = {'content_id': content_id, 'type': 'series', 'title': title, 'overview': 'Work-life balance.',
            'genres': ['Drama'], 'networks': ['Apple TV+'], 'first_air_date': '2022-02-18',
            'added_at': '2026-01-02T03:04:05Z', 'tvdb_id': '371980', 'poster_url': '/api/v2/artwork/s.webp?sig=4',
            'cast': [{'name': 'Adam Scott', 'character': 'Mark', 'order': 0, 'person_id': '9'}], 'crew': []}
    item.update(extra)
    return item


def season(series_id, number, content_id=None):
    return {'content_id': content_id or '%s-s%d' % (series_id, number), 'season_number': number,
            'title': '', 'poster_url': '/api/v2/artwork/%s-%d.webp?sig=5' % (series_id, number)}


def episode(series_id, season_number, number, played=False, position=0):
    return {'content_id': '%s-s%de%d' % (series_id, season_number, number), 'type': 'episode',
            'series_id': series_id, 'season_number': season_number, 'episode_number': number,
            'title': 'Episode %d' % number, 'overview': 'Plot %d' % number, 'air_date': '2022-02-%02d' % (17 + number),
            'runtime': 55, 'still_url': '/api/v2/artwork/e%d.webp?sig=6' % number, 'tvdb_id': '9%d' % number,
            'files': [{'file_id': 'f%d' % number, 'resolution': '2160p', 'codec_video': 'hevc', 'hdr': True,
                       'audio_channels': 6}],
            'user_data': {'played': played, 'position_seconds': position},
            'user_state': {'played': played}}


class FakeSilo(object):
    def __init__(self):
        self.libraries_list = [{'id': '1', 'name': 'Movies', 'type': 'movies', 'enabled': True},
                               {'id': '2', 'name': 'TV', 'type': 'series', 'enabled': True}]
        self.movies = {}
        self.shows = {}      # series_id -> item
        self.seasons_of = {}  # series_id -> [season]
        self.episodes_of = {}  # (series_id, number) -> [episode]
        self.calls = []
        self.store = None
        self.server_url = 'https://silo.test'

    def add_movie(self, item):
        self.movies[item['content_id']] = item

    def add_show(self, item, seasons):
        """seasons: {number: [episode, ...]}"""
        self.shows[item['content_id']] = item
        self.seasons_of[item['content_id']] = [season(item['content_id'], n) for n in sorted(seasons)]
        for number, episodes in seasons.items():
            self.episodes_of[(item['content_id'], number)] = episodes

    # ----- the SiloClient surface the syncer uses -----

    def libraries(self):
        return copy.deepcopy(self.libraries_list)

    def catalog(self, library_id, media_type):
        self.calls.append(('catalog', library_id, media_type))
        if library_id == '1' and media_type == 'movie':
            return [card_of(item) for item in self.movies.values()]
        if library_id == '2' and media_type == 'series':
            return [card_of(item) for item in self.shows.values()]
        if library_id == '2' and media_type == 'episode':
            return [card_of(e) for episodes in self.episodes_of.values() for e in episodes]
        return []

    def item(self, content_id):
        self.calls.append(('item', content_id))
        for source in (self.movies, self.shows):
            if content_id in source:
                return copy.deepcopy(source[content_id])
        for episodes in self.episodes_of.values():
            for e in episodes:
                if e['content_id'] == content_id:
                    return copy.deepcopy(e)
        raise ApiError(404, 'not_found', 'gone')

    def seasons(self, series_id):
        self.calls.append(('seasons', series_id))
        return copy.deepcopy(self.seasons_of.get(series_id, []))

    def season_episodes(self, series_id, number):
        self.calls.append(('season_episodes', series_id, number))
        return copy.deepcopy(self.episodes_of.get((series_id, number), []))

    def url(self, path):
        return self.server_url + path if path.startswith('/') else path
