from resources.lib.silokodi import jump

TITLES = ['1917', '2001', 'Alien', 'Amélie', 'Brazil', 'Élite', 'Heat', 'Heat', 'Zodiac']
LETTERS = [title[0] for title in TITLES]


def first(letter, start=0):
    return jump.first_from(letter, len(LETTERS), LETTERS.__getitem__, start)


def test_a_letter_finds_its_first_title_or_the_next_one():
    assert first('#') == 0
    assert first('A') == 2
    assert first('B') == 4
    assert first('C') == 5      # no C: the next letter, E
    assert first('E') == 5      # Élite counts as E
    assert first('H') == 6
    assert first('Z') == 8


def test_past_the_last_title_and_after_kodis_parent_item():
    assert jump.first_from('Z', 3, ['A', 'B', 'C'].__getitem__) == 2
    assert jump.first_from('#', 4, ['', 'A', 'B', 'C'].__getitem__, 1) == 1
    assert jump.first_from('B', 4, ['', 'A', 'B', 'C'].__getitem__, 1) == 2


def test_only_the_skins_notifications_are_handled(monkeypatch):
    focused = []
    monkeypatch.setattr(jump.xbmc, 'executebuiltin', focused.append, raising=False)
    monkeypatch.setattr(jump.xbmc, 'getCondVisibility', lambda condition: False, raising=False)
    labels = {'Container(51).NumAllItems': str(len(LETTERS))}
    labels.update(('Container(51).ListItemAbsolute(%d).SortLetter' % i, letter) for i, letter in enumerate(LETTERS))
    monkeypatch.setattr(jump.xbmc, 'getInfoLabel', labels.get, raising=False)
    assert not jump.handle('xbmc', 'Other.jump.51.A')
    assert not jump.handle('silokodi', 'Other.upnext_data')
    assert jump.handle('silokodi', 'Other.jump.51.H')
    assert focused == ['SetFocus(51,6,absolute)']
