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
    skin's, not a library item, so Kodi has no menu for it: one in Continue
    Watching or Next Up gets Silo's; anything else Kodi's."""
    if not xbmc.getInfoLabel('ListItem.Property(home.dismiss)'):
        xbmc.executebuiltin('Action(ContextMenu)')
        return
    if xbmcgui.Dialog().contextmenu([kodi.string(30180)]) == 0:
        _dismiss(xbmc.getInfoLabel('ListItem.Property(home.type)'), xbmc.getInfoLabel('ListItem.Property(home.dbid)'))


def _dismiss(kind, dbid):
    answer = ask_service('command/dismiss', timeout=20, type=kind, dbid=dbid)
    if answer.get('error'):
        kodi.notify(kodi.string(30181) % answer['error'])
        return False
    return True
