#!/usr/bin/env python3
"""Spike: tables and columns of an agy conversation db, and every string cell
or blob fragment that names a model (gemini-..., *-spike)."""
import re, sqlite3, sys
db = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
for (name, sql) in db.execute("select name, sql from sqlite_master where type='table'"):
    print("table", name, "|", (sql or "").replace("\n", " ")[:300])
    for row in db.execute('select * from "%s"' % name):
        for i, v in enumerate(row):
            b = v if isinstance(v, bytes) else str(v).encode()
            for m in set(re.findall(rb"[A-Za-z0-9_.:/-]*(?:gemini|spike|modelVersion|model_version)[A-Za-z0-9_.:/-]*", b)):
                print("  ", name, "col", i, m.decode(errors="replace")[:120])
