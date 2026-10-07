#!/usr/bin/env python3
"""kernel_cycles.py - rough worst-case cycle count of a kernel, per audio block.

Reads the kernel disassembly the build writes (build/<NAME>.audio.dis) and
adds up cycles: one per instruction, n for NOP n, 1+n for BNOP x,n, nothing
for a parallel (||) instruction. Loops are found by their backward branch.

The estimate per block is
    straight-line code outside the loops
  + for each top-level loop: iterations x (its own code + its costliest inner loop)
taking the costliest top-level loop when several exist (they are alternatives:
one per mode or waveform). Innermost loops run 16 times (one per frame); a
loop that contains loops runs --outer times.

It counts every arm of every if inside a loop, so it is an upper estimate of
the instruction path, not a measurement: caches, the real branch outcomes and
the pedal's clock are unknown. Use it to compare kernels built the same way.

Usage:
  stomphacks/.venv/bin/python3 tools-local/kernel_cycles.py <file.audio.dis> [--outer N]
"""

import re
import sys


def parse(path):
    seq, labels = [], {}
    for raw in open(path):
        l = raw.rstrip()
        l = re.sub(r"^[0-9a-f]{8}\s+", "", l)             # address column
        l = re.sub(r"^[0-9a-f]{8}\s+|^[0-9a-f]{4}\s+", "", l)   # opcode column
        l = re.sub(r"\s*\(PC[^)]*\)", "", l).strip()
        if not l or l.startswith(".fphead"):
            continue
        m = re.match(r"^([\w$]+):\s*(.*)$", l)
        if m:
            labels[m.group(1)] = len(seq)
            l = m.group(2).strip()
            if not l:
                continue
        par = l.startswith("||")
        body = re.sub(r"^\|\|\s*", "", l)
        body = re.sub(r"^\[\s*!?\s*\w+\s*\]\s*", "", body)
        op = body.split()[0]
        cyc = 0 if par else 1
        m = re.match(r"NOP\s+(\d+)", body)
        if m:
            cyc = int(m.group(1))
        m = re.match(r"BNOP\S*\s+[^,]+,\s*(\d+)", body)
        if m:
            cyc = 1 + int(m.group(1))
        tgt = None
        m = re.match(r"(?:BNOP|BDEC|BPOS|B)\.\S+\s+(\$[\w$]+)", body)
        if m:
            tgt = m.group(1)
        seq.append((op, cyc, tgt))
    return seq, labels


def estimate(path, outer=2):
    seq, labels = parse(path)
    total = sum(c for _, c, _ in seq)
    loops = []                                   # (start, end, kind)
    for j, (op, _, tgt) in enumerate(seq):
        if tgt in labels and labels[tgt] <= j:
            loops.append((labels[tgt], j))
        if op.startswith("SPLOOP"):
            k = next(q for q in range(j, len(seq)) if seq[q][0].startswith("SPKERNEL"))
            loops.append((j, k))
    loops = sorted(set(loops))

    def body(i, j):
        return sum(c for _, c, _ in seq[i:j + 1])

    def children(i, j):
        inner = [(a, b) for a, b in loops if i <= a and b <= j and (a, b) != (i, j)]
        return [(a, b) for a, b in inner
                if not any(c <= a and b <= d and (c, d) != (a, b) for c, d in inner)]

    def cost(i, j):
        kids = children(i, j)
        if not kids:
            return 16 * body(i, j)
        own = body(i, j) - sum(body(a, b) for a, b in kids)
        return outer * (own + max(cost(a, b) for a, b in kids))

    top = [(a, b) for a, b in loops
           if not any(c <= a and b <= d and (c, d) != (a, b) for c, d in loops)]
    straight = total - sum(body(a, b) for a, b in top)
    worst = max((cost(a, b) for a, b in top), default=0)
    return {"instr": len(seq), "straight": straight, "block": straight + worst,
            "loops": [(a, b, body(a, b), len(children(a, b))) for a, b in loops]}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    outer = 2
    if "--outer" in sys.argv:
        outer = int(sys.argv[sys.argv.index("--outer") + 1])
        args = [a for a in args if a != str(outer)]
    if not args:
        print(__doc__)
        return 2
    for path in args:
        e = estimate(path, outer)
        print("%s" % path)
        print("  %d instructions; outside loops ~%d cycles; per block ~%d cycles (upper estimate)"
              % (e["instr"], e["straight"], e["block"]))
        for a, b, cy, kids in e["loops"]:
            print("    loop at %4d..%4d: ~%3d cycles per pass%s"
                  % (a, b, cy, " (contains %d loops)" % kids if kids else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
