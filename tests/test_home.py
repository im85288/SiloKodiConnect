# -*- coding: utf-8 -*-
"""Home's cards as the service builds them (ranks, places, removable cards,
Next Episode), the widget answer the plugin lists, the commands a skin's
settings send, and the plugin's and Home menu key's side."""
from resources.lib.silokodi import context, plugin
from resources.lib.silokodi import rows as home_cards
from resources.lib.silokodi import service as service_module

SHOW = {'title': 'MobLand', 'episode': 10, 'watchedepisodes': 3, 'art': {}}
EPISODE = {'title': 'Birdland', 'season': 2, 'episode': 5, 'showtitle': 'MobLand', 'art': {}}


class Details(object):
    def get(self, kind, kodi_id):
        return {'details': dict(EPISODE if kind == 'episode' else SHOW)}


def service(items, title='', ranked=()):
    """A Service with just what page_cards and the widget command use: the
    row's items from Silo, its title as Silo last sent it, the ranked rows."""
    host = service_module.Service.__new__(service_module.Service)
    host.details = Details()
    host.ranked = set(ranked)
    host.row_titles = {}
    host.queued = []

    def widget_items(section, library_id=None):
        host.row_titles[(library_id or None, str(section))] = title
        return [dict(item) for item in items]
    host.widget_items = widget_items
    host.series_art = lambda source: ''
    host.enqueue = host.queued.append
    host.home_rows = []
    return host


def shows(count):
    return [{'type': 'tvshow', 'kodi_id': n} for n in range(1, count + 1)]


def test_a_chart_rows_top_ten_have_places_and_a_ranked_row_numbers_every_card():
    host = service(shows(12), 'IMDb Popular Shows', ranked={'7'})
    cards = host.page_cards('home', '7', None)
    assert [c.get('rank') for c in cards] == [str(n) for n in range(1, 13)]
    assert [c.get('place') for c in cards] == [str(n) for n in range(1, 11)] + [None, None]


def test_places_dont_need_the_row_numbered():
    cards = service(shows(3), 'Trending This Week Shows').page_cards('home', '7', None)
    assert [c.get('place') for c in cards] == ['1', '2', '3'] and not any(c.get('rank') for c in cards)


def test_other_rows_have_neither():
    cards = service(shows(3), 'Family Halloween', ranked={'8'}).page_cards('home', '7', None)
    assert not any(c.get('rank') or c.get('place') for c in cards)


def test_a_library_rows_cards_are_never_ranked():
    cards = service(shows(3), 'IMDb Popular Shows', ranked={'7'}).page_cards('movies', '7', '2')
    assert not any(c.get('rank') or c.get('place') for c in cards)


def test_continue_watching_cards_are_removable_and_unstarted_episodes_say_next_episode():
    items = [{'type': 'episode', 'kodi_id': 1, 'dismiss': True, 'next': True},
             {'type': 'tvshow', 'kodi_id': 2}]
    cards = service(items, 'Continue Watching').page_cards('home', '1', None)
    assert cards[0]['dismiss'] == '1' and cards[0]['left'] == home_cards.NEXT_EPISODE
    assert not cards[1].get('dismiss') and not cards[1].get('left')


def test_the_widget_answer_carries_ranks_places_and_the_title_silo_just_sent():
    host = service(shows(11), 'IMDb Popular Shows', ranked={'7'})
    host.with_details = lambda items: items
    answer = host.command('widget', {'section': '7'})
    assert answer['title'] == 'IMDb Popular Shows'
    assert [i.get('rank') for i in answer['items']] == [str(n) for n in range(1, 12)]
    assert answer['items'][9].get('place') == '10' and 'place' not in answer['items'][10]


def test_a_library_widget_is_never_ranked():
    host = service(shows(3), 'IMDb Popular Shows', ranked={'7'})
    host.with_details = lambda items: items
    answer = host.command('widget', {'section': '7', 'library': '2'})
    assert not any(i.get('rank') or i.get('place') for i in answer['items'])


def test_sections_say_which_rows_are_ranked_and_which_are_wide():
    host = service([])
    host.ranked = {'7'}
    host.home_sections = lambda: [{'id': '7', 'title': 'IMDb Popular Shows', 'type': 'collection'},
                                  {'id': '1', 'title': 'Continue Watching', 'type': 'continue_watching'}]
    sections = host.command('sections', {})['sections']
    assert [(s['ranked'], s['wide']) for s in sections] == [(True, False), (False, True)]


def test_choosing_top_10_rows_republishes_the_layout():
    host = service([])
    host.command('top10', {})
    assert host.queued == ['layout']


class Item(object):
    """A ListItem that remembers its properties."""
    def __init__(self):
        self.properties = {}

    def setProperty(self, key, value):
        self.properties[key] = value

    def getProperty(self, key):
        return self.properties.get(key, '')

    def getVideoInfoTag(self):
        return type('Tag', (), {'getTVShowTitle': lambda self: ''})()


def test_the_plugin_puts_the_marks_on_a_lazy_rows_cards(monkeypatch):
    answer = {'title': 'Continue Watching', 'items': [
        {'type': 'episode', 'kodi_id': 1, 'dismiss': True, 'next': True, 'rank': '1', 'place': '1'},
        {'type': 'tvshow', 'kodi_id': 2}]}
    added = []
    monkeypatch.setattr(plugin, 'ask_service', lambda path, **query: answer)
    monkeypatch.setattr(plugin, 'library_item', lambda kind, kodi_id, shows, cached: ('url', Item(), False))
    monkeypatch.setattr(plugin.xbmcplugin, 'addDirectoryItem', lambda handle, url, item, isFolder: added.append(item),
                        raising=False)
    monkeypatch.setattr(plugin.xbmcplugin, 'setContent', lambda *a: None, raising=False)
    plugin.run(['plugin://x/', '1', '?mode=widget&section=1'])
    first, second = (item.properties for item in added)
    assert first['silo.dismiss'] == '1' and first['silo.left'] == home_cards.NEXT_EPISODE
    assert (first['silo.rank'], first['silo.place']) == ('1', '1')
    assert not any(key in second for key in ('silo.dismiss', 'silo.left', 'silo.rank', 'silo.place'))


def test_the_plugin_sends_the_relays_token(monkeypatch):
    asked = []
    monkeypatch.setattr(plugin.kodi, 'get_property', lambda key: 'secret' if key == 'relay_token' else '')

    class Response(object):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{}'
    monkeypatch.setattr(plugin, 'urlopen', lambda url, timeout: asked.append(url) or Response())
    plugin.ask_service('command/sync', timeout=1)
    assert asked == ['http://127.0.0.1:%d/command/sync?token=secret' % plugin.relay_port()]


def test_the_home_menu_key_offers_remove_on_a_continue_watching_card(monkeypatch):
    labels = {'ListItem.Property(home.dismiss)': '1', 'ListItem.Property(home.type)': 'episode',
              'ListItem.Property(home.dbid)': '42'}
    asked, ran = [], []
    monkeypatch.setattr(context.xbmc, 'getInfoLabel', lambda label: labels.get(label, ''))
    monkeypatch.setattr(context.xbmc, 'executebuiltin', ran.append)
    monkeypatch.setattr(context.xbmcgui, 'Dialog', lambda: type('D', (), {'contextmenu': lambda self, items: 0})())
    monkeypatch.setattr(context, 'ask_service', lambda path, **query: asked.append((path, query)) or {})
    context.home_menu()
    assert asked == [('command/dismiss', {'timeout': 20, 'type': 'episode', 'dbid': '42'})] and ran == []


def test_the_home_menu_key_opens_kodis_menu_elsewhere(monkeypatch):
    ran = []
    monkeypatch.setattr(context.xbmc, 'getInfoLabel', lambda label: '')
    monkeypatch.setattr(context.xbmc, 'executebuiltin', ran.append)
    monkeypatch.setattr(context, 'ask_service', lambda *a, **k: (_ for _ in ()).throw(AssertionError('asked')))
    context.home_menu()
    assert ran == ['Action(ContextMenu)']
