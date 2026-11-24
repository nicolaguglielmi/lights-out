#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The typescript mutation runner (docs/adr/0102): StrykerJS over one
# source directory (.ts, .tsx, .mts, .cts), scored into one JSON line.
#
#   sh .lights-out/runners/mutation.sh <scope> "<test command>"
#
# MUTATION_LINES scopes the run ("<file>=<start>-<end>,..." space-separated,
# or "all" / unset for the whole scope excluding .d.ts and test files).
# Output contract: {"total","killed","survived","score"} on stdout,
# survivors on stderr as "SURVIVOR file:line mutator".
set -eu

scope=${1:?usage: mutation.sh <scope> "<test command>"}
test_cmd=${2:?usage: mutation.sh <scope> "<test command>"}
here=$(dirname "$0")
src=${scope%/}
[ -n "$src" ] || src="."
[ -d "$src" ] || { printf 'mutation.sh: %s is not a directory here (%s)\n' "$src" "$(pwd)" >&2; exit 1; }

prefix=${LIGHTS_OUT_RUNNERS_PREFIX:-/tmp/lights-out-typescript-runners}
if [ ! -x "$prefix/node_modules/.bin/stryker" ]; then
  mkdir -p "$prefix"
  cp "$here/package.json" "$here/package-lock.json" "$prefix/"
  (cd "$prefix" && npm ci --no-audit --no-fund --loglevel=error) >&2
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

start=$(date +%s)
if ! sh -c "$test_cmd" >&2; then
  printf 'mutation.sh: the unmutated test suite does not pass (%s); not a mutation score\n' "$test_cmd" >&2
  exit 1
fi
elapsed=$(( $(date +%s) - start ))
[ "$elapsed" -ge 1 ] || elapsed=1
timeout_ms=$(( elapsed * 10 * 1000 ))

MUTATION_LINES="${MUTATION_LINES:-}" node - "$work" "$src" "$test_cmd" "$timeout_ms" <<'JS' >&2
const fs = require("node:fs");
const path = require("node:path");
const [work, src, testCmd, timeoutMs] = process.argv.slice(2);
const lines = (process.env.MUTATION_LINES || "").trim();
let mutate;
if (lines && lines !== "all") {
  mutate = [];
  for (const group of lines.split(/\s+/)) {
    const eq = group.indexOf("=");
    if (eq < 0) continue;
    const file = group.slice(0, eq);
    for (const range of group.slice(eq + 1).split(",")) {
      if (range) mutate.push(`${file}:${range}`);
    }
  }
} else {
  mutate = [
    `${src}/**/*.ts`,
    `${src}/**/*.tsx`,
    `${src}/**/*.mts`,
    `${src}/**/*.cts`,
    `!${src}/**/*.d.ts`,
    `!${src}/**/*.test.*`,
    `!${src}/**/*.spec.*`,
    `!${src}/**/*-test.*`,
    `!${src}/**/*_test.*`,
  ];
}
const config = {
  testRunner: "command",
  commandRunner: { command: testCmd },
  mutate,
  reporters: ["json"],
  jsonReporter: { fileName: path.join(work, "mutation.json") },
  tempDirName: path.join(work, "stryker-tmp"),
  cleanTempDir: "always",
  timeoutMS: Number(timeoutMs),
  timeoutFactor: 1,
  incremental: false,
  allowConsoleColors: false,
  logLevel: "warn",
};
fs.writeFileSync(path.join(work, "stryker.json"), JSON.stringify(config, null, 2));
JS

"$prefix/node_modules/.bin/stryker" run "$work/stryker.json" >&2

[ -f "$work/mutation.json" ] || { printf 'mutation.sh: Stryker left no JSON report; not a mutation score\n' >&2; exit 1; }
node "$here/score.mjs" "$work/mutation.json"
