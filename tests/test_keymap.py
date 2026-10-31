# -*- coding: utf-8 -*-
import os

import pytest

from conftest import ROOT
from resources.lib.silokodi import keymap


@pytest.fixture
def kodi_home(tmp_path, monkeypatch):
    """Kodi's userdata in tmp_path, the Skyline skin, the setting on."""
    state = {'skin': keymap.SKIN, 'on': True}
    monkeypatch.setattr(keymap.xbmcvfs, 'translatePath',
                        lambda path: path.replace('special://profile/', str(tmp_path) + '/'))
    monkeypatch.setattr(keymap.xbmcvfs, 'mkdirs', lambda path: os.makedirs(path, exist_ok=True))
    monkeypatch.setattr(keymap.xbmc, 'getSkinDir', lambda: state['skin'], raising=False)
    monkeypatch.setattr(keymap.kodi, 'setting_bool', lambda key: state['on'])
    monkeypatch.setattr(keymap.kodi, 'addon', lambda: type('A', (), {'getAddonInfo': lambda self, key: ROOT})())
    return tmp_path / 'keymaps' / keymap.NAME, state


def test_the_keymaps_follow_the_skin_and_setting(kodi_home):
    target, state = kodi_home
    player = target.parent / keymap.PLAYER_NAME
    assert keymap.sync() == {keymap.NAME: 'installed', keymap.PLAYER_NAME: 'installed'}
    assert keymap.MARK in target.read_bytes() and keymap.PLAYER_MARK in player.read_bytes()
    assert keymap.sync() == {}                        # already there
    state['skin'] = 'skin.estuary'
    assert keymap.sync() == {keymap.NAME: 'removed', keymap.PLAYER_NAME: 'removed'}
    assert not target.exists() and not player.exists()
    # The Live TV setting off: only the player's keys.
    state['skin'], state['on'] = keymap.SKIN, False
    assert keymap.sync() == {keymap.PLAYER_NAME: 'installed'} and not target.exists()


def test_someone_elses_keymap_of_that_name_is_left_alone(kodi_home):
    target, state = kodi_home
    target.parent.mkdir()
    target.write_bytes(b'<keymap/>')
    state['on'] = False
    keymap.sync()
    assert target.read_bytes() == b'<keymap/>'
