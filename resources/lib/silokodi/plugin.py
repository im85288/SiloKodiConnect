# -*- coding: utf-8 -*-
"""The plugin entry point: playback of library items and the add-on's menu.

The plugin runs in a short-lived process per call. Everything that needs the
Silo session goes through the service's loopback relay.
"""
import json
import time

try:
    from urllib.parse import parse_qsl, unquote, urlencode
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
from .library import CAST_LIMIT, details_of
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
    if mode == 'profile':
        return switch_profile()
    if mode == 'reload_skin':
        # For skin development: reload the skin's XML without restarting Kodi.
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        return xbmc.executebuiltin('ReloadSkin()')
    if mode == 'previous':
        return play_previous(params.get('dbid'))
    if mode == 'show':
        return show_of(params.get('episode'))
    if mode == 'more':
        return more_actions(params.get('type'), params.get('dbid'))
    if mode == 'info':
        return info(params.get('type'), params.get('dbid'))
    if mode == 'watchlist':
        return toggle_watchlist(params.get('id') or '', params.get('add') == '1')
    if mode == 'sections':
        return sections(base, handle, params.get('type'))
    if mode == 'widget':
        return widget(handle, params.get('section') or 'continue_watching', params.get('library'),
                      params.get('collection'), params.get('title'), raw_tail(argv, 'exclude_show'))
    if mode == 'requests':
        # The skin can't URL-encode the search words, so take them as written
        # (they may hold & or =), up to the refresh suffix.
        query = argv[2] if len(argv) > 2 else ''
        words = unquote(query.split('search=', 1)[1].rsplit('&refresh=', 1)[0]) if 'search=' in query else ''
        row = 'search:' + words if words else params.get('row') or 'mine'
        return request_row(base, handle, row)
    if mode == 'request_info':
        return request_info(base, params.get('type') or 'movie', params.get('tmdb') or '')
    if mode == 'request':
        return make_request(params.get('type') or 'movie', params.get('tmdb') or '')
    if mode == 'collections':
        return collections(base, handle, params.get('libraries') or '', int(params.get('top') or 0))
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


def switch_profile():
    """Watch as another of the account's Silo profiles: the service then
    brings Home's rows, Requests and watch states in line with it."""
    from . import auth
    from .api import ApiError, SiloClient
    store = Store(kodi.profile_dir())
    client = SiloClient(store, kodi.version(), xbmc.getInfoLabel('System.FriendlyName'))
    try:
        switched = auth.choose_profile(store, client, switching=True)
    except ApiError as error:
        kodi.notify(str(error))
        return
    if switched:
        ask_service('command/profile_switched', timeout=10)
        kodi.notify(kodi.string(30159) % (store.get('profile_name') or ''))
        xbmc.executebuiltin('Container.Refresh')


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


def raw_tail(argv, key):
    """A parameter a skin can't URL-encode (a title, search words): taken as
    written, from key= to the end, so it must come last in the path."""
    query = argv[2] if len(argv) > 2 else ''
    marker = '&%s=' % key
    if marker not in query and not query.lstrip('?').startswith(key + '='):
        return ''
    return unquote(query.split(key + '=', 1)[1])


def widget(handle, section, library=None, collection=None, title=None, exclude_show=''):
    """A Silo row (a home row, Continue Watching by default, a library's own
    row, or one of its collections) for a skin widget, as the matching Kodi
    library items so art, resume and play behave as usual. exclude_show
    leaves out a show's episodes (Up Next's row skips the show it offers)."""
    query = {'section': section}
    if library:
        query['library'] = library
    if collection:
        query['collection'] = collection
    if title:
        xbmcplugin.setPluginCategory(handle, title)
    started = time.time()
    answer = ask_service('command/widget', timeout=20, **query)
    asked = time.time()
    if answer.get('title'):
        # Names the folder when the row is browsed rather than used as a widget.
        xbmcplugin.setPluginCategory(handle, answer['title'])
    kinds = set()
    shows = {}  # an episode's show, read once per row
    for entry in answer.get('items') or []:
        item = library_item(entry['type'], entry['kodi_id'], shows, entry.get('cached'))
        if item:
            url, listitem, folder = item
            if exclude_show and listitem.getVideoInfoTag().getTVShowTitle() == exclude_show:
                continue
            listitem.setProperty('silo.tint', entry.get('tint') or '')
            kinds.add(entry['type'])
            xbmcplugin.addDirectoryItem(handle, url, listitem, isFolder=folder)
    # A skin draws a folder by its content: posters for films and shows.
    xbmcplugin.setContent(handle, {frozenset(['movie']): 'movies', frozenset(['episode']): 'episodes',
                                   frozenset(['tvshow']): 'tvshows'}.get(frozenset(kinds), 'videos'))
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    # How long a row takes, and where: the service (Silo) or Kodi's library.
    kodi.log('Row %s%s: %d items, %.2f s from the service, %.2f s from Kodi\'s library (%.2f s in JSON-RPC)'
             % (section, '/' + library if library else '', len(answer.get('items') or []), asked - started,
                time.time() - asked, kodi.JSONRPC_SECONDS[0]))


def request_row(base, handle, row):
    """A Requests row: titles in the library as their Kodi items, the rest
    as cards a Silo-style skin can request from (silo.request.* properties)."""
    answer = ask_service('command/requests', timeout=30, row=row)
    if answer.get('error'):
        kodi.notify(answer['error'])
    for card in answer.get('cards') or []:
        if card.get('kodi_id'):
            item = library_item(card['kind'], card['kodi_id'])
            if item:
                item[1].setProperty('silo.request.label', card.get('label') or '')
                item[1].setProperty('silo.request.tint', card.get('tint') or '')
                xbmcplugin.addDirectoryItem(handle, item[0], item[1], isFolder=item[2])
            continue
        url = '%s?%s' % (base, urlencode({'mode': 'request', 'type': card['type'], 'tmdb': card['tmdb']}))
        xbmcplugin.addDirectoryItem(handle, url, request_listitem(card), isFolder=False)
    xbmcplugin.setContent(handle, 'videos')
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def request_listitem(card):
    """A title Silo can request as a Kodi item: TMDB art and info, and the
    silo.request.* properties a Silo-style skin draws and requests from."""
    listitem = xbmcgui.ListItem(card['title'], offscreen=True)
    listitem.setArt({'poster': card['poster'], 'thumb': card['poster'], 'fanart': card['backdrop']})
    tag = listitem.getVideoInfoTag()
    tag.setTitle(card['title'])
    tag.setPlot(card['overview'])
    if card.get('year'):
        tag.setYear(int(card['year']))
    if card.get('rating'):
        tag.setRating(float(card['rating']))
    listitem.setProperties({'silo.request.type': card['type'], 'silo.request.tmdb': str(card['tmdb']),
                            'silo.request.label': card['label'], 'silo.request.state': card['state'],
                            'silo.request.tint': card.get('tint') or '',
                            'silo.request.requestable': 'true' if card['requestable'] else 'false',
                            'silo.rating': 'TMDB %.1f' % card['rating'] if card.get('rating') else ''})
    return listitem


def request_info(base, media_type, tmdb_id):
    """Open a requestable title's info page with Silo's full details: cast,
    genres, network or studio, and its recommendations as More Like This."""
    summary = ask_service('command/request_detail', timeout=30, type=media_type, tmdb=tmdb_id)
    if summary.get('error') or not summary.get('card'):
        return kodi.notify(summary.get('error') or 'Not found on Silo')
    listitem = request_listitem(summary['card'])
    tag = listitem.getVideoInfoTag()
    tag.setMediaType('tvshow' if media_type == 'series' else 'movie')
    tag.setGenres(summary.get('genres') or [])
    tag.setStudios(summary.get('studios') or [])
    tag.setDirectors(summary.get('directors') or [])
    tag.setTagLine(summary.get('tagline') or '')
    tag.setTvShowStatus(summary.get('status') or '')
    tag.setMpaa(summary.get('mpaa') or '')
    if summary.get('runtime'):
        tag.setDuration(summary['runtime'] * 60)
    tag.setCast([xbmc.Actor(person['name'], person['role'], index, person['thumbnail'])
                 for index, person in enumerate(summary.get('cast') or [])])
    if summary.get('season_count'):
        listitem.setProperty('silo.request.seasons', str(summary['season_count']))
    similar = urlencode({'mode': 'requests', 'row': 'similar:%s:%s' % (media_type, tmdb_id)})
    listitem.setProperty('silo.similar', '%s?%s' % (base, similar))
    xbmcgui.Dialog().info(listitem)


def make_request(media_type, tmdb_id):
    """Ask, then request a title from Silo: for a series with several seasons
    still to get, choose which. The skin's rows reload after."""
    summary = ask_service('command/request_detail', timeout=30, type=media_type, tmdb=tmdb_id)
    if summary.get('error') or not summary.get('requestable'):
        return kodi.notify(summary.get('error') or summary.get('reason') or 'Not available to request')
    title, seasons = summary.get('title') or '', summary.get('seasons') or []
    chosen = []
    if media_type == 'series' and len(seasons) > 1:
        picked = xbmcgui.Dialog().multiselect('Request %s: which seasons?' % title,
                                              [season['label'] for season in seasons],
                                              preselect=list(range(len(seasons))))
        if not picked:
            return
        chosen = [seasons[index]['number'] for index in picked]
    elif not xbmcgui.Dialog().yesno('Silo', 'Request %s?' % title):
        return
    answer = ask_service('command/request', timeout=30, type=media_type, tmdb=tmdb_id,
                         seasons=','.join(str(number) for number in chosen))
    if answer.get('error'):
        return kodi.notify(answer['error'])
    kodi.notify('%s: %s' % (answer.get('title') or title, answer.get('state') or 'Requested'))
    kodi.set_property('library_updated', str(int(time.time())))


def collections(base, handle, library_ids, top=0):
    """Silo's Collections section for a page's libraries: each collection a
    folder with its poster and count, opening its titles. top: only the
    biggest few, then a "See all" folder (a page's Collections row)."""
    answer = ask_service('command/collections', timeout=20, libraries=library_ids)
    if answer.get('error'):
        kodi.notify(answer['error'])
    xbmcplugin.setPluginCategory(handle, 'Collections')
    cards = answer.get('collections') or []
    if top:
        cards = sorted(cards, key=lambda card: -(card.get('count') or 0))[:top]
    for card in cards:
        item = xbmcgui.ListItem(card['title'], offscreen=True)
        # A property, not label2: Kodi overwrites a folder's label2 with sort details.
        item.setProperty('silo.count', str(card.get('count') or ''))
        art = {'poster': card['poster'], 'thumb': card['poster']} if card.get('poster') else {}
        if card.get('backdrop'):
            art['fanart'] = card['backdrop']  # its first title's: a skin's hero behind the card
        item.setArt(art)
        # Its first titles' posters, for a Silo-style skin's fanned card.
        for number, url in enumerate(card.get('fan') or [], 1):
            item.setProperty('silo.fan%d' % number, url)
        item.getVideoInfoTag().setPlot('%d titles' % (card.get('count') or 0))
        query = {'mode': 'widget', 'section': 'collection', 'library': card['library'], 'collection': card['id'],
                 'title': card['title']}
        xbmcplugin.addDirectoryItem(handle, '%s?%s' % (base, urlencode(query)), item, isFolder=True)
    if top and len(answer.get('collections') or []) > len(cards):
        more = xbmcgui.ListItem(kodi.string(30165), offscreen=True)
        more.setProperty('silo.see_all', 'true')
        xbmcplugin.addDirectoryItem(handle, '%s?%s' % (base, urlencode({'mode': 'collections', 'libraries': library_ids})),
                                    more, isFolder=True)
    xbmcplugin.setContent(handle, 'sets')
    # A Silo-style skin shows this folder as fanned cards (Container.Property(silo.fans)).
    xbmcplugin.setProperty(handle, 'silo.fans', 'true')
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def sections(base, handle, kind=None):
    """Every row on the profile's Silo home screen, each usable as a widget;
    with kind (such as collection), only rows of that type."""
    answer = ask_service('command/sections', timeout=20)
    if answer.get('error'):
        kodi.notify(answer['error'])
    if kind == 'collection':
        xbmcplugin.setPluginCategory(handle, 'Collections')
    for section in answer.get('sections') or []:
        if kind and section.get('type') != kind:
            continue
        item = xbmcgui.ListItem(section['title'], offscreen=True)
        item.setArt({'icon': 'DefaultVideoPlaylists.png'})
        xbmcplugin.addDirectoryItem(handle, '%s?%s' % (base, urlencode({'mode': 'widget', 'section': section['id']})),
                                    item, isFolder=True)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def play_previous(dbid):
    """Play the episode before this one (an info page's previous button)."""
    if not str(dbid or '').isdigit():
        return
    current = (kodi.jsonrpc('VideoLibrary.GetEpisodeDetails', episodeid=int(dbid),
                            properties=['tvshowid', 'season', 'episode']) or {}).get('episodedetails') or {}
    episodes = (kodi.jsonrpc('VideoLibrary.GetEpisodes', tvshowid=current.get('tvshowid'),
                             properties=['season', 'episode']) or {}).get('episodes') or []
    key = (current.get('season'), current.get('episode'))
    earlier = [e for e in episodes if (e['season'], e['episode']) < key and (e['season'] or not key[0])]
    if not earlier:
        return kodi.notify('This is the first episode')
    previous = max(earlier, key=lambda e: (e['season'], e['episode']))
    xbmc.executebuiltin('Dialog.Close(movieinformation)')
    kodi.jsonrpc('Player.Open', item={'episodeid': previous['episodeid']}, options={'resume': False})


def show_of(episode_id):
    """An episode page's Show pill: its series' page. The skin can't read
    the series' id from the episode, so it's looked up here."""
    if not str(episode_id or '').isdigit():
        return
    details = (kodi.jsonrpc('VideoLibrary.GetEpisodeDetails', episodeid=int(episode_id),
                            properties=['tvshowid']) or {}).get('episodedetails') or {}
    if details.get('tvshowid'):
        info('tvshow', details['tvshowid'])


def more_actions(kind, dbid):
    """An info page's More button: Silo's actions Kodi can do for a title."""
    if kind not in ('movie', 'episode', 'tvshow') or not str(dbid or '').isdigit():
        return
    dbid = int(dbid)
    method, key, result = {'movie': ('Movie', 'movieid', 'moviedetails'),
                           'episode': ('Episode', 'episodeid', 'episodedetails'),
                           'tvshow': ('TVShow', 'tvshowid', 'tvshowdetails')}[kind]
    fields = ['playcount'] + (['resume'] if kind != 'tvshow' else ['watchedepisodes', 'episode'])
    details = (kodi.jsonrpc('VideoLibrary.Get%sDetails' % method, properties=fields, **{key: dbid}) or {}).get(result) or {}
    watched = (details.get('watchedepisodes') == details.get('episode')) if kind == 'tvshow' else bool(details.get('playcount'))
    choices = [('unwatched' if watched else 'watched', 'Mark as unwatched' if watched else 'Mark as watched')]
    if (details.get('resume') or {}).get('position'):
        choices.append(('start', 'Play from the beginning'))
    # The small context menu, as Silo's More popover (not a big list). Kodi
    # can't place it at a button inside the page's button row, so the skin
    # moves it under More while silo.popover says how many rows it has.
    home = xbmcgui.Window(10000)
    home.setProperty('silo.popover', str(len(choices)))
    try:
        picked = xbmcgui.Dialog().contextmenu([label for _, label in choices])
    finally:
        home.clearProperty('silo.popover')
    if picked < 0:
        return
    action = choices[picked][0]
    if action == 'start':
        xbmc.executebuiltin('Dialog.Close(movieinformation)')
        return kodi.jsonrpc('Player.Open', item={key: dbid}, options={'resume': False})
    count = 0 if action == 'unwatched' else 1
    if kind == 'tvshow':
        for episode in (kodi.jsonrpc('VideoLibrary.GetEpisodes', tvshowid=dbid) or {}).get('episodes') or []:
            kodi.jsonrpc('VideoLibrary.SetEpisodeDetails', episodeid=episode['episodeid'], playcount=count)
    else:
        kodi.jsonrpc('VideoLibrary.Set%sDetails' % method, playcount=count,
                     resume={'position': 0, 'total': 0}, **{key: dbid})
    kodi.notify('Marked as %s' % action)


def info(kind, dbid):
    """Open a library title's info page, with the details a Silo-style skin
    shows. A skin's info page can't open another one itself (its More Like
    This rail closes the page and asks for the next one here)."""
    if kind not in ('movie', 'tvshow', 'episode') or not str(dbid or '').isdigit():
        return
    item = library_item(kind, int(dbid))
    if item:
        url, listitem, _ = item
        listitem.setPath(url)
        content_id = mapping.content_id_from_path(url)
        if content_id:
            extra = ask_service('command/tint', timeout=5, id=content_id)
            listitem.setProperty('silo.tint', extra.get('tint') or '')
            # A Silo-style skin's Trailer button plays this through the YouTube
            # add-on (or offers to install it); its Watchlist button toggles.
            listitem.setProperty('silo.trailer', extra.get('trailer') or '')
            listitem.setProperty('silo.content_id', content_id)
            listitem.setProperty('silo.watchlist', extra.get('watchlist') or '')
            # A toggle on an earlier page doesn't carry over to this one.
            xbmcgui.Window(12003).clearProperty('silo.watchlist.for')
        xbmcgui.Dialog().info(listitem)


def toggle_watchlist(content_id, add):
    """Add a film or series to the profile's watchlist, or take it off, and
    tell the open info page (Window(movieinformation) properties)."""
    answer = ask_service('command/watchlist', timeout=10, id=content_id, add='1' if add else '0')
    if answer.get('error'):
        kodi.notify(answer['error'])
        return
    page = xbmcgui.Window(12003)
    page.setProperty('silo.watchlist.now', answer.get('watchlist') or '')
    page.setProperty('silo.watchlist.for', content_id)
    kodi.notify(kodi.string(30163 if add else 30164))


def library_item(kind, kodi_id, shows=None, cached=None):
    """A Kodi library title as a ListItem for a skin. cached is the service's
    {'details', 'show'} for it, which saves the library lookups (about 60 ms a
    title on a SHIELD); without it they're made here."""
    if cached and cached.get('details'):
        details, show = cached['details'], cached.get('show')
    else:
        details, show = details_of(kind, kodi_id, shows)
    if not details:
        return None
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
    # (label, mode, description, icon): a Silo-style skin draws the icon
    # (silo.menu.icon names one of its own) and the description under the label.
    if store.signed_in:
        who = store.get('profile_name') or store.get('username') or ''
        entries.append((kodi.string(30130) % (who, store.get('server_url')), 'profile', kodi.string(30158), 'profile'))
        entries.append((kodi.string(30137), 'widget', kodi.string(30150), 'play'))
        entries.append((kodi.string(30138), 'sections', kodi.string(30151), 'rows'))
        entries.append((kodi.string(30131), 'sync', kodi.string(30152), 'sync'))
        entries.append((kodi.string(30132), 'libraries', kodi.string(30153), 'libraries'))
        entries.append((kodi.string(30133), 'fullsync', kodi.string(30154), 'rewrite'))
        entries.append((kodi.string(30134), 'signout', kodi.string(30155), 'signout'))
        entries.append((kodi.string(30135), 'reset', kodi.string(30156), 'remove'))
    else:
        entries.append((kodi.string(30136), 'signin', kodi.string(30157), 'profile'))
    for label, mode, description, icon in entries:
        item = xbmcgui.ListItem(label, offscreen=True)
        item.setProperty('silo.menu.icon', icon)
        item.setProperty('silo.menu.description', description)
        item.getVideoInfoTag().setPlot(description)
        url = '%s?mode=%s' % (base, mode) if mode else ''
        xbmcplugin.addDirectoryItem(handle, url, item, isFolder=mode in ('widget', 'sections'))
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
