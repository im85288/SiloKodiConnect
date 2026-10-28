"""Live TV keys for the Silo Skyline skin, as TiviMate's (OK the recent
channels, Left the quick guide, Up/Down the channel above or below).

A skin can't set keys, so the add-on installs its keymap in Kodi's
userdata/keymaps while the skin is Skyline and the setting is on, and takes
its own copy away otherwise (the keys open Skyline's windows)."""
import os

import xbmc
import xbmcvfs

from . import kodi

SKIN = 'skin.silo.skyline'
NAME = 'zz_skyline_livetv.xml'
MARK = b'Silo Skyline: Live TV keys'


def wanted():
    return kodi.setting_bool('livetv_keys') and xbmc.getSkinDir() == SKIN


def sync():
    """Install, update or remove the keymap to match the skin and setting;
    Kodi reloads its keymaps when it changes. Returns what it did."""
    target = xbmcvfs.translatePath('special://profile/keymaps/' + NAME)
    source = os.path.join(xbmcvfs.translatePath(kodi.addon().getAddonInfo('path')), 'resources', 'keymaps', NAME)
    current = _read(target)
    if wanted():
        new = _read(source)
        if not new or current == new:
            return None
        xbmcvfs.mkdirs(os.path.dirname(target))
        with open(target, 'wb') as handle:
            handle.write(new)
        action = 'installed' if current is None else 'updated'
    elif current is not None and MARK in current:
        os.remove(target)
        action = 'removed'
    else:
        return None
    xbmc.executebuiltin('Action(reloadkeymaps)')
    kodi.log('Live TV keymap %s' % action)
    return action


def _read(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read()
    except OSError:
        return None
