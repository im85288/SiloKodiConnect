from resources.lib.silokodi import tracks

DETAILS = {
    'audiostreams': [{'index': 0, 'language': 'eng', 'codec': 'eac3_ddp_atmos', 'channels': 6, 'name': 'Original'},
                     {'index': 1, 'language': 'ger', 'codec': 'ac3', 'channels': 2, 'name': ''}],
    'currentaudiostream': {'index': 0},
    'subtitles': [{'index': 0, 'language': 'eng', 'name': 'SDH', 'isimpaired': True},
                  {'index': 1, 'language': 'eng', 'name': '', 'isforced': True},
                  {'index': 2, 'language': 'fre', 'name': ''}],
    'currentsubtitle': {'index': 2}, 'subtitleenabled': True,
}


def test_tracks_as_the_skin_lists_them():
    props = tracks.track_properties(DETAILS)
    assert props['silo.tracks.audio.1.label'] == 'English'
    assert props['silo.tracks.audio.1.detail'] == 'ATMOS · 5.1 · Original'
    assert props['silo.tracks.audio.1.current'] == 'true' and props['silo.tracks.audio.2.current'] == ''
    assert props['silo.tracks.audio.2.detail'] == 'AC3 · 2.0' and props['silo.tracks.audio.count'] == '2'
    assert props['silo.tracks.audio.3.label'] == ''  # empty slots, so a shorter list leaves none behind
    assert props['silo.tracks.sub.1.detail'] == 'SDH · SDH' and props['silo.tracks.sub.2.detail'] == 'Forced'
    assert props['silo.tracks.sub.3.label'] == 'French' and props['silo.tracks.sub.3.current'] == 'true'
    assert props['silo.tracks.sub.off'] == ''


def test_subtitles_off_marks_none_current():
    props = tracks.track_properties(dict(DETAILS, subtitleenabled=False))
    assert props['silo.tracks.sub.off'] == 'true'
    assert not any(props['silo.tracks.sub.%d.current' % n] for n in range(1, 4))


def test_only_the_skins_track_notifications_are_handled(monkeypatch):
    calls = []
    monkeypatch.setattr(tracks.kodi, 'jsonrpc', lambda method, **params: calls.append((method, params)) or
                        ([{'type': 'video', 'playerid': 1}] if method == 'Player.GetActivePlayers' else {}))
    assert not tracks.handle('xbmc', 'Other.tracks.audio.1')
    assert tracks.handle('silokodi', 'Other.tracks.audio.1')
    assert ('Player.SetAudioStream', {'playerid': 1, 'stream': 1}) in calls
    assert tracks.handle('silokodi', 'Other.tracks.sub.off')
    assert ('Player.SetSubtitle', {'playerid': 1, 'subtitle': 'off'}) in calls


def test_unreadable_titles_are_dropped_and_codes_named(monkeypatch):
    monkeypatch.setattr(tracks.xbmc, 'convertLanguage', lambda code, form: {'cze': 'Czech'}.get(code, ''),
                        raising=False)
    monkeypatch.setattr(tracks.xbmc, 'ENGLISH_NAME', 2, raising=False)
    entry = tracks.subtitle_entry({'index': 3, 'language': 'cze', 'name': 'Čeština (Česko)'}, False)
    assert entry['label'] == 'Czech' and entry['detail'] == 'Čeština (Česko)'
    arabic = tracks.subtitle_entry({'index': 4, 'language': 'ara', 'name': '(العربية) السعودية'}, False)
    assert arabic['label'] == 'Arabic' and arabic['detail'] == ''
