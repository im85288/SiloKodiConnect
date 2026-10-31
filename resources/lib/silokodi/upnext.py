# -*- coding: utf-8 -*-
"""Tell Up Next (service.upnext) about a Silo episode as it starts.

Up Next's add-on interface (upnext_data) takes the current and next episode,
how to play the next one, when the credits start (notification_offset), and
the intro and recap ranges to offer a Skip button for (skip_segments). The
next episode is played back here as a Kodi library item, so its resume point
and watched state stay linked.
"""
import base64
import json

ADDON_ID = 'plugin.video.silokodiconnect'
SENDER = ADDON_ID + '.SIGNAL'
PLAY_ACTION = ADDON_ID + '_play_action'
# What Up Next shows in its popup.
EPISODE_PROPERTIES = ['art', 'dateadded', 'episode', 'file', 'firstaired', 'lastplayed', 'playcount', 'plot',
                      'rating', 'resume', 'runtime', 'season', 'showtitle', 'streamdetails', 'title', 'tvshowid',
                      'writer']
MARKER_KINDS = ('intro', 'recap', 'credits', 'preview')


def markers(version, item=None):
    """[(kind, start, end)] in source seconds, from a file version's
    marker_segments, or the older single intro/recap/credits fields."""
    found = []
    for segment in (version or {}).get('marker_segments') or []:
        if segment.get('kind') in MARKER_KINDS and segment.get('end_seconds') is not None:
            found.append((segment['kind'], float(segment.get('start_seconds') or 0), float(segment['end_seconds'])))
    if found:
        return sorted(found, key=lambda marker: marker[1])
    for source in (version or {}, item or {}):
        for kind in MARKER_KINDS:
            marker = source.get(kind) or {}
            if marker.get('end') is not None and not any(m[0] == kind for m in found):
                found.append((kind, float(marker.get('start') or 0), float(marker['end'])))
    return sorted(found, key=lambda marker: marker[1])


def next_episode(episodes, current):
    """The episode after `current` in season order. Specials only follow specials."""
    key = (current.get('season'), current.get('episode'))
    candidates = [e for e in episodes if (e.get('season'), e.get('episode')) > key
                  and (e.get('season') or key[0] == 0)]
    return min(candidates, key=lambda e: (e.get('season'), e.get('episode'))) if candidates else None


def payload(current, following, found_markers, origin=0.0):
    """upnext_data for the current episode (following may be None). Marker times are converted from
    the source file's timeline to the player's (they differ only when Silo
    converts a stream that starts part-way in)."""
    data = {'current_episode': current}
    if following:
        # Without one (the last episode in the library) Up Next still offers
        # Skip, and shows no popup.
        data['next_episode'] = following
        data['play_info'] = {'episodeid': following['episodeid']}
    to_player = lambda seconds: max(0.0, seconds - origin)
    credits = [m for m in found_markers if m[0] == 'credits']
    if credits:
        data['notification_offset'] = int(to_player(credits[-1][1]))
    segments = [{'kind': kind, 'start': to_player(start), 'end': to_player(end)}
                for kind, start, end in found_markers if kind in ('intro', 'recap') and end > origin]
    if segments:
        data['skip_segments'] = segments
    return data


def marker_ranges(found_markers, total, origin=0.0):
    """Each marker kind's ranges as a skin's ranges control reads them:
    'start,end[,start,end...]' in percent of the player's timeline."""
    ranges = {}
    if total <= 0:
        return ranges
    for kind, start, end in found_markers:
        start, end = max(0.0, start - origin), min(total, end - origin)
        if end > start:
            ranges.setdefault(kind, []).append('%.2f,%.2f' % (start * 100 / total, end * 100 / total))
    return {kind: ','.join(parts) for kind, parts in ranges.items()}


def encode(data):
    """The NotifyAll data Up Next decodes: a one-element list of base64 JSON."""
    return [base64.b64encode(json.dumps(data).encode('utf-8')).decode('ascii')]


def decode(data):
    """Up Next's play_action payload (same encoding) back to a dict."""
    try:
        return json.loads(base64.b64decode(json.loads(data)[0]))
    except (ValueError, TypeError, IndexError):
        return None
