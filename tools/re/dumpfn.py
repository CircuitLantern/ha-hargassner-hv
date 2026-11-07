#!/usr/bin/env python3
"""Dump a decompiled function by name (or substring) from the kuna export."""
import json, sys, os
from pathlib import Path

BASE = Path(os.environ.get(
    "KUNA_DIR",
    Path(__file__).resolve().parents[2] / "smart_hv_2.7.4/data/app/bin/app.kuna",
))

def load():
    rows = [json.loads(l) for l in open(BASE / 'index.jsonl')]
    return rows

def main():
    if len(sys.argv) < 2:
        print("usage: dumpfn.py <name-or-substring> [...]")
        return 1
    rows = load()
    data = open(BASE / 'app.c', 'rb').read()
    want = sys.argv[1:]
    shown = 0
    for r in rows:
        if any(w.lower() in r['name'].lower() for w in want):
            txt = data[r['c_offset']:r['c_offset'] + r['c_len']].decode('utf-8', 'replace')
            print(f"/* ===== {r['name']} @ {r['addr']} ({r['size']} bytes) ===== */")
            print(txt)
            shown += 1
    if not shown:
        print("no match")
    return 0

if __name__ == '__main__':
    sys.exit(main())
