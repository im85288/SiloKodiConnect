# -*- coding: utf-8 -*-
"""Silo playback protocol v3 for Kodi's player.

The service starts each session (it owns the sign-in tokens), reports
progress while Kodi plays and stops the session when Kodi does. Silo's media
routes need the sign-in header, so Kodi opens them through the service's
loopback relay, which adds a fresh token to each request.
"""
import json
import os
import platform
import threading
import time
import uuid

from . import mapping, upnext
from .api import ApiError

PROTOCOL = 3
HLS_DELIVERIES = ('server_remux_hls', 'server_transcode_hls')
PLAYABLE_DELIVERIES = ('original_http',) + HLS_DELIVERIES
PROGRESS_INTERVAL = 10

# Kodi's FFmpeg decodes all of these in software on every platform.
VIDEO_CODECS = ['h264', 'hevc', 'av1', 'vp9', 'vp8', 'mpeg4', 'mpeg2video', 'vc1', 'wmv3']
AUDIO_CODECS = ['aac', 'ac3', 'eac3', 'truehd', 'dts', 'flac', 'alac', 'mp3', 'mp2', 'opus', 'vorbis',
                'pcm_s16le', 'pcm_s24le', 'wmav2', 'wmapro']
CONTAINERS = ['mkv', 'matroska', 'mp4', 'mov', 'avi', 'mpegts', 'ts', 'm2ts', 'webm', 'wmv', 'flv', 'ogg', 'mpeg']
HLS_VIDEO_CODECS = ['h264', 'hevc']
HLS_AUDIO_CODECS = ['aac', 'ac3', 'eac3', 'mp3', 'flac', 'opus']

# Settings value -> what the display can show.
DYNAMIC_RANGE = {
    'sdr': {'hdr10': False, 'hdr10_plus': False, 'hlg': False, 'dolby_vision_profiles': []},
    'hdr10': {'hdr10': True, 'hdr10_plus': False, 'hlg': True, 'dolby_vision_profiles': []},
    'dv': {'hdr10': True, 'hdr10_plus': True, 'hlg': True, 'dolby_vision_profiles': [5, 8]},
    # Kodi 21 on an NVIDIA SHIELD plays profile 7 (UHD Blu-ray) files itself.
    # Without this Silo strips them to HDR10 in an HLS remux, which also
    # turns TrueHD into AAC.
    'dv7': {'hdr10': True, 'hdr10_plus': True, 'hlg': True, 'dolby_vision_profiles': [5, 7, 8]},
}
# A 4K stream remuxed to HLS reaches Kodi without its picture size, so the
# SHIELD's hardware decoder refuses it and Kodi decodes 4K HEVC in software
# until Android kills it for memory. Refuse such plans with an explanation.
MAX_HLS_HEIGHT = 1080


class PlaybackError(Exception):
    pass


def capabilities(version, max_resolution='1080p', dynamic_range='sdr'):
    """(client_capabilities, client_playback_context) for a start request."""
    hdr = dict(DYNAMIC_RANGE.get(dynamic_range, DYNAMIC_RANGE['sdr']))
    is_hdr = dynamic_range != 'sdr'
    client = {
        'video_evidence': 'declared', 'audio_evidence': 'declared',
        'codecs_video': list(VIDEO_CODECS), 'codecs_video_hardware': [],
        'codecs_audio': list(AUDIO_CODECS), 'containers': list(CONTAINERS),
        'max_resolution': max_resolution, 'hdr': is_hdr, 'hdr_details': hdr,
    }
    text_subtitles = {'embedded_text': True, 'sidecar_text': True, 'ass_styling': True,
                      'embedded_bitmap': True, 'sidecar_bitmap': False, 'font_attachments': True}
    original = {
        'enabled': True, 'supported_on_device': True,
        'containers': list(CONTAINERS), 'video_codecs': list(VIDEO_CODECS),
        'audio_decode_codecs': list(AUDIO_CODECS), 'audio_passthrough_codecs': [],
        'hdr_details': dict(hdr), 'auth_header_refresh': False, 'subtitles': text_subtitles,
        'features': [], 'transformations': [],
        # Kodi plays whichever audio track it picks from the original file,
        # and with dv7 handles Dolby Vision profile 7 itself.
        'validated_claims': ['client_selected_audio_track_v1']
        + (['client_managed_dynamic_range_v1'] if dynamic_range == 'dv7' else []),
    }
    hls = {
        'enabled': True, 'supported_on_device': True,
        'containers': ['hls', 'm3u8'], 'video_codecs': list(HLS_VIDEO_CODECS),
        'audio_decode_codecs': list(HLS_AUDIO_CODECS), 'audio_passthrough_codecs': [],
        'hdr_details': dict(hdr), 'auth_header_refresh': False,
        'subtitles': dict(text_subtitles, embedded_bitmap=False, font_attachments=False),
        'features': ['hls'], 'transformations': [], 'validated_claims': [],
    }
    context = {
        'protocol_version': PROTOCOL, 'form_factor': 'tv', 'app_version': version, 'app_channel': 'stable',
        'device': {'platform': (platform.system() or 'Kodi')[:32], 'os_version': platform.release()[:128]},
        'output': {'hdr_details': dict(hdr)},
        # Progressive remux cannot seek, which Kodi expects to do freely; a
        # class left out is unavailable.
        'deliveries': {'original_http': original, 'hls': hls},
    }
    return client, context


class Session(object):
    """One Silo playback attempt that Kodi is (about to be) playing."""

    def __init__(self, client, installation_id, session_id, content_id, plan):
        self.client = client
        self.installation_id = installation_id
        self.session_id = session_id
        self.content_id = content_id
        self.plan = plan
        self.origin = float((plan.get('timeline') or {}).get('stream_origin_seconds') or 0)
        self.sequence = 0
        self.stop_id = str(uuid.uuid4())
        self.position = 0.0
        self.paused = False
        self.stopped = False
        self.last_report = 0
        self.started_at = time.time()
        self.markers = []
        self.subtitles = []
        self.series_id = None
        self.subtitle_state = None  # (enabled, track index) as last applied or saved
        # The audio track Silo picks for the profile (playeraudio): an ordinal
        # into the file's audio tracks, for direct play only.
        self.audio_track = None
        self.audio_count = 0
        self.audio_language = ''

    def source_position(self, player_seconds):
        return max(0.0, self.origin + float(player_seconds or 0))

    def sample(self, player_seconds, paused):
        self.sequence += 1
        self.position = round(self.source_position(player_seconds), 3)
        self.paused = bool(paused)
        return {'installation_id': self.installation_id, 'sequence': self.sequence,
                'position': self.position, 'is_paused': self.paused}

    def report(self, player_seconds, paused):
        if self.stopped:
            return
        self.last_report = time.time()
        try:
            self.client.playback_progress(self.session_id, self.sample(player_seconds, paused))
        except ApiError as error:
            if error.status in (404, 410):
                # The server ended this session (an admin stop, or it expired).
                self.stopped = True
            raise

    def stop(self, player_seconds=None):
        if self.stopped:
            return
        self.stopped = True
        body = {'installation_id': self.installation_id, 'stop_id': self.stop_id}
        if player_seconds is not None:
            sample = self.sample(player_seconds, False)
            body.update(sequence=sample['sequence'], position=sample['position'], is_paused=False)
        for attempt in range(3):
            try:
                self.client.playback_stop(self.session_id, body)
                return
            except ApiError as error:
                if error.status in (404, 410):
                    return
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)


class PlaybackManager(object):
    def __init__(self, client, version, settings, log=None, media_url=None, ledger=None):
        """settings: callable returning {'max_resolution', 'dynamic_range', 'quality'}.
        media_url: maps a Silo media URL to the address Kodi should open.
        ledger: a file naming the sessions still open, so the next start can
        end the ones Kodi was killed or crashed holding (close_leftovers)."""
        self.client = client
        self.ledger = ledger
        self.media_url = media_url or (lambda url: url)
        self.version = version
        self.settings = settings
        self.log = log or (lambda message: None)
        self._lock = threading.Lock()
        self._capability = None
        self.pending = None  # started, waiting for Kodi to begin playing
        self.active = None

    def capability(self, refresh=False):
        if self._capability is None or refresh:
            capability = self.client.playback_capabilities()
            if capability.get('state') != 'available' or not capability.get('allowed'):
                raise PlaybackError('Playback is not available for this profile on the Silo server.')
            if PROTOCOL not in (capability.get('protocol_versions') or []):
                raise PlaybackError('This Silo server does not offer playback protocol 3.')
            self._capability = capability
        return self._capability

    def start(self, content_id, resume=False):
        """Start a Silo session; returns what Kodi needs to play it."""
        item = self.client.item(content_id)
        version = mapping.default_version(item)
        if not version or not version.get('file_id'):
            raise PlaybackError('Silo has no playable file for this title.')
        profile_id = self.client.store.get('profile_id')
        if not profile_id:
            raise PlaybackError('Choose a Silo profile first.')
        settings = self.settings()
        client_caps, context = capabilities(self.version, settings.get('max_resolution', '1080p'),
                                            settings.get('dynamic_range', 'sdr'))
        body = {
            'protocol_version': PROTOCOL, 'client_features': [],
            'file_id': str(version['file_id']), 'profile_id': profile_id,
            'playback_attempt_id': str(uuid.uuid4()),
            'quality_preference': settings.get('quality') or 'auto',
            'subtitle_fidelity_preference': 'compatible', 'metered': False,
            'progress_persistence': 'server',
            'client_capabilities': client_caps, 'client_playback_context': context,
        }
        position = mapping.play_state(item)[1]
        if resume and position:
            body['start_position'] = position
        decision = None
        for attempt in (1, 2):
            body['installation_id'] = self.capability(refresh=attempt == 2)['installation_id']
            try:
                decision = self.client.playback_start(body)
                break
            except ApiError as error:
                # The server was reinstalled or restored; learn its new identity once.
                if error.code != 'installation_changed' or attempt == 2:
                    raise
        if decision.get('outcome') != 'playable':
            terminal = decision.get('terminal') or {}
            raise PlaybackError(terminal.get('message') or terminal.get('reason') or 'Silo found no way to play this.')
        plan = decision.get('playback_plan') or {}
        stream = plan.get('stream') or {}
        recipe = plan.get('effective_recipe') or {}
        self.log('Silo plan for %s: %s, reason %s, %sp %s %s, audio %s; transformations %s; warnings %s' % (
            content_id, plan.get('delivery'), plan.get('decision_reason'), recipe.get('height'),
            recipe.get('video_codec'), recipe.get('dynamic_range'), recipe.get('audio_codec'),
            [t.get('name') for t in plan.get('transformations') or []],
            [w.get('code') for w in plan.get('degradation_warnings') or []]))
        session_id = decision.get('session_id') or plan.get('session_id')
        if plan.get('delivery') not in PLAYABLE_DELIVERIES or not stream.get('url'):
            self._release(body['installation_id'], session_id)
            raise PlaybackError('Silo offered a stream this add-on cannot play (%s).' % plan.get('delivery'))
        if plan.get('delivery') in HLS_DELIVERIES and (recipe.get('height') or 0) > MAX_HLS_HEIGHT:
            self._release(body['installation_id'], session_id)
            raise PlaybackError('Silo can only send this as a converted 4K stream, which this device would '
                                'have to decode in software. If it is Dolby Vision from a UHD Blu-ray, set '
                                'Playback > HDR to the profile 7 option.')
        session = Session(self.client, body['installation_id'], session_id, content_id, plan)
        # An episode's series: its remembered subtitle choice (subtitleprefs).
        session.series_id = item.get('series_id') if item.get('type') == 'episode' else None
        # Intro, recap and credits ranges, for Up Next's Skip button and popup.
        played = self._played_version(item, plan, version)
        session.markers = upnext.markers(played, item)
        # The audio track Silo picks for this profile, which Kodi puts on as
        # it starts (a converted stream already carries it).
        if plan.get('delivery') == 'original_http' and isinstance(played.get('effective_audio_track_index'), int):
            session.audio_track = played['effective_audio_track_index']
            session.audio_count = len(played.get('audio_tracks') or [])
            session.audio_language = played.get('effective_audio_language') or ''
        with self._lock:
            if self.pending:
                self._safe_stop(self.pending)
            self.pending = session
            self._remember()
        timeline = plan.get('timeline') or {}
        # Text sidecars: the file's own and ones downloaded through Silo (its apps' or this add-on's).
        subtitles = [self.media_url(self.client.url(entry['url'])) for entry in (plan.get('subtitle') or {}).get('inventory') or []
                     if entry.get('source') in ('external', 'downloaded') and entry.get('url')
                     and entry.get('codec') in ('srt', 'subrip', 'vtt', 'webvtt', 'ass', 'ssa')]
        session.subtitles = subtitles  # in the order Kodi adds them, after the file's own tracks
        self.log('Playing %s as %s' % (content_id, plan.get('delivery')))
        return {
            'url': self.media_url(self.client.url(stream['url'])),
            'mime_type': stream.get('mime_type') or '',
            'hls': plan.get('delivery') in HLS_DELIVERIES,
            # Where the stream starts relative to the title, for a converted stream.
            'start_offset': float(timeline.get('player_start_seconds') or 0) if plan.get('delivery') in HLS_DELIVERIES
            else 0,
            'headers': stream.get('headers') or {},
            'subtitles': subtitles,
            'title': item.get('title') or '',
        }

    @staticmethod
    def _played_version(item, plan, fallback):
        """The file version Silo actually chose (it may pick another one)."""
        chosen = str(plan.get('effective_media_file_id') or '')
        for version in item.get('versions') or []:
            if chosen and str(version.get('file_id')) == chosen:
                return version
        return fallback

    def _release(self, installation_id, session_id):
        """Stop a session Silo started but Kodi won't play."""
        if session_id:
            self._safe_stop(Session(self.client, installation_id, session_id, None, {}))

    def session_ids(self):
        return [s.session_id for s in (self.pending, self.active) if s and not s.stopped]

    # ----- driven by the service's xbmc.Player -----

    def on_started(self):
        with self._lock:
            if self.active:
                self._safe_stop(self.active)
            self.active, self.pending = self.pending, None
            self._remember()
        return self.active

    def on_tick(self, player_seconds, paused):
        session = self.active
        if not session or session.stopped:
            return
        if time.time() - session.last_report >= PROGRESS_INTERVAL or paused != session.paused:
            try:
                session.report(player_seconds, paused)
            except ApiError as error:
                self.log('Progress report failed: %s' % error)

    def on_stopped(self, player_seconds=None):
        with self._lock:
            session, self.active = self.active, None
        if session:
            self._safe_stop(session, player_seconds)
            with self._lock:
                self._remember()
        return session

    def _safe_stop(self, session, player_seconds=None):
        try:
            session.stop(player_seconds)
        except Exception as error:  # never let a failed stop break the player loop
            self.log('Stopping Silo session %s failed: %s' % (session.session_id, error))

    # ----- sessions left open by a Kodi that was killed or crashed -----

    def _remember(self):
        """Write the open sessions to the ledger (under self._lock)."""
        if not self.ledger:
            return
        sessions = [[s.installation_id, s.session_id] for s in (self.pending, self.active)
                    if s and s.session_id and not s.stopped]
        try:
            with open(self.ledger + '.tmp', 'w') as handle:
                json.dump(sessions, handle)
            os.replace(self.ledger + '.tmp', self.ledger)
        except (OSError, ValueError) as error:
            self.log('Could not note the open Silo session: %s' % error)

    def leftovers(self):
        """The sessions the ledger names that this run didn't start: the last
        run's, if Kodi stopped without ending them. Read before any playback."""
        try:
            with open(self.ledger) as handle:
                sessions = json.load(handle)
        except (OSError, TypeError, ValueError):
            return []
        mine = set(self.session_ids())
        return [(installation_id, session_id) for installation_id, session_id in sessions
                if session_id and session_id not in mine]

    def close_leftovers(self, sessions):
        """End them on Silo, which would otherwise list them as playing (or
        paused) until its own timeout, 30 minutes for a paused one."""
        for installation_id, session_id in sessions:
            self.log('Ending Silo session %s, left open when Kodi last stopped' % session_id)
            self._release(installation_id, session_id)
        with self._lock:
            self._remember()
