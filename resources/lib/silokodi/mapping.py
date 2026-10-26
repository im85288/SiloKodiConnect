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


def ratings(item):
    """Every rating Silo has for a title, as [(rating_type, value out of 10,
    votes)], the default (IMDb if there is one) first."""
    votes = {s.get('source'): s.get('votes') for s in item.get('rating_sources') or []}
    found = []
    if item.get('rating_imdb'):
        found.append(('imdb', float(item['rating_imdb']), votes.get('imdb') or 0))
    if item.get('rating_tmdb'):
        found.append(('themoviedb', float(item['rating_tmdb']), votes.get('tmdb') or 0))
    if not found:
        best = rating(item)
        found = [best] if best else []
    return found


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
    if 'hdr10plus' in kind or 'hdr10+' in kind:
        return 'hdr10plus'
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
    audio = [{'codec': audio_codec(track.get('codec'), track.get('profile')), 'channels': track.get('channels'),
              'language': track.get('language') or ''}
             for track in version.get('audio_tracks') or []]
    subtitles = [track.get('language') or '' for track in version.get('subtitle_tracks') or []]
    return {'video': video, 'audio': audio, 'subtitle': subtitles}


def audio_codec(codec, profile=''):
    """Kodi's codec name for a track, with Atmos as Kodi's own players name
    it (eac3_ddp_atmos, truehd_atmos), from Silo's profile ("... Atmos")."""
    codec = (codec or '').lower()
    if 'atmos' in (profile or '').lower() and codec in ('eac3', 'truehd'):
        return 'eac3_ddp_atmos' if codec == 'eac3' else 'truehd_atmos'
    return codec


def episode_stream_details(episode):
    """Kodi streamdetails from a season listing's lighter file summary."""
    files = episode.get('files') or []
    if not files:
        return None
    first = files[0]
    width, height = RESOLUTION_SIZES.get((first.get('resolution') or '').lower(), (None, None))
    details = {'video': [], 'audio': [], 'subtitle': []}
    # Silo's card summary names the HDR format and audio codec the file
    # summary leaves out (HDR10+, EAC3), as Silo's badges show them.
    overlay = episode.get('overlay_summary') or {}
    hdr = overlay_hdr(overlay.get('hdr'), 'hdr10' if first.get('hdr') else '')
    if first.get('codec_video'):
        details['video'].append({'codec': first['codec_video'], 'aspect': None, 'width': width,
                                 'height': height, 'hdr': hdr, 'stereo': ''})
    if first.get('audio_channels'):
        codec = OVERLAY_AUDIO.get((overlay.get('audio') or '').lower(), '')
        details['audio'].append({'codec': codec, 'channels': first['audio_channels'], 'language': ''})
    return details


def overlay_hdr(label, default=''):
    """Kodi's HDR type for Silo's card label, which can name several formats
    ("DV HDR10+"): the first one Kodi knows, Dolby Vision ahead of the rest."""
    label = (label or '').lower()
    if label in OVERLAY_HDR:
        return OVERLAY_HDR[label]
    for part in label.split():
        if part in OVERLAY_HDR:
            return OVERLAY_HDR[part]
    return default


# Silo's overlay_summary labels to Kodi's stream detail names.
OVERLAY_HDR = {'hdr10': 'hdr10', 'hdr10+': 'hdr10plus', 'dolby vision': 'dolbyvision', 'dv': 'dolbyvision',
               'hlg': 'hlg'}
OVERLAY_AUDIO = {'atmos': 'eac3_ddp_atmos', 'truehd atmos': 'truehd_atmos', 'eac3': 'eac3', 'e-ac3': 'eac3', 'ac3': 'ac3', 'truehd': 'truehd', 'dts': 'dts',
                 'dts-hd ma': 'dtshd_ma', 'dts-hd': 'dtshd_ma', 'aac': 'aac', 'flac': 'flac', 'opus': 'opus',
                 'mp3': 'mp3'}


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
AUDIO_CODECS = {'eac3_ddp_atmos': 'ATMOS', 'truehd_atmos': 'ATMOS', 'truehd': 'TRUEHD', 'eac3': 'EAC3', 'ac3': 'AC3', 'dts': 'DTS', 'dtshd_ma': 'DTS-HD MA',
                'dca': 'DTS', 'aac': 'AAC', 'flac': 'FLAC', 'opus': 'OPUS', 'mp3': 'MP3'}
AUDIO_LINE = {'eac3_ddp_atmos': 'EAC3 Atmos', 'truehd_atmos': 'TrueHD Atmos'}  # the info line's names
HDR_NAMES = {'dolbyvision': 'Dolby Vision', 'hdr10plus': 'HDR10+', 'hdr10': 'HDR10', 'hlg': 'HLG'}
CHANNEL_LAYOUTS = {8: '7.1', 7: '6.1', 6: '5.1', 2: '2.0', 1: 'MONO'}
HDR_LABELS = {'dolbyvision': 'DOLBY VISION', 'hdr10': 'HDR10', 'hdr10plus': 'HDR10+', 'hlg': 'HLG'}
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


LANGUAGE_NAMES = {'eng': 'English', 'en': 'English', 'fre': 'French', 'fra': 'French', 'fr': 'French',
                  'ger': 'German', 'deu': 'German', 'de': 'German', 'spa': 'Spanish', 'es': 'Spanish',
                  'ita': 'Italian', 'it': 'Italian', 'jpn': 'Japanese', 'ja': 'Japanese', 'kor': 'Korean',
                  'dut': 'Dutch', 'nld': 'Dutch', 'por': 'Portuguese', 'swe': 'Swedish', 'nor': 'Norwegian',
                  'dan': 'Danish', 'fin': 'Finnish', 'pol': 'Polish', 'rus': 'Russian', 'chi': 'Chinese',
                  'zho': 'Chinese', 'hin': 'Hindi', 'ara': 'Arabic', 'tur': 'Turkish'}


def language_name(code):
    return LANGUAGE_NAMES.get((code or '').lower(), (code or '').upper())


def tech_lines(streamdetails):
    """Silo's Version / Audio / Subtitles line for an info page:
    silo.version (4K · HDR), silo.audio (English · EAC3 · 5.1) and
    silo.subtitles (English, French, or None)."""
    details = streamdetails or {}
    video = (details.get('video') or [{}])[0]
    audio = (details.get('audio') or [{}])[0]
    badges = quality_badges(streamdetails)
    version = [badges[0]] if badges and badges[0] in ('4K', '1080P', '720P', 'SD') else []
    # The format by name (Dolby Vision, HDR10+), as the row's badges have it.
    hdr = (video.get('hdrtype') or '').lower()
    if hdr:
        version.append(HDR_NAMES.get(hdr, 'HDR'))
    codec = (audio.get('codec') or '').lower()
    audio_parts = [part for part in (language_name(audio.get('language')),
                                     AUDIO_LINE.get(codec) or AUDIO_CODECS.get(codec, codec.upper()),
                                     CHANNEL_LAYOUTS.get(audio.get('channels') or 0, '')) if part]
    subtitles = []
    for track in details.get('subtitle') or []:
        name = language_name(track.get('language'))
        if name and name not in subtitles:
            subtitles.append(name)
    return {'silo.version': ' · '.join(version), 'silo.audio': ' · '.join(audio_parts),
            'silo.subtitles': ', '.join(subtitles[:3]) + (' +%d' % (len(subtitles) - 3) if len(subtitles) > 3 else '')
            if subtitles else 'None'}


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
        props['silo.air_date'] = props['silo.aired'][len('Aired '):]
        if details.get('tvshowid') is not None:
            # The episode's show and season in Kodi's library, for an episode
            # page's season pills and episode rail.
            props['silo.show_path'] = 'videodb://tvshows/titles/%d/' % details['tvshowid']
            props['silo.season_path'] = '%s%d/' % (props['silo.show_path'], details.get('season') or 0)
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
    # The info page's ratings (an episode shows its series', as Silo does)
    # and its Version / Audio / Subtitles line.
    for source, key in (('imdb', 'imdb'), ('themoviedb', 'tmdb')):
        value = ((details.get('ratings') or {}).get(source) or ((show or {}).get('ratings') or {}).get(source)
                 or {}).get('rating')
        props['silo.rating.%s' % key] = '%.1f' % value if value else ''
    props.update(tech_lines(details.get('streamdetails')))
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


# ----- requests -----

TMDB_IMAGES = 'https://image.tmdb.org/t/p/'
REQUEST_STATES = {'pending': 'Requested', 'approved': 'Approved', 'processing': 'Downloading',
                  'partially_available': 'Partly available', 'available': 'Available', 'declined': 'Declined',
                  'cancelled': 'Cancelled', 'failed': 'Failed'}


# Silo's request status colours (RequestColors) as Kodi colours.
REQUEST_TINTS = {'amber': 'FFF59E0B', 'sky': 'FF38BDF8', 'emerald': 'FF34D399', 'rose': 'FFFB7185',
                 'neutral': '99EDEDED'}
REQUEST_STEPS = 4  # Requested, Approved, Downloading, In your library


def request_progress(state, status='', outcome='', available=False, requestable=False):
    """What Silo's TV app shows for a request (RequestDisplayState and
    RequestProgress): {'label', 'long', 'tint', 'done', 'step'} with the short
    card caption, the hero's phrase, the dot colour, the steps behind it and
    the step it is on (1-4, 0 for none); None for a title with nothing to show
    (it can simply be requested)."""
    def progress(label, long, tint, done, step):
        return {'label': label, 'long': long, 'tint': REQUEST_TINTS[tint], 'done': done, 'step': step}
    display = {'pending': 'pending', 'approved': 'on_the_way', 'processing': 'on_the_way',
               'partially_available': 'on_the_way', 'available': 'in_library', 'declined': 'declined',
               'failed': 'failed', 'cancelled': 'unavailable'}.get(state or '')
    if not display:
        if outcome in ('declined', 'failed'):
            display = outcome
        elif outcome == 'cancelled':
            display = 'unavailable'
        elif available and not outcome:
            display = 'in_library'
        elif status:
            display = {'pending': 'pending', 'completed': 'in_library', 'failed': 'failed'}.get(status, 'on_the_way')
        elif requestable or outcome:
            return None
        else:
            display = 'unavailable'
    if display == 'pending':
        return progress('Pending', 'Waiting for approval', 'amber', 1, 2)
    if display == 'on_the_way':
        if state == 'partially_available':
            return progress('Partly in library', 'Partly in your library', 'sky', 3, 4)
        if status == 'downloading':
            return progress('Downloading', 'Downloading', 'sky', 2, 3)
        if status == 'completed':
            return progress('Adding to library', 'Adding to your library', 'sky', 3, 4)
        if status in ('approved', 'queued'):
            return progress('Queued', 'Queued for download', 'sky', 2, 3)
        return progress('On the way', 'On the way', 'sky', 2, 3)
    if display == 'in_library':
        return progress('In library', 'In your library', 'emerald', REQUEST_STEPS, 0)
    if display == 'declined':
        return progress('Declined', 'Declined', 'rose', 1, 2)
    if display == 'failed':
        return progress('Failed', 'Request failed', 'rose', 2, 3)
    return progress('Unavailable', 'Unavailable', 'neutral', 0, 0)


def request_card(item):
    """A request, or a title Silo can request, as a card for a skin: TMDB
    artwork, Silo's one state to show and whether it can be requested now."""
    request = item.get('request') or {}
    state = item.get('state') or request.get('state') or ''
    if item.get('availability') == 'available' or (item.get('library_content_id') and not state):
        state = state or 'available'
    poster, backdrop = item.get('poster_path') or '', item.get('backdrop_path') or ''
    shown = request_progress(item.get('state') or request.get('state'), item.get('status') or request.get('status') or '',
                             item.get('outcome') or '', item.get('availability') == 'available'
                             or bool(item.get('library_content_id') and not state),
                             bool(request.get('requestable'))) or {}
    return {'type': item.get('media_type') or 'movie', 'tmdb': item.get('tmdb_id'),
            'title': item.get('title') or '', 'year': item.get('year') or 0, 'overview': item.get('overview') or '',
            'poster': TMDB_IMAGES + 'w500' + poster if poster else '',
            'backdrop': TMDB_IMAGES + 'w1280' + backdrop if backdrop else '',
            'rating': round(float(item.get('vote_average') or 0), 1),
            'state': state, 'label': shown.get('label', ''), 'long': shown.get('long', ''),
            'tint': shown.get('tint', ''), 'done': shown.get('done', 0), 'step': shown.get('step', 0),
            'requestable': bool(request.get('requestable')), 'content_id': item.get('library_content_id') or ''}


def request_body(detail):
    """The createRequest body for a title, from its request detail."""
    body = {'media_type': detail['media_type'], 'tmdb_id': detail['tmdb_id'], 'title': detail.get('title') or ''}
    for key in ('imdb_id', 'overview', 'poster_path', 'backdrop_path'):
        if detail.get(key):
            body[key] = detail[key]
    for key in ('year', 'tvdb_id'):
        if detail.get(key):
            body[key] = int(detail[key])
    return body


def request_summary(detail):
    """A title's request detail for the plugin: its card and the facts its
    info page shows, whether it can be requested (if not, why) and a series'
    aired seasons still to get."""
    state = detail.get('request') or {}
    seasons = [{'number': season['season_number'],
                'label': season.get('name') or 'Season %d' % season['season_number']}
               for season in detail.get('seasons') or []
               if season.get('season_number') and season.get('availability') != 'available'
               and not season.get('requested') and (season.get('episode_count') or 0) > 0]
    reason = '' if state.get('requestable') else (
        REQUEST_STATES.get(state.get('state'), '') or
        (state.get('reason') or 'not requestable').replace('_', ' ').capitalize())
    cast = [{'name': person.get('name') or '', 'role': person.get('character') or '',
             'thumbnail': TMDB_IMAGES + 'w185' + person['profile_path'] if person.get('profile_path') else ''}
            for person in (detail.get('cast') or [])[:20] if person.get('name')]
    director = detail.get('director')
    return {'title': detail.get('title') or '', 'requestable': bool(state.get('requestable')), 'reason': reason,
            'seasons': seasons, 'card': request_card(detail), 'cast': cast,
            'genres': list(detail.get('genres') or []), 'tagline': detail.get('tagline') or '',
            'status': detail.get('status') or '', 'mpaa': detail.get('content_rating') or '',
            'runtime': int(detail.get('runtime') or 0),
            'studios': list(detail.get('networks') or detail.get('production_companies') or [])[:3],
            'directors': [director] if isinstance(director, str) and director else list(director or []),
            'season_count': int(detail.get('number_of_seasons') or 0)}
