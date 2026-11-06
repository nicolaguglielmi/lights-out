#!/usr/bin/env python3
"""Spike: a script that calls agy's file tools once each, arguments filled from
the declared schema by name; prints it."""
import json, re, sys
tools = {t["name"]: t for t in json.load(open(sys.argv[1]))}
ws = sys.argv[2]
want = {"view_file": "README", "write_to_file": "written.txt", "grep_search": "README", "list_dir": "."}
steps = []
for name, target in want.items():
    t = tools.get(name)
    if not t: continue
    p = t.get("parameters") or t.get("parametersJsonSchema") or {}
    args = {}
    for k, s in (p.get("properties") or {}).items():
        typ = (s.get("type") or "").lower()
        if re.search(r"path|file|dir", k, re.I) and typ == "string":
            args[k] = ws if target == "." or re.search(r"dir|search", k, re.I) else ws + "/" + target
        elif re.search(r"content|code", k, re.I) and typ == "string":
            args[k] = "written by the spike\n"
        elif re.search(r"query|pattern", k, re.I) and typ == "string":
            args[k] = "hello"
        elif re.search(r"includ", k, re.I):
            args[k] = ["README"] if typ == "array" else "README"
        elif k in (p.get("required") or []):
            args[k] = {"integer": 1, "number": 1, "boolean": False, "array": [], "object": {}}.get(typ, "spike")
    steps.append({"op": "tool", "name": name, "args": args})
steps.append({"op": "final", "text": "done"})
print(json.dumps(steps))
