# -*- coding: utf-8 -*-
from resources.lib.silokodi import subtitleprefs

TRACKS = [
    {'index': 0, 'language': 'eng', 'name': 'English (Forced)', 'isforced': True, 'isimpaired': False},
    {'index': 1, 'language': 'eng', 'name': 'English', 'isforced': False, 'isimpaired': False},
    {'index': 2, 'language': 'eng', 'name': 'English SDH', 'isforced': False, 'isimpaired': True},
    {'index': 3, 'language': 'fre', 'name': 'Français', 'isforced': False, 'isimpaired': False},
]


def test_no_choice_leaves_kodi_alone():
    assert subtitleprefs.pick(None, TRACKS) is None
    assert subtitleprefs.pick({'subtitle_mode': ''}, TRACKS) is None


def test_off_turns_subtitles_off():
    assert subtitleprefs.pick({'subtitle_mode': 'off'}, TRACKS) == 'off'
    assert subtitleprefs.pick({'subtitle_track_index': -1}, TRACKS) == 'off'


def test_the_track_most_like_the_remembered_one_in_its_language():
    plain = {'subtitle_mode': 'always', 'subtitle_language': 'en',
             'track_signature': {'language': 'eng', 'forced': False, 'hearing_impaired': False}}
    assert subtitleprefs.pick(plain, TRACKS) == 1
    sdh = dict(plain, track_signature={'language': 'eng', 'hearing_impaired': True})
    assert subtitleprefs.pick(sdh, TRACKS) == 2
    assert subtitleprefs.pick({'subtitle_mode': 'always', 'subtitle_language': 'fr'}, TRACKS) == 3
    # A language the episode has no track in: Kodi's own default stands.
    assert subtitleprefs.pick({'subtitle_mode': 'always', 'subtitle_language': 'de'}, TRACKS) is None


def test_the_choice_saved_round_trips():
    saved = subtitleprefs.choice(True, TRACKS[2])
    assert saved['subtitle_mode'] == 'always' and saved['subtitle_language'] == 'en'
    assert saved['track_signature']['hearing_impaired'] is True
    assert subtitleprefs.pick(saved, TRACKS) == 2
    assert subtitleprefs.choice(False, TRACKS[1]) == {'subtitle_mode': 'off', 'subtitle_track_index': -1}
