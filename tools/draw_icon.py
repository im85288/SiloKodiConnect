"""Draw SiloKodiConnect's 512x512 icon: a silo with a play symbol, anti-aliased.

Pure Python (no Pillow): each pixel is 4x4 supersampled and written as a PNG.
Usage: python draw_icon.py OUT.png [SIZE]
"""
import math
import struct
import sys
import zlib

SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 512
S = SIZE / 512.0  # shapes are designed on a 512 grid
SUB = 4

TOP = (14, 26, 51)        # background gradient
BOTTOM = (29, 61, 122)
GLOW = (64, 120, 220)     # soft light behind the silo
SILO = (238, 242, 248)
SILO_SHADE = (205, 215, 232)
PLAY = (36, 99, 235)
SHADOW = (5, 10, 25)

CX = 256
BODY_L, BODY_R = 186, 326
DOME_CY, DOME_R = 166, 70
BODY_BOTTOM = 430
CAP_H = 8                 # a thin band where the dome meets the body
RINGS = (220, 380)        # hoops around the body, clear of the play symbol
RING_H = 5


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def in_silo(x, y):
    if BODY_L <= x <= BODY_R and DOME_CY <= y <= BODY_BOTTOM:
        return True
    return y < DOME_CY and (x - CX) ** 2 + (y - DOME_CY) ** 2 <= DOME_R ** 2


def in_play(x, y):
    # A right-pointing triangle centred on the body.
    cx, cy, h = CX + 6, 300, 84
    left, right = cx - h * 0.42, cx + h * 0.58
    if not left <= x <= right:
        return False
    half = (h / 2) * (right - x) / (right - left)
    return abs(y - cy) <= half


def in_shadow(x, y):
    return ((x - CX) / 112.0) ** 2 + ((y - 440) / 14.0) ** 2 <= 1


def colour(x, y):
    """Colour at a point on the 512 grid."""
    base = mix(TOP, BOTTOM, y / 512.0)
    glow = max(0.0, 1 - math.hypot(x - CX, y - 300) / 260.0) ** 2 * 0.35
    base = mix(base, GLOW, glow)
    if in_shadow(x, y) and not in_silo(x, y):
        base = mix(base, SHADOW, 0.45)
    if in_silo(x, y):
        # A cylinder lit from the upper left: brightest a third of the way across.
        u = (x - BODY_L) / float(BODY_R - BODY_L)
        shade = min(1.0, abs(u - 0.33) / 0.67) ** 1.6 * 0.75
        base = mix(SILO, SILO_SHADE, shade)
        if DOME_CY <= y <= DOME_CY + CAP_H or any(r <= y <= r + RING_H for r in RINGS):
            base = mix(base, SILO_SHADE, 0.75)
        if in_play(x, y):
            base = PLAY
    return base



def render():
    rows = []
    step = 1.0 / SUB
    for py in range(SIZE):
        row = bytearray([0])
        for px in range(SIZE):
            r = g = b = 0.0
            for sy in range(SUB):
                for sx in range(SUB):
                    c = colour((px + (sx + 0.5) * step) / S, (py + (sy + 0.5) * step) / S)
                    r += c[0]
                    g += c[1]
                    b += c[2]
            n = SUB * SUB
            row += bytes((int(r / n + 0.5), int(g / n + 0.5), int(b / n + 0.5)))
        rows.append(bytes(row))
    return b''.join(rows)


def png(raw):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', SIZE, SIZE, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


if __name__ == '__main__':
    with open(sys.argv[1], 'wb') as handle:
        handle.write(png(render()))
