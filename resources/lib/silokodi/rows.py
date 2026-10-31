# -*- coding: utf-8 -*-
"""Silo's rows as window properties, for a skin to draw without a plugin.

A plugin row costs Kodi starting Python every time the row loads (about
0.5 s a row on a SHIELD, most of a row's time), and a Kodi library list
can't keep Silo's order (a title is #1 in one row and #7 in another). So
the service publishes Home's and Requests' rows' cards, in Silo's order,
as Home window properties (Movies' and Series' rows Kodi can order load from the library;
the others stay plugin rows, as static cards for every page made the skin
slow to load):

    silo.<page>.<row>.<card>.<field>   (row 1-MAX_ROWS, card 1-CARDS)

with the text a Silo-style skin shows already made, and the artwork already
resolved (an episode falls back to its show's). A card's `type` and `dbid`
open its info page; a title Silo can request has `request.type` and
`request.tmdb` instead.
"""
from . import mapping

CARDS = 20  # cards per row; Silo's rows go up to 50 (most hold 13-20), and each costs a skin's load time
FIELDS = ('type', 'dbid', 'label', 'card.title', 'card.sub', 'card.wide_sub', 'hero.title', 'hero.meta',
          'hero.facts', 'rating.imdb', 'rating.tmdb',
          'hero.detail', 'plot', 'mpaa', 'badge1', 'badge2', 'badge3', 'tint', 'percent', 'playcount',
          'art.poster', 'art.landscape', 'art.fanart', 'art.clearlogo', 'request.type', 'request.tmdb',
          'status.tint', 'status.seg1', 'status.seg2', 'status.seg3', 'status.seg4',
          'request.library', 'request.state', 'request.more', 'request.logo', 'request.from', 'request.to')


def card(kind, details, show=None, tint='', landscape=''):
    """One card's fields, from Kodi's JSON-RPC details (and an episode's show).
    landscape: the wide art to show instead (an episode's series art)."""
    props = mapping.skyline_properties(kind, details, show)
    art = details.get('art') or {}
    episode = kind == 'episode'
    title = details.get('title') or details.get('label') or ''
    show_title = details.get('showtitle') or ''
    code = props.get('silo.code', '')
    year = str(details.get('year') or '') if details.get('year') else ''
    logo = art.get('clearlogo') or art.get('tvshow.clearlogo') or ''
    resume = details.get('resume') or {}
    percent = int(100 * resume['position'] / resume['total']) if resume.get('position') and resume.get('total') else 0
    if episode:
        meta = ' · '.join(part for part in (code, title) if part)
        detail = ' · '.join(part for part in (props.get('silo.aired', ''), props.get('silo.cast', '')) if part)
    else:
        meta = ' · '.join(part for part in (year, props.get('silo.genre', ''), rating_markup(props.get('silo.rating', '')))
                          if part)
        detail = props.get('silo.cast', '')
    return {
        'type': kind, 'dbid': str(details.get('%sid' % kind) or details.get('id') or ''),
        'label': title,
        'card.title': show_title if episode else title,
        'card.sub': code if episode else year,
        'card.wide_sub': ' · '.join(part for part in (code, title) if part) if episode else year,
        'hero.title': '' if logo else (show_title if episode else title),
        'hero.meta': meta, 'hero.detail': detail, 'plot': details.get('plot') or '',
        # The info line in parts, for a skin that shows the ratings' marks
        # (Skyline: IMDb's badge, TMDB's wordmark): an episode's has none.
        'hero.facts': meta if episode else ' · '.join(part for part in (year, props.get('silo.genre', '')) if part),
        'rating.imdb': '' if episode else props.get('silo.rating.imdb', ''),
        'rating.tmdb': '' if episode else props.get('silo.rating.tmdb', ''),
        'mpaa': props.get('silo.mpaa', ''),
        'badge1': props.get('silo.badge1', ''), 'badge2': props.get('silo.badge2', ''),
        'badge3': props.get('silo.badge3', ''), 'tint': tint or '',
        'percent': str(percent) if percent else '', 'playcount': watched_mark(kind, details),
        'art.poster': art.get('poster') or art.get('tvshow.poster') or art.get('thumb') or '',
        'art.landscape': landscape or (art.get('thumb') if episode and art.get('thumb') else
                                       art.get('landscape') or art.get('fanart') or art.get('tvshow.fanart') or ''),
        'art.fanart': art.get('fanart') or art.get('tvshow.fanart') or art.get('landscape') or '',
        'art.clearlogo': logo,
    }


def rating_markup(rating):
    """Silo's rating on the hero: the source dimmed, the score bold ("IMDb 6.6")."""
    source, _, score = rating.rpartition(' ')
    if not source:
        return rating
    return '[COLOR 66EDEDED]%s[/COLOR] [B]%s[/B]' % (source, score)


def watched_mark(kind, details):
    """A card's watched mark: a film or episode played, a show with every
    episode watched (Kodi gives a show no play count)."""
    if kind == 'tvshow':
        episodes = details.get('episode') or 0
        return '1' if episodes and (details.get('watchedepisodes') or 0) >= episodes else ''
    return str(details.get('playcount') or '')


def request_card(card):
    """A title Silo can request (mapping.request_card), as a card."""
    meta = ' · '.join(part for part in (str(card.get('year') or ''),
                                        rating_markup('TMDB %.1f' % card['rating']) if card.get('rating') else '')
                    if part)
    available = card.get('state') == 'available'
    props = {
        'type': 'request', 'label': card.get('title') or '',
        'card.title': card.get('title') or '', 'card.sub': type_year(card),
        'card.wide_sub': type_year(card), 'hero.title': card.get('title') or '',
        # Silo's tags on the poster: LIBRARY when it's in, and its state.
        'request.library': '1' if available or card.get('content_id') else '',
        'request.state': 'Available' if available else card.get('label') or '',
        'hero.meta': meta, 'hero.detail': card.get('label') or '', 'plot': card.get('overview') or '',
        'hero.facts': str(card.get('year') or ''),
        'rating.tmdb': '%.1f' % card['rating'] if card.get('rating') else '',
        'art.poster': card.get('poster') or '', 'art.landscape': card.get('backdrop') or '',
        'art.fanart': card.get('backdrop') or '',
        'request.type': card.get('type') or '', 'request.tmdb': str(card.get('tmdb') or ''),
    }
    props.update(request_status(card))
    return props


def type_year(card):
    """Silo's line under a request card: "MOVIE · 2026", "SERIES · 2021"."""
    kind = 'SERIES' if card.get('type') in ('series', 'tvshow') else 'MOVIE'
    return '%s · %s' % (kind, card['year']) if card.get('year') else kind


def colour(value):
    """A "#rrggbb" colour as Kodi's AARRGGBB."""
    value = (value or '').lstrip('#')
    return 'FF' + value.upper() if len(value) == 6 else ''


def brand_card(item, kind, picture=None):
    """A Studios, Networks or Genres card (kind: studio, network, genre):
    its logo (TMDB's, in white) or, for a genre, its name on its colours;
    request.more is the browse page it opens. picture: the hero's backdrop
    and tint (its most popular title's)."""
    name = item.get('display_name') or item.get('slug') or ''
    picture = picture or {}
    return {'type': 'brand', 'label': name, 'card.title': name, 'hero.title': name,
            'art.fanart': picture.get('backdrop') or '', 'tint': picture.get('tint') or '',
            'request.logo': item.get('logo_url') or '', 'request.from': colour(item.get('gradient_from')),
            'request.to': colour(item.get('gradient_to')),
            'request.more': 'browse:%s:%s' % (kind, item.get('slug') or '')}


def request_status(card):
    """A request's status for a Silo-style skin: the dot colour beside the
    card's caption, the hero's phrase, and the colours of Silo's four-step
    track under it (steps behind it white, the one it is on in its colour)."""
    props = {'status.tint': card.get('tint') or '', 'hero.detail': card.get('long') or card.get('label') or ''}
    for step in range(1, mapping.REQUEST_STEPS + 1):
        colour = '' if not card.get('label') else 'FFEDEDED' if step <= (card.get('done') or 0) \
            else card.get('tint') if step == card.get('step') else '33FFFFFF'
        props['status.seg%d' % step] = colour or ''
    return props


def row_properties(row, cards, page='home', count=CARDS):
    """Every property for a page's row `row` (1-based): its cards, then
    empty values for the `count` slots it doesn't fill, so a shorter row
    leaves none."""
    props = {}
    for index in range(1, count + 1):
        fields = cards[index - 1] if index <= len(cards) else {}
        for field in FIELDS:
            props['silo.%s.%d.%d.%s' % (page, row, index, field)] = fields.get(field, '')
    props['silo.%s.%d.cards' % (page, row)] = str(min(len(cards), count))
    return props
