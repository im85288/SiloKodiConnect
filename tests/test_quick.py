# -*- coding: utf-8 -*-
from resources.lib.silokodi import channels, kodi, quick


def test_the_focused_channels_schedule_is_published_and_old_rows_cleared(monkeypatch):
    published = {}
    monkeypatch.setattr(kodi, 'set_property', lambda key, value: published.__setitem__(key, value))
    monkeypatch.setattr(kodi, 'jsonrpc', lambda method, **params: {'channels': [
        {'channelid': 7, 'channelnumber': 45, 'label': 'Sky Sports', 'hasarchive': True}]})
    rows = [{'header': 'Today', 'day': 0, 'today': True},
            {'title': 'Cricket', 'start': 0, 'end': 60, 'plot': '', 'date': None, 'day': 0,
             'broadcastid': 3, 'when': 'now', 'archive': False}]
    monkeypatch.setattr(channels, 'channel_schedule', lambda channel, now=None: rows)
    monkeypatch.setattr(channels, 'around_now', lambda found: found)
    monkeypatch.setattr(channels, 'day_label', lambda day: 'Fri, 30 Oct')
    guide = quick.QuickGuide(monitor=None)
    guide.rows = 4                       # an earlier channel had more rows
    monkeypatch.setattr(quick.xbmc, 'getRegion', lambda what: '%H:%M:%S', raising=False)
    guide.publish('45', 'Sky Sports')
    assert published['quick.for'] == '45' and published['quick.count'] == '2'
    assert published['quick.1.label'] == 'Today' and published['quick.1.silo.header'] == '1'
    assert published['quick.2.label'] == 'Cricket' and published['quick.2.silo.now'] == '1'
    assert published['quick.4.label'] == ''   # cleared
