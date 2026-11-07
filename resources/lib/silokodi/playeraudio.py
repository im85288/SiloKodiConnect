# -*- coding: utf-8 -*-
"""The audio track Silo picks, put on when a title starts. Silo resolves the
profile's audio language (for the series, the library, the device or the
profile) to a track of the file, as its own apps play it; Kodi would play the
file's default track, which in a multi-language release can be another
language (a MULTi release with French first). Direct play only: a converted
stream carries the track Silo chose."""
import threading
import time

from . import kodi

WAIT_SECONDS = 10  # at most, for Kodi to list the file's audio tracks


def player_audio():
    """(the current track's index, how many tracks) in Kodi's video player."""
    answer = kodi.jsonrpc('Player.GetProperties', playerid=1, properties=['audiostreams', 'currentaudiostream']) or {}
    return (answer.get('currentaudiostream') or {}).get('index'), len(answer.get('audiostreams') or [])


def apply(session, wait=WAIT_SECONDS):
    """Switch to Silo's track, if it isn't playing already. Silo's track is
    an ordinal into the file's audio tracks, which Kodi lists in the same
    order for a file it plays itself; when the counts differ, nothing."""
    wanted = session.audio_track
    if wanted is None:
        return

    def run():
        for _ in range(int(wait * 2)):
            current, count = player_audio()
            if count:
                break
            time.sleep(0.5)
        else:
            return
        if count != session.audio_count or not 0 <= wanted < count:
            kodi.log_warning('Audio: Kodi lists %d tracks, Silo %d; left as it is' % (count, session.audio_count))
            return
        if current != wanted:
            kodi.jsonrpc('Player.SetAudioStream', playerid=1, stream=wanted)
            kodi.log('Audio for %s: track %d (%s), as Silo picks it' % (session.content_id, wanted, session.audio_language))
    thread = threading.Thread(target=run, name='silokodi-audio')
    thread.daemon = True
    thread.start()
    return thread
