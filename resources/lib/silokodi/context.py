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
