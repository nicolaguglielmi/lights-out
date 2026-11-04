#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-promote: proves core/promote.sh, go-for-launch (docs/adr/0020 §4,
# §8), against a stub release role and a stub delivery adapter, cold: no
# plane, no real model. The ci/tower.sh shape: a throwaway root
# symlinking core/ and ci/, a roles/release/role.env pointing HARNESS at
# a stub harness whose run.sh answers with whatever $root/world/calls.json
# the case set (so the case controls what "the release role answered"),
# adapters/stub-delivery/{shift.sh,rollback.sh} logging their own call
# instead of touching Envoy, and adapters/stub-delivery/status.sh printing
# $root/world/status.json (so the case controls what "the plane runs").
# Cases k-p are docs/adr/0047 §3: who answered and how (GO_FOR_LAUNCH_BY,
# GO_FOR_LAUNCH_VIA), the release log's answer lines (RELEASES_LOG), a NO-GO
# that is recorded, the brief written with no answer, and an answer on the
# brief already shown (GO_FOR_LAUNCH_BRIEF_SHA256).
# Written from docs/adr/0020 §4, §8, docs/adr/0026 §2, docs/adr/0047 §3 and
# ticket 0008, not from core/promote.sh.
set -eu
name=promote
. ci/lib.sh

[ -f core/promote.sh ] || nogo "core/promote.sh not found"

repo=$(pwd)
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/adapters/stub-delivery" "$root/adapters/stub-harness" \
  "$root/roles/release" "$root/.crew" "$root/world"
ln -s "$repo/core" "$root/core"
ln -s "$repo/ci" "$root/ci"

cat > "$root/roles/release/role.env" <<'ENV'
THINKING=low
TIMEOUT_S=900
HARNESS=stub-harness
ENV

cat > "$root/adapters/stub-delivery/shift.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
printf 'shift %s\n' "$1" >> "$d/effects.log"
STUB
cat > "$root/adapters/stub-delivery/rollback.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
printf 'rollback\n' >> "$d/effects.log"
STUB
# status.sh reads, it changes nothing: not logged to effects.log.
cat > "$root/adapters/stub-delivery/status.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cat "$d/world/status.json"
STUB
chmod +x "$root/adapters/stub-delivery/shift.sh" "$root/adapters/stub-delivery/rollback.sh" \
  "$root/adapters/stub-delivery/status.sh"

# stub-harness/run.sh: the ADK adapter's artefact contract (docs/adr/0020
# §1), but the answer is whatever $root/world/calls.json the case wrote --
# that file stands in for "what the release role said".
cat > "$root/adapters/stub-harness/run.sh" <<'STUB'
#!/bin/sh
set -eu
d=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
: "${OUT:?OUT not set}"
mkdir -p "$OUT"
printf 'harness-called\n' >> "$d/harness.log"
if [ -f "$d/world/calls.json" ]; then
  cp "$d/world/calls.json" "$OUT/calls.json"
else
  printf '[]\n' > "$OUT/calls.json"
fi
printf 'stub release brief run\n' > "$OUT/final.md"
printf '{"harness":"stub-harness","model":"stub"}\n' > "$OUT/run.json"
STUB
chmod +x "$root/adapters/stub-harness/run.sh"

cd "$root"

record="$root/release.json"
out_dir="$root/.crew/promote"
releases_log="$root/releases.jsonl"

reset() {
  rm -f "$root/effects.log" "$root/harness.log" "$record" "$releases_log"
  rm -rf "$out_dir"
  : > "$root/effects.log"
  : > "$root/harness.log"
  plane_status '"0.2.0-waitlist"' 25
}

# plane_status CANARY_JSON WEIGHT: what status.sh reports (docs/adr/0026
# §2's three keys). reset() sets the plane held_record() describes.
plane_status() {
  printf '{"stable":"0.1.0-m2","canary":%s,"weight":%s}\n' "$1" "$2" > "$root/world/status.json"
}

held_record() {
  cat > "$record" <<'JSON'
{
  "version": "0.2.0-waitlist",
  "stable": "0.1.0-m2",
  "stages": [
    {"pct": 5, "verdict": "GO", "numbers": ["slo: traffic canary(0.2.0-waitlist)=0.208 stable(0.1.0-m2)=4.018"]},
    {"pct": 25, "verdict": "GO", "numbers": ["slo: traffic canary(0.2.0-waitlist)=1.040 stable(0.1.0-m2)=3.186"]}
  ],
  "verdict": "held",
  "held_at": 25,
  "image": "stub-image"
}
JSON
}

rolled_back_record() {
  cat > "$record" <<'JSON'
{
  "version": "0.2.0-waitlist",
  "stable": "0.1.0-m2",
  "stages": [
    {"pct": 5, "verdict": "NO-GO: launchpad — 5%: error_rate 1.000 → rolled back", "numbers": ["slo: error_rate canary(0.2.0-waitlist)=1.000 stable(0.1.0-m2)=0.000"]}
  ],
  "verdict": "rolled-back",
  "held_at": null,
  "image": "stub-image"
}
JSON
}

good_brief_calls() {
  cat > "$root/world/calls.json" <<'JSON'
[{"name": "submit_brief", "args": {"version": "0.2.0-waitlist", "brief": "Release 0.2.0-waitlist: stage 5% GO, stage 25% GO. Recommend promoting to 100%."}}]
JSON
}

brief_missing_version_calls() {
  cat > "$root/world/calls.json" <<'JSON'
[{"name": "submit_brief", "args": {"version": "0.2.0-waitlist", "brief": "Stages passed: 5% and 25%. Ready to promote."}}]
JSON
}

decline_calls() {
  cat > "$root/world/calls.json" <<'JSON'
[{"name": "decline_brief", "args": {"reason": "not confident in the numbers"}}]
JSON
}

log="$root/run.log"
mismatch=""
fail_case() {  # NAME REASON: records the first disagreement only
  [ -z "$mismatch" ] || return 0
  mismatch="$1"
  printf 'ci-promote: %s: %s\n' "$1" "$2" >&2
  cat "$log" >&2
}

effects() { cat "$root/effects.log" 2>/dev/null || true; }
harness_calls() { cat "$root/harness.log" 2>/dev/null || true; }

# run_promote [VAR=val...]: sets $out and $rc; stdin is always /dev/null,
# so "no human answer" (case g) is reached deterministically, never a hang.
# `env` (not a bare VAR=val prefix) because the extra assignments arrive
# through "$@", and a shell only recognises literal NAME=value tokens as
# prefix assignments, never the result of an expansion.
run_promote() {
  out=$(env -u GO_FOR_LAUNCH -u GO_FOR_LAUNCH_BY -u GO_FOR_LAUNCH_VIA -u GO_FOR_LAUNCH_BRIEF_SHA256 \
    DELIVERY=stub-delivery AGENT_HARNESS=stub-harness PLANE_HOST=nowhere.invalid \
    RELEASE_RECORD="$record" RELEASES_LOG="$releases_log" PROMOTE_OUT="$out_dir" "$@" \
    sh core/promote.sh 2>"$log" </dev/null) && rc=0 || rc=$?
}

want_one_line() {  # NAME: fails the case if $out is not exactly one line
  lines=$(printf '%s' "$out" | awk 'END{print NR}')
  [ "$lines" -eq 1 ] || fail_case "$1" "stdout has $lines lines: $out"
}

# (a) no release record at all.
reset
run_promote
want_one_line a
[ "$out" = "NO-GO: go-for-launch — no release record; run ./go release" ] \
  || fail_case a "stdout: $out"
[ "$rc" -eq 1 ] || fail_case a "exit $rc, want 1"
[ -z "$(effects)" ] || fail_case a "effects: $(effects), want none"
[ -z "$(harness_calls)" ] || fail_case a "the release role was called with no record"

# (b) a rolled-back record.
if [ -z "$mismatch" ]; then
  reset
  rolled_back_record
  run_promote
  want_one_line b
  case "$out" in "NO-GO: go-for-launch — "*"rolled-back"*) : ;; *) fail_case b "stdout: $out, want a NO-GO naming the rolled-back verdict" ;; esac
  [ "$rc" -eq 1 ] || fail_case b "exit $rc, want 1"
  [ -z "$(effects)" ] || fail_case b "effects: $(effects), want none"
  [ -z "$(harness_calls)" ] || fail_case b "the release role was called on a rolled-back record"
fi

# (c) held + a good brief + GO_FOR_LAUNCH=GO: promotes.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  run_promote GO_FOR_LAUNCH=GO
  want_one_line c
  [ "$out" = "GO: go-for-launch" ] || fail_case c "stdout: $out"
  [ "$rc" -eq 0 ] || fail_case c "exit $rc, want 0"
  [ "$(effects)" = "shift 100" ] || fail_case c "effects: $(effects), want exactly 'shift 100'"
fi

# (d) held + the same good brief + GO_FOR_LAUNCH=NO-GO: rolls back.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  run_promote GO_FOR_LAUNCH=NO-GO
  want_one_line d
  [ "$out" = "NO-GO: go-for-launch — human said NO-GO" ] || fail_case d "stdout: $out"
  [ "$rc" -eq 1 ] || fail_case d "exit $rc, want 1"
  [ "$(effects)" = "rollback" ] || fail_case d "effects: $(effects), want exactly 'rollback'"
fi

# (e) held + a brief that never names the version + GO_FOR_LAUNCH=GO:
# NO-GO before anything shifts.
if [ -z "$mismatch" ]; then
  reset
  held_record
  brief_missing_version_calls
  run_promote GO_FOR_LAUNCH=GO
  want_one_line e
  case "$out" in "NO-GO: go-for-launch — "*) : ;; *) fail_case e "stdout: $out" ;; esac
  [ "$rc" -eq 1 ] || fail_case e "exit $rc, want 1"
  [ -z "$(effects)" ] || fail_case e "effects: $(effects), want none: a brief missing the version must not reach the shift"
fi

# (f) held + decline_brief: nothing to promote.
if [ -z "$mismatch" ]; then
  reset
  held_record
  decline_calls
  run_promote GO_FOR_LAUNCH=GO
  want_one_line f
  case "$out" in "NO-GO: go-for-launch — "*) : ;; *) fail_case f "stdout: $out" ;; esac
  [ "$rc" -eq 1 ] || fail_case f "exit $rc, want 1"
  [ -z "$(effects)" ] || fail_case f "effects: $(effects), want none: a decline must not reach the shift"
fi

# (g) held + a good brief + no GO_FOR_LAUNCH + stdin not a TTY: no human answer.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  run_promote
  want_one_line g
  case "$out" in "NO-GO: go-for-launch — "*) : ;; *) fail_case g "stdout: $out" ;; esac
  [ "$rc" -eq 1 ] || fail_case g "exit $rc, want 1"
  [ -z "$(effects)" ] || fail_case g "effects: $(effects), want none: no answer must not reach the shift"
fi

# (h) L0: AGENT_HARNESS=replay — no model, so the brief is rendered from
# the record by core/promote.sh itself (docs/adr/0020 §4 note): held +
# GO → GO, exactly `shift 100`, the release role's harness never called,
# and the brief names the version and every stage.
if [ -z "$mismatch" ]; then
  reset
  held_record
  run_promote AGENT_HARNESS=replay GO_FOR_LAUNCH=GO
  want_one_line h
  [ "$out" = "GO: go-for-launch" ] || fail_case h "stdout: $out"
  [ "$rc" -eq 0 ] || fail_case h "exit $rc, want 0"
  [ "$(effects)" = "shift 100" ] || fail_case h "effects: $(effects), want exactly 'shift 100'"
  [ -z "$(harness_calls)" ] || fail_case h "the release role was called at L0"
  brief_file="$out_dir/brief.md"
  [ -f "$brief_file" ] || fail_case h "no brief written"
  grep -q '0.2.0-waitlist' "$brief_file" 2>/dev/null || fail_case h "the L0 brief does not name the version"
  for pct in 5 25; do
    grep -q "$pct%" "$brief_file" 2>/dev/null || fail_case h "the L0 brief does not name the $pct% stage"
  done
fi

# (i) held at 25 for 0.2.0-waitlist, but the plane's canary is another
# version (ticket 0008): NO-GO naming what the plane has, nothing moves,
# no brief, even with GO_FOR_LAUNCH=GO.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  plane_status '"0.3.0-other"' 25
  run_promote GO_FOR_LAUNCH=GO
  want_one_line i
  case "$out" in "NO-GO: go-for-launch — "*"0.3.0-other"*) : ;; *) fail_case i "stdout: $out, want a NO-GO naming the plane's canary 0.3.0-other" ;; esac
  [ "$rc" -eq 1 ] || fail_case i "exit $rc, want 1"
  [ -z "$(effects)" ] || fail_case i "effects: $(effects), want none: a canary the plane does not run must not reach the shift"
  [ -z "$(harness_calls)" ] || fail_case i "the release role was called for a canary the plane does not run"
fi

# (j) held at 25, the plane runs the same canary at another weight (here
# 100: already promoted): NO-GO naming the weight, nothing moves.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  plane_status '"0.2.0-waitlist"' 100
  run_promote GO_FOR_LAUNCH=GO
  want_one_line j
  case "$out" in "NO-GO: go-for-launch — "*"100"*) : ;; *) fail_case j "stdout: $out, want a NO-GO naming the plane's weight 100" ;; esac
  [ "$rc" -eq 1 ] || fail_case j "exit $rc, want 1"
  [ -z "$(effects)" ] || fail_case j "effects: $(effects), want none: a weight other than held_at must not reach the shift"
  [ -z "$(harness_calls)" ] || fail_case j "the release role was called for a plane that does not hold the canary"
fi

# answer_lines: how many lines the release log holds (0 when it does not exist).
answer_lines() { if [ -f "$releases_log" ]; then awk 'END{print NR}' "$releases_log"; else echo 0; fi; }
# one_answer CASE ANSWER BY VIA: the release log holds exactly one line, the
# answer line of docs/adr/0047 §3.
one_answer() {
  [ "$(answer_lines)" -eq 1 ] || { fail_case "$1" "release log has $(answer_lines) lines, want exactly 1"; return 0; }
  jq -e --arg a "$2" --arg by "$3" --arg via "$4" '
    .kind == "answer" and .version == "0.2.0-waitlist" and .answer == $a and .by == $by and .via == $via
    and (.at | type == "string" and length > 0)' "$releases_log" >/dev/null \
    || fail_case "$1" "release log line: $(cat "$releases_log"), want an answer line for 0.2.0-waitlist: $2 by $3 via $4"
}
# no_answer CASE: nothing was written to the release log.
no_answer() { [ "$(answer_lines)" -eq 0 ] || fail_case "$1" "release log has a line, want none: $(cat "$releases_log")"; }
sha_of() { sha256sum "$1" | cut -d' ' -f1; }

# (k) held + a good brief + GO, no GO_FOR_LAUNCH_BY/VIA: the record says who
# answered (unauthenticated, via cli) and the release log gets one line.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  run_promote GO_FOR_LAUNCH=GO
  want_one_line k
  [ "$out" = "GO: go-for-launch" ] || fail_case k "stdout: $out"
  [ "$rc" -eq 0 ] || fail_case k "exit $rc, want 0"
  [ "$(effects)" = "shift 100" ] || fail_case k "effects: $(effects), want exactly 'shift 100'"
  jq -e '.promoted.weight == 100 and .promoted.by == "unauthenticated" and .promoted.via == "cli"
    and (.promoted.at | type == "string" and length > 0) and (has("rejected") | not)' "$record" >/dev/null \
    || fail_case k "record: $(cat "$record"), want promoted {weight 100, at, by unauthenticated, via cli} and no rejected"
  one_answer k GO unauthenticated cli
fi

# (l) the same with GO_FOR_LAUNCH_BY=alice GO_FOR_LAUNCH_VIA=console.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  run_promote GO_FOR_LAUNCH=GO GO_FOR_LAUNCH_BY=alice GO_FOR_LAUNCH_VIA=console
  want_one_line l
  [ "$out" = "GO: go-for-launch" ] || fail_case l "stdout: $out"
  [ "$(effects)" = "shift 100" ] || fail_case l "effects: $(effects), want exactly 'shift 100'"
  jq -e '.promoted.weight == 100 and .promoted.by == "alice" and .promoted.via == "console"' "$record" >/dev/null \
    || fail_case l "record: $(cat "$record"), want promoted by alice via console"
  one_answer l GO alice console
fi

# (m) NO-GO: the rollback is logged, the record gets rejected {at, by, via}
# and no promoted, and the release log gets one NO-GO answer line.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  run_promote GO_FOR_LAUNCH=NO-GO GO_FOR_LAUNCH_BY=bob GO_FOR_LAUNCH_VIA=console
  want_one_line m
  [ "$out" = "NO-GO: go-for-launch — human said NO-GO" ] || fail_case m "stdout: $out"
  [ "$rc" -eq 1 ] || fail_case m "exit $rc, want 1"
  [ "$(effects)" = "rollback" ] || fail_case m "effects: $(effects), want exactly 'rollback'"
  jq -e '.rejected.by == "bob" and .rejected.via == "console" and (.rejected.at | type == "string" and length > 0)
    and (has("promoted") | not)' "$record" >/dev/null \
    || fail_case m "record: $(cat "$record"), want rejected {at, by bob, via console} and no promoted"
  one_answer m NO-GO bob console
fi

# (n) no answer: promote is "prepare the brief" -- brief.md and brief.author
# written, nothing moved, nothing recorded, no answer line.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  cp "$record" "$root/record.before"
  run_promote
  want_one_line n
  case "$out" in "NO-GO: go-for-launch — "*) : ;; *) fail_case n "stdout: $out" ;; esac
  [ "$rc" -eq 1 ] || fail_case n "exit $rc, want 1"
  grep -q '0.2.0-waitlist' "$out_dir/brief.md" 2>/dev/null || fail_case n "no brief.md naming the version in $out_dir"
  [ -s "$out_dir/brief.author" ] || fail_case n "no brief.author in $out_dir"
  [ -z "$(effects)" ] || fail_case n "effects: $(effects), want none: preparing the brief moves nothing"
  cmp -s "$record" "$root/record.before" || fail_case n "the record changed: $(cat "$record")"
  no_answer n
fi

# (o) GO_FOR_LAUNCH_BRIEF_SHA256 = the hash of the brief already on disk
# (written first, by a prepare under a non-replay harness): GO answers that
# brief -- shift 100, the release role is not called again, brief.md is the
# same bytes. The harness would write another brief if it were called.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  run_promote
  [ -f "$out_dir/brief.md" ] || fail_case o "the prepare run wrote no brief.md"
  cp "$out_dir/brief.md" "$root/brief.before"
  sha=$(sha_of "$out_dir/brief.md")
  : > "$root/harness.log"
  cat > "$root/world/calls.json" <<'JSON'
[{"name": "submit_brief", "args": {"version": "0.2.0-waitlist", "brief": "A different brief for 0.2.0-waitlist: stages 5% and 25%. Hold."}}]
JSON
  run_promote GO_FOR_LAUNCH=GO GO_FOR_LAUNCH_BRIEF_SHA256="$sha"
  want_one_line o
  [ "$out" = "GO: go-for-launch" ] || fail_case o "stdout: $out"
  [ "$rc" -eq 0 ] || fail_case o "exit $rc, want 0"
  [ "$(effects)" = "shift 100" ] || fail_case o "effects: $(effects), want exactly 'shift 100'"
  [ -z "$(harness_calls)" ] || fail_case o "the release role was called again: the human answered the brief already on disk"
  cmp -s "$out_dir/brief.md" "$root/brief.before" || fail_case o "brief.md changed under an answer on its hash"
  one_answer o GO unauthenticated cli
fi

# (p) GO_FOR_LAUNCH_BRIEF_SHA256 that is not the hash of the brief on disk:
# one fixed NO-GO line, nothing moved, no answer line, the brief untouched.
if [ -z "$mismatch" ]; then
  reset
  held_record
  good_brief_calls
  run_promote
  [ -f "$out_dir/brief.md" ] || fail_case p "the prepare run wrote no brief.md"
  cp "$out_dir/brief.md" "$root/brief.before"
  cp "$record" "$root/record.before"
  : > "$root/harness.log"
  run_promote GO_FOR_LAUNCH=GO GO_FOR_LAUNCH_BRIEF_SHA256=0000000000000000000000000000000000000000000000000000000000000000
  want_one_line p
  [ "$out" = "NO-GO: go-for-launch — the brief on disk is not the one answered; nothing moved" ] || fail_case p "stdout: $out"
  [ "$rc" -eq 1 ] || fail_case p "exit $rc, want 1"
  [ -z "$(effects)" ] || fail_case p "effects: $(effects), want none"
  cmp -s "$record" "$root/record.before" || fail_case p "the record changed: $(cat "$record")"
  cmp -s "$out_dir/brief.md" "$root/brief.before" || fail_case p "the brief on disk changed"
  no_answer p
fi

cd "$repo"
[ -z "$mismatch" ] || nogo "stub case $mismatch disagrees with docs/adr/0020 §4, docs/adr/0047 §3 or ticket 0008"

go
