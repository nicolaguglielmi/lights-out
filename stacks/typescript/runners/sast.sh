#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The typescript SAST runner (docs/adr/0102): eslint with
# @typescript-eslint/parser and eslint-plugin-security's recommended rules
# over one source directory (.ts, .tsx, .mts, .cts).
#
#   sh .lights-out/runners/sast.sh <scope>
#
# Output contract (docs/adr/0072 §1): one JSON line on stdout,
# {"findings": N, "items": [{"rule", "file", "line"}]}, with `file`
# relative to the directory the command runs from; diagnostics on stderr;
# non-zero exit only when eslint itself failed.
set -eu

scope=${1:?usage: sast.sh <scope>}
here=$(dirname "$0")
[ -d "$scope" ] || { printf 'sast.sh: %s is not a directory here (%s)\n' "$scope" "$(pwd)" >&2; exit 1; }

prefix=${LIGHTS_OUT_RUNNERS_PREFIX:-/tmp/lights-out-typescript-runners}
if [ ! -x "$prefix/node_modules/.bin/eslint" ]; then
  mkdir -p "$prefix"
  cp "$here/package.json" "$here/package-lock.json" "$prefix/"
  (cd "$prefix" && npm ci --no-audit --no-fund --loglevel=error) >&2
fi
cp "$here/eslint.security.mjs" "$prefix/eslint.config.mjs"

out=$(mktemp)
log=$(mktemp)
trap 'rm -f "$out" "$log"' EXIT

rc=0
"$prefix/node_modules/.bin/eslint" --config "$prefix/eslint.config.mjs" --no-color -f json "$scope" >"$out" 2>"$log" || rc=$?
cat "$log" >&2

if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
  printf 'sast.sh: eslint exited %s -- not a scored run\n' "$rc" >&2
  exit 1
fi

# shellcheck disable=SC2016 # the script is node's, its ${} are template strings
line=$(node -e '
const fs = require("node:fs");
const path = require("node:path");
const results = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const items = [];
for (const file of results) {
  const rel = path.relative(process.cwd(), file.filePath).split(path.sep).join("/");
  for (const m of file.messages) {
    items.push({ rule: m.ruleId ?? "parse", file: rel, line: m.line ?? 0 });
    console.error(`${rel}:${m.line}:${m.column} ${m.ruleId ?? "parse"} ${m.message}`);
  }
}
console.log(JSON.stringify({ findings: items.length, items }));
' "$out") || { printf 'sast.sh: eslint printed no JSON report -- not a scored run\n' >&2; exit 1; }

printf '%s\n' "$line"
