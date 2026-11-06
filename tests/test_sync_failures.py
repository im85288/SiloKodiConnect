# -*- coding: utf-8 -*-
"""Telling the user when syncs keep failing."""
import requests

from resources.lib.silokodi.api import ApiError
from resources.lib.silokodi.service import SYNC_NOTICE_EVERY, sync_failure_notice

REFUSED = ApiError(422, 'validation_failed', 'query.group: expected value to be one of "work"')


def fail(state, error=REFUSED, now=1000000):
    return sync_failure_notice(state, error, now)


def test_tells_the_user_at_the_third_failure_in_a_row():
    state, told = fail(None)
    assert not told
    state, told = fail(state)
    assert not told
    state, told = fail(state)
    assert told


def test_tells_the_user_at_most_once_a_day():
    state = {'count': 2}
    state, told = fail(state, now=1000000)
    assert told
    state, told = fail(state, now=1000000 + SYNC_NOTICE_EVERY - 1)
    assert not told
    state, told = fail(state, now=1000000 + SYNC_NOTICE_EVERY)
    assert told


def test_a_crash_counts():
    state, _ = fail({'count': 2}, error=ValueError('bad'))
    assert state['count'] == 3


def test_silo_or_the_network_being_down_doesnt_count():
    for error in (requests.ConnectionError('offline'), ApiError(503, 'unavailable', 'starting up')):
        state, told = fail({'count': 2}, error=error)
        assert state == {'count': 2} and not told
