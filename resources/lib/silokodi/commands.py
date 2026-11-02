# -*- coding: utf-8 -*-
"""The service's commands: what the plugin and a skin ask it for through the
relay (http://127.0.0.1:<port>/command/<name>?...), answered as JSON. A
function of the service: its client, store, sync database and published rows."""
import json

import requests
import xbmc

from . import kodi, mapping, playersubs
from . import rows as home_cards
from .api import ApiError
from .sync import WIDE_SECTIONS

PERSONAL_LISTS = {'watchlist': ('/watchlist', 30160), 'favorites': ('/favorites', 30161),
                  'history': ('/history', 30162)}


def handle(service, name, query):
    if name == 'signed_in':
        service.store.reload()
        service.signed_out_notice = False
        service.stop_listener()
        service.next_sync = 0
    elif name == 'profile_switched':
        # Another profile: its own rows, requests and watch states.
        service.store.reload()
        service.stop_listener()
        service.db.set_meta('history_since', None)  # its own watch history, from the start
        service.enqueue('layout')
        service.next_sync = 0
    elif name == 'signout':
        service.client.logout()
        service.stop_listener()
        service.store.sign_out()
    elif name in ('sync', 'fullsync', 'reset'):
        service.enqueue(name)
    elif name == 'tint':
        # For an info page opened by the plugin: the title's ambient tint,
        # its trailer and (a film or series) whether it's on the watchlist.
        try:
            item = service.client.item(query.get('id') or '')
        except (ApiError, requests.RequestException):
            return {'tint': ''}
        answer = {'tint': mapping.ambient_tint(item.get('poster_thumbhash') or ''),
                  'trailer': mapping.trailer_key(item), 'watchlist': '', 'rating': ''}
        service.offer_theme(item)
        if item.get('type') in ('movie', 'series'):
            try:
                answer['watchlist'] = 'true' if service.client.in_watchlist(item['content_id']) else 'false'
            except (ApiError, requests.RequestException):
                pass
            try:
                answer['rating'] = str(service.client.rating(item['content_id']))  # '0': not rated
            except (ApiError, requests.RequestException):
                pass
        return answer
    elif name == 'watchlist':
        # Add a title to the watchlist or take it off; the lists refresh.
        content_id = query.get('id') or ''
        try:
            if query.get('add') == '1':
                service.client.add_to_watchlist(content_id)
            else:
                service.client.remove_from_watchlist(content_id)
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
        service.personal.refresh_soon(['/watchlist'])
        return {'watchlist': 'true' if query.get('add') == '1' else 'false'}
    elif name == 'rate':
        # Rate a film or series 1-5 stars, or 0 to take the rating away.
        try:
            stars = max(0, min(5, int(query.get('stars') or 0)))
            service.client.rate(query.get('id') or '', stars)
        except (ApiError, requests.RequestException, ValueError) as error:
            return {'error': str(error)}
        return {'rating': str(stars)}
    elif name == 'subtitle_search':
        # The Options sheet's Search online: Silo's providers for the playing file.
        session = service.playback.active
        file_id = (session.plan.get('effective_media_file_id') if session else None) or ''
        if not file_id:
            return {'error': kodi.string(30196)}
        try:
            if not service.client.subtitle_search_enabled():
                return {'error': kodi.string(30201)}
            return {'results': service.client.search_subtitles(file_id, playersubs.subtitle_languages())}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'subtitle_get':
        # Store the chosen result with the file, then play on from the same
        # moment: Silo serves a subtitle only through a session that lists it.
        session = service.playback.active
        row = service.db.get(session.content_id) if session else None
        if not row or row.get('kodi_id') is None or row.get('kind') not in ('movie', 'episode'):
            return {'error': kodi.string(30196)}
        try:
            result = json.loads(query.get('result') or '{}')
            stored = service.client.download_subtitle(session.plan.get('effective_media_file_id'), result)
        except (ApiError, requests.RequestException, ValueError) as error:
            return {'error': str(error)}
        try:
            position = xbmc.Player().getTime()
        except RuntimeError:
            position = 0
        service.subtitle_pick = (str(stored.get('id') or ''), position)
        key = 'movieid' if row['kind'] == 'movie' else 'episodeid'
        kodi.jsonrpc('Player.Open', item={key: row['kodi_id']}, options={'resume': False})
        return {'subtitle': stored.get('id')}
    elif name == 'top10':
        # The Top 10 rows changed in the settings: Home's layout and cards again.
        service.enqueue('layout')
    elif name == 'card':
        # A card's menu (a library title): its title (an episode's show),
        # and whether it's on the watchlist, rated and Not interested.
        row = service.db.by_kodi(query.get('type') or '', query.get('dbid') or '')
        if not row:
            return {'error': kodi.string(30196)}
        title = row.get('parent_id') if row.get('kind') == 'episode' else row['content_id']
        try:
            return {'content_id': title, 'watchlist': service.client.in_watchlist(title),
                    'rating': service.client.rating(title), 'not_interested': service.client.not_interested(title)}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'not_interested':
        try:
            service.client.set_not_interested(query.get('id') or '', query.get('on') == '1')
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
        # Silo rebuilds its rows within seconds: Home's cards after that.
        service.home_cards_soon(delay=20)
        return {'not_interested': query.get('on') == '1'}
    elif name == 'dismiss':
        try:
            return service.dismiss(query.get('type') or '', query.get('dbid') or '')
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'sections':
        try:
            sections = service.home_sections()
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
        for section in sections:
            section['ranked'] = str(section['id']) in service.ranked
            section['wide'] = section['type'] in WIDE_SECTIONS
        return {'sections': sections}
    elif name == 'requests':
        try:
            return {'cards': service.request_cards(query.get('row') or 'mine')}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'request_detail':
        try:
            return mapping.request_summary(service.client.request_detail(query.get('type') or 'movie',
                                                                      query.get('tmdb') or ''))
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'request':
        try:
            seasons = [int(n) for n in (query.get('seasons') or '').split(',') if n.strip().isdigit()]
            answer = service.make_request(query.get('type') or 'movie', query.get('tmdb') or '', seasons)
            service.home_cards_soon()  # Your requests shows it
            return answer
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'collections':
        try:
            return {'collections': service.library_collections(query.get('libraries') or '')}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'widget' and query.get('collection'):
        try:
            # With the saved library details, as other rows: looking each
            # title up in Kodi took 13 s for 191 on a SHIELD.
            return {'items': service.with_details(service.collection_items(query.get('library'), query['collection']))}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'widget' and query.get('person'):
        try:
            return {'items': service.with_details(service.person_items(query['person'], query.get('type')))}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'videos':
        try:
            return {'videos': service.title_videos(query.get('id') or '')}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'person':
        try:
            return service.person(query.get('id'), query.get('name'))
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'widget' and query.get('section') == 'comingsoon':
        try:
            return {'items': service.with_details(service.coming_soon()), 'title': kodi.string(30191)}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'widget' and query.get('section') in PERSONAL_LISTS:
        try:
            path, title = PERSONAL_LISTS[query['section']]
            return {'items': service.with_details(service.personal_items(path)), 'title': kodi.string(title)}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'widget':
        try:
            section = query.get('section') or 'continue_watching'
            items = service.with_details(service.widget_items(section, query.get('library')))
            if not query.get('library'):
                numbered = str(section) in service.ranked
                placed = home_cards.ranking(service.row_titles.get((None, str(section))))
                for number, item in enumerate(items[:home_cards.CARDS], 1):
                    if numbered:
                        item['rank'] = str(number)
                    if placed and number <= home_cards.PLACES:
                        item['place'] = str(number)
            # The title Silo just sent with the items (it names a ranked
            # row), else the one last published.
            title = service.row_titles.get((query.get('library') or None, str(section))) \
                or service.row_title(section, query.get('library'))
            return {'items': items, 'title': title}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    elif name == 'list_libraries':
        excluded = service.excluded()
        try:
            return {'libraries': [{'id': str(library['id']), 'name': library.get('name') or '',
                                   'type': library.get('type') or '',
                                   'synced': str(library['id']) not in excluded}
                                  for library in service.syncer.libraries()]}
        except (ApiError, requests.RequestException) as error:
            return {'error': str(error)}
    else:
        return {'error': 'unknown command'}
    return {'ok': True}
