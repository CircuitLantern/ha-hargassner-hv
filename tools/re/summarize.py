#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Summarize handler functions: string literals, statuses, direct calls."""
import json, os, re, struct, sys
from pathlib import Path

BASE = Path(os.environ.get(
    "KUNA_DIR",
    Path(__file__).resolve().parents[2] / "smart_hv_2.7.4/data/app/bin/app.kuna",
))
BIN = BASE / '..' / 'app'
segs = [(0x10000, 0, 0xecc338), (0xeec82c, 0xecc82c, 0xd774)]
blob = open(BIN, 'rb').read()

def rd(v, n=64):
    for va, off, sz in segs:
        if va <= v < va + sz:
            fo = off + (v - va)
            return blob[fo:fo+n]
    return b''

def cstr(v, n=64):
    b = rd(v, n)
    return b.split(b'\0')[0].decode('utf-8', 'replace')

rows = [json.loads(l) for l in open(BASE / 'index.jsonl')]
data = open(BASE / 'app.c', 'rb').read()

def body(r):
    return data[r['c_offset']:r['c_offset']+r['c_len']].decode('utf-8', 'replace')

lit_re = re.compile(r'fromAscii_helper\((?:[^,()]*,\s*)?(0x[0-9a-fA-F]+|"[^"]*")')
call_re = re.compile(r'(_ZN[0-9A-Za-z_]+|(?:[A-Za-z_][A-Za-z0-9_:<>~]*::[A-Za-z_][A-Za-z0-9_]*))\(')
status_re = re.compile(r'setStatusEi10QByteArray\([^,]+,(0x[0-9a-fA-F]+|\d+)')

for r in rows:
    n = r['name']
    if not n.startswith(sys.argv[1] if len(sys.argv) > 1 else ''):
        continue
    b = body(r)
    lits = []
    for m in lit_re.finditer(b):
        t = m.group(1)
        if t.startswith('"'):
            lits.append(t.strip('"'))
        else:
            s = cstr(int(t, 16), 48)
            if s and all(32 <= ord(c) < 127 for c in s):
                lits.append(s)
    lits = list(dict.fromkeys(lits))
    sts = sorted(set(int(x, 0) for x in status_re.findall(b)))
    calls = [c for c in dict.fromkeys(call_re.findall(b)) if 'Handler::' in c or c.startswith('_ZN') and 'Handler' in c]
    print(f"{r['addr']:>8} {r['size']:>5} {n}")
    if lits:
        print("    strings:", lits)
    if sts:
        print("    status:", sts)
    if calls:
        print("    calls:", calls[:12])
