#!/usr/bin/env python3
"""set_icon.py - put your own picture into a stomphacks build.

Offline only; never talks to the pedal.

stomphacks' build draws the effect's picture itself (a frame with the name)
and takes no picture from the manifest. This tool replaces that picture in
both places it lives, in the icon file (.ZIC, two frames) and in the ICON
section of the effect file (one of those frames), and recomputes the
checksum with stomphacks' own function. Nothing else changes: not the code,
not the parameters, not the header.

Run it right after the build and before adapt_ms60b.py. The build's own
pair is kept in <effect>/build/icon-plain/.

Usage (from the project root):
  stomphacks/.venv/bin/python3 tools-local/set_icon.py \\
      effects/<name>/build/<FILE>.ZD2 effects/<name>/icon
The folder holds icon_0.png, icon_1.png, ...: 1-bit PNGs, one per frame of
the build's icon file and of exactly its size (72x97 and 102x128).
"""

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stomphacks" / "zoom-zt2"))
sys.path.insert(0, str(ROOT / "stomphacks" / "tools"))

from PIL import Image  # noqa: E402

import convert_zic  # noqa: E402  (zoom-zt2, read-only use)
import zd2_from_scratch as zfs  # noqa: E402  (stomphacks, read-only use)
import zoomzt2  # noqa: E402

CHECKSUM = range(8, 12)
STOMPHACKS_TARGET = 0x0090


def die(msg):
    sys.exit("set_icon: REFUSED - " + msg)


def frame_image(icon, data):
    """A frame of an icon file as a picture (as convert_zic.py does it)."""
    striped = Image.frombytes("1", (8, icon.bytes), data, "raw", "1;I")
    return convert_zic.destripe(striped, icon.stripes, icon.width, icon.height)


def set_icon(zd2, zic_raw, pictures):
    try:
        zfs.verify_envelope(zd2)
        zfs.verify_zic(zic_raw)
    except ValueError as e:
        die("the build fails stomphacks' own check: %s" % e)
    if struct.unpack_from("<I", zd2, 12)[0] != STOMPHACKS_TARGET:
        die("the effect file's target is not 0x%04x - run this on the build, "
            "before adapt_ms60b.py" % STOMPHACKS_TARGET)

    zic = convert_zic.ZIC.parse(zic_raw)
    if len(pictures) != len(zic.icons):
        die("the icon file has %d frames, %d pictures given" % (len(zic.icons), len(pictures)))
    old_section = bytes(zoomzt2.ZD2.parse(zd2).ICON.data)
    places = [i for i, d in enumerate(zic.datas) if bytes(d.data) == old_section]
    if len(places) != 1:
        die("the ICON section equals %d frames of the icon file, expected one "
            "- not a fresh stomphacks build?" % len(places))
    at = zd2.find(old_section)
    if at < 0 or zd2.find(old_section, at + 1) >= 0:
        die("the ICON section's bytes do not occur exactly once in the effect file")

    for i, (icon, picture) in enumerate(zip(zic.icons, pictures)):
        if picture.mode != "1":
            die("icon_%d.png is not a 1-bit picture" % i)
        if picture.size != (icon.width, icon.height):
            die("icon_%d.png is %dx%d, the frame is %dx%d"
                % (i, *picture.size, icon.width, icon.height))
        zic.datas[i].data = convert_zic.restripe(picture).tobytes()
    new_zic = convert_zic.ZIC.build(zic)
    new_section = bytes(zic.datas[places[0]].data)

    out = bytearray(zd2)
    out[at:at + len(old_section)] = new_section
    out[CHECKSUM.start:CHECKSUM.stop] = struct.pack("<I", zfs.crc32_zd2(bytes(out)))
    out = bytes(out)

    # --- prove the result
    checks = []
    zfs.verify_envelope(out)
    zfs.verify_zic(new_zic)
    checks.append("stomphacks' checks pass on the effect file and on the icon file")
    if len(new_zic) != len(zic_raw) or len(out) != len(zd2):
        die("internal error: a file changed its length")
    allowed = set(CHECKSUM) | set(range(at, at + len(old_section)))
    diff = {i for i in range(len(zd2)) if zd2[i] != out[i]}
    if not diff <= allowed:
        die("internal error: bytes changed outside the ICON section: %s"
            % sorted(diff - allowed)[:8])
    checks.append("the effect file differs from the build only in the checksum and inside "
                  "the ICON section (%d bytes at %d..%d)"
                  % (len(old_section), at, at + len(old_section) - 1))
    a, b = zoomzt2.ZD2.parse(zd2), zoomzt2.ZD2.parse(out)
    for key in ("version", "group", "id", "name", "target"):
        if a[key] != b[key]:
            die("internal error: %s changed" % key)
    if bytes(a.DATA.data) != bytes(b.DATA.data) or a.INFO.dspload != b.INFO.dspload:
        die("internal error: code or dspload changed")
    checks.append("id, name, version, target, dspload and the code section are unchanged")
    back = convert_zic.ZIC.parse(new_zic)
    for i, (icon, data, picture) in enumerate(zip(back.icons, back.datas, pictures)):
        if frame_image(icon, bytes(data.data)).tobytes() != picture.tobytes():
            die("internal error: frame %d does not read back as icon_%d.png" % (i, i))
    if bytes(b.ICON.data) != bytes(back.datas[places[0]].data):
        die("internal error: the ICON section is not frame %d" % places[0])
    checks.append("every frame reads back pixel for pixel as the picture given; the ICON "
                  "section is frame %d (%dx%d)"
                  % (places[0], zic.icons[places[0]].width, zic.icons[places[0]].height))
    return out, new_zic, checks


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    zd2_p, pictures_p = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    if zd2_p.parent.name != "build":
        die("expected <effect>/build/<FILE>.ZD2, got %s" % zd2_p)
    zic_p = zd2_p.with_suffix(".ZIC")
    if not zic_p.is_file():
        die("no icon file next to the build: %s" % zic_p.name)
    keep = zd2_p.parent / "icon-plain"
    if keep.exists():
        die("%s exists - this build already has its picture replaced" % keep.relative_to(ROOT))
    files = sorted(pictures_p.glob("icon_*.png"))
    if not files:
        die("no icon_*.png in %s" % pictures_p)
    pictures = [Image.open(f) for f in files]

    zd2, zic = zd2_p.read_bytes(), zic_p.read_bytes()
    out, new_zic, checks = set_icon(zd2, zic, pictures)

    keep.mkdir()
    (keep / zd2_p.name).write_bytes(zd2)
    (keep / zic_p.name).write_bytes(zic)
    zd2_p.write_bytes(out)
    zic_p.write_bytes(new_zic)
    (zd2_p.parent / "ICON.md").write_text("\n".join(
        ["# Picture: %s" % zd2_p.name, "",
         "Replaced by tools-local/set_icon.py with `%s`." % ", ".join(f.name for f in files),
         "The build's own pair is in `icon-plain/`; `REPORT.md` describes that pair.", "",
         "Checks:", ""] + ["- OK: " + c for c in checks] + [""]))
    for c in checks:
        print("OK  " + c)
    print("wrote %s and %s (build's own pair kept in %s/)"
          % (zd2_p.relative_to(ROOT), zic_p.name, keep.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
