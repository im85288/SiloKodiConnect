"""Draw SiloKodiConnect's 512x512 icon in the Skyline skin's look: Skyline's
dark splash, its purple-to-pink disc, and the add-on's own silo with a play
symbol in place of the skyline, over the name "Connect".

Needs Pillow and a checkout of the Skyline skin (its splash and Inter font).
Usage: python draw_icon.py OUT.png [SKYLINE_CHECKOUT]
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

SKYLINE = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser('~/dev/skin.silo.skyline')
SCALE = 4                       # drawn large, then reduced: smooth edges
TOP, BOTTOM = (119, 81, 230), (205, 118, 142)   # Skyline's disc, top to bottom
WHITE = (247, 244, 242)         # Skyline's buildings
PLAY = (152, 94, 197)           # the disc's middle

DISC_CX, DISC_CY, DISC_R = 256, 196, 150
# The silo: a dome on a body, standing on the disc's lower edge.
BODY_L, BODY_R, DOME_CY = 206, 306, 150
PLAY_CX, PLAY_CY, PLAY_H = 262, 250, 62


def disc():
    size = 512 * SCALE
    gradient = Image.new('RGB', (1, size))
    for y in range(size):
        t = min(1.0, max(0.0, (y / SCALE - (DISC_CY - DISC_R)) / (2 * DISC_R)))
        gradient.putpixel((0, y), tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)))
    layer = gradient.resize((size, size)).convert('RGBA')
    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    s = SCALE
    draw.ellipse([(DISC_CX - DISC_R) * s, (DISC_CY - DISC_R) * s, (DISC_CX + DISC_R) * s, (DISC_CY + DISC_R) * s], 255)
    layer.putalpha(mask)
    # The silo, cut off by the disc's edge as Skyline's buildings are.
    silo = Image.new('L', (size, size), 0)
    d = ImageDraw.Draw(silo)
    half = (BODY_R - BODY_L) / 2.0
    d.ellipse([BODY_L * s, (DOME_CY - half) * s, BODY_R * s, (DOME_CY + half) * s], 255)
    d.rectangle([BODY_L * s, DOME_CY * s, BODY_R * s, (DISC_CY + DISC_R) * s], 255)
    silo = Image.composite(silo, Image.new('L', (size, size), 0), mask)
    white = Image.new('RGBA', (size, size), WHITE + (255,))
    layer.paste(white, (0, 0), silo)
    # A play symbol on the silo, in the disc's colour.
    d = ImageDraw.Draw(layer)
    left, right = PLAY_CX - PLAY_H * 0.42, PLAY_CX + PLAY_H * 0.58
    d.polygon([(left * s, (PLAY_CY - PLAY_H / 2) * s), (right * s, PLAY_CY * s), (left * s, (PLAY_CY + PLAY_H / 2) * s)],
              fill=PLAY + (255,))
    return layer.resize((512, 512), Image.LANCZOS)


def main(out):
    splash = Image.open(os.path.join(SKYLINE, 'media', 'silo', 'splash_bg.png')).convert('RGB')
    icon = splash.resize((1920, 1080), Image.BICUBIC).crop((420, 0, 1500, 1080)).resize((512, 512), Image.BICUBIC)
    icon = icon.convert('RGBA')
    icon.alpha_composite(disc())
    draw = ImageDraw.Draw(icon)
    font = ImageFont.truetype(os.path.join(SKYLINE, 'fonts', 'Inter-Bold.ttf'), 64)
    draw.text((256, 400), 'Connect', font=font, fill=(0xED, 0xED, 0xED), anchor='mm')
    icon.convert('RGB').save(out)


if __name__ == '__main__':
    main(sys.argv[1])
