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


def youtube_search():
    """Skyline's YouTube page's search box: the keyboard, then the page shows
    the YouTube add-on's results for it (Window(Home) silo.youtube.query, the
    words, and silo.youtube.search, their list's path). Kodi's own <content>
    can't URL-encode what's typed, so it's done here."""
    from urllib.parse import quote_plus
    home = xbmcgui.Window(10000)
    keyboard = xbmc.Keyboard(home.getProperty('silo.youtube.query'), kodi.string(30216))
    keyboard.doModal()
    words = keyboard.getText().strip() if keyboard.isConfirmed() else ''
    if not words:
        return
    home.setProperty('silo.youtube.query', words)
    home.setProperty('silo.youtube.search', YOUTUBE_SEARCH % quote_plus(words))
    home.setProperty('silo.youtube.section', 'search')
    xbmc.executebuiltin('SetFocus(610)')  # the results, from the side menu


def youtube_back():
    """Back on Skyline's YouTube page (zz_skyline_home.xml): a search's
    results close, and the page's Home shows again (its first row focused,
    as the app's); otherwise Back as usual. A keymap has no conditions, and
    the page's own controls never see this Back, so it's done here."""
    home = xbmcgui.Window(10000)
    if xbmc.getCondVisibility('Control.HasFocus(100)'):
        # The page's side menu: closed, back to its section (the menu's Right).
        xbmc.executebuiltin('Action(Right)')
        return
    if not home.getProperty('silo.youtube.query'):
        xbmc.executebuiltin('Action(Back)')
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
