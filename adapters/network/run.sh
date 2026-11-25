#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# harness-network: the `network` AgentHarness (docs/adr/0036). Fans one
# ticket out to the members its `## Network` section declares, each a run
# of the profile's own harness (AGENT_HARNESS: replay at L0, gemini-cli at
# L1) in its own clone of BASE, with an input document that holds the
# ticket, its part and its own `## Perimeter`. Members run in parallel and
# share nothing while they run. Then each is judged on its own — the
# member harness's exit code, core/harness-check.sh, core/perimeter.sh on
# its own document — and no path may be changed by two members. Only if
# every member passes are their commits applied to WORKSPACE in the
# declared order; a refusal integrates nothing. The artefacts are the
# port's usual ones (patch, pr.json, final.md, run.json with `members`),
# and from here crew and the tower govern the composite as one PR.
#
# A ticket without `## Network` goes to the member harness unchanged: a
# network of one is a single worker. Picked by core/crew.sh only when
# CREW_HARNESS=network. Adapter convention (docs/adr/0012 §6): not a
# gate, members' output on stderr, one stderr line and exit 1 on failure.
# No pathname expansion anywhere: globs here are patterns, not files.
set -euf
: "${ROLE:?ROLE not set}"
: "${TICKET:?TICKET not set}"
: "${TICKET_FILE:?TICKET_FILE not set}"
: "${WORKSPACE:?WORKSPACE not set}"
: "${BASE:?BASE not set}"
: "${OUT:?OUT not set}"
: "${AGENT_HARNESS:?AGENT_HARNESS not set}"
: "${WORKLOAD:?WORKLOAD not set}"

# Provenance (docs/adr/0036, "The number"): the Gemini API free tier's
# ~250 requests a day (external constraint, plan §5) over the
# dev-worker's MAX_TURNS=60 (decision, roles/dev-worker/role.env) is 4.2
# member runs a day.
max_members=4

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
member_run="$here/adapters/$AGENT_HARNESS/run.sh"

refuse() { printf 'harness-network: %s\n' "$1" >&2; exit 1; }

[ "$AGENT_HARNESS" != network ] || refuse "AGENT_HARNESS=network: a network's members must run on a real harness"
[ -f "$member_run" ] || refuse "no member harness at adapters/$AGENT_HARNESS/run.sh"

if ! grep -q '^## Network[[:space:]]*$' "$TICKET_FILE"; then
  case "${NETWORK_AUTO_SPLIT:-off}" in
    off) exec sh "$member_run" ;;
    on)
      [ -f "$here/core/spec_split.py" ] || refuse "core/spec_split.py not found for NETWORK_AUTO_SPLIT=on"
      mkdir -p "$OUT"
      split_spec="$OUT/split-spec.md"
      split_err="$OUT/split-spec.err"
      if ! python3 "$here/core/spec_split.py" split "$TICKET_FILE" > "$split_spec" 2> "$split_err"; then
        why=$(head -n1 "$split_err" 2>/dev/null || true)
        rm -f "$split_spec" "$split_err"
        refuse "${why:-$TICKET_FILE has no ## Network section and cannot be auto-split}"
      fi
      rm -f "$split_err"
      TICKET_FILE="$split_spec"
      ;;
    *) refuse "NETWORK_AUTO_SPLIT must be on or off, got '${NETWORK_AUTO_SPLIT}'" ;;
  esac
fi

# The section, one record per line: "m <id>", "g <id> <glob>", "b <id> <brief line>".
parsed=$(awk '
  /^## Network[[:space:]]*$/ { inside = 1; next }
  inside && /^## / { exit }
  inside && /^### / { id = $2; print "m " id; next }
  inside && id != "" && /^- / { line = $0; sub(/^- +/, "", line); gsub(/`/, "", line); print "g " id " " line; next }
  inside && id != "" { print "b " id " " $0 }
' "$TICKET_FILE")

ids=$(printf '%s\n' "$parsed" | awk '$1 == "m" { print $2 }')
count=$(printf '%s\n' "$ids" | grep -c . || true)
[ "$count" -gt 0 ] || refuse "## Network declares no member"
[ "$count" -le "$max_members" ] || refuse "## Network declares $count members, at most $max_members"
for id in $ids; do
  printf '%s\n' "$id" | grep -qx '[a-z0-9-][a-z0-9-]*' || refuse "member id '$id' is not [a-z0-9-]+"
done
dup=$(printf '%s\n' "$ids" | sort | uniq -d | head -n1)
[ -z "$dup" ] || refuse "member $dup is declared twice"

globs_of() { printf '%s\n' "$parsed" | awk -v id="$1" '$1 == "g" && $2 == id { sub(/^g [^ ]+ /, ""); print }'; }

# Overlap on their face, before any member spends a token: one glob that
# matches the other's text. The exact rule — no path changed by two
# members — is checked after the runs.
seen=""
for b in $ids; do
  [ -n "$(globs_of "$b")" ] || refuse "member $b declares no perimeter"
  for a in $seen; do
    old_ifs=$IFS; IFS='
'
    for ga in $(globs_of "$a"); do
      for gb in $(globs_of "$b"); do
        # shellcheck disable=SC2254 # unquoted on purpose: globs as patterns
        case "$gb" in $ga) refuse "members $a and $b overlap: $ga matches $gb" ;; esac
        # shellcheck disable=SC2254
        case "$ga" in $gb) refuse "members $a and $b overlap: $gb matches $ga" ;; esac
      done
    done
    IFS=$old_ifs
  done
  seen="$seen $b"
done

net_dir="$(dirname "$OUT")/network"
rm -rf "$net_dir"
mkdir -p "$net_dir" "$OUT"
all=$(printf '%s\n' "$ids" | tr '\n' ' ' | sed 's/ $//')

for id in $ids; do
  m="$net_dir/$id"
  mkdir -p "$m/out"
  git clone -q "$WORKSPACE" "$m/workspace" >&2
  git -C "$m/workspace" checkout -q -B "member/$id" "$BASE" >&2
  # The contract's directory (docs/adr/0037 §2), as core/crew.sh reads it.
  if [ -f "${WORKLOAD_CONTRACT:-$WORKLOAD}/crew-prepare.sh" ]; then
    sh "${WORKLOAD_CONTRACT:-$WORKLOAD}/crew-prepare.sh" "$m/workspace"
  fi
  {
    awk '/^## / { skip = ($0 ~ /^## (Perimeter|Network)[[:space:]]*$/) } !skip' "$TICKET_FILE"
    printf '\n## Your part of this ticket: %s\n\n' "$id"
    # shellcheck disable=SC2016 # the backticks are Markdown, not a command
    printf 'This ticket is split between %s workers: %s. Each works alone, in its own copy of the repository at the same commit, and none sees the others'"'"' work until the platform puts the parts together. You are `%s`.\n\n' \
      "$count" "$all" "$id"
    printf '%s\n' "$parsed" | awk -v id="$id" '$1 == "b" && $2 == id { sub(/^b [^ ]+ ?/, ""); print }'
    printf '\n## Perimeter\n\n'
    # shellcheck disable=SC2016 # the backticks are Markdown, not a command
    globs_of "$id" | sed 's/.*/- `&`/'
  } > "$m/ticket.md"
  TICKET="$TICKET/$id" TICKET_FILE="$m/ticket.md" WORKSPACE="$m/workspace" OUT="$m/out" \
    sh "$member_run" > "$m/log" 2>&1 &
  printf '%s %s\n' "$id" "$!" >> "$net_dir/pids"
  printf '[network] %s: member %s started (%s)\n' "$TICKET" "$id" "$AGENT_HARNESS" >&2
done

: > "$net_dir/failed"
while read -r id pid; do
  wait "$pid" || printf '%s\n' "$id" >> "$net_dir/failed"
done < "$net_dir/pids"
for id in $ids; do
  sed "s/^/[network] $id: /" "$net_dir/$id/log" >&2
done

: > "$net_dir/paths"
for id in $ids; do
  m="$net_dir/$id"
  if grep -qx "$id" "$net_dir/failed"; then
    refuse "member $id failed: $(tail -n1 "$m/log")"
  fi
  why=$(sh "$here/core/harness-check.sh" "$m/out" "$m/workspace" "$BASE" 2>&1) \
    || refuse "member $id: $why"
  why=$(git -C "$m/workspace" diff --no-renames --name-only "$BASE"..HEAD \
    | sh "$here/core/perimeter.sh" "$m/ticket.md" 2>&1) \
    || refuse "member $id: $why of its part"
  git -C "$m/workspace" diff --no-renames --name-only "$BASE"..HEAD | sed "s|^|$id |" >> "$net_dir/paths"
done
shared=$(awk '{ if ($2 in by && by[$2] != $1) { print $2 " (" by[$2] ", " $1 ")"; exit } by[$2] = $1 }' "$net_dir/paths")
[ -z "$shared" ] || refuse "one path changed by two members: $shared"

for id in $ids; do
  if ! git -C "$net_dir/$id/workspace" format-patch --stdout "$BASE"..HEAD \
      | git -C "$WORKSPACE" -c user.name=lights-out-crew -c user.email=crew@lights-out.invalid am >&2; then
    git -C "$WORKSPACE" -c user.name=lights-out-crew -c user.email=crew@lights-out.invalid am --abort >&2 || true
    git -C "$WORKSPACE" -c user.name=lights-out-crew -c user.email=crew@lights-out.invalid reset -q --hard "$BASE"
    refuse "member $id does not apply after the members before it"
  fi
done
git -C "$WORKSPACE" format-patch --stdout "$BASE"..HEAD > "$OUT/patch"

members='[]'
for id in $ids; do
  m="$net_dir/$id"
  run='{}'
  [ -f "$m/out/run.json" ] && run=$(cat "$m/out/run.json")
  members=$(printf '%s' "$members" | jq -c --arg id "$id" --argjson run "$run" \
    --arg globs "$(globs_of "$id")" --slurpfile pr "$m/out/pr.json" \
    '. + [{id: $id, perimeter: ($globs | split("\n")), run: $run, pr: $pr[0]}]')
done

title=$(head -n1 "$TICKET_FILE" | sed 's/^# *//')
printf '%s' "$members" | jq --arg title "$title" '{
  title: $title,
  body: ("Built by a network of \(length) members (docs/adr/0036), each in its own copy of the base, integrated in this order.\n\n"
    + (map("### \(.id)\n\n- harness: \(.run.harness // "unknown"), model: \(.run.model // .run.recorded.model // "none")"
      + (if .run.source then ", replayed from \(.run.source)" else "" end)
      + (if .run.recorded.hand_written then " (hand-written, no model ran)" else "" end)
      + "\n\n\(.pr.title // "")\n\n\(.pr.body // "")") | join("\n\n")))
}' > "$OUT/pr.json"

printf '%s' "$members" | jq '{
  harness: "network",
  model: ([.[] | .run.model // .run.recorded.model] | unique | if length == 1 then .[0] else null end),
  members: map({id, perimeter, run})
}' > "$OUT/run.json"

{
  printf 'Network of %s members, integrated in this order: %s.\n' "$count" "$all"
  for id in $ids; do
    printf -- '- %s: %s\n' "$id" "$(jq -r '.title // ""' "$net_dir/$id/out/pr.json")"
  done
} > "$OUT/final.md"
