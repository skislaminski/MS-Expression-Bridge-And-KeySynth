#!/usr/bin/env python3
"""adapt_ms60b.py - give a stomphacks build the header of an MS-60B+ effect.

Offline only; never talks to the pedal.

stomphacks writes the container header the way MS-70CDR+ effects carry it.
On the MS-60B+ every effect in the backup differs in three header fields,
one fixed value per group:

    offset  12, 4 bytes   target bitfield   0x00a0 (stomphacks: 0x0090)
    offset 111, 11 bytes  group name        the pedal's category name
    offset 122, 3 bytes   category tag      one value per group

This tool copies exactly those 18 bytes from a REFERENCE effect of the same
group, taken from your own pedal backup, into a copy of the build, and
recomputes the checksum with stomphacks' own function. Nothing else changes:
not the id, not the name, not a single byte of the sections.

The result goes to <effect>/ms60b/ together with the unchanged icon. The
build in <effect>/build/ is left alone.

Usage (from the project root):
  stomphacks/.venv/bin/python3 tools-local/adapt_ms60b.py \\
      effects/<name>/build/<FILE>.ZD2 backup/<date>/files/<REFERENCE>.ZD2
"""

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stomphacks" / "zoom-zt2"))
sys.path.insert(0, str(ROOT / "stomphacks" / "tools"))

import zd2_from_scratch as zfs  # noqa: E402  (stomphacks, read-only use)
import zoomzt2  # noqa: E402

TARGET = slice(12, 16)
GROUPNAME = slice(111, 122)
TAG = slice(122, 125)
CHECKSUM = slice(8, 12)
CHANGED = set(range(8, 16)) | set(range(111, 125))

STOMPHACKS_TARGET = 0x0090
MS60B_TARGET = 0x00A0


def die(msg):
    sys.exit("adapt_ms60b: REFUSED - " + msg)


def fields(raw):
    return {
        "target": "0x%04x" % struct.unpack_from("<I", raw, 12)[0],
        "group": "%02x" % raw[95],
        "id": "%08x" % struct.unpack_from("<I", raw, 96)[0],
        "name": raw[100:111].split(b"\0")[0].decode("ascii", "replace"),
        "group name": raw[GROUPNAME].split(b"\0")[0].decode("ascii", "replace"),
        "tag": raw[TAG].hex(),
        "checksum": "%08x" % struct.unpack_from("<I", raw, 8)[0],
    }


def adapt(build, ref):
    # --- the build must be an untouched, valid stomphacks container
    try:
        zfs.verify_envelope(build)
    except ValueError as e:
        die("the build fails stomphacks' envelope check: %s" % e)
    if struct.unpack_from("<I", build, 12)[0] != STOMPHACKS_TARGET:
        die("the build's target is not 0x%04x - already adapted, or not a "
            "stomphacks build" % STOMPHACKS_TARGET)
    if build[TAG] != b"\0\0\0":
        die("the build's category tag is not zero - already adapted?")

    # --- the reference must be a real, valid effect of the same group
    if len(ref) < 144 or ref[:4] != b"ZDLF":
        die("the reference is not a ZD2 file")
    if struct.unpack_from("<I", ref, 4)[0] != 120:
        die("the reference lacks the constant 120 at offset 4")
    if struct.unpack_from("<I", ref, 8)[0] != zfs.crc32_zd2(ref):
        die("the reference's own checksum does not validate - the checksum "
            "recipe would not be proven on this pedal's files")
    if ref[95] != build[95]:
        die("the reference is group %02x, the build is group %02x - use a "
            "reference from the same group" % (ref[95], build[95]))
    if struct.unpack_from("<I", ref, 12)[0] != MS60B_TARGET:
        die("the reference's target is 0x%04x, not 0x%04x"
            % (struct.unpack_from("<I", ref, 12)[0], MS60B_TARGET))
    if ref[125:128] != b"\0\0\0":
        die("the reference has non-zero bytes at 125..127")
    gn = ref[GROUPNAME].split(b"\0")[0]
    if not gn or not all(0x20 <= b < 0x7F for b in gn):
        die("the reference's group name is not printable")

    out = bytearray(build)
    out[TARGET] = ref[TARGET]
    out[GROUPNAME] = ref[GROUPNAME]
    out[TAG] = ref[TAG]
    out[CHECKSUM] = struct.pack("<I", zfs.crc32_zd2(bytes(out)))
    out = bytes(out)

    # --- prove the result
    checks = []
    zfs.verify_envelope(out)
    checks.append("stomphacks envelope check passes on the adapted file")
    diff = {i for i in range(len(build)) if build[i] != out[i]}
    if len(out) != len(build) or not diff <= CHANGED:
        die("internal error: bytes changed outside the three fields: %s"
            % sorted(diff - CHANGED)[:8])
    checks.append("only header bytes at 8..15 and 111..124 differ from the "
                  "build (%d bytes changed); every section is byte-identical"
                  % len(diff))
    for sl, what in ((TARGET, "target"), (GROUPNAME, "group name"), (TAG, "tag")):
        if out[sl] != ref[sl]:
            die("internal error: %s does not match the reference" % what)
    checks.append("target, group name and tag equal the reference's")
    a, b = zoomzt2.ZD2.parse(build), zoomzt2.ZD2.parse(out)
    for key in ("version", "group", "id", "name"):
        if a[key] != b[key]:
            die("internal error: %s changed" % key)
    if a.INFO.dspload != b.INFO.dspload:
        die("internal error: dspload changed")
    checks.append("zoom-zt2 grammar parses it; id, name, version, group and "
                  "dspload unchanged")
    return out, checks


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    build_p, ref_p = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    if build_p.parent.name != "build":
        die("expected <effect>/build/<FILE>.ZD2, got %s" % build_p)
    zic_p = build_p.with_suffix(".ZIC")
    if not zic_p.is_file():
        die("no companion icon next to the build: %s" % zic_p.name)
    build, ref, zic = build_p.read_bytes(), ref_p.read_bytes(), zic_p.read_bytes()
    zfs.verify_zic(zic)

    out, checks = adapt(build, ref)

    outdir = build_p.parent.parent / "ms60b"
    outdir.mkdir(exist_ok=True)
    (outdir / build_p.name).write_bytes(out)
    (outdir / zic_p.name).write_bytes(zic)

    fb, fo, fr = fields(build), fields(out), fields(ref)
    lines = ["# MS-60B+ header: %s" % build_p.name, "",
             "Made by tools-local/adapt_ms60b.py from `build/%s`." % build_p.name,
             "Reference: `%s` from the pedal backup." % ref_p.name, "",
             "| Field | build | adapted | reference |", "|---|---|---|---|"]
    for k in fb:
        lines.append("| %s | %s | %s | %s |" % (k, fb[k], fo[k], fr[k]))
    lines += ["", "Checks:", ""] + ["- OK: " + c for c in checks] + [""]
    (outdir / "ADAPT.md").write_text("\n".join(lines))

    print("%-11s %-12s %-12s %-12s" % ("field", "build", "adapted", "reference"))
    for k in fb:
        print("%-11s %-12s %-12s %-12s" % (k, fb[k], fo[k], fr[k]))
    for c in checks:
        print("OK  " + c)
    print("wrote %s (+ %s, ADAPT.md)" % ((outdir / build_p.name).relative_to(ROOT), zic_p.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
