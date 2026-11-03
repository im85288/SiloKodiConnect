# -*- coding: utf-8 -*-
"""The service's own running: its task queue, its main loop's second, a
stopped player, the relay's early port and the commands the plugin sends."""
import queue
import socket
import threading
import time

import pytest

from resources.lib.silokodi import commands, kodi, quickstart
from resources.lib.silokodi import service as service_module
from resources.lib.silokodi.api import ApiError


def bare_service(**attrs):
    service = service_module.Service.__new__(service_module.Service)
    service.tasks = queue.PriorityQueue()
    service.task_number = iter(range(10 ** 6))
    service.queued, service.queued_lock = set(), threading.Lock()
    service.tick_error = ('', 0)
    service.__dict__.update(attrs)
    return service


@pytest.fixture
def logged(monkeypatch):
    lines = []
    for name in ('log', 'log_warning', 'log_error'):
        monkeypatch.setattr(kodi, name, lambda message, *args, name=name: lines.append((name, message)))
    return lines


# ----- the task queue -----

def test_a_task_already_waiting_is_not_queued_again():
    service = bare_service()
    for task in ('progress', 'progress', ('state', 'ep-1'), ('state', 'ep-1'), ('state', 'ep-2'), 'homecards'):
        service.enqueue(task)
    waiting = [service.tasks.get()[2] for _ in range(service.tasks.qsize())]
    assert waiting == ['homecards', 'progress', ('state', 'ep-1'), ('state', 'ep-2')]  # urgent first


def test_a_task_running_may_queue_again(monkeypatch):
    service = bare_service(monitor=type('Monitor', (), {'abortRequested': lambda self: False})())
    ran = []

    def do(task):
        ran.append(task)
        if len(ran) == 1:
            service.enqueue('progress')  # changed again while it ran: runs again after
    service.do = do
    service.syncer = type('Syncer', (), {'art_changed': set(), 'changed': False})()
    service.enqueue('progress')
    service.tasks.put((2, 10 ** 7, None))  # then stop
    service.work()
    assert ran == ['progress', 'progress']


# ----- the main loop's second -----

class Events(object):
    def due(self):
        return []


def ticking_service(**attrs):
    calls = []
    store = type('Store', (), {'signed_in': True, 'changed_on_disk': lambda self: False})()
    service = bare_service(
        store=store, next_sync=0, next_progress=time.time() + 60, listener=None, native_recheck=False,
        layout={}, home_cards_due=0, next_recheck=None, next_save=time.time() + 60, personal_due=False,
        deferred=[], library_update_pending=False, library_events=Events(), item_events=Events(),
        personal_events=type('Personal', (), {'due': lambda self: False})(),
        details=type('Details', (), {'dirty': False})(),
        player=type('Player', (), {'tick': lambda self: None})(),
        theme=type('Theme', (), {'tick': lambda self: None})())
    for name in ('sync_keymap', 'publish_signed_in', 'start_listener', 'note_viewed_page', 'note_refresh',
                 'end_loading'):
        setattr(service, name, lambda name=name: calls.append(name))
    service.stop_listener = lambda: calls.append('stop_listener')
    service.playing = lambda: False
    service.__dict__.update(attrs)
    return service, calls


def test_a_signed_out_second_only_stops_listening(monkeypatch):
    service, calls = ticking_service()
    service.store.signed_in = False
    service.tick(1)
    assert 'stop_listener' in calls and 'start_listener' not in calls
    assert service.tasks.empty()


def test_a_second_queues_the_sync_when_due_once(monkeypatch):
    monkeypatch.setattr(kodi, 'setting_int', lambda key: 60)
    service, calls = ticking_service()
    service.tick(1)
    assert service.tasks.get()[2] == 'sync' and service.tasks.empty()
    assert service.next_sync > time.time() + 59 * 60
    service.tick(2)
    assert service.tasks.empty()  # not due again for an hour


def test_tasks_held_for_playback_queue_once_it_stops(monkeypatch):
    service, calls = ticking_service(next_sync=time.time() + 3600, deferred=['sync', ('library', '2')])
    service.playing = lambda: True
    service.tick(1)
    assert service.tasks.empty() and service.deferred == ['sync', ('library', '2')]
    service.playing = lambda: False
    service.tick(2)
    assert [service.tasks.get()[2] for _ in range(2)] == ['sync', ('library', '2')] and service.deferred == []


def test_a_failing_second_is_logged_and_the_loop_goes_on(monkeypatch, logged):
    service, calls = ticking_service()

    def broken():
        raise ValueError('bad layout')
    service.note_refresh = broken
    for second in range(3):
        try:
            service.tick(second)
        except Exception as error:
            service.tick_failed(error)
    # Logged once (not every second) while it fails the same way.
    assert [line for line in logged if line[0] == 'log_error'] == [('log_error', 'Main loop: ValueError: bad layout')]
    service.tick_error = (service.tick_error[0], time.time() - 61)
    service.tick_failed(ValueError('bad layout'))
    assert len([line for line in logged if line[0] == 'log_error']) == 2  # and again a minute on


# ----- a stopped player -----

def test_a_stopped_episode_refreshes_continue_watching_even_with_the_worker_busy(monkeypatch):
    monkeypatch.setattr(kodi, 'clear_property', lambda key: None)
    monkeypatch.setattr(kodi, 'get_property', lambda key: '')
    published = threading.Event()
    session = type('Session', (), {'content_id': 'ep-7'})()
    service = bare_service(recently_played={})
    service.playback = type('Playback', (), {'on_stopped': lambda self, seconds: session})()
    service.publish_home_cards_safely = published.set
    player = service_module.Player.__new__(service_module.Player)
    player.service, player.last_time = service, 1234.0
    player._ended()
    assert published.wait(2)  # its own thread: nothing queued holds it
    assert service.tasks.get()[2] == ('state', 'ep-7') and 'ep-7' in service.recently_played


def test_a_trailer_or_nothing_stopping_publishes_nothing(monkeypatch):
    monkeypatch.setattr(kodi, 'clear_property', lambda key: None)
    monkeypatch.setattr(kodi, 'get_property', lambda key: '')
    service = bare_service(recently_played={})
    service.playback = type('Playback', (), {'on_stopped': lambda self, seconds: None})()
    service.publish_home_cards_safely = lambda: pytest.fail('nothing of Silo played')
    player = service_module.Player.__new__(service_module.Player)
    player.service, player.last_time = service, None
    player._ended()
    assert service.tasks.empty()


def test_home_cards_failing_off_the_worker_are_logged(logged):
    service = bare_service()

    def broken():
        raise KeyError('row')
    service.publish_home_cards = broken
    service.publish_home_cards_safely()
    assert logged == [('log_warning', "Could not publish Home's cards: 'row'")]


# ----- the relay's early port -----

def test_the_early_port_is_skipped_when_something_else_has_it():
    taken = socket.socket()
    taken.bind(('127.0.0.1', 0))
    taken.listen(1)
    try:
        port = taken.getsockname()[1]
        assert quickstart.listen_early(port) is None
        assert port not in quickstart.LISTENING  # the relay binds (and reports) it itself
    finally:
        taken.close()


def test_the_relay_port_is_the_settings_or_silos_default():
    class Addon(object):
        def __init__(self, value):
            self.value = value

        def getSetting(self, key):
            return self.value
    assert quickstart.relay_port(Addon('')) == quickstart.RELAY_PORT
    assert quickstart.relay_port(Addon('58000')) == 58000
    assert quickstart.relay_port(Addon('not a port')) == quickstart.RELAY_PORT


# ----- the commands the plugin sends -----

class Client(object):
    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name,) + args)
            if self.fail:
                raise self.fail
            return {'in_watchlist': True, 'rating': 4, 'not_interested': False}.get(name)
        return call


def commanding(client=None, **attrs):
    service = bare_service(client=client or Client())
    service.personal = type('Personal', (), {'refresh_soon': lambda self, paths=None: service.__dict__
                                             .setdefault('refreshed', []).append(paths)})()
    service.home_cards_soon = lambda delay=2: service.__dict__.setdefault('cards_in', []).append(delay)
    service.__dict__.update(attrs)
    return service


def test_the_watchlist_command_adds_and_removes_and_refreshes_the_list():
    service = commanding()
    assert commands.handle(service, 'watchlist', {'id': 'm1', 'add': '1'}) == {'watchlist': 'true'}
    assert commands.handle(service, 'watchlist', {'id': 'm1'}) == {'watchlist': 'false'}
    assert service.client.calls == [('add_to_watchlist', 'm1'), ('remove_from_watchlist', 'm1')]
    assert service.refreshed == [['/watchlist'], ['/watchlist']]


def test_a_command_silo_refuses_answers_its_error():
    service = commanding(Client(fail=ApiError(503, 'unavailable', 'Silo is down')))
    down = {'error': 'HTTP 503 unavailable: Silo is down'}
    assert commands.handle(service, 'watchlist', {'id': 'm1', 'add': '1'}) == down
    assert not service.__dict__.get('refreshed')
    assert commands.handle(service, 'not_interested', {'id': 'm1', 'on': '1'}) == down


def test_rating_is_kept_to_zero_to_five_stars():
    service = commanding()
    assert commands.handle(service, 'rate', {'id': 's1', 'stars': '9'}) == {'rating': '5'}
    assert commands.handle(service, 'rate', {'id': 's1', 'stars': '-2'}) == {'rating': '0'}
    assert 'error' in commands.handle(service, 'rate', {'id': 's1', 'stars': 'lots'})
    assert service.client.calls == [('rate', 's1', 5), ('rate', 's1', 0)]


def test_an_episodes_card_menu_is_about_its_show():
    rows = {('episode', '42'): {'kind': 'episode', 'content_id': 'ep-1', 'parent_id': 'show-1'}}
    service = commanding(db=type('Db', (), {'by_kodi': lambda self, kind, dbid: rows.get((kind, dbid))})())
    answer = commands.handle(service, 'card', {'type': 'episode', 'dbid': '42'})
    assert answer == {'content_id': 'show-1', 'watchlist': True, 'rating': 4, 'not_interested': False}


def test_a_card_not_from_silo_has_no_menu(monkeypatch):
    monkeypatch.setattr(kodi, 'string', lambda string_id: 'Not a Silo title')
    service = commanding(db=type('Db', (), {'by_kodi': lambda self, kind, dbid: None})())
    assert commands.handle(service, 'card', {'type': 'movie', 'dbid': '9'}) == {'error': 'Not a Silo title'}


def test_not_interested_refreshes_homes_cards_once_silo_has_rebuilt_them():
    service = commanding()
    assert commands.handle(service, 'not_interested', {'id': 'm1', 'on': '1'}) == {'not_interested': True}
    assert service.cards_in == [20]


def test_home_sections_say_which_are_ranked_and_wide():
    service = commanding(ranked={'7'})
    service.home_sections = lambda: [{'id': 7, 'type': 'top10'}, {'id': 8, 'type': 'continue_watching'}]
    sections = commands.handle(service, 'sections', {})['sections']
    assert [section['ranked'] for section in sections] == [True, False]
    assert sections[1]['wide'] is (('continue_watching') in commands.WIDE_SECTIONS)


def test_sync_commands_queue_and_others_are_unknown():
    service = commanding(playing=lambda: False)
    assert commands.handle(service, 'sync', {}) == {'ok': True}
    assert commands.handle(service, 'fullsync', {}) == {'waiting': False}
    assert commands.handle(service, 'nonsense', {}) == {'error': 'unknown command'}
    assert [service.tasks.get()[2] for _ in range(2)] == ['sync', 'fullsync']
