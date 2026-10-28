# -*- coding: utf-8 -*-
"""Live TV's recently watched channels for a Silo-style skin, as TiviMate
keeps them: newest first, not the one playing, and without any the viewer
removed (until they watch it again; Kodi itself can't forget a channel)."""
import glob
import json
import os
import re
import sqlite3

from . import kodi

REMOVED_FILE = 'recent_channels_removed.json'
LIMIT = 20


def recent(channels, watched, playing=None, removed=None):
    """channels: Kodi's PVR.GetChannels items; watched: {channel id: when it
    was last watched, epoch seconds} (Kodi's JSON-RPC gives only the day).
    The watched ones, newest first, leaving out the playing channel and the
    removed ones (removed: {channel id: when last watched as removed})."""
    removed = removed or {}
    seen = [c for c in channels
            if watched.get(c.get('channelid'), 0) > removed.get(str(c.get('channelid')), 0)
            and not c.get('hidden') and c.get('channelid') != playing]
    return sorted(seen, key=lambda c: watched[c['channelid']], reverse=True)[:LIMIT]


def previous(channels, watched, playing=None):
    """The channel watched before the playing one, or None."""
    seen = recent(channels, watched, playing)
    return seen[0] if seen else None


def last_watched():
    """{channel id: when last watched} from Kodi's TV database (read only)."""
    paths = sorted(glob.glob(os.path.join(kodi.database_dir(), 'TV*.db')),
                   key=lambda path: int(re.sub(r'\D', '', os.path.basename(path)) or 0))
    if not paths:
        return {}
    connection = sqlite3.connect('file:%s?mode=ro' % paths[-1], uri=True)
    try:
        return dict(connection.execute('SELECT idChannel, iLastWatched FROM channels WHERE iLastWatched > 0'))
    finally:
        connection.close()


def removed_path():
    return os.path.join(kodi.profile_dir(), REMOVED_FILE)


def load_removed():
    try:
        with open(removed_path()) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def remove(channel_id, watched):
    """Leave a channel out of the recent list until it is watched again."""
    removed = load_removed()
    removed[str(channel_id)] = watched.get(int(channel_id), 0)
    with open(removed_path(), 'w') as handle:
        json.dump(removed, handle)
