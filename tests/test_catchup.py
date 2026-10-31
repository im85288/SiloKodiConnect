# -*- coding: utf-8 -*-
from resources.lib.silokodi import catchup

LINE = ('#EXTINF:-1 tvg-id="SkySports.uk" catchup="default" catchup-days="2" '
        'catchup-source="https://host.example/live/user1/pass2/369613.ts?utc={utc}&lutc=${end}",Sky Sports')


def test_a_live_address_catch_up_becomes_the_providers_archive():
    text, count = catchup.rewrite(LINE + '\nhttps://host.example/live/user1/pass2/369613.ts\n')
    assert count == 1
    assert ('catchup-source="https://host.example/streaming/timeshift.php?username=user1&password=pass2'
            '&stream=369613&start={Y}-{m}-{d}:{H}-{M}&duration={duration:60}"') in text
    assert text.endswith('\nhttps://host.example/live/user1/pass2/369613.ts\n')   # the live address stays
    assert catchup.rewrite(text) == (text, 0)                                     # once only


def test_live_stream_modes_become_timeshift_so_any_channel_pauses():
    text = '#KODIPROP:inputstream.ffmpegdirect.stream_mode=live\n#EXTINF:-1,Sky News\nhttps://host.example/x.ts\n'
    rewritten, count = catchup.rewrite(text)
    assert count == 1 and 'stream_mode=timeshift' in rewritten and 'stream_mode=live' not in rewritten
    assert catchup.rewrite(rewritten) == (rewritten, 0)


def test_the_playlists_come_from_iptv_simples_local_instances(tmp_path):
    folder = tmp_path / 'pvr.iptvsimple'
    folder.mkdir()
    (folder / 'instance-settings-1.xml').write_text(
        '<settings><setting id="m3uPathType" default="true">1</setting><setting id="m3uPath">/x.m3u</setting></settings>')
    (folder / 'instance-settings-31.xml').write_text(
        '<settings><setting id="m3uPathType">0</setting><setting id="m3uPath">/data/live.m3u8</setting></settings>')
    assert catchup.playlists(str(tmp_path)) == ['/data/live.m3u8']
