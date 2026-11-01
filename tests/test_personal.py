import time

from resources.lib.silokodi import personal
from resources.lib.silokodi.personal import PersonalLists


class FakeClient(object):
    """History as Silo pages it: a page of watches holds few titles."""

    def __init__(self, titles, per_page=3):
        self.titles = titles
        self.per_page = per_page
        self.calls = []

    def get(self, path, limit=None, cursor=None):
        self.calls.append((path, limit, cursor))
        start = int(cursor or 0)
        items = [{'content_id': title} for title in self.titles[start:start + self.per_page]]
        more = start + self.per_page < len(self.titles)
        return {'items': items, 'page': {'has_more': more, 'next_cursor': str(start + self.per_page) if more else ''}}


class Clock(object):
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def lists(client, profile='p1', clock=None):
    profiles = {'id': profile}
    return PersonalLists(client, lambda: profiles['id'], clock=clock or Clock()), profiles


def test_a_list_is_fetched_in_silos_largest_pages_and_kept():
    client = FakeClient(['a', 'b', 'c', 'd', 'e', 'f', 'g'])
    kept, _ = lists(client)
    assert [card['content_id'] for card in kept.cards('/history')] == list('abcdefg')
    assert len(client.calls) == 3 and all(limit == personal.PAGE for _, limit, _ in client.calls)
    kept.cards('/history')
    assert len(client.calls) == 3  # the second visit asks Silo nothing


def test_another_profile_fetches_its_own_list():
    client = FakeClient(['a'])
    kept, profiles = lists(client)
    kept.cards('/favorites')
    profiles['id'] = 'p2'
    kept.cards('/favorites')
    assert len(client.calls) == 2


def test_an_old_copy_shows_while_it_is_fetched_again():
    client = FakeClient(['a'])
    clock = Clock()
    kept, _ = lists(client, clock=clock)
    kept.cards('/watchlist')
    client.titles = ['b']
    clock.now += personal.FRESH + 1
    assert [card['content_id'] for card in kept.cards('/watchlist')] == ['a']
    for _ in range(50):  # the background fetch
        if kept.cards('/watchlist')[0]['content_id'] == 'b':
            break
        time.sleep(0.01)
    assert kept.cards('/watchlist')[0]['content_id'] == 'b'


def test_refresh_soon_fetches_every_list_once():
    client = FakeClient(['a'])
    kept, _ = lists(client)
    kept.refresh_soon().join(5)
    assert sorted(path for path, _, _ in client.calls) == sorted(personal.PATHS)
    kept.cards('/history')
    assert len(client.calls) == 3


def test_a_long_list_stops_at_the_limit():
    client = FakeClient([str(n) for n in range(personal.LIMIT + 50)], per_page=personal.PAGE)
    kept, _ = lists(client)
    assert len(kept.cards('/history')) == personal.LIMIT
