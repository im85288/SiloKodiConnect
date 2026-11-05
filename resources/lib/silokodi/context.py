# -*- coding: utf-8 -*-
"""Context menu items."""
import xbmc
import xbmcgui

from . import kodi
from .plugin import ask_service


def dismiss(listitem):
    """The context menu's item on a plugin row's card (a library item)."""
    tag = listitem.getVideoInfoTag()
    if _dismiss(tag.getMediaType(), str(tag.getDbId())):
        xbmc.executebuiltin('Container.Refresh')


def home_menu():
    """Home's context menu key. A card of Home's published rows is the
    skin's, not a library item, so Kodi has no menu for it: a library
    title's gets Silo's card menu; anything else Kodi's."""
    kind = xbmc.getInfoLabel('ListItem.Property(home.type)')
    dbid = xbmc.getInfoLabel('ListItem.Property(home.dbid)')
    if kind not in ('movie', 'tvshow', 'episode') or not dbid.isdigit():
        xbmc.executebuiltin('Action(ContextMenu)')
        return
    card_menu(kind, dbid, xbmc.getInfoLabel('ListItem.Property(home.playcount)') not in ('', '0'),
              bool(xbmc.getInfoLabel('ListItem.Property(home.dismiss)')))


YOUTUBE_PLAY = 'plugin://plugin.video.youtube/play/'


def youtube_menu():
    """Skyline's YouTube page's context menu key (zz_skyline_home.xml): a video
    plays as OK plays it, or after the YouTube add-on's list of its qualities,
    as the YouTube app's menu offers; anything else gets Kodi's own menu."""
    path = xbmc.getInfoLabel('ListItem.FileNameAndPath')
    if not path.startswith(YOUTUBE_PLAY):
        xbmc.executebuiltin('Action(ContextMenu)')
        return
    choice = xbmcgui.Dialog().contextmenu([kodi.string(30214), kodi.string(30215)])
    if choice == 0:
        xbmc.executebuiltin('PlayMedia(%s)' % path)
    elif choice == 1:
        xbmc.executebuiltin('PlayMedia(%s%sask_for_quality=true)' % (path, '&' if '?' in path else '?'))


YOUTUBE_SEARCH = ('plugin://plugin.video.youtube/kodion/search/query/'
                  '?q=%s&hide_folders=true&hide_next_page=true')


YOUTUBE_HISTORY = 620  # Skyline's YouTube page: Search's history list
YOUTUBE_RESULTS = 610  # and its results grid
YOUTUBE_ROW_PARAMS = 'hide_folders=true&hide_next_page=true'


def youtube_search():
    """Skyline's YouTube page's New search: the keyboard, then the page shows
    the YouTube add-on's results for it (Window(Home) silo.youtube.query, the
    words, and silo.youtube.search, their list's path). Kodi's own <content>
    can't URL-encode what's typed, so it's done here."""
    from urllib.parse import quote_plus
    keyboard = xbmc.Keyboard('', kodi.string(30216))
    keyboard.doModal()
    words = keyboard.getText().strip() if keyboard.isConfirmed() else ''
    if not words:
        return
    _youtube_show_search(words, YOUTUBE_SEARCH % quote_plus(words))


def youtube_pick():
    """OK on Search's history list (the YouTube add-on's own): its New search
    opens the keyboard; a past search shows its results."""
    path = xbmc.getInfoLabel('Container(%d).ListItem.FolderPath' % YOUTUBE_HISTORY)
    if '/search/query' not in path:
        youtube_search()
        return
    words = xbmc.getInfoLabel('Container(%d).ListItem.Label' % YOUTUBE_HISTORY)
    _youtube_show_search(words, '%s%s%s' % (path, '&' if '?' in path else '?', YOUTUBE_ROW_PARAMS))


def _youtube_show_search(words, path):
    import time
    home = xbmcgui.Window(10000)
    home.setProperty('silo.youtube.query', words)
    home.setProperty('silo.youtube.search', path)
    home.setProperty('silo.youtube.section', 'search')
    youtube_focus('search', [YOUTUBE_RESULTS])
    # The add-on saves a search in its history as it runs it (the results
    # have loaded by now): a new token changes the history list's path, so
    # Kodi reloads it.
    home.setProperty('silo.youtube.history', str(int(time.time())))


YOUTUBE_FOCUS_WAIT = 15  # seconds the YouTube add-on gets to fill a row
YOUTUBE_EMPTY_GRACE = 2  # seconds before an empty row counts as empty, not yet loading


def youtube_focus(section, ids, controls=()):
    """Focus the first of a section's rows (Skyline's YouTube page, control
    ids in order) once the YouTube add-on has filled it, or a plain control
    (controls) once it shows. Kodi can't focus an empty list or a control
    not showing yet, and a SetFocus it can't do drops focus altogether, so
    the page leaves it to this; a row that loads empty is passed over. Gives
    up when the page or its section changes, or the user has moved on into
    the section."""
    home = xbmcgui.Window(10000)
    monitor = xbmc.Monitor()
    for tick in range(YOUTUBE_FOCUS_WAIT * 10):
        if monitor.abortRequested():
            return
        # Home's section is empty as the page opens.
        if ((home.getProperty('silo.youtube.section') or 'home') != section
                or not xbmc.getCondVisibility('Window.IsActive(1198)')
                or any(xbmc.getCondVisibility('Control.HasFocus(%d)' % i) for i in list(ids) + list(controls))):
            return
        for control in controls:
            if xbmc.getCondVisibility('Control.IsVisible(%d)' % control):
                xbmc.executebuiltin('SetFocus(%d)' % control)
                return
        for list_id in ids:
            if xbmc.getCondVisibility('Integer.IsGreater(Container(%d).NumItems,0)' % list_id):
                xbmc.executebuiltin('SetFocus(%d)' % list_id)
                return
            if (tick < YOUTUBE_EMPTY_GRACE * 10
                    or xbmc.getCondVisibility('Container(%d).IsUpdating' % list_id)):
                break  # this row may still come; the rows after it wait
        monitor.waitForAbort(0.1)


def youtube_back():
    """Back on Skyline's YouTube page (zz_skyline_home.xml): from Search's
    results to its history, and from the history to the page's Home (its
    first row focused, as the app's); otherwise Back as usual. A keymap has
    no conditions, and the page's own controls never see this Back, so it's
    done here."""
    home = xbmcgui.Window(10000)
    if xbmc.getCondVisibility('Control.HasFocus(100)'):
        # The page's side menu: closed, back to its section (the menu's Right).
        xbmc.executebuiltin('Action(Right)')
        return
    if home.getProperty('silo.youtube.section') != 'search':
        xbmc.executebuiltin('Action(Back)')
        return
    if xbmc.getCondVisibility('Control.HasFocus(%d)' % YOUTUBE_RESULTS):
        xbmc.executebuiltin('SetFocus(%d)' % YOUTUBE_HISTORY)
        return
    for key in ('search', 'section', 'query'):
        home.clearProperty('silo.youtube.' + key)
    xbmc.executebuiltin('SetFocus(301)')


def library_menu(listitem):
    """Kodi's context menu's "Silo" on a library title (Browse, a page's rails)."""
    tag = listitem.getVideoInfoTag()
    card_menu(tag.getMediaType(), str(tag.getDbId()), tag.getPlayCount() > 0,
              bool(listitem.getProperty('silo.dismiss')))


def card_menu(kind, dbid, watched, dismissable):
    """Netflix's card menu, with Silo's lists: Remove from Continue Watching
    (a card there), the watchlist, a rating, watched or not, and Not
    interested (Silo's recommendations then leave the title out). An
    episode's card acts on its show, but for Mark watched."""
    title = ask_service('command/card', timeout=20, type=kind, dbid=dbid)
    if title.get('error'):
        kodi.notify(title['error'])
        return
    content_id, plugin = title['content_id'], 'plugin://plugin.video.silokodiconnect/'
    entries = []
    if dismissable:
        entries.append((kodi.string(30180), lambda: _dismiss(kind, dbid)))
    entries.append((kodi.string(30203 if title.get('watchlist') else 30202),
                    lambda: ask_service('command/watchlist', timeout=20, id=content_id,
                                        add='0' if title.get('watchlist') else '1')))
    entries.append((kodi.string(30204), lambda: xbmc.executebuiltin(
        'RunPlugin(%s?mode=rate&id=%s&current=%s)' % (plugin, content_id, title.get('rating') or 0))))
    entries.append((kodi.string(30206 if watched else 30205), lambda: xbmc.executebuiltin(
        'RunPlugin(%s?mode=mark&type=%s&dbid=%s&action=%s)' % (plugin, kind, dbid,
                                                                'unwatched' if watched else 'watched'))))
    marked = title.get('not_interested')
    entries.append((kodi.string(30208 if marked else 30207), lambda: _not_interested(content_id, not marked)))
    chosen = xbmcgui.Dialog().contextmenu([label for label, _ in entries])
    if chosen >= 0:
        entries[chosen][1]()


def _not_interested(content_id, marked):
    answer = ask_service('command/not_interested', timeout=20, id=content_id, on='1' if marked else '0')
    if answer.get('error'):
        kodi.notify(answer['error'])
    elif marked:
        kodi.notify(kodi.string(30209))


def _dismiss(kind, dbid):
    answer = ask_service('command/dismiss', timeout=20, type=kind, dbid=dbid)
    if answer.get('error'):
        kodi.notify(kodi.string(30181) % answer['error'])
        return False
    return True
