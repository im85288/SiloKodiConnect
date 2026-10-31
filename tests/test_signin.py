# -*- coding: utf-8 -*-
import socket
import struct

from resources.lib.silokodi import discovery, pairing


def _name(name):
    return b''.join(bytes([len(p)]) + p.encode() for p in name.rstrip('.').split('.')) + b'\0'


def _record(name, kind, data):
    return _name(name) + struct.pack('>2HIH', kind, 1, 120, len(data)) + data


def test_an_mdns_answer_gives_the_servers_address_port_and_id():
    instance = 'Silo._silo._tcp.local.'
    host = 'silo-64a806e5-abc123.local.'
    txt = b''.join(bytes([len(item)]) + item for item in (b'v=1', b'id=64a8'))
    answers = [_record(discovery.SERVICE, discovery.PTR, _name(instance)),
               _record(instance, discovery.SRV, struct.pack('>3H', 0, 0, 8090) + _name(host)),
               _record(instance, discovery.TXT, txt),
               _record(host, discovery.A, socket.inet_aton('192.168.1.49'))]
    packet = struct.pack('>6H', 0, 0x8400, 0, len(answers), 0, 0) + b''.join(answers)
    assert discovery.candidates([packet]) == {('192.168.1.49', 8090): '64a8'}


def test_the_query_asks_for_silos_service():
    assert _name(discovery.SERVICE) in discovery.query()


def test_a_server_whose_identity_doesnt_match_its_advertisement_is_left_out(monkeypatch):
    monkeypatch.setattr(discovery, 'listen', lambda seconds: [])
    monkeypatch.setattr(discovery, 'candidates', lambda packets: {('10.0.0.5', 8090): 'right', ('10.0.0.6', 8090): 'right'})

    class Answer(object):
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def get(url, timeout):
        if url.endswith('/identity'):
            return Answer({'server_id': 'right' if '10.0.0.5' in url else 'other'})
        return Answer({'server_name': 'Silo'})
    assert discovery.servers(get=get) == [('Silo (10.0.0.5)', 'http://10.0.0.5:8090')]


class _Answer(object):
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def test_frames_are_length_prefixed_json_and_split_however_they_arrive():
    data = pairing.frame({'type': 'done'}) + pairing.frame({'type': 'cancel', 'reason': 'x'})
    frames = pairing.Frames()
    assert frames.append(data[:5]) == []
    assert frames.append(data[5:]) == [{'type': 'done', 'v': 1}, {'type': 'cancel', 'reason': 'x', 'v': 1}]


def test_the_advertisement_reads_back_as_silos_pairing_service():
    advertiser = pairing.Advertiser('Kodi (SHIELD)', 'kodi-abc12345', '192.168.1.248', 40123,
                                    {'v': '1', 'name': 'Kodi (SHIELD)', 'id': 'dev-1', 'sid': 's', 'st': 'setup'})
    found = {(name, kind): data for name, kind, data in discovery.records(advertiser.records())}
    instance = 'Kodi (SHIELD)._silopair-roku._tcp.local.'
    assert found[(pairing.SERVICE, discovery.PTR)] == instance
    assert found[(instance, discovery.SRV)] == ('kodi-abc12345.local.', 40123)
    assert found[(instance, discovery.TXT)]['st'] == 'setup' and found[(instance, discovery.TXT)]['id'] == 'dev-1'
    assert found[('kodi-abc12345.local.', discovery.A)] == '192.168.1.248'


def test_only_queries_name_their_questions():
    assert pairing.questions(discovery.query(pairing.SERVICE)) == [pairing.SERVICE.lower()]
    advertiser = pairing.Advertiser('Kodi', 'kodi-1', '10.0.0.2', 1, {})
    assert pairing.questions(advertiser.records()) == []          # a response


def test_an_address_is_used_only_when_its_identity_matches():
    push = {'serverURL': 'https://silo.example/', 'serverIdentity': 'abc',
            'endpoints': [{'url': 'http://10.0.0.5:8090', 'kind': 'public'}]}
    ids = {'https://silo.example': 'other', 'http://10.0.0.5:8090': 'abc'}
    get = lambda url, timeout: _Answer({'server_id': ids[url.split('/api/')[0]]})
    assert pairing.choose_address(push, get) == ('http://10.0.0.5:8090', None)
    ids['http://10.0.0.5:8090'] = 'other'
    assert pairing.choose_address(push, get) == (None, 'identity_mismatch')


def test_a_phone_pairs_kodi_end_to_end():
    import socket
    import threading
    tv, phone = socket.socketpair()
    events = []

    def sign_in(address, on_code):
        on_code('48217730', 'apple pear')
        return None
    receiver = pairing.Receiver('Kodi', 'dev-1', sign_in, events.append,
                                choose=lambda push: ('https://silo.example', None))
    thread = threading.Thread(target=receiver.run, args=(tv,))
    thread.start()
    frames, got = pairing.Frames(), []

    def read_until(kind):
        while not any(m['type'] == kind for m in got):
            got.extend(frames.append(phone.recv(4096)))
    read_until('hello')
    assert got[0]['state'] == 'setup' and got[0]['supportedVersions'] == [1]
    phone.sendall(pairing.frame({'type': 'pushServer', 'serverURL': 'https://silo.example', 'serverName': 'Silo'}))
    read_until('serverResult')
    started = next(m for m in got if m['type'] == 'deviceStarted')
    assert started['userCode'] == '48217730' and started['serverURL'] == 'https://silo.example'
    assert next(m for m in got if m['type'] == 'serverResult')['status'] == 'signedIn'
    phone.sendall(pairing.frame({'type': 'done'}))
    thread.join(5)
    assert receiver.signed_in == 'https://silo.example'
    assert [e[0] for e in events] == ['checking', 'code', 'signed_in']
    tv.close()
    phone.close()


def test_a_failed_sign_in_is_reported_to_the_phone():
    import socket
    import threading
    tv, phone = socket.socketpair()
    receiver = pairing.Receiver('Kodi', 'dev-1', lambda address, on_code: 'denied',
                                choose=lambda push: ('https://silo.example', None))
    thread = threading.Thread(target=receiver.run, args=(tv,))
    thread.start()
    frames, got = pairing.Frames(), []
    phone.sendall(pairing.frame({'type': 'pushServer', 'serverURL': 'https://silo.example'}))
    while not any(m['type'] == 'serverResult' for m in got):
        got.extend(frames.append(phone.recv(4096)))
    result = next(m for m in got if m['type'] == 'serverResult')
    assert result['status'] == 'failed' and result['error'] == 'denied'
    phone.sendall(pairing.frame({'type': 'cancel', 'reason': 'user'}))
    thread.join(5)
    assert receiver.signed_in is None
    tv.close()
    phone.close()
