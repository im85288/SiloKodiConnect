# -*- coding: utf-8 -*-
"""The plugin entry point: playback of library items and the add-on's menu.

The plugin runs in a short-lived process per call. Everything that needs the
Silo session goes through the service's loopback relay.
"""
import json

try:
    from urllib.parse import parse_qsl, urlencode
    from urllib.request import urlopen
except ImportError:  # pragma: no cover
    raise

import xbmc
import xbmcgui
import xbmcplugin

# Kept light: Kodi starts a fresh Python for every play and widget refresh,
# and on a 2 GB device the ~7 MB that requests costs is worth saving. Sign-in
# code (which needs requests) is imported only when used.
from . import kodi, mapping
from .mapping import RELAY_PORT as DEFAULT_PORT
from .store import Store

ADAPTIVE = 'inputstream.adaptive'


def relay_port():
    try:
        return kodi.setting_int('relay_port') or DEFAULT_PORT
    except Exception:
        return DEFAULT_PORT


def ask_service(path, timeout=75, **query):
    url = 'http://127.0.0.1:%d/%s' % (relay_port(), path)
    if query:
        url += '?' + urlencode(query)
    try:
        with urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as error:
        kodi.log_error('SiloKodiConnect service did not answer: %s' % error)
        return {'error': kodi.string(30120)}


def run(argv):
    base, handle = argv[0], int(argv[1])
    params = dict(parse_qsl(argv[2].lstrip('?'))) if len(argv) > 2 else {}
    mode = params.get('mode')
    if mode == 'play':
        return play(handle, params, len(argv) > 3 and argv[3] == 'resume:true')
    if mode == 'signin':
        return sign_in()
    if mode == 'signout':
        return sign_out()
    if mode == 'reload_skin':
        # For skin development: reload the skin's XML without restarting Kodi.
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        return xbmc.executebuiltin('ReloadSkin()')
    if mode == 'sections':
        return sections(base, handle)
    if mode == 'widget':
        return widget(handle, params.get('section') or 'continue_watching', params.get('library'))
    if mode in ('sync', 'fullsync', 'libraries', 'reset'):
        return command(mode)
    return menu(base, handle)


def play(handle, params, resume):
    answer = ask_service('play', id=params.get('id', ''), resume='1' if resume else '0')
    if answer.get('error'):
        kodi.notify(answer['error'])
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        return
    item = xbmcgui.ListItem(answer.get('title') or '', path=answer['url'], offscreen=True)
    item.setContentLookup(False)
    if answer.get('mime_type'):
        item.setMimeType(answer['mime_type'])
    if answer.get('hls') and xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % ADAPTIVE):
        item.setProperty('inputstream', ADAPTIVE)
    if answer.get('start_offset'):
        item.setProperty('StartOffset', '%.3f' % answer['start_offset'])
    if answer.get('subtitles'):
        item.setSubtitles(answer['subtitles'])
    # Silo lists its own subtitles; don't let Kodi scan a "folder" on the server.
    item.setProperty('no-ext-subs-scan', 'true')
    xbmcplugin.setResolvedUrl(handle, True, item)


def sign_in():
    from . import auth
    store = Store(kodi.profile_dir())
    if auth.sign_in(store):
        ask_service('command/signed_in', timeout=10)
        kodi.notify(kodi.string(30107) % (store.get('profile_name') or store.get('username') or ''))


def sign_out():
    if not xbmcgui.Dialog().yesno('SiloKodiConnect', kodi.string(30108)):
        return
    ask_service('command/signout', timeout=20)


def command(name):
    if name == 'reset' and not xbmcgui.Dialog().yesno('SiloKodiConnect', kodi.string(30109)):
        return
    if name == 'libraries':
        return choose_libraries()
    answer = ask_service('command/%s' % name, timeout=10)
    if answer.get('error'):
        kodi.notify(answer['error'])


def choose_libraries():
    answer = ask_service('command/list_libraries', timeout=20)
    if answer.get('error'):
        kodi.notify(answer['error'])
        return
    libraries = answer.get('libraries') or []
    if not libraries:
        kodi.notify(kodi.string(30110))
        return
    labels = ['%s (%s)' % (library['name'], library['type']) for library in libraries]
    selected = [index for index, library in enumerate(libraries) if library['synced']]
    chosen = xbmcgui.Dialog().multiselect(kodi.string(30111), labels, preselect=selected)
    if chosen is None:
        return
    excluded = [library['id'] for index, library in enumerate(libraries) if index not in chosen]
    kodi.set_setting('excluded_libraries', ','.join(excluded))
    ask_service('command/sync', timeout=10)


MOVIE_FIELDS = ['title', 'plot', 'year', 'rating', 'ratings', 'runtime', 'genre', 'mpaa', 'art', 'resume',
                'playcount', 'file', 'premiered', 'streamdetails', 'dateadded', 'lastplayed', 'tagline', 'studio',
                'cast', 'director']
EPISODE_FIELDS = ['title', 'plot', 'rating', 'ratings', 'runtime', 'art', 'resume', 'playcount', 'file',
                  'firstaired', 'showtitle', 'season', 'episode', 'tvshowid', 'streamdetails', 'dateadded',
                  'lastplayed', 'cast', 'director']
CAST_LIMIT = 20
SHOW_FIELDS = ['title', 'plot', 'year', 'rating', 'ratings', 'genre', 'mpaa', 'art', 'file', 'premiered',
               'studio', 'episode', 'watchedepisodes', 'cast']


def widget(handle, section, library=None):
    """A Silo row (a home row, Continue Watching by default, or a library's own
    row) for a skin widget, as the matching Kodi library items so art, resume
    and play behave as usual."""
    query = {'section': section}
    if library:
        query['library'] = library
    answer = ask_service('command/widget', timeout=20, **query)
    kinds = set()
    shows = {}  # an episode's show, read once per row
    for entry in answer.get('items') or []:
        item = library_item(entry['type'], entry['kodi_id'], shows)
        if item:
            url, listitem, folder = item
            kinds.add(entry['type'])
            xbmcplugin.addDirectoryItem(handle, url, listitem, isFolder=folder)
    xbmcplugin.setContent(handle, 'movies' if kinds == {'movie'} else 'episodes' if kinds == {'episode'}
                          else 'videos')
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def sections(base, handle):
    """Every row on the profile's Silo home screen, each usable as a widget."""
    answer = ask_service('command/sections', timeout=20)
    if answer.get('error'):
        kodi.notify(answer['error'])
    for section in answer.get('sections') or []:
        item = xbmcgui.ListItem(section['title'], offscreen=True)
        item.setArt({'icon': 'DefaultVideoPlaylists.png'})
        xbmcplugin.addDirectoryItem(handle, '%s?%s' % (base, urlencode({'mode': 'widget', 'section': section['id']})),
                                    item, isFolder=True)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def library_item(kind, kodi_id, shows=None):
    if kind == 'movie':
        details = (kodi.jsonrpc('VideoLibrary.GetMovieDetails', movieid=kodi_id, properties=MOVIE_FIELDS)
                   or {}).get('moviedetails')
    elif kind == 'episode':
        details = (kodi.jsonrpc('VideoLibrary.GetEpisodeDetails', episodeid=kodi_id, properties=EPISODE_FIELDS)
                   or {}).get('episodedetails')
    else:
        details = (kodi.jsonrpc('VideoLibrary.GetTVShowDetails', tvshowid=kodi_id, properties=SHOW_FIELDS)
                   or {}).get('tvshowdetails')
    if not details:
        return None
    show = None
    if kind == 'episode' and details.get('tvshowid'):
        shows = {} if shows is None else shows
        if details['tvshowid'] not in shows:
            shows[details['tvshowid']] = (kodi.jsonrpc('VideoLibrary.GetTVShowDetails', tvshowid=details['tvshowid'],
                                                       properties=['mpaa', 'genre']) or {}).get('tvshowdetails')
        show = shows[details['tvshowid']]
    listitem = xbmcgui.ListItem(details.get('title') or '', offscreen=True)
    # What the Silo Skyline skin's hero shows about the title.
    listitem.setProperties(mapping.skyline_properties(kind, details, show))
    listitem.setArt(details.get('art') or {})
    tag = listitem.getVideoInfoTag()
    tag.setDbId(kodi_id)
    tag.setMediaType(kind)
    tag.setTitle(details.get('title') or '')
    tag.setPlot(details.get('plot') or '')
    if details.get('rating'):
        tag.setRating(float(details['rating']))
    if details.get('playcount'):
        tag.setPlaycount(int(details['playcount']))
    # Skins' info lines use these for shows too.
    if details.get('year'):
        tag.setYear(int(details['year']))
    if details.get('mpaa'):
        tag.setMpaa(details['mpaa'])
    if details.get('genre'):
        tag.setGenres(details['genre'])
    # The info page's cast list and credit line.
    if details.get('cast'):
        tag.setCast([xbmc.Actor(person.get('name') or '', person.get('role') or '', person.get('order') or 0,
                                person.get('thumbnail') or '') for person in details['cast'][:CAST_LIMIT]])
    if details.get('director'):
        tag.setDirectors(details['director'])
    if details.get('studio'):
        tag.setStudios(details['studio'])
    if kind == 'tvshow':
        listitem.setProperty('TotalEpisodes', str(details.get('episode') or 0))
        listitem.setProperty('WatchedEpisodes', str(details.get('watchedepisodes') or 0))
        return 'videodb://tvshows/titles/%d/' % kodi_id, listitem, True
    if details.get('runtime'):
        tag.setDuration(int(details['runtime']))
    resume = details.get('resume') or {}
    if resume.get('position'):
        tag.setResumePoint(float(resume['position']), float(resume.get('total') or 0))
    if kind == 'episode':
        tag.setTvShowTitle(details.get('showtitle') or '')
        tag.setSeason(int(details.get('season') or 0))
        tag.setEpisode(int(details.get('episode') or 0))
        tag.setFirstAired(details.get('firstaired') or '')
    listitem.setProperty('IsPlayable', 'true')
    return details['file'], listitem, False


def menu(base, handle):
    store = Store(kodi.profile_dir())
    entries = []
    if store.signed_in:
        who = store.get('profile_name') or store.get('username') or ''
        entries.append((kodi.string(30130) % (who, store.get('server_url')), None))
        entries.append((kodi.string(30137), 'widget'))
        entries.append((kodi.string(30138), 'sections'))
        entries.append((kodi.string(30131), 'sync'))
        entries.append((kodi.string(30132), 'libraries'))
        entries.append((kodi.string(30133), 'fullsync'))
        entries.append((kodi.string(30134), 'signout'))
        entries.append((kodi.string(30135), 'reset'))
    else:
        entries.append((kodi.string(30136), 'signin'))
    for label, mode in entries:
        item = xbmcgui.ListItem(label, offscreen=True)
        url = '%s?mode=%s' % (base, mode) if mode else ''
        xbmcplugin.addDirectoryItem(handle, url, item, isFolder=mode in ('widget', 'sections'))
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
