# -*- coding: utf-8 -*-
"""Silo servers on the local network, for sign-in without typing an address.

Silo advertises itself with DNS-SD over multicast DNS (_silo._tcp.local.,
TXT id = its server ID). Its responder answers only multicast queries sent
from port 5353, by multicast, so this asks from 5353 too and listens a
moment. What it finds is a suggestion: each address is kept only if Silo's
public identity there matches the advertised ID. A Silo in a Docker bridge
network isn't advertised on the LAN (Silo's server-discovery.md), so an
empty list is normal; sign-in then offers the phone page or typing."""
import socket
import struct
import time

import requests

GROUP, PORT = '224.0.0.251', 5353
SERVICE = '_silo._tcp.local.'
PTR, TXT, SRV, A = 12, 16, 33, 1


def query(name=SERVICE):
    """A one-question multicast DNS query for name's PTR records."""
    labels = b''.join(bytes([len(part)]) + part.encode('ascii') for part in name.rstrip('.').split('.')) + b'\0'
    return struct.pack('>6H', 0, 0, 1, 0, 0, 0) + labels + struct.pack('>2H', PTR, 1)


def read_name(packet, at):
    """A (possibly compressed) DNS name at offset at: (name, next offset)."""
    parts, end, jumps = [], None, 0
    while True:
        length = packet[at]
        if length & 0xC0 == 0xC0:
            if end is None:
                end = at + 2
            at = ((length & 0x3F) << 8) | packet[at + 1]
            jumps += 1
            if jumps > 20:
                raise ValueError('name loop')
            continue
        at += 1
        if not length:
            return '.'.join(parts) + '.', end if end is not None else at
        parts.append(packet[at:at + length].decode('utf-8', 'replace'))
        at += length


def records(packet):
    """[(name, type, data)] of a DNS message's answers and additional
    records: PTR and SRV targets as names, SRV as (target, port), TXT as a
    {key: value} dict, A as a dotted address."""
    _, _, qd, an, ns, ar = struct.unpack('>6H', packet[:12])
    at = 12
    for _ in range(qd):
        _, at = read_name(packet, at)
        at += 4
    found = []
    for _ in range(an + ns + ar):
        name, at = read_name(packet, at)
        kind, _, _, length = struct.unpack('>2HIH', packet[at:at + 10])
        at += 10
        data = packet[at:at + length]
        if kind == PTR:
            found.append((name, kind, read_name(packet, at)[0]))
        elif kind == SRV:
            port = struct.unpack('>H', data[4:6])[0]
            found.append((name, kind, (read_name(packet, at + 6)[0], port)))
        elif kind == TXT:
            pairs, i = {}, 0
            while i < len(data):
                item = data[i + 1:i + 1 + data[i]].decode('utf-8', 'replace')
                key, _, value = item.partition('=')
                pairs[key] = value
                i += 1 + data[i]
            found.append((name, kind, pairs))
        elif kind == A and length == 4:
            found.append((name, kind, socket.inet_ntoa(data)))
        at += length
    return found


def candidates(packets):
    """{(address, port): advertised server id} from mDNS responses."""
    by_type = {}
    for packet in packets:
        try:
            for name, kind, data in records(packet):
                by_type.setdefault(kind, {})[name.lower()] = data
        except (ValueError, IndexError, struct.error):
            continue
    found = {}
    for instance in by_type.get(PTR, {}).values():
        srv = by_type.get(SRV, {}).get(instance.lower())
        if not srv:
            continue
        address = by_type.get(A, {}).get(srv[0].lower())
        if address:
            found[(address, srv[1])] = (by_type.get(TXT, {}).get(instance.lower()) or {}).get('id', '')
    return found


def listen(seconds=2.0):
    """The raw mDNS responses heard for a moment after asking for Silo."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    packets = []
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(('', PORT))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                        struct.pack('4s4s', socket.inet_aton(GROUP), socket.inet_aton('0.0.0.0')))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.sendto(query(), (GROUP, PORT))
        deadline = time.time() + seconds
        while time.time() < deadline:
            sock.settimeout(max(0.05, deadline - time.time()))
            try:
                packets.append(sock.recvfrom(9000)[0])
            except socket.timeout:
                break
    except OSError:
        pass
    finally:
        sock.close()
    return packets


def servers(seconds=2.0, get=requests.get):
    """[(name, url)] of the Silo servers on the LAN whose identity matches
    their advertisement."""
    found = []
    for (address, port), server_id in sorted(candidates(listen(seconds)).items()):
        url = 'http://%s:%d' % (address, port)
        try:
            identity = get(url + '/api/v2/system/identity', timeout=2).json()
            if server_id and identity.get('server_id') != server_id:
                continue
            name = get(url + '/api/v2/theme/branding', timeout=2).json().get('server_name') or 'Silo'
        except (requests.RequestException, ValueError):
            continue
        if url not in [u for _, u in found]:
            found.append(('%s (%s)' % (name, address), url))
    return found
