#!/usr/bin/env python3
"""Spike: every string inside each gen_metadata blob, with its protobuf field
path (wire format decoded by hand: no schema is published)."""
import sqlite3, sys

def walk(b, path, out, depth=0):
    i, n = 0, len(b)
    while i < n:
        key, i = varint(b, i)
        if key is None: return False
        f, wt = key >> 3, key & 7
        if f == 0: return False
        if wt == 0:
            v, i = varint(b, i)
            if v is None: return False
            out.append(("%s.%d" % (path, f), "int", v))
        elif wt == 1: i += 8
        elif wt == 5: i += 4
        elif wt == 2:
            ln, i = varint(b, i)
            if ln is None or i + ln > n: return False
            chunk = b[i:i + ln]; i += ln
            sub = []
            if depth < 12 and chunk and walk(chunk, "%s.%d" % (path, f), sub, depth + 1):
                out.extend(sub)
            else:
                try:
                    s = chunk.decode()
                    if s.isprintable(): out.append(("%s.%d" % (path, f), "str", s[:100]))
                except UnicodeDecodeError: pass
        else: return False
    return i == n

def varint(b, i):
    r = s = 0
    while i < len(b):
        c = b[i]; i += 1
        r |= (c & 0x7f) << s; s += 7
        if not c & 0x80: return r, i
    return None, i

db = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
for idx, data in db.execute("select idx, data from gen_metadata order by idx"):
    out = []
    walk(data, "", out)
    for p, t, v in out:
        if t == "str" or p.count(".") <= 3:
            print("row", idx, p, t, v)
