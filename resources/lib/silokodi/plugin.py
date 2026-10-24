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

from . import auth, kodi
from .relay import DEFAULT_PORT
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


def menu(base, handle):
    store = Store(kodi.profile_dir())
    entries = []
    if store.signed_in:
        who = store.get('profile_name') or store.get('username') or ''
        entries.append((kodi.string(30130) % (who, store.get('server_url')), None))
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
        xbmcplugin.addDirectoryItem(handle, url, item, isFolder=False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
