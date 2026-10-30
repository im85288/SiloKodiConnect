# -*- coding: utf-8 -*-
"""Live TV's recently watched channels for a Silo-style skin, as TiviMate
keeps them: newest first, not the one playing, and without any the viewer
removed (until they watch it again; Kodi itself can't forget a channel)."""
import calendar
import glob
import json
import os
import re
import sqlite3
import time

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


CATCHUP_DAYS = 7
AHEAD_DAYS = 7


def day_header(day, today):
    """A schedule's day header: Yesterday, Today, Tomorrow, else "Mon, 26 Oct"."""
    offset = (day - today).days
    return {-1: 'Yesterday', 0: 'Today', 1: 'Tomorrow'}.get(offset) or day_label(day)


def schedule_rows(broadcasts, now, archive, local=None):
    """A channel's programmes (dicts with broadcastid, title, start, end in
    epoch seconds) as schedule rows, from CATCHUP_DAYS back to AHEAD_DAYS
    ahead: a {'header'} row where the day changes (with its 'date' and
    whether it is 'today'), then each programme with 'when' (past, now or
    future) and 'archive' (past and catch-up can play it). Every row has
    'day', the day's number from the first."""
    import datetime
    local = local or (lambda epoch: datetime.datetime.fromtimestamp(epoch))
    today = local(now).date()
    rows, day, number = [], None, -1
    for broadcast in sorted(broadcasts, key=lambda b: b['start']):
        date = local(broadcast['start']).date()
        if not -CATCHUP_DAYS <= (date - today).days <= AHEAD_DAYS:
            continue
        if date != day:
            day, number = date, number + 1
            rows.append({'header': day_header(date, today), 'date': date, 'today': date == today, 'day': number})
        when = 'past' if broadcast['end'] <= now else 'now' if broadcast['start'] <= now else 'future'
        rows.append({'title': broadcast.get('title') or broadcast.get('label') or '', 'start': broadcast['start'],
                     'end': broadcast['end'], 'plot': broadcast.get('plot') or '', 'date': date, 'day': number,
                     'broadcastid': broadcast.get('broadcastid'), 'when': when,
                     'archive': when == 'past' and archive})
    return rows


def day_label(day):
    """A day as the day column and the programme card show it: "Tue, 27 Oct"."""
    return '%s, %d %s' % (day.strftime('%a'), day.day, day.strftime('%b'))


def around_now(rows, before=2):
    """The rows from a couple of programmes before the one on now, with its
    day's header: what the quick guide shows before the viewer moves in."""
    now = next((i for i, row in enumerate(rows) if row.get('when') == 'now'), None)
    if now is None:
        now = next((i for i, row in enumerate(rows) if row.get('when') == 'future'), len(rows))
    start = now
    while start > 0 and now - start < before and not rows[start - 1].get('header'):
        start -= 1
    header = next((i for i in range(start, -1, -1) if rows[i].get('header')), None) if rows else None
    picked = rows[start:]
    return ([rows[header]] + picked) if header is not None and header < start else picked


def find_channel(number, name, found=None):
    """A Live TV channel by the skin's number, else its name (with catch-up).
    found: Kodi's channel list, if already read."""
    if found is None:
        found = (kodi.jsonrpc('PVR.GetChannels', channelgroupid='alltv', properties=['channelnumber', 'hasarchive'])
                 or {}).get('channels') or []
    return next((c for c in found if str(c.get('channelnumber')) == str(number or '').strip()
                 and (not name or c.get('label') == name)), None) or \
        next((c for c in found if c.get('label') == name), None)


def channel_schedule(channel, now=None):
    """The channel's programmes as schedule_rows, from Kodi's guide."""
    broadcasts = (kodi.jsonrpc('PVR.GetBroadcasts', channelid=channel['channelid'],
                               properties=['title', 'starttime', 'endtime', 'plot'])
                  or {}).get('broadcasts') or []
    for broadcast in broadcasts:
        broadcast['start'], broadcast['end'] = (calendar.timegm(time.strptime(broadcast[key], '%Y-%m-%d %H:%M:%S'))
                                                for key in ('starttime', 'endtime'))
    return schedule_rows(broadcasts, now or time.time(), bool(channel.get('hasarchive')))


def items(rows, channel, clock):
    """Schedule rows as a skin's list items: (label, label2, properties).
    Label2 is the start time in Kodi's time format (clock); silo.header
    marks a day header, silo.now the one on now, silo.archive a past one
    catch-up can play; silo.day is the row's day (the day column's),
    silo.timeline and silo.plot fill the skin's programme card."""
    def at(epoch):
        return time.strftime(clock, time.localtime(epoch)).lstrip('0')
    found = []
    for row in rows:
        if row.get('header'):
            found.append((row['header'], '', {'silo.header': '1', 'silo.day': str(row['day']),
                                              'silo.today': '1' if row['today'] else ''}))
            continue
        found.append((row['title'], at(row['start']), {
            'silo.day': str(row['day']), 'silo.plot': row['plot'],
            'silo.timeline': '%s, %s — %s' % (day_label(row['date']), at(row['start']), at(row['end'])),
            'silo.now': '1' if row['when'] == 'now' else '',
            'silo.past': '1' if row['when'] == 'past' else '',
            'silo.archive': '1' if row.get('archive') else '',
            'silo.when': row['when'],
            'silo.broadcastid': str(row['broadcastid']), 'silo.channelid': str(channel['channelid'])}))
    return found
