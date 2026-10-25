# -*- coding: utf-8 -*-
import pytest

from resources.lib.silokodi import mapping
from resources.lib.silokodi.sync import Syncer
from resources.lib.silokodi.syncdb import SyncDB

from silo_fakes import FakeSilo, episode, movie, series

PORT = 57578


@pytest.fixture
def silo():
    fake = FakeSilo()
    fake.add_movie(movie('movie-1', 'Heat'))
    fake.add_movie(movie('movie-2', 'Thief', imdb_id='tt0083190', cast=[], crew=[]))
    fake.add_show(series('series-1'), {1: [episode('series-1', 1, 1, played=True),
                                           episode('series-1', 1, 2, position=600)],
                                       2: [episode('series-1', 2, 1)]})
    return fake


@pytest.fixture
def syncer(silo, open_kodi, tmp_path):
    return Syncer(silo, SyncDB(str(tmp_path)), open_kodi, PORT)


def test_first_sync_writes_movies_into_kodi(syncer, query):
    assert syncer.sync() is True
    rows = query('SELECT idMovie, c00, c14, c15, c06, c12, premiered, c22 FROM movie ORDER BY c00')
    assert [r[1] for r in rows] == ['Heat', 'Thief']
    heat = rows[0]
    assert heat[2] == 'Crime / Drama'
    assert heat[3] == 'Michael Mann' and heat[4] == 'Michael Mann'
    assert heat[5] == 'R' and heat[6] == '1995-12-15'
    assert heat[7] == mapping.MOVIES_PATH
    files = query('SELECT strFilename FROM files f JOIN movie m ON m.idFile = f.idFile WHERE m.c00 = ?', ('Heat',))
    assert files[0][0] == mapping.MOVIES_PATH + '?mode=play&id=movie-1&type=movie'
    # Library features skins rely on.
    assert query("SELECT type, value FROM uniqueid WHERE media_type = 'movie' AND media_id = ? ORDER BY type",
                 (heat[0],)) == [('imdb', 'tt0113277'), ('tmdb', '949')]
    assert query("SELECT rating, votes FROM rating WHERE media_id = ? AND media_type = 'movie'", (heat[0],)) \
        == [(8.3, 673852)]
    assert query("SELECT name FROM tag JOIN tag_link USING (tag_id) WHERE media_id = ? AND media_type = 'movie'",
                 (heat[0],)) == [('Movies',)]
    assert query('SELECT idMedia, itemType, idType FROM videoversion') != []


def test_artwork_uses_stable_local_urls(syncer, query):
    syncer.sync()
    art = dict(query("SELECT type, url FROM art WHERE media_type = 'movie' AND media_id = "
                     "(SELECT idMovie FROM movie WHERE c00 = 'Heat')"))
    assert art['poster'] == 'http://127.0.0.1:57578/art/movie-1/poster'
    assert art['fanart'] == 'http://127.0.0.1:57578/art/movie-1/fanart'
    # The signed Silo URL is kept in the add-on's own database for the relay.
    assert syncer.db.art('movie-1', 'poster') == '/api/v2/artwork/abc/w500.r1.webp?sig=1'
    actor = query("SELECT a.name, art.url FROM actor a JOIN art ON art.media_id = a.actor_id "
                  "AND art.media_type = 'actor'")
    assert actor == [('Al Pacino', 'http://127.0.0.1:57578/art/person%3A7/thumb')]


def test_stream_details_show_4k_dolby_vision(syncer, query):
    syncer.sync()
    video = query("SELECT strVideoCodec, iVideoWidth, iVideoHeight, strHdrType, fVideoAspect FROM streamdetails "
                  "WHERE iStreamType = 0 AND idFile = (SELECT idFile FROM movie WHERE c00 = 'Heat')")
    assert video == [('hevc', 3840, 1600, 'dolbyvision', 2.4)]
    audio = query("SELECT strAudioCodec, iAudioChannels FROM streamdetails WHERE iStreamType = 1 AND idFile = "
                  "(SELECT idFile FROM movie WHERE c00 = 'Heat')")
    assert audio == [('truehd', 8)]


def test_tv_shows_seasons_and_episodes(syncer, query):
    syncer.sync()
    show = query('SELECT idShow, c00, c14, c08 FROM tvshow')
    assert show == [(show[0][0], 'Severance', 'Apple TV+', 'Drama')]
    assert query('SELECT season, name FROM seasons ORDER BY season') == [(1, 'Season 1'), (2, 'Season 2')]
    episodes = query('SELECT c12, c13, c00, idShow, idSeason FROM episode ORDER BY c12, c13')
    assert [(e[0], e[1], e[2]) for e in episodes] == [('1', '1', 'Episode 1'), ('1', '2', 'Episode 2'),
                                                      ('2', '1', 'Episode 1')]
    season_ids = dict(query('SELECT season, idSeason FROM seasons'))
    assert episodes[2][4] == season_ids[2]
    # Episodes live under the show's path, so Kodi links them to it.
    path = query('SELECT p.strPath FROM path p JOIN tvshowlinkpath l ON l.idPath = p.idPath')
    assert path == [(mapping.show_path('series-1'),)]
    assert query('SELECT COUNT(*) FROM files f JOIN episode e ON e.idFile = f.idFile '
                 'JOIN tvshowlinkpath l ON l.idPath = f.idPath')[0][0] == 3


def test_watch_state_written(syncer, query):
    syncer.sync()
    states = query('SELECT e.c13, f.playCount, b.timeInSeconds FROM episode e JOIN files f ON f.idFile = e.idFile '
                   'LEFT JOIN bookmark b ON b.idFile = f.idFile WHERE e.c12 = 1 ORDER BY e.c13')
    assert states == [('1', 1, None), ('2', None, 600.0)]


def test_movie_resume_comes_from_item_details(silo, syncer, query):
    silo.movies['movie-2']['user_data']['position_seconds'] = 900.0
    syncer.sync()
    resume = "SELECT b.timeInSeconds FROM bookmark b JOIN movie m ON m.idFile = b.idFile WHERE m.c00 = 'Thief'"
    assert query(resume) == [(900.0,)]
    # Cards have no positions: a later sync must not wipe the resume point.
    syncer.sync()
    assert query(resume) == [(900.0,)]


def test_second_sync_fetches_nothing_when_unchanged(syncer, silo):
    syncer.sync()
    silo.calls = []
    assert syncer.sync() is False
    assert [c for c in silo.calls if c[0] != 'catalog'] == []


def test_watch_state_change_updates_without_refetching(syncer, silo, query):
    syncer.sync()
    silo.movies['movie-1']['user_state'] = {'played': True}
    silo.calls = []
    syncer.sync()
    assert [c for c in silo.calls if c[0] != 'catalog'] == []
    assert query("SELECT f.playCount FROM files f JOIN movie m ON m.idFile = f.idFile WHERE m.c00 = 'Heat'") \
        == [(1,)]


def test_changed_metadata_rewrites_the_same_row(syncer, silo, query):
    syncer.sync()
    before = query("SELECT idMovie, idFile FROM movie WHERE c00 = 'Heat'")
    silo.movies['movie-1']['title'] = 'Heat (Director\'s Definitive Edition)'
    silo.movies['movie-1']['cast'] = []
    syncer.sync()
    after = query("SELECT idMovie, idFile, c00 FROM movie WHERE c00 LIKE 'Heat%'")
    assert [(r[0], r[1]) for r in after] == before
    assert after[0][2].startswith('Heat (Director')
    assert query('SELECT COUNT(*) FROM movie')[0][0] == 2
    # Al Pacino is no longer credited anywhere, so the actor row and photo went too.
    assert query("SELECT name FROM actor WHERE name = 'Al Pacino'") == []


def test_signed_artwork_urls_changing_daily_do_not_count_as_changes(syncer, silo):
    syncer.sync()
    silo.movies['movie-1']['poster_url'] = '/api/v2/artwork/abc/w500.r1.webp?sig=tomorrow'
    silo.calls = []
    syncer.sync()
    assert [c for c in silo.calls if c[0] == 'item'] == []


def test_removed_titles_leave_kodi(syncer, silo, query):
    syncer.sync()
    del silo.movies['movie-2']
    silo.episodes_of[('series-1', 2)] = []
    syncer.sync()
    assert [r[0] for r in query('SELECT c00 FROM movie')] == ['Heat']
    assert query('SELECT COUNT(*) FROM episode')[0][0] == 2
    assert query("SELECT COUNT(*) FROM uniqueid WHERE media_type = 'movie'")[0][0] == 2  # Heat's two ids
    del silo.shows['series-1']
    syncer.sync()
    assert query('SELECT COUNT(*) FROM tvshow')[0][0] == 0
    assert query('SELECT COUNT(*) FROM episode')[0][0] == 0
    assert query('SELECT COUNT(*) FROM seasons')[0][0] == 0
    assert query("SELECT COUNT(*) FROM files WHERE strFilename LIKE '%type=episode%'")[0][0] == 0
    assert query("SELECT COUNT(*) FROM path WHERE strPath = ?", (mapping.show_path('series-1'),))[0][0] == 0


def test_excluded_library_is_removed(syncer, query):
    syncer.sync()
    syncer.sync(excluded=('2',))
    assert query('SELECT COUNT(*) FROM tvshow')[0][0] == 0
    assert query('SELECT COUNT(*) FROM movie')[0][0] == 2


def test_new_episode_fetches_only_its_season(syncer, silo, query):
    syncer.sync()
    silo.episodes_of[('series-1', 2)].append(episode('series-1', 2, 2))
    silo.calls = []
    syncer.sync()
    assert [c for c in silo.calls if c[0] != 'catalog'] == [('season_episodes', 'series-1', 2)]
    assert query('SELECT COUNT(*) FROM episode')[0][0] == 4


def test_resync_item_from_event(syncer, silo, query):
    syncer.sync()
    silo.movies['movie-1']['overview'] = 'New plot'
    syncer.resync_item('movie-1', '1')
    assert query("SELECT c01 FROM movie WHERE c00 = 'Heat'") == [('New plot',)]
    del silo.movies['movie-1']
    syncer.resync_item('movie-1', '1')
    assert query("SELECT COUNT(*) FROM movie WHERE c00 = 'Heat'") == [(0,)]


def test_progress_from_other_apps_sets_resume(syncer, query):
    syncer.sync()
    syncer.apply_progress([{'media_item_id': 'movie-2', 'position_seconds': 1325.5, 'duration_seconds': 7380,
                            'completed': False, 'updated_at': '2026-10-24T10:00:00Z'}])
    assert query("SELECT b.timeInSeconds, b.totalTimeInSeconds FROM bookmark b JOIN movie m ON m.idFile = b.idFile "
                 "WHERE m.c00 = 'Thief'") == [(1325.5, 7380.0)]


def test_lost_bookkeeping_does_not_duplicate(syncer, silo, query, tmp_path, open_kodi):
    syncer.sync()
    # The add-on's own database is gone (reinstall): a new sync must reuse Kodi's rows.
    (tmp_path / 'other').mkdir()
    fresh = Syncer(silo, SyncDB(str(tmp_path / 'other')), open_kodi, PORT)
    fresh.sync()
    assert query('SELECT COUNT(*) FROM movie')[0][0] == 2
    assert query('SELECT COUNT(*) FROM tvshow')[0][0] == 1
    assert query('SELECT COUNT(*) FROM episode')[0][0] == 3


def test_progress_moves_within_libraries(silo, open_kodi, tmp_path):
    for number in range(60):
        silo.add_movie(movie('extra-%d' % number, 'Film %d' % number, cast=[], crew=[]))
    seen = []
    Syncer(silo, SyncDB(str(tmp_path)), open_kodi, PORT, progress=lambda p, m: seen.append((p, m))).sync()
    percents = [p for p, _ in seen]
    assert percents == sorted(percents) and percents[-1] == 100
    assert 'Movies: 25 / 62' in [m for _, m in seen]
    assert any(m.startswith('TV: 0 / 2 seasons') for _, m in seen)


def test_home_rows_map_to_library_items(syncer, query):
    from resources.lib.silokodi.sync import home_row_items
    syncer.sync()
    movie_id = query("SELECT idMovie FROM movie WHERE c00 = 'Heat'")[0][0]
    show_id = query('SELECT idShow FROM tvshow')[0][0]
    next_episode = query("SELECT e.idEpisode FROM episode e JOIN files f ON f.idFile = e.idFile "
                         "WHERE f.strFilename LIKE '%series-1-s1e2%'")[0][0]
    cards = [{'content_id': 'movie-1', 'type': 'movie'},
             {'content_id': 'series-1', 'type': 'series', 'play_content_id': 'series-1-s1e2'},
             {'content_id': 'ebook-9', 'type': 'ebook'}]
    # In "what next" rows a show card is its next episode; elsewhere it is the show.
    assert home_row_items({'section_type': 'continue_watching', 'items': cards}, 'continue_watching', syncer.db) \
        == [{'type': 'movie', 'kodi_id': movie_id}, {'type': 'episode', 'kodi_id': next_episode}]
    assert home_row_items({'section_type': 'collection', 'items': cards}, 'popular-imdb', syncer.db) \
        == [{'type': 'movie', 'kodi_id': movie_id}, {'type': 'tvshow', 'kodi_id': show_id}]


def test_last_played_comes_from_watch_history(silo, syncer, query):
    syncer.sync()
    silo.history_entries = [
        {'content_id': 'series-1', 'type': 'series',
         'watch': {'media_item_id': 'series-1-s1e1', 'watched_at': '2026-10-20T20:00:00Z'}},
        {'content_id': 'movie-1', 'type': 'movie', 'watch': {'media_item_id': 'movie-1', 'watched_at': '2026-10-01T19:00:00Z'}},
        {'content_id': 'movie-1', 'type': 'movie', 'watch': {'media_item_id': 'movie-1', 'watched_at': '2025-01-01T19:00:00Z'}},
    ]
    since = syncer.sync_history()
    assert since == '2026-10-20T20:00:00Z'
    heat = "SELECT f.lastPlayed FROM files f JOIN movie m ON m.idFile = f.idFile WHERE m.c00 = 'Heat'"
    assert query(heat) == [(mapping.kodi_datetime('2026-10-01T19:00:00Z'),)]  # the latest of the two
    # Episodes get theirs too (Kodi derives a show's date from them).
    assert query("SELECT f.lastPlayed FROM files f JOIN episode e ON e.idFile = f.idFile WHERE e.c12 = '1' AND e.c13 = '1'") \
        == [(mapping.kodi_datetime('2026-10-20T20:00:00Z'),)]
    # A later check reads only newer entries...
    silo.history_entries.insert(0, {'content_id': 'movie-2', 'type': 'movie',
                                    'watch': {'media_item_id': 'movie-2', 'watched_at': '2026-10-24T21:00:00Z'}})
    assert syncer.sync_history(since) == '2026-10-24T21:00:00Z'
    # ...and a full resync, whose cards have no dates, keeps them.
    syncer.db.forget_fingerprints()
    syncer.sync()
    assert query(heat) == [(mapping.kodi_datetime('2026-10-01T19:00:00Z'),)]


def test_a_running_sync_waits_between_batches(silo, open_kodi, tmp_path):
    waits = []
    Syncer(silo, SyncDB(str(tmp_path)), open_kodi, PORT, wait_while_busy=lambda: waits.append(1)).sync()
    assert waits  # called between batches, so playback can pause it


def test_only_added_or_removed_titles_ask_for_a_library_update(silo, syncer):
    syncer.sync()
    assert syncer.added_or_removed  # the first sync adds everything
    syncer.added_or_removed = False
    silo.movies['movie-1']['user_state'] = {'played': True}
    silo.movies['movie-2']['title'] = 'Thief (1981)'
    syncer.sync()
    assert syncer.changed and not syncer.added_or_removed  # updates only
    del silo.movies['movie-2']
    syncer.sync()
    assert syncer.added_or_removed
    syncer.added_or_removed = False
    silo.episodes_of[('series-1', 2)].append(episode('series-1', 2, 2))
    syncer.sync()
    assert syncer.added_or_removed
