# -*- coding: utf-8 -*-
"""A show's remembered subtitle choice, as Silo's apps keep it.

Silo stores one choice per profile and series (subtitle-prefs): its own apps
apply it when an episode starts and save it when the viewer changes track,
so a choice made on one device follows to the next episode on any of them.
Silo's playback plan doesn't apply it; the client does, here with Kodi's
subtitle tracks (JSON-RPC Player.GetProperties' subtitles).
"""


def language_key(code):
    """A language as one comparable key: Kodi's tracks say 'eng', Silo 'en'."""
    code = (code or '').strip().lower().split('-')[0]
    return {'eng': 'en', 'fre': 'fr', 'fra': 'fr', 'ger': 'de', 'deu': 'de', 'spa': 'es', 'ita': 'it',
            'dut': 'nl', 'nld': 'nl', 'por': 'pt', 'swe': 'sv', 'nor': 'no', 'nob': 'no', 'dan': 'da',
            'fin': 'fi', 'pol': 'pl', 'rus': 'ru', 'jpn': 'ja', 'chi': 'zh', 'zho': 'zh', 'kor': 'ko',
            'ara': 'ar', 'heb': 'he', 'tur': 'tr', 'gre': 'el', 'ell': 'el', 'cze': 'cs', 'ces': 'cs',
            'hun': 'hu', 'rum': 'ro', 'ron': 'ro', 'hin': 'hi'}.get(code, code)


def pick(pref, tracks):
    """What the remembered choice means for this episode's tracks: 'off', a
    track's index, or None (no choice, or no track in its language: Kodi's
    own default stands). A track like the remembered one (forced and
    hearing-impaired flags, then its label) wins over others in its language."""
    if not pref:
        return None
    if pref.get('subtitle_mode') == 'off' or pref.get('subtitle_track_index') == -1:
        return 'off'
    signature = pref.get('track_signature') or {}
    wanted = language_key(pref.get('subtitle_language') or signature.get('language'))
    if not wanted:
        return None
    same = [t for t in tracks if language_key(t.get('language')) == wanted]
    if not same:
        return None

    def likeness(track):
        return ((bool(track.get('isforced')) == bool(signature.get('forced'))) * 4
                + (bool(track.get('isimpaired')) == bool(signature.get('hearing_impaired'))) * 2
                + (bool(signature.get('label')) and track.get('name') == signature.get('label')))
    return max(same, key=likeness)['index']


def choice(enabled, track):
    """The preference to store for the track now showing (None when off)."""
    if not enabled or not track:
        return {'subtitle_mode': 'off', 'subtitle_track_index': -1}
    return {'subtitle_mode': 'always', 'subtitle_language': language_key(track.get('language')) or '',
            'subtitle_track_index': int(track.get('index') or 0),
            'track_signature': {'language': track.get('language') or '', 'label': track.get('name') or '',
                                'forced': bool(track.get('isforced')),
                                'hearing_impaired': bool(track.get('isimpaired'))}}
