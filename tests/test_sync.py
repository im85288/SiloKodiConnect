# -*- coding: utf-8 -*-
import pytest

from resources.lib.silokodi import mapping
from resources.lib.silokodi import rows as rows_module
from resources.lib.silokodi import sync as sync_module
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


def test_a_watch_state_change_names_only_that_title_for_the_cache(syncer, silo, query):
    syncer.sync()
    assert len(syncer.touched) == 6 and not syncer.rewrote  # the first sync wrote every title
    syncer.rewrote, syncer.touched = False, set()
    silo.movies['movie-1']['user_state'] = {'played': True}
    syncer.sync()
    heat = query("SELECT idMovie FROM movie WHERE c00 = 'Heat'")[0][0]
    assert not syncer.rewrote and syncer.touched == {('movie', heat)}


def test_rewriting_a_title_names_it_and_removing_one_forgets_all(syncer, silo, query):
    syncer.sync()
    syncer.touched = set()
    silo.movies['movie-1']['title'] = 'Heat (Director\'s Definitive Edition)'
    syncer.sync()
    heat = query("SELECT idMovie FROM movie WHERE c00 LIKE 'Heat%'")[0][0]
    assert syncer.touched == {('movie', heat)} and not syncer.rewrote
    del silo.movies['movie-1']
    syncer.sync()
    assert syncer.rewrote


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


def test_fields_silo_adds_to_cards_do_not_count_as_changes(syncer, silo):
    # As Silo did in October 2026: the parents' artwork and thumbhashes on every episode card.
    syncer.sync()
    for episodes in silo.episodes_of.values():
        for e in episodes:
            e.update(season_poster_url='/api/v2/artwork/s1/w500.webp?sig=x', season_poster_thumbhash='abc',
                     backdrop_is_episode_still=True)
    silo.movies['movie-1']['poster_thumbhash'] = 'xyz'
    silo.calls = []
    assert syncer.sync() is False
    assert [c for c in silo.calls if c[0] != 'catalog'] == []


def test_rows_saved_with_the_old_fingerprint_are_upgraded_not_rewritten(syncer, silo):
    syncer.sync()
    cards = {c['content_id']: c for library, kind in (('1', 'movie'), ('2', 'series'), ('2', 'episode'))
             for c in silo.catalog(library, kind)}
    syncer.db.set_fingerprints([(cid, mapping.legacy_fingerprint(card)) for cid, card in cards.items()])
    silo.calls = []
    assert syncer.sync() is False
    assert [c for c in silo.calls if c[0] != 'catalog'] == []
    assert all(syncer.db.get(cid)['fingerprint'] == mapping.fingerprint(card) for cid, card in cards.items())


def test_a_new_episode_still_is_dropped_from_kodis_image_cache(syncer, silo):
    syncer.sync()
    syncer.art_changed = set()
    first = silo.episodes_of[('series-1', 1)][0]
    # A custom still: Silo's episode card shows it as the poster too.
    first.update(still_url='/api/v2/artwork/custom-e1.webp?sig=7', poster_url='/api/v2/artwork/custom-e1.webp?sig=7')
    syncer.sync()
    assert syncer.art_changed == {mapping.art_url(PORT, first['content_id'], 'thumb')}


def test_only_a_new_image_counts_as_changed_art_not_a_new_signature(tmp_path):
    db = SyncDB(str(tmp_path))
    assert db.set_art('item', {'thumb': '/api/v2/artwork/a.webp?sig=1'}) == []
    assert db.set_art('item', {'thumb': '/api/v2/artwork/a.webp?sig=2'}) == []
    assert db.set_art('item', {'thumb': '/api/v2/artwork/b.webp?sig=2', 'fanart': '/f.webp'}) == ['thumb']


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
    assert 'Movies: 50 / 62' in [m for _, m in seen]
    assert any(m.startswith('TV: 0 / 2 seasons') for _, m in seen)


# Toy Story's poster thumbhash on a real Silo server, and the tint it gives.
TOY_STORY_TINT = 'FF2C3B42'


def test_ambient_tint_is_the_thumbhash_average_at_silo_luminance():
    from resources.lib.silokodi.mapping import ambient_tint
    tint = ambient_tint('U9cNLQhmeH+HSIhoh3Znd0hwlgVn')
    assert tint == TOY_STORY_TINT
    red, green, blue = (int(tint[i:i + 2], 16) / 255.0 for i in (2, 4, 6))
    assert abs(0.2126 * red + 0.7152 * green + 0.0722 * blue - 0.22) < 0.01
    assert ambient_tint('') == '' and ambient_tint('!!') == '' and ambient_tint(None) == ''

def test_home_rows_map_to_library_items(syncer, query):
    from resources.lib.silokodi.sync import home_row_items
    syncer.sync()
    movie_id = query("SELECT idMovie FROM movie WHERE c00 = 'Heat'")[0][0]
    show_id = query('SELECT idShow FROM tvshow')[0][0]
    next_episode = query("SELECT e.idEpisode FROM episode e JOIN files f ON f.idFile = e.idFile "
                         "WHERE f.strFilename LIKE '%series-1-s1e2%'")[0][0]
    cards = [{'content_id': 'movie-1', 'type': 'movie', 'poster_thumbhash': 'U9cNLQhmeH+HSIhoh3Znd0hwlgVn'},
             {'content_id': 'series-1', 'type': 'series', 'play_content_id': 'series-1-s1e2'},
             {'content_id': 'ebook-9', 'type': 'ebook'}]
    # In "what next" rows a show card is its next episode; elsewhere it is the show.
    assert home_row_items({'section_type': 'continue_watching', 'items': cards}, 'continue_watching', syncer.db) \
        == [{'type': 'movie', 'kodi_id': movie_id, 'tint': TOY_STORY_TINT},
            {'type': 'episode', 'kodi_id': next_episode, 'tint': ''}]
    assert home_row_items({'section_type': 'collection', 'items': cards}, 'popular-imdb', syncer.db) \
        == [{'type': 'movie', 'kodi_id': movie_id, 'tint': TOY_STORY_TINT},
            {'type': 'tvshow', 'kodi_id': show_id, 'tint': ''}]


def test_continue_watching_episodes_carry_silos_series_backdrop(syncer, query):
    from resources.lib.silokodi.sync import home_row_items
    syncer.sync()
    card = {'content_id': 'series-1', 'type': 'series', 'play_content_id': 'series-1-s1e2',
            'backdrop_url': '/api/v2/artwork/season-1-backdrop.webp?sig=1'}
    [item] = home_row_items({'section_type': 'next_up', 'items': [card]}, 'next_up', syncer.db)
    assert item['backdrop'] == card['backdrop_url'] and item['content_id'] == 'series-1-s1e2'
    # Elsewhere a show card is the show, with its own art.
    [item] = home_row_items({'section_type': 'collection', 'items': [card]}, 'popular', syncer.db)
    assert 'backdrop' not in item


def test_a_card_takes_the_series_art_given_instead_of_the_still():
    details = {'title': 'Episode 2', 'art': {'thumb': 'still.jpg', 'tvshow.fanart': 'show.jpg'}}
    assert rows_module.card('episode', details)['art.landscape'] == 'still.jpg'
    assert rows_module.card('episode', details, landscape='row-art.jpg')['art.landscape'] == 'row-art.jpg'


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


def test_skin_layout_follows_silo_home_and_libraries():
    from resources.lib.silokodi.sync import MAX_ROWS, skin_layout
    home = [{'id': 'continue_watching', 'title': 'Continue Watching', 'section_type': 'continue_watching'},
            {'id': 'abc 1', 'title': 'Popular on IMDb', 'section_type': 'collection'}]
    libraries = [{'id': '1', 'name': 'Movies', 'type': 'movies'}, {'id': '2', 'name': 'Shows', 'type': 'series'}]
    library_sections = {'1': [{'id': 'recent', 'title': 'Recently Added', 'section_type': 'recently_added'}],
                        '2': [{'id': 's%d' % n, 'title': 'Row %d' % n} for n in range(20)]}
    layout = skin_layout(home, libraries, library_sections)
    assert layout['silo.pages'] == 'home,movies,series'
    assert layout['silo.home.count'] == '2'
    assert layout['silo.home.1.style'] == 'landscape' and layout['silo.home.2.style'] == 'poster'
    assert layout['silo.home.2.path'] == 'plugin://plugin.video.silokodiconnect/?mode=widget&section=abc%201'
    # Recently Added is a row Kodi's library can list in Silo's order: native.
    assert layout['silo.movies.1.path'] == sync_module.native_path(
        {'section_type': 'recently_added'}, libraries[0])
    assert layout['silo.series.count'] == str(MAX_ROWS)
    # Unused slots are cleared, so rows removed in Silo don't linger in the skin.
    assert layout['silo.home.3.title'] == '' and layout['silo.home.%d.path' % MAX_ROWS] == ''


def test_skin_layout_without_movie_libraries_has_no_movies_page():
    from resources.lib.silokodi.sync import skin_layout
    layout = skin_layout([], [{'id': '2', 'type': 'series'}], {'2': [{'id': 'x', 'title': 'X'}]})
    assert layout['silo.pages'] == 'home,series'
    assert layout['silo.movies.count'] == '0'


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


def test_similar_path_encodes_the_genre_and_leaves_the_title_out():
    import json as json_module
    from urllib.parse import unquote
    from resources.lib.silokodi.mapping import similar_path
    path = similar_path('tvshow', 'Action & Adventure', 'Vigil')
    assert path.startswith('videodb://tvshows/titles/?xsp=') and '&' not in path and ' ' not in path
    rules = json_module.loads(unquote(path.split('=', 1)[1]))
    assert rules['type'] == 'tvshows' and rules['rules']['and'] == [
        {'field': 'genre', 'operator': 'is', 'value': ['Action & Adventure']},
        {'field': 'title', 'operator': 'isnot', 'value': ['Vigil']}]


def test_content_id_comes_from_play_urls_and_show_folders():
    from resources.lib.silokodi.mapping import SHOWS_PATH, content_id_from_path, play_url
    assert content_id_from_path(play_url(SHOWS_PATH + 'series-1/', 'episode-1-3-3', 'episode')) == 'episode-1-3-3'
    assert content_id_from_path(SHOWS_PATH + 'series-tvdb-384968/') == 'series-tvdb-384968'
    assert content_id_from_path('videodb://movies/titles/') is None


def test_collection_cards_follow_silos_groups_and_skip_empty_ones():
    from resources.lib.silokodi.sync import collection_cards
    tab = {'collections': [{'id': 'a', 'title': 'IMDb Popular', 'poster_url': 'https://x/a.jpg', 'item_count': 63}],
           'groups': [{'name': 'Lists', 'collections': [{'id': 'b', 'title': 'Oscars', 'item_count': 98},
                                                       {'id': 'e', 'title': 'Empty', 'item_count': 0}]}],
           'ungrouped': {'collections': [{'id': 'a', 'title': 'IMDb Popular', 'poster_url': 'https://x/a.jpg',
                                          'item_count': 63}]}}
    assert collection_cards(tab) == [{'id': 'b', 'title': 'Oscars', 'poster': '', 'count': 98},
                                     {'id': 'a', 'title': 'IMDb Popular', 'poster': 'https://x/a.jpg', 'count': 63}]


def test_skin_layout_links_each_page_to_its_collections():
    from resources.lib.silokodi.sync import skin_layout
    props = skin_layout([], [{'id': 1, 'type': 'movies'}, {'id': 4, 'type': 'movies'}], {})
    assert props['silo.movies.collections'] == \
        'plugin://plugin.video.silokodiconnect/?mode=collections&libraries=1%2C4'
    assert props['silo.series.collections'] == ''


def test_request_cards_show_silos_state_and_tmdb_art():
    from resources.lib.silokodi.mapping import request_card
    result = {'media_type': 'movie', 'tmdb_id': 949, 'title': 'Heat', 'year': 1995, 'poster_path': '/p.jpg',
              'backdrop_path': '/b.jpg', 'vote_average': 7.94, 'availability': 'missing',
              'request': {'requestable': True}}
    card = request_card(result)
    assert card['poster'] == 'https://image.tmdb.org/t/p/w500/p.jpg' and card['rating'] == 7.9
    assert card['requestable'] and card['state'] == '' and card['label'] == ''
    mine = {'media_type': 'series', 'tmdb_id': 1, 'title': 'Vigil', 'state': 'processing', 'status': 'queued',
            'outcome': 'active'}
    assert request_card(mine)['label'] == 'Queued' and not request_card(mine)['requestable']
    assert request_card(mine)['long'] == 'Queued for download' and request_card(mine)['tint'] == 'FF38BDF8'
    held = dict(result, availability='available', library_content_id='movie-1', request={})
    assert request_card(held)['label'] == 'In library' and request_card(held)['content_id'] == 'movie-1'


def test_yours_splits_requests_on_the_way_from_those_you_can_watch():
    from resources.lib.silokodi import service as service_module

    class Client(object):
        def my_requests(self):
            return [{'media_type': 'movie', 'tmdb_id': 1, 'title': 'Dune', 'state': 'processing', 'status': 'queued'},
                    {'media_type': 'series', 'tmdb_id': 2, 'title': 'Industry', 'library_content_id': 'show-2'},
                    {'media_type': 'movie', 'tmdb_id': 3, 'title': 'Heat', 'state': 'available'}]

    class Db(object):
        def get(self, content_id):
            return {'kind': 'tvshow', 'kodi_id': 7}
    service = service_module.Service.__new__(service_module.Service)
    service.client, service.db = Client(), Db()
    assert [c['title'] for c in service.request_cards('mine:active')] == ['Dune']
    assert [c['title'] for c in service.request_cards('mine:available')] == ['Industry', 'Heat']
    assert len(service.request_cards('mine')) == 3


def test_request_progress_reads_as_silos_tv_app():
    progress = mapping.request_progress
    assert progress('pending')['label'] == 'Pending' and progress('pending')['tint'] == 'FFF59E0B'
    assert progress('processing', 'downloading')['label'] == 'Downloading'
    assert progress('processing')['label'] == 'On the way'
    assert progress('partially_available')['label'] == 'Partly in library'
    assert (progress('available')['done'], progress('available')['step']) == (4, 0)
    assert progress('failed')['long'] == 'Request failed' and progress('declined')['tint'] == 'FFFB7185'
    # No state (an older server): status and outcome decide.
    assert progress('', 'completed', 'active')['label'] == 'In library'
    assert progress('', 'queued', 'declined')['label'] == 'Declined'
    # A title nobody asked for: nothing to show, it can just be requested.
    assert progress('', requestable=True) is None
    assert progress('', available=True)['label'] == 'In library'


def test_request_body_carries_the_title_as_silo_needs_it():
    from resources.lib.silokodi.mapping import request_body
    detail = {'media_type': 'series', 'tmdb_id': 5, 'title': 'Vigil', 'year': '2021', 'tvdb_id': 9, 'imdb_id': '',
              'poster_path': '/p.jpg', 'seasons': [{'season_number': 1}]}
    assert request_body(detail) == {'media_type': 'series', 'tmdb_id': 5, 'title': 'Vigil', 'year': 2021,
                                    'tvdb_id': 9, 'poster_path': '/p.jpg'}


def test_skin_layout_adds_a_requests_page_when_the_profile_can_request():
    from resources.lib.silokodi.sync import skin_layout
    props = skin_layout([], [], {}, [('mine:active', 'In progress', 'poster', 'yours'),
                                     ('trending_movies', 'Trending Movies')])
    assert props['silo.pages'] == 'home,requests' and props['silo.requests.count'] == '2'
    assert props['silo.requests.2.title'] == 'Trending Movies'
    assert props['silo.requests.2.path'] == 'plugin://plugin.video.silokodiconnect/?mode=requests&row=trending_movies'
    # Which pill shows the row: Yours, or Discover (none).
    assert props['silo.requests.1.section'] == 'yours' and props['silo.requests.2.section'] == ''
    assert 'requests' not in skin_layout([], [], {})['silo.pages']


def test_a_rows_path_is_also_published_under_its_style():
    from resources.lib.silokodi import sync
    props = sync.skin_layout([{'id': 'cw', 'title': 'Continue', 'section_type': 'continue_watching'},
                              {'id': 'top', 'title': 'Top'}], [], {})
    assert props['silo.home.1.landscape'] == props['silo.home.1.path'] and props['silo.home.1.poster'] == ''
    assert props['silo.home.1.type'] == 'continue_watching' and props['silo.home.2.type'] == ''
    assert props['silo.home.2.poster'] == props['silo.home.2.path'] and props['silo.home.2.landscape'] == ''
    assert sync.page_of('silo.movies.3.poster') == 'movies'
    assert sync.page_of('silo.movies.3.path') is None and sync.page_of('silo.pages') is None


def test_the_details_cache_looks_up_once_until_told_of_a_change():
    from resources.lib.silokodi.library import DetailsCache
    calls = []

    def fetch(kind, kodi_id, shows):
        calls.append((kind, kodi_id))
        return {'title': '%s %d' % (kind, kodi_id)}, None
    cache = DetailsCache(fetch)
    assert cache.get('episode', 1)['details']['title'] == 'episode 1'
    cache.get('episode', 1)
    cache.get('tvshow', 9)
    assert calls == [('episode', 1), ('tvshow', 9)]
    cache.forget('episode', 1)  # Kodi changed an uncached-show episode: shows are read again
    cache.get('episode', 1)
    cache.get('tvshow', 9)
    assert calls[2:] == [('episode', 1), ('tvshow', 9)]
    cache.clear()
    cache.get('episode', 1)
    assert len(calls) == 5


def test_an_episode_change_forgets_only_its_own_show():
    from resources.lib.silokodi.library import DetailsCache
    calls = []

    def fetch(kind, kodi_id, shows):
        calls.append((kind, kodi_id))
        return ({'tvshowid': 9} if kind == 'episode' else {'title': 'show'}), None
    cache = DetailsCache(fetch)
    for key in (('episode', 1), ('tvshow', 9), ('tvshow', 10)):
        cache.get(*key)
    cache.forget('episode', 1)
    for key in (('episode', 1), ('tvshow', 9), ('tvshow', 10)):
        cache.get(*key)
    assert calls[3:] == [('episode', 1), ('tvshow', 9)]


def test_the_details_cache_survives_a_restart_and_is_rechecked(tmp_path):
    from resources.lib.silokodi.library import DetailsCache
    library = {('episode', 1): {'title': 'Pilot', 'playcount': 0}, ('movie', 2): {'title': 'Heat'}}

    def fetch(kind, kodi_id, shows):
        details = library.get((kind, kodi_id))
        return (dict(details) if details else None), None
    path = str(tmp_path / 'details.json')
    before = DetailsCache(fetch)
    before.get('episode', 1)
    before.get('movie', 2)
    assert before.dirty
    before.save(path)
    assert not before.dirty

    after = DetailsCache(fetch)
    assert after.load(path) == 2
    assert after.get('episode', 1)['details']['title'] == 'Pilot'
    assert not after.recheck()  # nothing changed while Kodi was off
    library[('episode', 1)]['playcount'] = 1
    del library[('movie', 2)]
    assert after.recheck()
    assert after.get('episode', 1)['details']['playcount'] == 1
    assert ('movie', 2) not in after.titles
    assert DetailsCache(fetch).load(str(tmp_path / 'missing.json')) == 0


def test_episode_badges_take_silos_hdr_and_audio_labels():
    details = mapping.episode_stream_details({
        'files': [{'codec_video': 'hevc', 'hdr': True, 'audio_channels': 6, 'resolution': '2160p'}],
        'overlay_summary': {'hdr': 'HDR10+', 'audio': 'EAC3', 'audio_channels': '5.1'}})
    assert details['video'][0]['hdr'] == 'hdr10plus' and details['audio'][0]['codec'] == 'eac3'
    # As Kodi's JSON-RPC hands the stored details back (hdr is read as hdrtype):
    stored = {'video': [{'height': 2160, 'hdrtype': details['video'][0]['hdr']}], 'audio': details['audio']}
    assert mapping.quality_badges(stored) == ['4K', 'HDR10+', 'EAC3 5.1']
    # Without the summary, as before.
    plain = mapping.episode_stream_details({'files': [{'codec_video': 'hevc', 'hdr': True, 'audio_channels': 6}]})
    assert plain['video'][0]['hdr'] == 'hdr10' and plain['audio'][0]['codec'] == ''


def test_tech_lines_and_both_ratings():
    lines = mapping.tech_lines({'video': [{'height': 2160, 'hdrtype': 'hdr10plus'}],
                                'audio': [{'codec': 'eac3', 'channels': 6, 'language': 'eng'}],
                                'subtitle': [{'language': 'eng'}, {'language': 'fre'}, {'language': 'eng'}]})
    assert lines == {'silo.version': '4K · HDR10+', 'silo.audio': 'English · EAC3 · 5.1',
                     'silo.subtitles': 'English, French'}
    assert mapping.tech_lines({})['silo.subtitles'] == 'None'
    assert mapping.ratings({'rating_imdb': 8.7, 'rating_tmdb': 8}) == [('imdb', 8.7, 0), ('themoviedb', 8.0, 0)]
    props = mapping.skyline_properties('episode', {'season': 7, 'episode': 6, 'ratings': {}},
                                       {'ratings': {'imdb': {'rating': 8.7}, 'themoviedb': {'rating': 8.0}}})
    assert props['silo.rating.imdb'] == '8.7' and props['silo.rating.tmdb'] == '8.0'


def test_home_cards_carry_silos_text_and_resolved_art():
    from resources.lib.silokodi import rows
    episode = rows.card('episode', {
        'episodeid': 7, 'title': 'Zone of Death', 'showtitle': 'Marshals', 'season': 1, 'episode': 2,
        'firstaired': '2026-03-08', 'resume': {'position': 600, 'total': 2400}, 'playcount': 0,
        'art': {'thumb': 'still.jpg', 'tvshow.poster': 'poster.jpg', 'tvshow.fanart': 'fanart.jpg',
                'tvshow.clearlogo': 'logo.png'}}, {'mpaa': 'TV-14', 'genre': ['Drama']}, 'FF223344')
    assert episode['type'] == 'episode' and episode['dbid'] == '7'
    assert episode['card.title'] == 'Marshals' and episode['card.sub'] == 'S01E02'
    assert episode['card.wide_sub'] == 'S01E02 • Zone of Death' and episode['hero.title'] == ''
    assert episode['hero.meta'] == 'S01E02 · Zone of Death' and episode['percent'] == '25'
    assert episode['hero.facts'] == 'S01E02 · Zone of Death' and episode['rating.imdb'] == ''
    assert (episode['art.poster'], episode['art.landscape'], episode['art.fanart'], episode['art.clearlogo']) == \
        ('poster.jpg', 'still.jpg', 'fanart.jpg', 'logo.png')
    assert episode['mpaa'] == 'TV-14' and episode['tint'] == 'FF223344'
    movie = rows.card('movie', {'movieid': 3, 'title': 'Heat', 'year': 1995, 'art': {'poster': 'p.jpg'}})
    assert movie['card.title'] == 'Heat' and movie['card.sub'] == '1995' and movie['hero.title'] == 'Heat'
    props = rows.row_properties(2, [movie])
    assert props['silo.home.2.1.label'] == 'Heat' and props['silo.home.2.cards'] == '1'
    assert props['silo.home.2.2.label'] == '' and 'silo.home.2.%d.label' % rows.CARDS in props


def test_quickstart_sets_home_from_what_the_service_saved(tmp_path):
    from resources.lib.silokodi import quickstart
    from resources.lib.silokodi.syncdb import SyncDB
    assert quickstart.saved(str(tmp_path)) == ({}, {})
    db = SyncDB(str(tmp_path))
    db.set_meta('layout', {'silo.pages': 'home,movies', 'silo.home.1.landscape': 'plugin://x/?a=1',
                           'silo.movies.1.poster': 'plugin://x/?b=2', 'silo.movies.1.title': 'Recently Added'})
    db.set_meta('home_cards', {'silo.home.1.1.label': 'Heat'})
    db.close()
    props = quickstart.home_properties(*quickstart.saved(str(tmp_path)))
    assert props['silo.home.1.landscape'] == 'plugin://x/?a=1' and props['silo.movies.1.poster'] == ''
    assert props['silo.movies.1.title'] == 'Recently Added' and props['silo.home.1.1.label'] == 'Heat'


def test_a_requestable_title_card_and_page_prefixes():
    from resources.lib.silokodi import rows
    card = rows.request_card({'type': 'series', 'tmdb': 95396, 'title': 'Severance', 'year': 2022, 'rating': 8.4,
                              'label': 'Requested', 'overview': 'Work.', 'poster': 'p.jpg', 'backdrop': 'b.jpg'})
    assert card['type'] == 'request' and card['request.tmdb'] == '95396' and card['card.sub'] == 'SERIES · 2022'
    assert card['request.state'] == 'Requested' and card['request.library'] == ''
    assert card['hero.meta'] == '2022 · [COLOR 66EDEDED]TMDB[/COLOR] [B]8.4[/B]' and card['art.fanart'] == 'b.jpg'
    assert (card['hero.facts'], card['rating.tmdb']) == ('2022', '8.4')
    props = rows.row_properties(3, [card], 'requests')
    assert props['silo.requests.3.1.request.type'] == 'series' and props['silo.requests.3.cards'] == '1'
    # A Requests row has the skin's 12 slots, not Home's 25.
    props = rows.row_properties(3, [card] * 20, 'requests', 12)
    assert props['silo.requests.3.cards'] == '12' and 'silo.requests.3.12.type' in props
    assert 'silo.requests.3.13.type' not in props


def test_library_rows_kodi_can_order_load_natively():
    from resources.lib.silokodi import sync
    import json
    from urllib.parse import unquote
    movies = {'id': 1, 'type': 'movies', 'name': 'Films'}
    path = sync.native_path({'section_type': 'continue_watching'}, movies)
    assert path.startswith('videodb://movies/titles/?xsp=')
    query = json.loads(unquote(path.split('xsp=', 1)[1]))
    assert 'order' not in query and 'limit' not in query  # Kodi's list sorts, then cuts
    assert {'field': 'inprogress', 'operator': 'true', 'value': []} in query['rules']['and']
    assert {'field': 'tag', 'operator': 'is', 'value': ['Films']} in query['rules']['and']
    shows = {'id': 2, 'type': 'series', 'name': 'TV'}
    assert sync.native_path({'section_type': 'recently_added'}, shows).startswith('videodb://tvshows/titles/')
    assert sync.native_path({'section_type': 'continue_watching'}, shows) is None  # Silo's next-up
    assert sync.native_path({'section_type': 'recommended_for_you'}, movies) is None
    entry = sync.row_entry({'id': 'x', 'section_type': 'random', 'title': 'Random'}, '1', movies)
    assert entry['path'].startswith('videodb://')
    assert (entry['sort'], entry['order'], entry['limit']) == ('random', 'ascending', '25')
    plugin = sync.row_entry({'id': 'y', 'section_type': 'imdb_popular', 'title': 'IMDb'}, '1', movies)
    assert (plugin['sort'], plugin['order'], plugin['limit']) == ('', '', '')


def test_an_empty_kodi_library_row_gets_no_path(monkeypatch):
    from resources.lib.silokodi import kodi, service as service_module
    set_props = {}

    class Window(object):
        def __init__(self, *args):
            pass

        def setProperty(self, key, value):
            set_props[key] = value

    monkeypatch.setattr(service_module.xbmcgui, 'Window', Window)
    monkeypatch.setattr(kodi, 'get_property', lambda name: '7')
    asked = []

    def jsonrpc(method, **params):
        asked.append(params['directory'])
        return {'limits': {'total': 0 if 'inprogress' in params['directory'] else 3}}
    monkeypatch.setattr(kodi, 'jsonrpc', jsonrpc)
    service = service_module.Service.__new__(service_module.Service)
    service.viewed_pages, service.loading_until, service.native_counts = {'movies'}, {}, {}
    service.layout = {'silo.movies.1.path': 'videodb://movies/titles/?xsp=inprogress',
                      'silo.movies.1.landscape': 'videodb://movies/titles/?xsp=inprogress',
                      'silo.movies.2.path': 'videodb://movies/titles/?xsp=recent',
                      'silo.movies.2.poster': 'videodb://movies/titles/?xsp=recent',
                      'silo.movies.3.poster': 'plugin://x/?mode=widget'}
    service.apply_layout()
    assert set_props['silo.movies.1.path'] == '' and set_props['silo.movies.1.landscape'] == ''
    assert set_props['silo.movies.2.poster'] == 'videodb://movies/titles/?xsp=recent&refresh=7'
    assert set_props['silo.movies.3.poster'] == 'plugin://x/?mode=widget&refresh=7'
    assert len(asked) == 2  # each path asked once, until the library changes


def test_dolby_vision_hdr10_plus_and_atmos_as_silo_names_them():
    from resources.lib.silokodi import mapping
    # MobLand S2E3 as Silo lists it: Dolby Vision with HDR10+, Atmos in EAC3.
    episode = {'files': [{'resolution': '2160p', 'codec_video': 'hevc', 'hdr': True, 'audio_channels': 6}],
               'overlay_summary': {'hdr': 'DV HDR10+', 'audio': 'Atmos'}}
    details = mapping.episode_stream_details(episode)
    assert details['video'][0]['hdr'] == 'dolbyvision'
    assert details['audio'][0]['codec'] == 'eac3_ddp_atmos'
    version = {'video_tracks': [{'codec': 'hevc', 'width': 3840, 'height': 1600, 'dolby_vision': 'Profile 8',
                                 'video_range_type': 'DOVIWithHDR10Plus'}],
               'audio_tracks': [{'codec': 'eac3', 'profile': 'Dolby Digital Plus + Dolby Atmos', 'channels': 6,
                                 'language': 'en'}]}
    full = mapping.stream_details(version)
    assert full['video'][0]['hdr'] == 'dolbyvision' and full['audio'][0]['codec'] == 'eac3_ddp_atmos'
    assert mapping.hdr_type({'video_range_type': 'HDR10Plus'}, {}) == 'hdr10plus'
    assert mapping.overlay_hdr('HDR10+') == 'hdr10plus' and mapping.overlay_hdr('', 'hdr10') == 'hdr10'
    kodi = {'video': [{'width': 3840, 'height': 1600, 'hdrtype': 'dolbyvision'}],
            'audio': [{'codec': 'eac3_ddp_atmos', 'channels': 6, 'language': 'en'}]}
    assert mapping.quality_badges(kodi) == ['4K', 'DOLBY VISION', 'ATMOS 5.1']
    assert mapping.tech_lines(kodi)['silo.audio'] == 'English · EAC3 Atmos · 5.1'
    assert mapping.tech_lines(kodi)['silo.version'] == '4K · Dolby Vision'



def test_a_second_sync_keeps_the_first_ones_change_pending(syncer):
    # A full resync, then the regular sync queued behind it before the
    # service handled the first one's change: it must still be flagged.
    assert syncer.sync() is True
    assert syncer.sync() is False
    assert syncer.changed is True
    syncer.changed = False  # the service handled it
    assert syncer.sync() is False and syncer.changed is False


def test_library_facts_line():
    from resources.lib.silokodi.service import library_facts
    assert library_facts({'movie': 1087, 'tvshow': 529, 'episode': 28374, 'season': 2125}) == \
        '1,087 films  ·  529 series  ·  28,374 episodes'
    assert library_facts({'movie': 1}) == '1 film'
    assert library_facts({}) == ''


def test_switching_profile_keeps_the_current_one_on_cancel(monkeypatch, tmp_path):
    from resources.lib.silokodi import auth
    from resources.lib.silokodi.store import Store
    store = Store(str(tmp_path))
    store.update(profile_id='me', profile_name='Ian')

    class Client(object):
        def profiles(self):
            return [{'id': 'me', 'name': 'Ian'}, {'id': 'aurora', 'name': 'Aurora'}]

    picks = []

    class Dialog(object):
        def select(self, heading, names, preselect=-1):
            picks.append(preselect)
            return answer[0]
    monkeypatch.setattr(auth.xbmcgui, 'Dialog', Dialog)
    answer = [-1]
    assert auth.choose_profile(store, Client(), switching=True) is False
    assert store.get('profile_id') == 'me' and picks == [0]  # the current profile preselected
    answer = [0]
    assert auth.choose_profile(store, Client(), switching=True) is False  # the same one: nothing to do
    answer = [1]
    assert auth.choose_profile(store, Client(), switching=True) is True
    assert store.get('profile_id') == 'aurora' and store.get('profile_name') == 'Aurora'


def test_a_household_profiles_libraries_come_from_its_own_list(syncer, silo):
    # The sync's list needs the primary profile; the layout's doesn't.
    assert [lib['id'] for lib in syncer.libraries(primary=False)] == [lib['id'] for lib in syncer.libraries()]


def test_a_household_profile_keeps_every_title(syncer, silo, query):
    from resources.lib.silokodi.api import ApiError
    syncer.sync()
    assert len(query('SELECT idMovie FROM movie')) == 2
    # As a household profile: /libraries is refused and Silo lists fewer titles.
    def refused():
        raise ApiError(403, 'permission_denied', "Admin access requires the account's primary profile")
    silo.libraries = refused
    del silo.movies['movie-2']
    syncer.sync()
    assert syncer.household is True
    assert len(query('SELECT idMovie FROM movie')) == 2  # Thief stays in Kodi


def test_trailer_key_prefers_an_official_english_youtube_trailer():
    videos = [{'kind': 'featurette', 'site': 'youtube', 'site_key': 'f'},
              {'kind': 'trailer', 'site': 'vimeo', 'site_key': 'v'},
              {'kind': 'trailer', 'site': 'youtube', 'site_key': 'de', 'language': 'de', 'is_official': True},
              {'kind': 'trailer', 'site': 'youtube', 'site_key': 'fan', 'language': 'en'},
              {'kind': 'trailer', 'site': 'youtube', 'site_key': 'en', 'language': 'en', 'is_official': True}]
    assert mapping.trailer_key({'videos': videos}) == 'en'
    assert mapping.trailer_key({'videos': videos[:2]}) == ''
    assert mapping.trailer_key({}) == ''


def test_a_show_is_marked_watched_when_every_episode_is():
    from resources.lib.silokodi.rows import watched_mark
    assert watched_mark('tvshow', {'episode': 8, 'watchedepisodes': 8}) == '1'
    assert watched_mark('tvshow', {'episode': 8, 'watchedepisodes': 7}) == ''
    assert watched_mark('tvshow', {'episode': 0, 'watchedepisodes': 0}) == ''
    assert watched_mark('movie', {'playcount': 2}) == '2'
    assert watched_mark('movie', {}) == ''


def test_the_hero_rating_dims_its_source_and_bolds_its_score():
    assert rows_module.rating_markup('IMDb 6.6') == '[COLOR 66EDEDED]IMDb[/COLOR] [B]6.6[/B]'
    assert rows_module.rating_markup('') == ''


def test_a_films_home_card_has_its_info_line_in_parts():
    from resources.lib.silokodi import rows
    film = rows.card('movie', {'movieid': 3, 'title': 'Heat', 'year': 1995, 'genre': ['Crime', 'Drama'],
                               'ratings': {'imdb': {'rating': 8.3, 'default': True}, 'themoviedb': {'rating': 7.9}}})
    assert film['hero.facts'] == '1995 · Crime'
    assert (film['rating.imdb'], film['rating.tmdb']) == ('8.3', '7.9')


def test_writes_inside_a_batch_commit_together(tmp_path):
    import sqlite3
    db = SyncDB(str(tmp_path))
    other = sqlite3.connect(db.path)

    def seen():
        return other.execute('SELECT COUNT(*) FROM items').fetchone()[0]
    with db.batch():
        db.put(content_id='a', kind='movie')
        with db.batch():
            db.put(content_id='b', kind='movie')
        assert seen() == 0            # not committed yet
    assert seen() == 2
    db.put(content_id='c', kind='movie')
    assert seen() == 3                # outside a batch, at once


def test_brand_cards_open_silos_browse_pages():
    studio = rows_module.brand_card({'slug': 'pixar', 'display_name': 'Pixar', 'logo_url': 'https://x/p.png'}, 'studio')
    assert studio['type'] == 'brand' and studio['request.more'] == 'browse:studio:pixar'
    assert studio['request.logo'] == 'https://x/p.png' and studio['card.title'] == 'Pixar'
    genre = rows_module.brand_card({'slug': 'action', 'display_name': 'Action', 'gradient_from': '#dc2626',
                                    'gradient_to': '#7f1d1d'}, 'genre')
    assert genre['request.from'] == 'FFDC2626' and genre['request.to'] == 'FF7F1D1D'
    pictured = rows_module.brand_card({'slug': 'pixar', 'display_name': 'Pixar'}, 'studio',
                                      {'backdrop': 'https://x/b.jpg', 'tint': 'FF101820'})
    assert pictured['art.fanart'] == 'https://x/b.jpg' and pictured['tint'] == 'FF101820'


def test_an_available_title_carries_silos_library_tags():
    card = rows_module.request_card({'type': 'movie', 'tmdb': 1, 'title': 'Heat', 'year': 1995, 'state': 'available'})
    assert card['request.library'] == '1' and card['request.state'] == 'Available' and card['card.sub'] == 'MOVIE · 1995'
