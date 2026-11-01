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


class Silo(object):
    """Silo's theme for a show, counting the downloads."""
    def __init__(self, has=True):
        self.has, self.asked = has, 0

    def __call__(self):
        self.asked += 1
        return (b'ID3 tune', '.mp3') if self.has else None


def themes(monkeypatch, tmp_path, other=False, page=True):
    state = {'page': page}
    monkeypatch.setattr(theme.xbmc, 'getCondVisibility', lambda condition: state['page'])
    player = theme.ThemePlayer(lambda: True, str(tmp_path / 'themes'))
    player.player = Player(other)
    return player, state


def test_a_shows_theme_plays_from_the_cache_while_its_page_is_open(monkeypatch, tmp_path):
    player, state = themes(monkeypatch, tmp_path)
    player._start('series-1', Silo())
    cached = str(tmp_path / 'themes' / 'silo-series-1.mp3')
    assert player.player.file == cached and open(cached, 'rb').read() == b'ID3 tune'
    player.tick()
    assert player.player.file  # the page is still open
    state['page'] = False
    player.tick()
    assert player.player.file is None and player.url is None


def test_a_theme_is_downloaded_once_and_a_show_without_one_asked_once(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path)
    tune, none = Silo(), Silo(has=False)
    player._start('series-1', tune)
    player.stop()
    player._start('series-1', tune)
    player._start('series-2', none)
    player._start('series-2', none)
    assert (tune.asked, none.asked) == (1, 1)


def test_a_failed_download_is_tried_again(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path)

    def broken():
        raise RuntimeError('grant expired')
    player._start('series-1', broken)
    player._start('series-1', Silo())
    assert player.player.file == str(tmp_path / 'themes' / 'silo-series-1.mp3')


def test_a_theme_never_plays_over_the_viewers_own_playback(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path, other=True)
    player._start('series-1', Silo())
    assert player.player.file is None


def test_a_show_without_a_theme_stops_the_last_ones(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path)
    player._start('series-1', Silo())
    player._start('series-2', Silo(has=False))
    assert player.player.file is None


def test_no_theme_when_the_page_never_opens(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path, page=False)
    monkeypatch.setattr(theme, 'PAGE_WAIT', 0)
    player._start('series-1', Silo())
    assert player.player.file is None


def test_the_cache_keeps_the_most_recent_themes(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path)
    monkeypatch.setattr(theme, 'KEEP', 3)
    for n in range(5):
        player.fetch('s%d' % n, Silo())
        os.utime(os.path.join(player.folder, 'silo-s%d.mp3' % n), (n, n))
    player.prune()
    assert sorted(os.listdir(player.folder)) == ['silo-s2.mp3', 'silo-s3.mp3', 'silo-s4.mp3']


def test_the_setting_off_offers_nothing(monkeypatch, tmp_path):
    player, _ = themes(monkeypatch, tmp_path)
    player.enabled = lambda: False
    tune = Silo()
    player.offer('series-1', tune)
    assert tune.asked == 0


def test_an_episode_page_plays_its_shows_theme():
    from resources.lib.silokodi import service as service_module
    offered = []
    host = service_module.Service.__new__(service_module.Service)
    host.theme = type('T', (), {'offer': lambda self, *args: offered.append(args)})()
    host.client = None
    host.offer_theme({'type': 'episode', 'series_id': 'series-tvdb-399917', 'series_title': 'The Terminal List'})
    host.offer_theme({'type': 'series', 'content_id': 'series-tvdb-1', 'title': 'Show'})
    host.offer_theme({'type': 'movie', 'content_id': 'movie-1'})
    assert [(a[0], a[2], a[3]) for a in offered] == [('series-tvdb-399917', 'The Terminal List', theme.EPISODE_RAIL),
                                                    ('series-tvdb-1', 'Show', theme.SHOW_RAIL)]
