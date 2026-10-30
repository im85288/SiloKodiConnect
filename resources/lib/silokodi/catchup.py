# -*- coding: utf-8 -*-
"""Catch-up that plays the past programme, not live TV.

Some IPTV playlists (Xstream Pro's, for one) give each channel a catch-up
address that is the live one with ?utc=... added; Xtream Codes providers
that ignore it then stream live TV for any past programme. Their archive is
streaming/timeshift.php, so this rewrites each such catch-up-source in the
playlists Kodi's IPTV Simple add-on reads from a local file, whenever the
file changes, and has Kodi reload them. Logs give counts, never addresses
(they carry the provider login).

The same playlists fix every channel's stream mode as live, which keeps
IPTV Simple's timeshift from applying: channels without catch-up couldn't
be paused. They're made timeshift (catch-up channels play in catch-up mode
either way)."""
import glob
import os
import re

import xbmc

from . import kodi

LIVE_CATCHUP = re.compile(r'catchup-source="(https?://[^/"]+)/live/([^/"]+)/([^/"]+)/(\d+)\.(?:ts|m3u8)\?utc=[^"]*"')
ARCHIVE = ('catchup-source="{0}/streaming/timeshift.php?username={1}&password={2}&stream={3}'
           '&start={{Y}}-{{m}}-{{d}}:{{H}}-{{M}}&duration={{duration:60}}"')
LIVE_MODE = '#KODIPROP:inputstream.ffmpegdirect.stream_mode=live'
TIMESHIFT_MODE = '#KODIPROP:inputstream.ffmpegdirect.stream_mode=timeshift'
SETTING = re.compile(r'<setting id="(m3uPathType|m3uPath)"(?: default="true")?>([^<]*)</setting>')


def rewrite(text):
    """The playlist with each live-address catch-up-source made an archive
    one and each live stream mode made timeshift, and how many lines were
    changed."""
    text, archives = LIVE_CATCHUP.subn(lambda m: ARCHIVE.format(*m.groups()), text)
    modes = text.count(LIVE_MODE)
    return text.replace(LIVE_MODE, TIMESHIFT_MODE), archives + modes


def playlists(addon_data):
    """The local playlist files of IPTV Simple's instances (m3uPathType 0)."""
    found = []
    for path in glob.glob(os.path.join(addon_data, 'pvr.iptvsimple', 'instance-settings-*.xml')):
        try:
            with open(path, encoding='utf-8') as handle:
                settings = dict(SETTING.findall(handle.read()))
        except OSError:
            continue
        if settings.get('m3uPathType', '0') == '0' and settings.get('m3uPath'):
            found.append(settings['m3uPath'])
    return found


class Rewriter(object):
    """Checks the playlists now and then; reloads Live TV after a rewrite,
    though not while something plays (it would stop)."""

    def __init__(self):
        self.seen = {}
        self.reload_due = False

    def check(self, playing):
        for path in playlists(kodi.addon_data_dir()):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if self.seen.get(path) == mtime:
                continue
            with open(path, encoding='utf-8', errors='surrogateescape') as handle:
                text, count = rewrite(handle.read())
            if count:
                temporary = path + '.silokodi'
                with open(temporary, 'w', encoding='utf-8', errors='surrogateescape') as handle:
                    handle.write(text)
                os.replace(temporary, path)
                self.reload_due = True
                kodi.log('Catch-up: %d playlist lines rewritten (archive, timeshift)' % count)
            self.seen[path] = os.path.getmtime(path)
        if self.reload_due and not playing:
            self.reload_due = False
            reload_live_tv()


def reload_live_tv(pause=8):
    """Have IPTV Simple read its playlists again: off, then on once Kodi's
    Live TV has stopped (turned on again at once, it stayed stopped)."""
    kodi.jsonrpc('Addons.SetAddonEnabled', addonid='pvr.iptvsimple', enabled=False)
    xbmc.Monitor().waitForAbort(pause)
    kodi.jsonrpc('Addons.SetAddonEnabled', addonid='pvr.iptvsimple', enabled=True)
    kodi.log('Catch-up: reloaded Live TV')
