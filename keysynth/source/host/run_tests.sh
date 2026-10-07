#!/bin/sh
# host/run_tests.sh - build the desktop harness around the KeySynth kernel,
# run the automatic tests and render the audition WAVs. No pedal involved.
#
# Needs effects/keysynth/build/sh_params.h, so build the effect first:
#   (cd stomphacks && .venv/bin/python3 tools/zd2_make_effect.py ../effects/keysynth/manifest.json)
#
# Usage (from anywhere):  host/run_tests.sh [--no-render]
set -e
cd "$(dirname "$0")/.."

PY=stomphacks/.venv/bin/python3
EFFECT=effects/keysynth
OUT=host/build

[ -f "$EFFECT/build/sh_params.h" ] || { echo "missing $EFFECT/build/sh_params.h - build the effect first"; exit 2; }

echo "== generated files current? =="
"$PY" tools-local/gen_tables.py --check

echo "== knob maxima: manifest vs. kernel =="
"$PY" - "$EFFECT" <<'EOF'
import json, re, sys
eff = sys.argv[1]
man = {p["name"]: (p["max"] if "max" in p else len(p["values"]) - 1)
       for p in json.load(open(eff + "/manifest.json"))["params"]}
src = open(eff + "/keysynth.c").read()
defs = {m.group(1): int(m.group(2)) for m in re.finditer(r"#define KS_MAX_(\w+)\s+(\d+)", src)}
want = {"KEY": man["Key"], "WAVE1": man["Wave1"], "WAVE2": man["Wave2"],
        "PITCH": man["Pitch"], "LFO": man["LFO"]}
bad = [k for k, v in want.items() if defs.get(k) != v]
for k in ("Dtune", "Mix", "Glide", "Atk", "Rel", "Rate"):
    if man[k] != defs.get("STEP"):
        bad.append(k)
if bad:
    sys.exit("MISMATCH between manifest.json and KS_MAX_* in keysynth.c: %s" % bad)
print("ok")
EOF

echo "== compile (clang) =="
mkdir -p "$OUT" host/out
clang -std=c99 -O2 -ffp-contract=off -fno-strict-aliasing \
      -Wall -Wextra -Wno-unknown-pragmas \
      -I "$EFFECT/build" -I "$EFFECT" -I host \
      host/harness.c "$EFFECT/keysynth.c" -lm -o "$OUT/harness"

echo "== tests =="
"$OUT/harness" test "$EFFECT/ks_tables.bin"

if [ "$1" != "--no-render" ]; then
    echo "== render =="
    "$OUT/harness" render host/out
fi
