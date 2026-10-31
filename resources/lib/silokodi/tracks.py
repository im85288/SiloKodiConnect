# -*- coding: utf-8 -*-
"""The playing video's audio and subtitle tracks, for a skin's player sheet.

A skin can't list a video's tracks itself (it only sees the current one),
so a Silo-style skin's Options sheet sends

    NotifyAll(silokodi,tracks.refresh)         when it opens
    NotifyAll(silokodi,tracks.audio.<index>)   to switch audio
    NotifyAll(silokodi,tracks.sub.<index>)     to switch subtitles
    NotifyAll(silokodi,tracks.sub.off)         to turn them off

and the service publishes the tracks as Home window properties:

    silo.tracks.audio.<n>.label / .detail / .index / .current   (n 1-MAX_AUDIO)
    silo.tracks.sub.<n>.label / .detail / .index / .current     (n 1-MAX_SUBTITLES)
    silo.tracks.audio.count, silo.tracks.sub.count, silo.tracks.sub.off ('true' when off)
"""
import xbmc

from . import kodi, mapping

SENDER = 'silokodi'
PREFIX = 'Other.tracks.'
MAX_AUDIO = 10
MAX_SUBTITLES = 30


def language(code):
    """A track's language in English: the add-on's own names first, then
    Kodi's (it knows every ISO 639 code, cze included)."""
    code = (code or '').strip()
    name = mapping.LANGUAGE_NAMES.get(code.lower())
    if not name and code:
        try:
            name = xbmc.convertLanguage(code, xbmc.ENGLISH_NAME)
        except Exception:
            name = ''
    return name or code.upper()


def readable(text):
    """A track's own title, unless the skin's font can't draw it (Inter has
    Latin, Greek and Cyrillic, not Arabic or CJK): boxes help no one."""
    return text if all(ord(c) < 0x0530 or 0x2000 <= ord(c) < 0x2070 for c in text or '') else ''


def audio_entry(stream, current):
    codec = (stream.get('codec') or '').lower()
    detail = ' · '.join(part for part in (
        mapping.AUDIO_CODECS.get(codec, codec.upper()),
        mapping.CHANNEL_LAYOUTS.get(stream.get('channels') or 0, ''),
        readable(stream.get('name'))) if part)
    return {'label': language(stream.get('language')) or readable(stream.get('name')) or 'Unknown',
            'detail': detail, 'index': str(stream.get('index', '')), 'current': 'true' if current else ''}


def subtitle_entry(stream, current):
    flags = [flag for flag, key in (('Forced', 'isforced'), ('SDH', 'isimpaired')) if stream.get(key)]
    detail = ' · '.join(part for part in [readable(stream.get('name'))] + flags if part)
    return {'label': language(stream.get('language')) or readable(stream.get('name')) or 'Unknown',
            'detail': detail, 'index': str(stream.get('index', '')), 'current': 'true' if current else ''}


def track_properties(details):
    """Every property for Player.GetProperties' answer, slots past the
    tracks left empty so a shorter list leaves none behind."""
    props = {}
    current_audio = (details.get('currentaudiostream') or {}).get('index')
    current_sub = (details.get('currentsubtitle') or {}).get('index')
    subtitles_on = bool(details.get('subtitleenabled'))
    groups = (('audio', MAX_AUDIO, [audio_entry(s, s.get('index') == current_audio)
                                    for s in details.get('audiostreams') or []]),
              ('sub', MAX_SUBTITLES, [subtitle_entry(s, subtitles_on and s.get('index') == current_sub)
                                      for s in details.get('subtitles') or []]))
    for kind, limit, entries in groups:
        for n in range(1, limit + 1):
            entry = entries[n - 1] if n <= len(entries) else {}
            for field in ('label', 'detail', 'index', 'current'):
                props['silo.tracks.%s.%d.%s' % (kind, n, field)] = entry.get(field, '')
        props['silo.tracks.%s.count' % kind] = str(min(len(entries), limit))
    props['silo.tracks.sub.off'] = '' if subtitles_on else 'true'
    return props


def video_player():
    for player in kodi.jsonrpc('Player.GetActivePlayers') or []:
        if player.get('type') == 'video':
            return player['playerid']
    return None


def publish():
    player = video_player()
    if player is None:
        return
    details = kodi.jsonrpc('Player.GetProperties', playerid=player, properties=[
        'audiostreams', 'currentaudiostream', 'subtitles', 'currentsubtitle', 'subtitleenabled']) or {}
    for key, value in track_properties(details).items():
        kodi.HOME.setProperty(key, value)


def handle(sender, method):
    """Act on the skin's track notifications; True when it was one."""
    if sender != SENDER or not method.startswith(PREFIX):
        return False
    request = method[len(PREFIX):]
    player = video_player()
    if player is not None:
        kind, _, value = request.partition('.')
        if kind == 'audio' and value.isdigit():
            kodi.jsonrpc('Player.SetAudioStream', playerid=player, stream=int(value))
        elif kind == 'sub' and value == 'off':
            kodi.jsonrpc('Player.SetSubtitle', playerid=player, subtitle='off')
        elif kind == 'sub' and value.isdigit():
            kodi.jsonrpc('Player.SetSubtitle', playerid=player, subtitle=int(value), enable=True)
    publish()
    return True
