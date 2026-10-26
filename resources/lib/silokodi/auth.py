# -*- coding: utf-8 -*-
"""Sign-in with Silo's device code: Kodi shows a code, you approve it on a
phone or computer where you're signed in to Silo (Pocket ID and other
external sign-ins work there), and Kodi collects the tokens."""
import time

import requests
import xbmc
import xbmcgui

from . import kodi
from .api import ApiError, SiloClient, normalize_server_url
from .mapping import parse_time


def ask_server(store):
    current = store.get('server_url') or 'https://'
    url = xbmcgui.Dialog().input(kodi.string(30100), current)
    return normalize_server_url(url) if url else None


def sign_in(store):
    """Interactive sign-in. Returns True when signed in with a profile chosen."""
    server = ask_server(store)
    if not server:
        return False
    client = SiloClient(store, kodi.version(), xbmc.getInfoLabel('System.FriendlyName'), can_refresh=False)
    store.update(server_url=server)
    try:
        request = client.device_start()
    except (ApiError, requests.RequestException) as error:
        xbmcgui.Dialog().ok('SiloKodiConnect', kodi.string(30101) % (server, error))
        return False
    if not wait_for_approval(client, request):
        return False
    return choose_profile(store, client)


def wait_for_approval(client, request):
    code = request['user_code']
    shown = '%s-%s' % (code[:4], code[4:]) if len(code) == 8 and code.isdigit() else code
    text = kodi.string(30102) % (request.get('verification_uri') or client.server_url + '/activate', shown)
    dialog = xbmcgui.DialogProgress()
    dialog.create('SiloKodiConnect', text)
    deadline = time.time() + (request.get('expires_in') or 900)
    interval = request.get('interval') or 5
    monitor = xbmc.Monitor()
    try:
        while time.time() < deadline:
            percent = int(100 * (deadline - time.time()) / (request.get('expires_in') or 900))
            dialog.update(max(0, min(100, percent)), text)
            if dialog.iscanceled() or monitor.waitForAbort(interval) or dialog.iscanceled():
                client.device_cancel(request['device_code'])
                return False
            try:
                answer = client.device_poll(request['device_code'])
            except ApiError as error:
                if error.status == 429:
                    interval = min(interval * 2, 30)
                    continue
                raise
            status = answer.get('status')
            if answer.get('expires_at'):
                parsed = _epoch(answer['expires_at'])
                deadline = parsed or deadline
            interval = answer.get('poll_after') or interval
            if status == 'approved' and answer.get('tokens'):
                client.save_tokens(answer['tokens'])
                user = answer['tokens'].get('user') or {}
                client.store.update(user_id=user.get('id'), username=user.get('username'),
                                    role=user.get('role'))
                if answer.get('profile_id'):
                    client.store.update(profile_id=answer['profile_id'],
                                        profile_token=answer.get('profile_token'))
                return True
            if status in ('denied', 'canceled', 'consumed', 'expired'):
                xbmcgui.Dialog().ok('SiloKodiConnect', kodi.string(30103))
                return False
        xbmcgui.Dialog().ok('SiloKodiConnect', kodi.string(30103))
        return False
    finally:
        dialog.close()


def _epoch(value):
    moment = parse_time(value)
    return moment.timestamp() if moment else None


def choose_profile(store, client, switching=False):
    """Ask who's watching. On sign-in, backing out picks the main profile;
    when switching, it keeps the current one (returns False)."""
    profiles = client.profiles()
    if not profiles:
        return not switching
    current = store.get('profile_id')
    if len(profiles) == 1 and not switching:
        chosen = profiles[0]
    else:
        names = [p.get('name') or p['id'] for p in profiles]
        at = next((i for i, p in enumerate(profiles) if p.get('id') == current), -1)
        index = xbmcgui.Dialog().select(kodi.string(30104), names, preselect=at)
        if index < 0:
            if switching:
                return False
            chosen = next((p for p in profiles if p.get('is_primary')), profiles[0])
        else:
            chosen = profiles[index]
    if switching and chosen.get('id') == current:
        return False
    token = None
    if chosen.get('has_pin'):
        while True:
            pin = xbmcgui.Dialog().numeric(0, kodi.string(30105) % chosen.get('name'), bHiddenInput=True)
            if not pin:
                return False
            answer = client.verify_pin(chosen['id'], pin)
            if answer.get('valid'):
                token = answer.get('profile_token')
                break
            xbmcgui.Dialog().ok('SiloKodiConnect', kodi.string(30106))
    store.update(profile_id=chosen['id'], profile_name=chosen.get('name'), profile_token=token)
    return True
