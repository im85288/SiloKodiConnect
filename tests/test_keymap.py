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


def test_the_live_tv_keymap_follows_the_skin_and_setting(kodi_home):
    target, state = kodi_home
    assert keymap.sync() == 'installed' and keymap.MARK in target.read_bytes()
    assert keymap.sync() is None                      # already there
    state['skin'] = 'skin.estuary'
    assert keymap.sync() == 'removed' and not target.exists()
    state['skin'], state['on'] = keymap.SKIN, False
    assert keymap.sync() is None and not target.exists()


def test_someone_elses_keymap_of_that_name_is_left_alone(kodi_home):
    target, state = kodi_home
    target.parent.mkdir()
    target.write_bytes(b'<keymap/>')
    state['on'] = False
    assert keymap.sync() is None and target.read_bytes() == b'<keymap/>'
