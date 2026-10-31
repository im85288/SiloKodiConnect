# -*- coding: utf-8 -*-
"""Jump a skin's list to a letter, for an A-Z bar.

Kodi has no built-in that selects the first title from a letter (a list only
jumps when a letter is typed), so a Silo-style skin's bar sends

    NotifyAll(silokodi,jump.<list id>.<letter>)

and the service, already running, finds the item in the list's sort letters
(a binary search: the list is sorted by title) and selects it. '#' is the
first title, whatever it starts with.
"""
import unicodedata

import xbmc

SENDER = 'silokodi'
PREFIX = 'Other.jump.'


def letter_key(letter):
    """Where a sort letter falls: 0 for a digit or symbol (they sort before
    A), then A-Z, with an accented letter as its plain one."""
    plain = unicodedata.normalize('NFKD', letter or '')[:1].upper()
    return ord(plain) - ord('A') + 1 if 'A' <= plain <= 'Z' else 0


def first_from(letter, count, letter_at, start=0):
    """The index of the first item, from `start`, whose letter is `letter`
    or after it (the last item when none is); letter_at(i) gives an item's."""
    if letter == '#':
        return start
    wanted = letter_key(letter)
    low, high = start, count
    while low < high:
        middle = (low + high) // 2
        if letter_key(letter_at(middle)) < wanted:
            low = middle + 1
        else:
            high = middle
    return min(low, count - 1)


def handle(sender, method):
    """Jump if this notification is the skin's; True when it was."""
    if sender != SENDER or not method.startswith(PREFIX):
        return False
    list_id, _, letter = method[len(PREFIX):].partition('.')
    if not list_id.isdigit() or not letter:
        return True
    container = 'Container(%s)' % list_id
    count = int(xbmc.getInfoLabel('%s.NumAllItems' % container) or 0)
    if not count:
        return True
    # Kodi's ".." item, when shown, comes first and has no title.
    start = 1 if xbmc.getCondVisibility('%s.ListItemAbsolute(0).IsParentFolder' % container) else 0
    index = first_from(letter, count, lambda i: xbmc.getInfoLabel(
        '%s.ListItemAbsolute(%d).SortLetter' % (container, i)), start)
    xbmc.executebuiltin('SetFocus(%s,%d,absolute)' % (list_id, index))
    return True
