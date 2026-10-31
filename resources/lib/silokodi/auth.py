# -*- coding: utf-8 -*-
"""Sign-in with Silo's device code: Kodi shows a code, you approve it on a
phone or computer where you're signed in to Silo (Pocket ID and other
external sign-ins work there), and Kodi collects the tokens.

The server: one Kodi found on the local network (discovery.py), one the
Silo phone app hands over (pairing.py, as Silo's own TV apps sign in), or
one typed in."""
import re
import time

import requests
import xbmc
import xbmcgui

from . import discovery, kodi, pairing
from .api import ApiError, SiloClient, normalize_server_url
from .mapping import parse_time


def ask_server(store):
    current = store.get('server_url') or 'https://'
    url = xbmcgui.Dialog().input(kodi.string(30100), current)
    return normalize_server_url(url) if url else None


def choose_server(found):
    """The servers Kodi found, then the phone and typing: the index chosen,
    'phone', 'type', or None."""
    names = [name for name, _ in found] + [kodi.string(30172), kodi.string(30173)]
    pick = xbmcgui.Dialog().select(kodi.string(30136), names)
    if pick < 0:
        return None
    if pick < len(found):
        return pick
    return 'phone' if pick == len(found) else 'type'


def sign_in(store):
    """Interactive sign-in. Returns True when signed in with a profile chosen."""
    # A background note, not Kodi's busy spinner: two spinners at once make Kodi quit.
    note = xbmcgui.DialogProgressBG()
    note.create('SiloKodiConnect', kodi.string(30175))
    try:
        found = discovery.servers()
    finally:
        note.close()
    choice = choose_server(found)
    if choice is None:
        return False
    client = SiloClient(store, kodi.version(), xbmc.getInfoLabel('System.FriendlyName'), can_refresh=False)
    if choice == 'phone':
        return sign_in_with_phone(store, client)
    server = found[choice][1] if choice != 'type' else ask_server(store)
    if not server:
        return False
    store.update(server_url=server)
    try:
        request = client.device_start()
    except (ApiError, requests.RequestException) as error:
        xbmcgui.Dialog().ok('SiloKodiConnect', kodi.string(30101) % (server, error))
        return False
    if not wait_for_approval(client, request):
        return False
    return choose_profile(store, client)


class PairingWindow(xbmcgui.WindowDialog):
    """While waiting for the phone: what to do, then how it's going. Plain
    controls (no skin XML), so it looks the same in any skin."""

    def __init__(self):
        xbmcgui.WindowDialog.__init__(self)
        self.closed = False
        # font13 and font45 are Estuary's names, which Kodi skins (Skyline too) keep.
        self.addControl(xbmcgui.ControlImage(0, 0, 1280, 720, 'colors/white.png', colorDiffuse='F20D0F12'))
        self.addControl(xbmcgui.ControlLabel(160, 150, 960, 60, kodi.string(30170), font='font45'))
        steps = xbmcgui.ControlTextBox(160, 240, 960, 260, font='font13', textColor='FFC7C7C7')
        self.addControl(steps)
        steps.setText(kodi.string(30171))
        self.status = xbmcgui.ControlLabel(160, 520, 960, 60, '', font='font13', textColor='FFEDEDED')
        self.addControl(self.status)

    def set_status(self, text):
        self.status.setLabel(text)

    def onAction(self, action):
        if action.getId() in (9, 10, 92):           # parent dir, previous menu, back
            self.closed = True
            self.close()


def sign_in_with_phone(store, client):
    """Wait for the Silo phone app to pair (pairing.py): it hands over the
    server, Kodi starts the device sign-in there, the phone approves. Then
    pick the profile here. Back cancels."""
    window = PairingWindow()
    statuses = {'checking': 30176, 'code': 30177, 'signed_in': 30178, 'failed': 30179}

    def on_status(event):
        kind, name = event[0], event[1]
        if kind == 'code':
            code = event[2]
            window.set_status(kodi.string(30177) % ('%s %s' % (code[:4], code[4:]) if len(code) == 8 else code))
        else:
            window.set_status(kodi.string(statuses[kind]) % name)

    def sign_in_at(address, on_code):
        store.update(server_url=address)
        try:
            request = client.device_start()
        except (ApiError, requests.RequestException):
            return 'unreachable'
        on_code(request.get('user_code') or '', request.get('match_code') or '')
        try:
            result = poll_for_approval(client, request, stop=lambda: window.closed)
        except (ApiError, requests.RequestException):
            return 'auth_failed'
        return None if result == 'approved' else ('expired' if result == 'expired' else 'denied')

    # Kodi's friendly name, as the phone lists it; "Kodi (192.168.1.248)" (Kodi's
    # default on Android) reads better as "Kodi".
    name = re.sub(r'\s*\(\d+(\.\d+){3}\)$', '', xbmc.getInfoLabel('System.FriendlyName') or '') or 'Kodi'
    session = pairing.Session(name, pairing.device_id(store),
                              lambda: pairing.Receiver(name, pairing.device_id(store), sign_in_at, on_status))
    if not session.address:
        xbmcgui.Dialog().ok('SiloKodiConnect', kodi.string(30174))
        return False
    try:
        session.start()
    except OSError as error:
        kodi.log_warning('Pairing could not start: %s' % error)
        xbmcgui.Dialog().ok('SiloKodiConnect', kodi.string(30174))
        return False
    window.show()
    monitor = xbmc.Monitor()
    try:
        while not session.signed_in and not window.closed and not monitor.waitForAbort(0.25):
            pass
        if session.signed_in:
            monitor.waitForAbort(1.5)               # "Signed in" on screen a moment
    finally:
        session.stop()
        window.close()
    return bool(session.signed_in) and choose_profile(store, client)


def poll_for_approval(client, request, stop=lambda: False, monitor=None):
    """Poll Silo until the device sign-in is approved (tokens saved), turned
    down or expired, or stop() says to give up (the request is then
    cancelled). Returns 'approved', 'denied', 'expired' or 'canceled'."""
    monitor = monitor or xbmc.Monitor()
    deadline = time.time() + (request.get('expires_in') or 900)
    interval = request.get('interval') or 5
    while time.time() < deadline:
        if stop() or monitor.waitForAbort(interval) or stop():
            try:
                client.device_cancel(request['device_code'])
            except (ApiError, requests.RequestException):
                pass
            return 'canceled'
        try:
            answer = client.device_poll(request['device_code'])
        except ApiError as error:
            if error.status == 429:
                interval = min(interval * 2, 30)
                continue
            raise
        status = answer.get('status')
        if answer.get('expires_at'):
            deadline = _epoch(answer['expires_at']) or deadline
        interval = answer.get('poll_after') or interval
        if status == 'approved' and answer.get('tokens'):
            client.save_tokens(answer['tokens'])
            user = answer['tokens'].get('user') or {}
            client.store.update(user_id=user.get('id'), username=user.get('username'), role=user.get('role'))
            if answer.get('profile_id'):
                client.store.update(profile_id=answer['profile_id'], profile_token=answer.get('profile_token'))
            return 'approved'
        if status in ('denied', 'canceled', 'consumed', 'expired'):
            return 'expired' if status in ('expired', 'consumed') else 'denied'
    return 'expired'


def wait_for_approval(client, request):
    """poll_for_approval with Kodi's dialog showing the code."""
    code = request['user_code']
    shown = '%s-%s' % (code[:4], code[4:]) if len(code) == 8 and code.isdigit() else code
    text = kodi.string(30102) % (request.get('verification_uri') or client.server_url + '/activate', shown)
    dialog = xbmcgui.DialogProgress()
    dialog.create('SiloKodiConnect', text)
    started, length = time.time(), request.get('expires_in') or 900

    def stop():
        dialog.update(max(0, min(100, int(100 * (1 - (time.time() - started) / length)))), text)
        return dialog.iscanceled()
    try:
        result = poll_for_approval(client, request, stop)
    finally:
        dialog.close()
    if result in ('denied', 'expired'):
        xbmcgui.Dialog().ok('SiloKodiConnect', kodi.string(30103))
    return result == 'approved'


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
