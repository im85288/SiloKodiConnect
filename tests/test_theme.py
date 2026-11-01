# -*- coding: utf-8 -*-
"""A show's theme tune on its page (theme.py)."""
import os

from resources.lib.silokodi import theme


class Player(object):
    """Kodi's player: what it plays, and whether something else is playing."""
    def __init__(self, other=False):
        self.file, self.other = None, other

    def play(self, url, item, windowed):
        self.file = url

    def stop(self):
        self.file = None

    def isPlaying(self):
        return self.other or bool(self.file)

    def isPlayingAudio(self):
        return bool(self.file)

    def getPlayingFile(self):
        return self.file or ''


class Session(object):
    def __init__(self, found):
        self.found, self.asked = found, []

    def get(self, url, **kwargs):
        self.asked.append(url)
        ok = any(url.endswith('/%s.mp3' % i) for i in self.found)
        return type('R', (), {'status_code': 200 if ok else 404, 'content': b'ID3 tune' if ok else b''})()


def themes(monkeypatch, tmp_path, found=('399917',), other=False, page=True):
    state = {'page': page}
    monkeypatch.setattr(theme.xbmc, 'getCondVisibility', lambda condition: state['page'])
    player = theme.ThemePlayer(lambda: True, str(tmp_path / 'themes'), session=Session(found))
    player.player = Player(other)
    return player, state


def test_a_shows_theme_plays_from_the_cache_while_its_page_is_open(monkeypatch, tmp_path):
    player, state = themes(monkeypatch, tmp_path)
    player._start('399917')
    cached = str(tmp_path / 'themes' / '399917.mp3')
    assert player.player.file == cached and open(cached, 'rb').read() == b'ID3 tune'
    player.tick()
    assert player.player.file  # the page is still open
    state['page'] = False
    player.tick()
    assert player.player.file is None and player.url is None


def test_a_theme_is_fetched_once_and_a_missing_one_asked_once(monkeypatch, tmp_path):
    player, state = themes(monkeypatch, tmp_path)
    player._start('399917')
    player.stop()
    player._start('399917')
    player._start('1')
    player._start('1')
    assert player.session.asked == ['https://tvthemes.plexapp.com/399917.mp3', 'https://tvthemes.plexapp.com/1.mp3']


def test_a_theme_never_plays_over_the_viewers_own_playback(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path, other=True)
    player._start('399917')
    assert player.player.file is None


def test_a_show_without_a_theme_stops_the_last_ones(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path, found=('399917',))
    player._start('399917')
    player._start('463433')
    assert player.player.file is None


def test_no_theme_when_the_page_never_opens(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path, page=False)
    monkeypatch.setattr(theme, 'PAGE_WAIT', 0)
    player._start('399917')
    assert player.player.file is None


def test_the_cache_keeps_the_most_recent_themes(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path, found=[str(n) for n in range(5)])
    monkeypatch.setattr(theme, 'KEEP', 3)
    for n in range(5):
        player.fetch(str(n))
        os.utime(os.path.join(player.folder, '%d.mp3' % n), (n, n))
    player.prune()
    assert sorted(os.listdir(player.folder)) == ['2.mp3', '3.mp3', '4.mp3']


def test_the_setting_off_offers_nothing(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path)
    player.enabled = lambda: False
    player.offer('399917')
    assert player.session.asked == []


def test_silos_own_theme_comes_before_plexs(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path)
    player._start('399917', ('t1', lambda: (b'own tune', '.m4a')))
    assert player.player.file == str(tmp_path / 'themes' / 'silo-t1.m4a') and player.session.asked == []


def test_plexs_theme_when_silos_fails(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path)

    def broken():
        raise RuntimeError('grant expired')
    player._start('399917', ('t1', broken))
    assert player.player.file == str(tmp_path / 'themes' / '399917.mp3')


def test_an_episode_page_plays_its_shows_theme():
    from resources.lib.silokodi import service as service_module
    offered = []
    host = service_module.Service.__new__(service_module.Service)
    host.theme = type('T', (), {'offer': lambda self, *args: offered.append(args)})()
    host.client = None
    host.offer_theme({'type': 'episode', 'series_id': 'series-tvdb-399917', 'series_title': 'The Terminal List'})
    host.offer_theme({'type': 'series', 'content_id': 'series-tvdb-1', 'tvdb_id': '1', 'title': 'Show'})
    host.offer_theme({'type': 'movie', 'content_id': 'movie-1'})
    assert [(a[0], a[1][0], a[2], a[3]) for a in offered] == [
        ('399917', 'silo-series-tvdb-399917', 'The Terminal List', theme.EPISODE_RAIL),
        ('1', 'silo-series-tvdb-1', 'Show', theme.SHOW_RAIL)]
