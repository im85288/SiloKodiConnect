# -*- coding: utf-8 -*-
import os

from resources.lib.silokodi import kodi, service

SERVER = 'https://silo.example.test'


class FakeClient(object):
    server_url = SERVER

    def auth_headers(self, profile=True):
        return {'Authorization': 'Bearer secret'}


class FakeResponse(object):
    def __init__(self, content_type):
        self.headers = {'Content-Type': content_type}
        self.content = b'picture'

    def raise_for_status(self):
        pass


class Fetcher(object):
    def __init__(self, content_type='image/png'):
        self.content_type = content_type
        self.calls = []

    def __call__(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        return FakeResponse(self.content_type)


class Host(object):
    client = FakeClient()


def save(monkeypatch, tmp_path, url, ref, content_type='image/png'):
    fetch = Fetcher(content_type)
    monkeypatch.setattr(service.requests, 'get', fetch)
    monkeypatch.setattr(kodi, 'profile_dir', lambda: str(tmp_path))
    return service.Service.save_avatar(Host(), url, ref), fetch.calls


def test_server_avatar_is_fetched_with_the_token_and_kept(monkeypatch, tmp_path):
    path, calls = save(monkeypatch, tmp_path, '/avatars/presets/fox.png', 'preset:fox')
    assert calls == [(SERVER + '/avatars/presets/fox.png', {'Authorization': 'Bearer secret'})]
    assert path.endswith('.png') and open(path, 'rb').read() == b'picture'
    # The same picture is not fetched again.
    again, calls = save(monkeypatch, tmp_path, '/avatars/presets/fox.png', 'preset:fox')
    assert again == path and calls == []


def test_the_token_never_goes_to_an_image_host(monkeypatch, tmp_path):
    _, calls = save(monkeypatch, tmp_path, 'https://s3.example.test/avatars/1/256.jpg?X-Amz-Signature=x',
                    'upload:avatars/1/p/original', 'image/jpeg')
    assert calls == [('https://s3.example.test/avatars/1/256.jpg?X-Amz-Signature=x', {})]
    _, calls = save(monkeypatch, tmp_path, 'https://silo.example.test.evil/a.png', 'preset:evil')
    assert calls[0][1] == {}


def test_a_new_picture_replaces_the_old_and_svg_is_skipped(monkeypatch, tmp_path):
    first, _ = save(monkeypatch, tmp_path, '/avatars/presets/fox.png', 'preset:fox')
    second, _ = save(monkeypatch, tmp_path, '/avatars/presets/owl.png', 'preset:owl')
    assert not os.path.exists(first) and os.path.exists(second)
    # Silo's bundled presets are SVG only: the skin draws the initial.
    svg, _ = save(monkeypatch, tmp_path, '/profile-avatars/fox.svg', 'preset:fox-svg', 'image/svg+xml')
    assert svg == '' and os.path.exists(second)


def test_dicebear_pictures_are_fetched_as_png(monkeypatch, tmp_path):
    path, calls = save(monkeypatch, tmp_path,
                       'https://api.dicebear.com/9.x/bottts/svg?backgroundType=gradientLinear&radius=24&seed=a&size=128',
                       'preset:dicebear:bottts:a')
    assert calls == [('https://api.dicebear.com/9.x/bottts/png?backgroundType=gradientLinear&radius=24&seed=a&size=256',
                      {})]
    assert path.endswith('.png')
