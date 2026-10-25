# -*- coding: utf-8 -*-
"""Pure translation from Silo's JSON to the fields Kodi's video library stores.

Nothing here touches Kodi or the network, so it is covered by plain tests.
"""
import base64
import hashlib
import json
import re
from datetime import datetime, timezone

try:
    from urllib.parse import parse_qsl, quote, unquote, urlencode
except ImportError:  # pragma: no cover
    from urllib import quote, unquote, urlencode
    from urlparse import parse_qsl

ADDON_ID = 'plugin.video.silokodiconnect'
# The service's loopback relay; Kodi's art URLs include it.
RELAY_PORT = 57578
PLUGIN_ROOT = 'plugin://%s/' % ADDON_ID
MOVIES_PATH = PLUGIN_ROOT + 'movies/'
SHOWS_PATH = PLUGIN_ROOT + 'tvshows/'

# Silo artwork field -> Kodi art type, per Kodi media type.
MOVIE_ART = (('poster_url', 'poster'), ('poster_url', 'thumb'), ('backdrop_url', 'fanart'),
             ('logo_url', 'clearlogo'), ('clearart_url', 'clearart'), ('banner_url', 'banner'),
             ('disc_url', 'discart'), ('thumb_url', 'landscape'))
SHOW_ART = (('poster_url', 'poster'), ('backdrop_url', 'fanart'), ('logo_url', 'clearlogo'),
            ('clearart_url', 'clearart'), ('banner_url', 'banner'), ('thumb_url', 'landscape'))
SEASON_ART = (('poster_url', 'poster'),)
EPISODE_ART = (('still_url', 'thumb'),)

WRITER_JOBS = ('writer', 'screenplay', 'story', 'novel', 'teleplay', 'author')
# The fraction of seconds in an ISO 8601 time, before any zone offset.
FRACTION = re.compile(r'T\d\d:\d\d:\d\d\.(\d+)')
# Fields of a catalog card that change with viewing, not with the title itself.
STATE_FIELDS = ('position_seconds', 'progress_updated_at', 'user_state', 'sort_metrics', 'user_data',
                'badges', 'upcoming_event', 'search_diagnostics')
# Signed artwork URLs change daily; their path names the image.
URL_FIELDS_SUFFIX = ('_url', '_urls')


# ----- identity and paths -----

def content_id_from_path(path):
    """The Silo content id in a Kodi path SiloKodiConnect wrote: a play URL's
    id, or a show folder's last segment; None for anything else."""
    if not path.startswith(PLUGIN_ROOT):
        return None
    query = dict(parse_qsl(path.partition('?')[2]))
    if query.get('id'):
        return query['id']
    segment = path.partition('?')[0].rstrip('/').rpartition('/')[2]
    return unquote(segment) if path.startswith(SHOWS_PATH) and segment else None


def play_url(path, content_id, kind):
    return '%s?%s' % (path, urlencode({'mode': 'play', 'id': content_id, 'type': kind}))


def show_path(series_id):
    return '%s%s/' % (SHOWS_PATH, quote(series_id, safe=''))


def art_url(port, content_id, kind):
    """A stable local address for an image; the service resolves it to Silo's
    current signed URL, so Kodi's texture cache keeps working across days."""
    return 'http://127.0.0.1:%d/art/%s/%s' % (port, quote(content_id, safe=''), kind)


def _strip_signature(value):
    return value.split('?', 1)[0] if isinstance(value, str) else value


def fingerprint(card):
    """Hash of the parts of a card that describe the title, not its viewing state."""
    data = {}
    for key, value in card.items():
        if key in STATE_FIELDS:
            continue
        if key.endswith(URL_FIELDS_SUFFIX):
            value = [_strip_signature(v) for v in value] if isinstance(value, list) else _strip_signature(value)
        data[key] = value
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()


# ----- dates and numbers -----

def parse_time(value):
    if not value:
        return None
    try:
        text = value.replace('Z', '+00:00')
        match = FRACTION.search(text)
        if match:
            # Python before 3.11 accepts only 3 or 6 fraction digits.
            text = text[:match.start(1)] + (match.group(1) + '000000')[:6] + text[match.end(1):]
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def kodi_datetime(value):
    """Silo UTC timestamp -> Kodi's local 'YYYY-MM-DD HH:MM:SS'."""
    moment = parse_time(value)
    if moment is None:
        return None
    return moment.astimezone().strftime('%Y-%m-%d %H:%M:%S')


def kodi_date(value):
    if not value:
        return None
    return value[:10]


def runtime_seconds(item):
    if item.get('duration_seconds'):
        return int(item['duration_seconds'])
    if item.get('runtime'):
        return int(item['runtime']) * 60
    return None


def joined(values):
    return ' / '.join(v for v in values or [] if v) or None


# ----- people, ratings, ids -----

def people(item, port):
    cast = []
    for person in sorted(item.get('cast') or [], key=lambda p: p.get('order', 999)):
        if not person.get('name'):
            continue
        thumb = (art_url(port, 'person:%s' % person['person_id'], 'thumb')
                 if person.get('person_id') and person.get('photo_url') else None)
        cast.append((person['name'], thumb, person.get('character') or '', person.get('order', len(cast))))
    directors, writers = [], []
    for person in item.get('crew') or []:
        job = (person.get('job') or '').lower()
        name = person.get('name')
        if not name:
            continue
        if job == 'director' and (name, ) not in directors:
            directors.append((name, ))
        elif job in WRITER_JOBS and (name, ) not in writers:
            writers.append((name, ))
    return {'actor': cast, 'director': directors, 'writer': writers}


def person_art(item):
    """{'person:<id>': signed photo URL} for the cast."""
    return {'person:%s' % p['person_id']: p['photo_url']
            for p in item.get('cast') or [] if p.get('person_id') and p.get('photo_url')}


def rating(item):
    """(rating_type, value out of 10, votes) or None."""
    votes = {s.get('source'): s.get('votes') for s in item.get('rating_sources') or []}
    if item.get('rating_imdb'):
        return 'imdb', float(item['rating_imdb']), votes.get('imdb') or 0
    if item.get('rating_tmdb'):
        return 'themoviedb', float(item['rating_tmdb']), votes.get('tmdb') or 0
    for entry in item.get('ratings') or []:
        if entry.get('score'):
            score = float(entry['score'])
            return entry.get('source') or 'default', score / 10 if score > 10 else score, 0
    return None


def unique_ids(item):
    ids = []
    for key, kind in (('tmdb_id', 'tmdb'), ('imdb_id', 'imdb'), ('tvdb_id', 'tvdb')):
        if item.get(key):
            ids.append((kind, str(item[key])))
    return ids


def artwork(item, mapping, port, content_id=None):
    """({kodi_art_type: local URL}, {kodi_art_type: Silo URL})."""
    content_id = content_id or item['content_id']
    local, remote = {}, {}
    for field, kind in mapping:
        if item.get(field):
            local[kind] = art_url(port, content_id, kind)
            remote[kind] = item[field]
    return local, remote


# ----- stream details -----

def hdr_type(track, version):
    if track.get('dolby_vision') or track.get('dv_profile'):
        return 'dolbyvision'
    transfer = (track.get('color_transfer') or '').lower()
    kind = (track.get('video_range_type') or track.get('video_range') or '').lower()
    if 'hlg' in kind or transfer == 'arib-std-b67':
        return 'hlg'
    if 'hdr10' in kind or 'pq' in kind or transfer == 'smpte2084' or version.get('hdr'):
        return 'hdr10'
    return ''


def aspect(track):
    value = track.get('aspect_ratio') or ''
    if ':' in value:
        left, right = value.split(':', 1)
        try:
            return round(float(left) / float(right), 4)
        except (ValueError, ZeroDivisionError):
            pass
    if track.get('width') and track.get('height'):
        return round(float(track['width']) / track['height'], 4)
    return None


RESOLUTION_SIZES = {'2160p': (3840, 2160), '4k': (3840, 2160), '1080p': (1920, 1080), '720p': (1280, 720),
                    '576p': (720, 576), '480p': (720, 480), 'sd': (720, 480)}


def stream_details(version):
    """Kodi streamdetails for one Silo file version (item detail form)."""
    if not version:
        return None
    video = []
    for track in version.get('video_tracks') or []:
        video.append({'codec': track.get('codec') or version.get('codec_video') or '',
                      'aspect': aspect(track), 'width': track.get('width'), 'height': track.get('height'),
                      'hdr': hdr_type(track, version), 'stereo': ''})
    if not video and version.get('codec_video'):
        width, height = RESOLUTION_SIZES.get((version.get('resolution') or '').lower(), (None, None))
        video.append({'codec': version['codec_video'], 'aspect': None, 'width': width, 'height': height,
                      'hdr': 'hdr10' if version.get('hdr') else '', 'stereo': ''})
    audio = [{'codec': track.get('codec') or '', 'channels': track.get('channels'),
              'language': track.get('language') or ''}
             for track in version.get('audio_tracks') or []]
    subtitles = [track.get('language') or '' for track in version.get('subtitle_tracks') or []]
    return {'video': video, 'audio': audio, 'subtitle': subtitles}


def episode_stream_details(episode):
    """Kodi streamdetails from a season listing's lighter file summary."""
    files = episode.get('files') or []
    if not files:
        return None
    first = files[0]
    width, height = RESOLUTION_SIZES.get((first.get('resolution') or '').lower(), (None, None))
    details = {'video': [], 'audio': [], 'subtitle': []}
    if first.get('codec_video'):
        details['video'].append({'codec': first['codec_video'], 'aspect': None, 'width': width,
                                 'height': height, 'hdr': 'hdr10' if first.get('hdr') else '', 'stereo': ''})
    if first.get('audio_channels'):
        details['audio'].append({'codec': '', 'channels': first['audio_channels'], 'language': ''})
    return details


def default_version(item):
    versions = item.get('versions') or []
    last = (item.get('user_data') or {}).get('last_file_id')
    for version in versions:
        if last and version.get('file_id') == last:
            return version
    return versions[0] if versions else None


# ----- watch state -----

def play_state(card):
    """(playcount, resume seconds, total seconds, last played) from a card or item."""
    user_state = card.get('user_state') or {}
    user_data = card.get('user_data') or {}
    played = bool(user_state.get('played') or user_data.get('played'))
    position = card.get('position_seconds') or user_data.get('position_seconds') or 0
    total = runtime_seconds(card) or user_data.get('duration_seconds') or 0
    count = user_data.get('watched_count') or (1 if played else 0)
    if not played:
        count = 0
    last = kodi_datetime(card.get('progress_updated_at') or (card.get('sort_metrics') or {}).get('viewed_at'))
    return count, float(position) if position and not played else 0.0, float(total), last


def state_fingerprint(card):
    """'1' watched, '0' not. Catalog cards carry no resume position, so
    positions come from item details, season listings and Silo's progress
    feed, and are not part of this comparison."""
    return '1' if play_state(card)[0] else '0'


# ----- the rows themselves -----

def movie_fields(item):
    return {
        'title': item.get('title') or '',
        'plot': item.get('overview') or '',
        'tagline': item.get('tagline') or '',
        'sorttitle': item.get('sort_title') or item.get('title') or '',
        'runtime': runtime_seconds(item),
        'mpaa': item.get('content_rating') or '',
        'genres': item.get('genres') or [],
        'studios': item.get('studios') or [],
        'countries': item.get('countries') or [],
        'originaltitle': item.get('original_title') or item.get('title') or '',
        'premiered': kodi_date(item.get('release_date')) or ('%s-01-01' % item['year'] if item.get('year') else None),
        'year': item.get('year'),
        'userrating': item.get('user_rating') or None,
        'date_added': kodi_datetime(item.get('added_at')),
    }


def show_fields(item):
    return {
        'title': item.get('title') or '',
        'plot': item.get('overview') or '',
        'status': item.get('show_status') or '',
        'sorttitle': item.get('sort_title') or item.get('title') or '',
        'mpaa': item.get('content_rating') or '',
        'genres': item.get('genres') or [],
        'studios': item.get('networks') or item.get('studios') or [],
        'originaltitle': item.get('original_title') or item.get('title') or '',
        'premiered': kodi_date(item.get('first_air_date') or item.get('release_date')),
        'userrating': item.get('user_rating') or None,
        'date_added': kodi_datetime(item.get('added_at')),
    }


def season_name(season):
    if season.get('title'):
        return season['title']
    if season.get('is_specials') or season.get('season_number') == 0:
        return 'Specials'
    return 'Season %s' % season.get('season_number')


def episode_fields(episode, added_at=None):
    return {
        'title': episode.get('title') or 'Episode %s' % episode.get('episode_number'),
        'plot': episode.get('overview') or '',
        'aired': kodi_date(episode.get('air_date')),
        'runtime': runtime_seconds(episode),
        'season': episode.get('season_number'),
        'episode': episode.get('episode_number'),
        'date_added': kodi_datetime(added_at or episode.get('added_at')),
    }


# ----- what a Silo-style skin shows about the focused title -----

RATING_SOURCES = {'imdb': 'IMDb', 'themoviedb': 'TMDB', 'tmdb': 'TMDB', 'trakt': 'Trakt'}
AUDIO_CODECS = {'truehd': 'TRUEHD', 'eac3': 'DD+', 'ac3': 'AC3', 'dts': 'DTS', 'dtshd_ma': 'DTS-HD MA',
                'dca': 'DTS', 'aac': 'AAC', 'flac': 'FLAC', 'opus': 'OPUS', 'mp3': 'MP3'}
CHANNEL_LAYOUTS = {8: '7.1', 7: '6.1', 6: '5.1', 2: '2.0', 1: 'MONO'}
HDR_LABELS = {'dolbyvision': 'DOLBY VISION', 'hdr10': 'HDR10', 'hdr10plus': 'HDR10+', 'hlg': 'HLG'}
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def quality_badges(streamdetails):
    """Silo's quality badges for a file: resolution, dynamic range, audio."""
    badges = []
    video = ((streamdetails or {}).get('video') or [{}])[0]
    width, height = video.get('width') or 0, video.get('height') or 0
    if height >= 1600 or width >= 3000:
        badges.append('4K')
    elif height >= 1000 or width >= 1800:
        badges.append('1080P')
    elif height >= 700 or width >= 1200:
        badges.append('720P')
    elif height:
        badges.append('SD')
    hdr = (video.get('hdrtype') or '').lower()
    if hdr:
        badges.append(HDR_LABELS.get(hdr, hdr.upper()))
    audio = ((streamdetails or {}).get('audio') or [{}])[0]
    codec = (audio.get('codec') or '').lower()
    layout = CHANNEL_LAYOUTS.get(audio.get('channels') or 0, '')
    # Episodes synced from season listings know their channels but not their codec.
    if codec or layout:
        badges.append(('%s %s' % (AUDIO_CODECS.get(codec, codec.upper()), layout)).strip())
    return badges


def aired_label(date):
    """2026-09-25 -> 'Aired Sep 25, 2026'."""
    try:
        year, month, day = (int(part) for part in (date or '')[:10].split('-'))
        return 'Aired %s %d, %d' % (MONTHS[month - 1], day, year)
    except (ValueError, IndexError):
        return ''


def ambient_tint(thumbhash):
    """Silo's ambient page tint for a title as Kodi's ARGB hex ('' if none): the
    average colour a thumbhash carries in its header, scaled to a luminance of
    0.22 as Silo's TV apps do (dark art lifted, bright art dimmed). Silo samples
    the backdrop; its API only has the poster's thumbhash, which shares the
    title's palette."""
    try:
        raw = base64.b64decode(thumbhash.replace('-', '+').replace('_', '/') + '=' * (-len(thumbhash) % 4))
    except (AttributeError, ValueError, TypeError):
        return ''
    if len(raw) < 3:
        return ''
    header = raw[0] | raw[1] << 8 | raw[2] << 16
    lightness = (header & 63) / 63.0
    p = ((header >> 6) & 63) / 31.5 - 1
    q = ((header >> 12) & 63) / 31.5 - 1
    blue = lightness - 2.0 / 3 * p
    red = (3 * lightness - blue + q) / 2
    green = red - q
    red, green, blue = (min(1.0, max(0.0, c)) for c in (red, green, blue))
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    target = 0.22
    if luminance <= 0.001:
        return ''
    scale = target / luminance if luminance > target else max(1.0, target / max(luminance, 0.05))
    return 'FF%02X%02X%02X' % tuple(int(round(min(1.0, c * scale) * 255)) for c in (red, green, blue))


def skyline_properties(kind, details, show=None):
    """ListItem properties (silo.*) for the Skyline hero, from Kodi's JSON-RPC
    details of a movie, show or episode; `show` is the episode's show."""
    props = {}
    if kind == 'episode':
        props['silo.code'] = 'S%02dE%02d' % (details.get('season') or 0, details.get('episode') or 0)
        props['silo.aired'] = aired_label(details.get('firstaired'))
        if details.get('tvshowid') is not None:
            # The episode's season in Kodi's library, for an episode page's rail.
            props['silo.season_path'] = 'videodb://tvshows/titles/%d/%d/' % (details['tvshowid'],
                                                                           details.get('season') or 0)
    props['silo.mpaa'] = details.get('mpaa') or (show or {}).get('mpaa') or ''
    genres = details.get('genre') or (show or {}).get('genre') or []
    props['silo.genre'] = genres[0] if genres else ''
    ratings = details.get('ratings') or {}
    best = next((name for name, value in ratings.items() if value.get('default')), None) or \
        next(iter(ratings), None)
    if best and ratings[best].get('rating'):
        props['silo.rating'] = '%s %.1f' % (RATING_SOURCES.get(best, best.title()), ratings[best]['rating'])
    cast = sorted(details.get('cast') or [], key=lambda person: person.get('order', 999))
    props['silo.cast'] = ', '.join(person['name'] for person in cast[:3] if person.get('name'))
    for index, badge in enumerate(quality_badges(details.get('streamdetails'))[:3], 1):
        props['silo.badge%d' % index] = badge
    if kind in ('movie', 'tvshow') and genres:
        props['silo.similar'] = similar_path(kind, genres[0], details.get('title') or '')
    return props


SIMILAR_LIMIT = 20


def similar_path(kind, genre, title):
    """A Kodi library query for the detail page's More Like This rail: other
    titles of the same kind sharing the first genre, best rated first. Built
    here so the genre is URL-encoded (TV genres such as "Action & Adventure"
    would break a query a skin builds)."""
    rules = {'type': kind + 's', 'order': {'method': 'rating', 'direction': 'descending'}, 'limit': SIMILAR_LIMIT,
             'rules': {'and': [{'field': 'genre', 'operator': 'is', 'value': [genre]},
                               {'field': 'title', 'operator': 'isnot', 'value': [title]}]}}
    folder = 'videodb://movies/titles/' if kind == 'movie' else 'videodb://tvshows/titles/'
    return folder + '?xsp=' + quote(json.dumps(rules, separators=(',', ':')), safe='')
