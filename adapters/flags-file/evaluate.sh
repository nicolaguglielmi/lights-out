#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# evaluate-flags-file: evaluates <flag> [version] directly against
# $FLAGS_FILE (OFREP/flagd JSON format) without a daemon. Fail-closed on
# missing file, missing/disabled flag, or invalid variant.
set -eu
flag=${1:-}
version=${2:-}
[ -n "$flag" ] || { printf 'evaluate-flags-file: usage: evaluate.sh <flag> [version]\n' >&2; exit 1; }
: "${FLAGS_FILE:?FLAGS_FILE not set}"

[ -f "$FLAGS_FILE" ] || { printf 'evaluate-flags-file: %s not found\n' "$FLAGS_FILE" >&2; exit 1; }

python3 - "$FLAGS_FILE" "$flag" "$version" <<'PY'
import json
import sys


def eval_logic(node, ctx):
    if not isinstance(node, dict) or len(node) != 1:
        return node
    op, args = next(iter(node.items()))
    if op == "var":
        if isinstance(args, list):
            key = args[0] if args else ""
            default = args[1] if len(args) > 1 else None
            return ctx.get(key, default)
        return ctx.get(args)
    if op == "!":
        val = args[0] if isinstance(args, list) else args
        return not bool(eval_logic(val, ctx))
    if op == "if":
        seq = args if isinstance(args, list) else []
        i = 0
        while i + 1 < len(seq):
            if eval_logic(seq[i], ctx):
                return eval_logic(seq[i + 1], ctx)
            i += 2
        return eval_logic(seq[i], ctx) if i < len(seq) else None
    seq = args if isinstance(args, list) else [args]
    if op in ("==", "==="):
        return len(seq) >= 2 and eval_logic(seq[0], ctx) == eval_logic(seq[1], ctx)
    if op in ("!=", "!=="):
        return len(seq) >= 2 and eval_logic(seq[0], ctx) != eval_logic(seq[1], ctx)
    if op == "in":
        if len(seq) < 2:
            return False
        a = eval_logic(seq[0], ctx)
        b = eval_logic(seq[1], ctx)
        try:
            return a in b
        except TypeError:
            return False
    if op == "and":
        res = True
        for item in seq:
            res = eval_logic(item, ctx)
            if not res:
                return res
        return res
    if op == "or":
        res = False
        for item in seq:
            res = eval_logic(item, ctx)
            if res:
                return res
        return res
    raise ValueError(f"unsupported targeting operator: {op}")


path, flag_name, version = sys.argv[1:4]
try:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
except (OSError, ValueError) as exc:
    print(f"evaluate-flags-file: cannot read {path}: {exc}", file=sys.stderr)
    sys.exit(1)

flags = doc.get("flags") if isinstance(doc, dict) else None
if not isinstance(flags, dict) or flag_name not in flags:
    print(f"evaluate-flags-file: no such flag {flag_name} in {path}", file=sys.stderr)
    sys.exit(1)

spec = flags[flag_name]
if not isinstance(spec, dict):
    print(f"evaluate-flags-file: invalid flag spec for {flag_name}", file=sys.stderr)
    sys.exit(1)

state = spec.get("state", "ENABLED")
if state != "ENABLED":
    print(f"evaluate-flags-file: flag {flag_name} has state={state}", file=sys.stderr)
    sys.exit(1)

variants = spec.get("variants")
if not isinstance(variants, dict):
    print(f"evaluate-flags-file: flag {flag_name} has no variants", file=sys.stderr)
    sys.exit(1)

ctx = {"version": version} if version else {}
chosen = None
if "targeting" in spec and spec["targeting"] is not None:
    try:
        targeted = eval_logic(spec["targeting"], ctx)
    except ValueError as exc:
        print(f"evaluate-flags-file: {flag_name}: {exc}", file=sys.stderr)
        sys.exit(1)
    if isinstance(targeted, str):
        chosen = targeted

if chosen is None:
    chosen = spec.get("defaultVariant")

if not isinstance(chosen, str) or chosen not in variants:
    print(f"evaluate-flags-file: flag {flag_name} resolved unknown variant {chosen!r}", file=sys.stderr)
    sys.exit(1)

value = variants[chosen]
if value is None:
    print(f"evaluate-flags-file: flag {flag_name} variant {chosen} has null value", file=sys.stderr)
    sys.exit(1)

if isinstance(value, str):
    print(value)
else:
    print(json.dumps(value))
PY
