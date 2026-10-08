#!/usr/bin/env python3
"""draw_icon.py - draw the KeySynth icon (both frames) as 1-bit PNGs.

Offline only. The pedal shows an effect as a picture, 1 bit per pixel, in two
sizes: 72x97 and 102x128. stomphacks' build draws a plain frame with the
name; this draws a light stompbox with an LED, four knobs, the name, a
keyboard with a footswitch, the maker's name in small print at the bottom
and the version from the manifest at the top right, and
tools-local/set_icon.py puts it into a build. Every pixel is drawn here.

The pedal draws the four knobs of the current page into the picture itself
when the effect is open, always at the same places. LED and knobs therefore
sit where the stock effects of the MS-60B+ have theirs (measured on icons
from the pedal backup: centres and radii only, no pixels copied). The icon
file carries a flag for a dark body (stock: 0 on light pedals, 1 on dark
ones); stomphacks writes 0, so the body here is light.

Usage (from the project root):
  stomphacks/.venv/bin/python3 tools-local/draw_icon.py effects/keysynth/icon
writes icon_0.png (72x97) and icon_1.png (102x128) there. The version comes
from manifest.json next to that folder ("1.00" is drawn as V1.0); draw the
icon again after changing it.
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = ImageFont.load_default()
BLACK, WHITE = 0, 255

MAKER = "SLAMINSKI"

# Per frame: all coordinates in pixels. knobs = (centres x, centre y, outer radius, ring width,
# radius of the inner disc, half width of the pointer); maker = (centre x, y, space between
# letters); version = (right edge x, y, space between letters).
FRAMES = {
    "icon_0.png": dict(size=(72, 97), radius=5, led=(31, 5, 39, 13),
                       knobs=((11, 27, 43, 59), 25, 7, 1, 5, 0),
                       key=(35, 2, 2), synth=(54, 2),
                       keys=(6, 65, 65, 84), white_keys=8, switch=(36, 75, 8), maker=(36, 87, 1),
                       version=(65, 7, 1)),
    "icon_1.png": dict(size=(102, 128), radius=7, led=(46, 7, 55, 16),
                       knobs=((16, 39, 62, 85), 33, 10, 2, 7, 1),
                       key=(49, 2, 2), synth=(70, 3),
                       keys=(9, 84, 92, 111), white_keys=10, switch=(51, 98, 12), maker=(51, 116, 2),
                       version=(92, 9, 2)),
}

# Small print, five pixels high: the letters of the maker's name, V, digits and the point.
SMALL = {
    "S": (".###", "#...", ".##.", "...#", "###."),
    "L": ("#...", "#...", "#...", "#...", "####"),
    "A": (".##.", "#..#", "####", "#..#", "#..#"),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#"),
    "I": ("###", ".#.", ".#.", ".#.", "###"),
    "N": ("#..#", "##.#", "#.##", "#..#", "#..#"),
    "K": ("#..#", "#.#.", "##..", "#.#.", "#..#"),
    "V": ("#...#", "#...#", ".#.#.", ".#.#.", "..#.."),
    "0": (".##.", "#..#", "#..#", "#..#", ".##."),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###.", "...#", ".##.", "#...", "####"),
    "3": ("###.", "...#", ".##.", "...#", "###."),
    "4": ("#..#", "#..#", "####", "...#", "...#"),
    "5": ("####", "#...", "###.", "...#", "###."),
    "6": (".##.", "#...", "###.", "#..#", ".##."),
    "7": ("####", "...#", "..#.", ".#..", ".#.."),
    "8": (".##.", "#..#", ".##.", "#..#", ".##."),
    "9": (".##.", "#..#", ".###", "...#", ".##."),
    ".": (".", ".", ".", ".", "#"),
}


def lettering(img, text, centre, y, scale, spacing):
    """Text in the default bitmap font, one pixel bolder, scaled and centred."""
    width = sum(int(FONT.getlength(c)) + spacing for c in text)
    mask = Image.new("L", (width + 2, 12), 0)
    d, x = ImageDraw.Draw(mask), 0
    for c in text:
        d.text((x, 0), c, fill=255, font=FONT)
        d.text((x + 1, 0), c, fill=255, font=FONT)
        x += int(FONT.getlength(c)) + spacing
    mask = mask.crop(mask.getbbox())
    mask = mask.resize((mask.width * scale, mask.height * scale), Image.NEAREST)
    img.paste(BLACK, (centre - mask.width // 2, y), mask.point(lambda v: 255 if v > 127 else 0))


def small_print(img, text, at, y, spacing, right=False):
    """Small print centred on x = at, or ending there."""
    width = sum(len(SMALL[c][0]) for c in text) + spacing * (len(text) - 1)
    x = at - width + 1 if right else at - width // 2
    for c in text:
        for row, line in enumerate(SMALL[c]):
            for column, dot in enumerate(line):
                if dot == "#":
                    img.putpixel((x + column, y + row), BLACK)
        x += len(SMALL[c][0]) + spacing


def knob(d, x, y, r, ring, disc, pointer):
    d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE, outline=BLACK, width=ring)
    d.ellipse([x - disc, y - disc, x + disc, y + disc], fill=BLACK)
    d.rectangle([x - pointer, y - disc + 2, x + pointer, y], fill=WHITE)     # pointing straight up


def keyboard(d, box, white_keys):
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=WHITE, outline=BLACK)
    key = (x1 - x0) / white_keys
    for i in range(1, white_keys):
        x = round(x0 + i * key)
        d.line([x, y0, x, y1], fill=BLACK)
    for i in range(white_keys - 1):
        if i % 7 in (0, 1, 3, 4, 5):          # no black key between E-F and B-C
            x = round(x0 + (i + 1) * key)
            d.rectangle([x - key * 0.3, y0, x + key * 0.3, y0 + int((y1 - y0) * 0.6)], fill=BLACK)


def footswitch(d, x, y, r):
    if r < 10:                                # too small for an octagon: a round one
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE, outline=BLACK, width=2)
    else:
        d.regular_polygon((x, y, r), 8, rotation=22.5, fill=WHITE, outline=BLACK)
        d.regular_polygon((x, y, r - 1), 8, rotation=22.5, outline=BLACK)
    inner = r // 2 + 1
    d.ellipse([x - inner, y - inner, x + inner, y + inner], outline=BLACK, width=2)


def version_label(version):
    """ "1.00" -> "V1.0", "1.25" -> "V1.25": one trailing zero is not drawn."""
    return "V" + (version[:-1] if version.endswith("0") else version)


def draw(label, size, radius, led, knobs, key, synth, keys, white_keys, switch, maker, version):
    w, h = size
    img = Image.new("L", size, WHITE)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1, 1, w - 2, h - 2], radius=radius, fill=WHITE, outline=BLACK, width=2)
    d.ellipse(led, fill=BLACK)
    centres, y, *shape = knobs
    for x in centres:
        knob(d, x, y, *shape)
    lettering(img, "KEY", w // 2, key[0], key[1], key[2])
    lettering(img, "SYNTH", w // 2, synth[0], 1, synth[1])
    keyboard(d, keys, white_keys)
    footswitch(d, *switch)
    small_print(img, MAKER, *maker)
    small_print(img, label, *version, right=True)
    return img.convert("1")


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    label = version_label(json.loads((out.parent / "manifest.json").read_text())["version"])
    for filename, frame in FRAMES.items():
        draw(label, **frame).save(out / filename)
        print("wrote %s (%dx%d)" % ((out / filename), *frame["size"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
