# -*- coding: utf-8 -*-
"""Sign in from the Silo phone app, as Silo's own TV apps do: Kodi shows up
in the app's "set up a TV" sheet, the phone hands it the server, and the
phone approves the sign-in. Nothing is typed on the TV.

This is Silo's companion-pairing protocol (silo-android and silo-apple,
PairingProtocol v1) in the plain-TCP form Silo's phone apps speak to a Roku
(_silopair-roku._tcp), since Kodi's Python can't do TLS-PSK. Nothing secret
crosses it: the phone sends a server address, Kodi sends back the sign-in
code Silo gave it, and the tokens come from Silo over HTTPS once the phone
approves. The pushed address is checked against Silo's public identity
before Kodi signs in there.

Pieces: an mDNS responder advertising the service (Advertiser), the
length-prefixed JSON frames (send, Frames), and the TV side of one
connection (Receiver)."""
import json
import socket
import struct
import threading
import time
import uuid

import requests

VERSION = 1
SERVICE = '_silopair-roku._tcp.local.'
META = '_services._dns-sd._udp.local.'
GROUP, MDNS_PORT = '224.0.0.251', 5353
PTR, TXT, SRV, A = 12, 16, 33, 1
CACHE_FLUSH = 0x8000
HOST_TTL, SERVICE_TTL = 120, 4500
MAX_FRAME = 1 << 20


# ----- DNS -----

def name_bytes(name):
    return b''.join(bytes([len(part)]) + part for part in
                    (label.encode('utf-8') for label in name.rstrip('.').split('.'))) + b'\0'


def record(name, kind, ttl, data, flush=False):
    return name_bytes(name) + struct.pack('>2HIH', kind, (CACHE_FLUSH if flush else 0) | 1, ttl, len(data)) + data


def questions(packet):
    """The names a multicast DNS query asks about, lower-cased."""
    from .discovery import read_name
    if len(packet) < 12:
        return []
    flags, count = struct.unpack('>H', packet[2:4])[0], struct.unpack('>H', packet[4:6])[0]
    if flags & 0x8000:            # a response, not a query
        return []
    found, at = [], 12
    for _ in range(count):
        name, at = read_name(packet, at)
        found.append(name.lower())
        at += 4
    return found


class Advertiser(object):
    """Answers mDNS queries for Kodi's pairing service on the local network
    (and announces it, and says goodbye), for as long as it runs."""

    def __init__(self, instance, host, address, port, txt):
        self.instance = '%s.%s' % (instance.replace('.', ' '), SERVICE)
        self.host = '%s.local.' % host
        self.address, self.port, self.txt = address, port, txt
        self.names = {n.lower() for n in (SERVICE, META, self.instance, self.host)}
        self.sock = None
        self.running = False
        self.last_sent = 0

    def records(self, ttl=None):
        txt = b''.join(bytes([len(item)]) + item for item in
                       ('%s=%s' % pair for pair in self.txt.items()) for item in [item.encode('utf-8')])
        answers = [
            record(SERVICE, PTR, SERVICE_TTL if ttl is None else ttl, name_bytes(self.instance)),
            record(META, PTR, SERVICE_TTL if ttl is None else ttl, name_bytes(SERVICE)),
            record(self.instance, SRV, HOST_TTL if ttl is None else ttl,
                   struct.pack('>3H', 0, 0, self.port) + name_bytes(self.host), flush=True),
            record(self.instance, TXT, SERVICE_TTL if ttl is None else ttl, txt, flush=True),
            record(self.host, A, HOST_TTL if ttl is None else ttl, socket.inet_aton(self.address), flush=True),
        ]
        return struct.pack('>6H', 0, 0x8400, 0, len(answers), 0, 0) + b''.join(answers)

    def send(self, ttl=None):
        try:
            self.sock.sendto(self.records(ttl), (GROUP, MDNS_PORT))
            self.last_sent = time.time()
        except OSError:
            pass

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(('', MDNS_PORT))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                        struct.pack('4s4s', socket.inet_aton(GROUP), socket.inet_aton(self.address)))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.address))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.settimeout(0.5)
        self.sock, self.running = sock, True
        thread = threading.Thread(target=self.serve, name='silokodi-pairing-mdns')
        thread.daemon = True
        thread.start()

    def serve(self):
        announced = 0
        while self.running:
            if announced < 3 and time.time() - self.last_sent >= 1:
                self.send()               # announce three times, a second apart
                announced += 1
            try:
                packet = self.sock.recvfrom(9000)[0]
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                asked = questions(packet)
            except (ValueError, IndexError, struct.error):
                continue
            # At most one answer a second (RFC 6762's rate limit), multicast.
            if self.names.intersection(asked) and time.time() - self.last_sent >= 1:
                self.send()

    def stop(self):
        if self.sock:
            self.running = False
            self.send(ttl=0)              # goodbye: phones drop Kodi from their list
            try:
                self.sock.close()
            except OSError:
                pass


# ----- frames and messages -----

def frame(message):
    payload = json.dumps(dict(message, v=VERSION), separators=(',', ':')).encode('utf-8')
    return struct.pack('>I', len(payload)) + payload


class Frames(object):
    """Complete JSON messages from bytes as they arrive."""

    def __init__(self):
        self.buffer = b''

    def append(self, data):
        self.buffer += data
        found = []
        while len(self.buffer) >= 4:
            length = struct.unpack('>I', self.buffer[:4])[0]
            if length > MAX_FRAME:
                raise ValueError('frame too large')
            if len(self.buffer) < 4 + length:
                break
            found.append(json.loads(self.buffer[4:4 + length].decode('utf-8')))
            self.buffer = self.buffer[4 + length:]
        return found


def choose_address(push, get=requests.get):
    """The address to sign in at: the pushed one or another the phone offered
    (the deployment's endpoints), the first whose public identity answers and
    matches the identity the phone verified. (address, None) or
    (None, failure code)."""
    wanted = push.get('serverIdentity')
    addresses = [push.get('serverURL') or ''] + [e.get('url') or '' for e in push.get('endpoints') or []]
    mismatch = False
    for address in addresses:
        address = address.strip().rstrip('/')
        if not address.startswith(('http://', 'https://')):
            continue
        try:
            identity = get(address + '/api/v2/system/identity', timeout=8).json().get('server_id')
        except (requests.RequestException, ValueError, AttributeError):
            continue
        if not identity or (wanted and identity != wanted):
            mismatch = mismatch or bool(identity)
            continue
        return address, None
    return None, 'identity_mismatch' if mismatch else 'unreachable'


class Receiver(object):
    """Kodi's side of one pairing connection.

    sign_in(address, on_code) signs Kodi in at an address: it starts Silo's
    device sign-in, calls on_code(user_code, match_code) once Silo has a code,
    and returns None when approved or a failure code ('denied', 'expired',
    'auth_failed'). on_status(text) reports progress for the TV's window."""

    def __init__(self, tv_name, device_id, sign_in, on_status=lambda text: None, choose=choose_address):
        self.tv_name, self.device_id = tv_name, device_id
        self.sign_in, self.on_status, self.choose = sign_in, on_status, choose
        self.signed_in = None             # the address Kodi signed in at

    def run(self, conn):
        """Drive one connection until the phone is done, cancels or goes."""
        conn.settimeout(1.0)

        def send(message):
            conn.sendall(frame(message))
        send({'type': 'hello', 'tvName': self.tv_name, 'tvDeviceId': self.device_id, 'state': 'setup',
              'supportedVersions': [VERSION]})
        frames = Frames()
        while True:
            try:
                data = conn.recv(16384)
            except socket.timeout:
                continue
            if not data:
                return
            for message in frames.append(data):
                kind = message.get('type')
                if kind in ('done', 'cancel'):
                    return
                if kind == 'pushServer':
                    self.push(message, send)

    def push(self, message, send):
        pushed = message.get('serverURL') or ''
        name = message.get('serverName') or pushed
        self.on_status(('checking', name))
        address, failure = self.choose(message)
        if address:
            def on_code(user_code, match_code):
                self.on_status(('code', name, user_code))
                send({'type': 'deviceStarted', 'serverURL': pushed, 'userCode': user_code, 'matchCode': match_code})
            failure = self.sign_in(address, on_code)
        if failure:
            self.on_status(('failed', name, failure))
            send({'type': 'serverResult', 'serverURL': pushed, 'status': 'failed', 'error': failure})
            return
        self.signed_in = address
        self.on_status(('signed_in', name))
        send({'type': 'serverResult', 'serverURL': pushed, 'status': 'signedIn'})


def device_id(store):
    """A stable id for this Kodi, as the phone keys TVs by it."""
    known = store.get('pairing_device_id')
    if not known:
        known = str(uuid.uuid4())
        store.update(pairing_device_id=known)
    return known


def local_address():
    """This device's address on the local network: the one its route out
    uses (a UDP socket's local end; nothing is sent). Kodi's
    Network.IPAddress can still read "Busy" when an add-on asks."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('192.0.2.1', 9))         # TEST-NET-1: never reached, only routed
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


class Session(object):
    """Advertise this Kodi and take pairing connections, one at a time, until
    stopped. receiver_for() makes a Receiver per connection."""

    def __init__(self, name, device, receiver_for):
        self.name, self.device, self.receiver_for = name, device, receiver_for
        self.address = local_address()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('0.0.0.0', 0))
        self.listener.listen(1)
        self.listener.settimeout(0.5)
        txt = {'v': str(VERSION), 'name': name, 'id': device, 'sid': uuid.uuid4().hex, 'st': 'setup'}
        self.advertiser = Advertiser(name, 'kodi-%s' % device[:8], self.address, self.listener.getsockname()[1], txt)
        self.running = False
        self.signed_in = None

    def start(self):
        self.advertiser.start()
        self.running = True
        thread = threading.Thread(target=self.serve, name='silokodi-pairing')
        thread.daemon = True
        thread.start()

    def serve(self):
        while self.running and not self.signed_in:
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            receiver = self.receiver_for()
            try:
                receiver.run(conn)
            except (OSError, ValueError):
                pass
            finally:
                conn.close()
            self.signed_in = self.signed_in or receiver.signed_in

    def stop(self):
        self.running = False
        self.advertiser.stop()
        try:
            self.listener.close()
        except OSError:
            pass
