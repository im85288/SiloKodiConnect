# -*- coding: utf-8 -*-
"""Pure translation from Silo's JSON to the fields Kodi's video library stores.

Nothing here touches Kodi or the network, so it is covered by plain tests.
"""
import hashlib
import json
from datetime import datetime, timezone

try:
    from urllib.parse import quote, urlencode
except ImportError:  # pragma: no cover
    from urllib import quote, urlencode

ADDON_ID = 'plugin.video.silokodiconnect'
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
# Fields of a catalog card that change with viewing, not with the title itself.
STATE_FIELDS = ('position_seconds', 'progress_updated_at', 'user_state', 'sort_metrics', 'user_data',
                'badges', 'upcoming_event', 'search_diagnostics')
# Signed artwork URLs change daily; their path names the image.
URL_FIELDS_SUFFIX = ('_url', '_urls')


# ----- identity and paths -----

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
        if '.' in text:
            # Python before 3.11 accepts only 3 or 6 fraction digits.
            head, tail = text.split('.', 1)
            digits = ''.join(c for c in tail if c.isdigit())
            text = head + '.' + (digits + '000000')[:6] + tail[len(digits):]
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
