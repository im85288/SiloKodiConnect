# -*- coding: utf-8 -*-
"""Silo's Home rows as window properties, for a skin to draw without a plugin.

A plugin row costs Kodi starting Python every time the row loads (about
0.5 s a row on a SHIELD, most of a row's time), and a Kodi library list
can't keep Silo's order (a title is #1 in one row and #7 in another). So
the service publishes each Home row's cards, in Silo's order, as Home
window properties:

    silo.home.<row>.<card>.<field>   (row 1-MAX_ROWS, card 1-CARDS)

with the text a Silo-style skin shows already made, and the artwork already
resolved (an episode falls back to its show's). A card's `type` and `dbid`
open its info page.
"""
from . import mapping

CARDS = 25  # cards per row; Silo's rows go up to 50, Home shows far fewer
FIELDS = ('type', 'dbid', 'label', 'card.title', 'card.sub', 'card.wide_sub', 'hero.title', 'hero.meta',
          'hero.detail', 'plot', 'mpaa', 'badge1', 'badge2', 'badge3', 'tint', 'percent', 'playcount',
          'art.poster', 'art.landscape', 'art.fanart', 'art.clearlogo')


def card(kind, details, show=None, tint=''):
    """One card's fields, from Kodi's JSON-RPC details (and an episode's show)."""
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
        meta = ' · '.join(part for part in (year, props.get('silo.rating', ''), props.get('silo.genre', '')) if part)
        detail = props.get('silo.cast', '')
    return {
        'type': kind, 'dbid': str(details.get('%sid' % kind) or details.get('id') or ''),
        'label': title,
        'card.title': show_title if episode else title,
        'card.sub': code if episode else year,
        'card.wide_sub': ' • '.join(part for part in (code, title) if part) if episode else year,
        'hero.title': '' if logo else (show_title if episode else title),
        'hero.meta': meta, 'hero.detail': detail, 'plot': details.get('plot') or '',
        'mpaa': props.get('silo.mpaa', ''),
        'badge1': props.get('silo.badge1', ''), 'badge2': props.get('silo.badge2', ''),
        'badge3': props.get('silo.badge3', ''), 'tint': tint or '',
        'percent': str(percent) if percent else '', 'playcount': str(details.get('playcount') or ''),
        'art.poster': art.get('poster') or art.get('tvshow.poster') or art.get('thumb') or '',
        'art.landscape': (art.get('thumb') if episode and art.get('thumb') else
                          art.get('landscape') or art.get('fanart') or art.get('tvshow.fanart') or ''),
        'art.fanart': art.get('fanart') or art.get('tvshow.fanart') or art.get('landscape') or '',
        'art.clearlogo': logo,
    }


def row_properties(row, cards):
    """Every property for Home row `row` (1-based): its cards, then empty
    values for the slots it doesn't fill, so a shorter row leaves none."""
    props = {}
    for index in range(1, CARDS + 1):
        fields = cards[index - 1] if index <= len(cards) else {}
        for field in FIELDS:
            props['silo.home.%d.%d.%s' % (row, index, field)] = fields.get(field, '')
    props['silo.home.%d.cards' % row] = str(min(len(cards), CARDS))
    return props
