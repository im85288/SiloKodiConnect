"""Keys for the Silo Skyline skin: Live TV's, as TiviMate's (OK the recent
channels, Left the quick guide, Up/Down the channel above or below), and the
player's (Back on the pause screen only dismisses it).

A skin can't set keys, so the add-on installs each keymap in Kodi's
userdata/keymaps while the skin is Skyline (Live TV's also needs its setting),
and takes its own copy away otherwise (the keys open Skyline's windows)."""
import os

import xbmc
import xbmcvfs

from . import kodi

SKIN = 'skin.silo.skyline'
NAME = 'zz_skyline_livetv.xml'
MARK = b'Silo Skyline: Live TV keys'
PLAYER_NAME = 'zz_skyline_player.xml'
PLAYER_MARK = b'Silo Skyline: player keys'
# (file, the mark that makes it ours, whether it's wanted with Skyline)
KEYMAPS = ((NAME, MARK, lambda: kodi.setting_bool('livetv_keys')),
           (PLAYER_NAME, PLAYER_MARK, lambda: True))


def sync():
    """Install, update or remove each keymap to match the skin and setting;
    Kodi reloads its keymaps when one changes. Returns {file: what it did}
    for those that changed."""
    done = {}
    for name, mark, setting in KEYMAPS:
        action = _sync(name, mark, setting() and xbmc.getSkinDir() == SKIN)
        if action:
            done[name] = action
            kodi.log('Keymap %s %s' % (name, action))
    if done:
        xbmc.executebuiltin('Action(reloadkeymaps)')
    return done


def _sync(name, mark, wanted):
    target = xbmcvfs.translatePath('special://profile/keymaps/' + name)
    source = os.path.join(xbmcvfs.translatePath(kodi.addon().getAddonInfo('path')), 'resources', 'keymaps', name)
    current = _read(target)
    if wanted:
        new = _read(source)
        if not new or current == new:
            return None
        xbmcvfs.mkdirs(os.path.dirname(target))
        with open(target, 'wb') as handle:
            handle.write(new)
        return 'installed' if current is None else 'updated'
    if current is not None and mark in current:
        os.remove(target)
        return 'removed'
    return None


def _read(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read()
    except OSError:
        return None
