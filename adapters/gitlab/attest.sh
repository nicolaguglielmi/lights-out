#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# attest-gitlab: records a signed provenance attestation digest and
# metadata on commit <subject-sha> via GitLab's commit comments endpoint
# (POST $GITLAB_PROJECT/repository/commits/<sha>/comments, docs/adr/0134).
# Passes PRIVATE-TOKEN strictly on curl stdin (-H @-) via env.sh's api().
# Emits one JSON receipt line on stdout or one stderr line and exit 1.
set -eu
sha=${1:-}
att_file=${2:-}
[ -n "$sha" ] && [ -n "$att_file" ] \
  || { printf 'usage: attest.sh <subject-sha> <statement-or-bundle-file>\n' >&2; exit 1; }
case "$sha" in
  *[!A-Za-z0-9:._-]* | -* | .*)
    printf 'attest-gitlab: invalid subject sha %s\n' "$sha" >&2
    exit 1 ;;
esac
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/gitlab/env.sh
. "$here/env.sh"

[ -f "$att_file" ] && [ -r "$att_file" ] && [ -s "$att_file" ] \
  || { printf 'attest-gitlab: attestation file missing or unreadable: %s\n' "$att_file" >&2; exit 1; }
jq -e 'type == "object"' "$att_file" >/dev/null 2>&1 \
  || { printf 'attest-gitlab: %s is not a valid JSON object\n' "$att_file" >&2; exit 1; }

if command -v sha256sum >/dev/null 2>&1; then
  att_sha256=$(sha256sum "$att_file" | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then
  att_sha256=$(shasum -a 256 "$att_file" | awk '{print $1}')
else
  att_sha256=$(openssl dgst -sha256 "$att_file" | awk '{print $NF}')
fi

enc_sha=$(printf '%s' "$sha" | jq -Rr @uri)
meta=$(jq -c '{
  type: (._type // null),
  predicateType: (.predicateType // null),
  mediaType: (.mediaType // null),
  subject: (.subject // null),
  ticket: (.predicate.ticket.id // null),
  gates_verdict: (.predicate.gates.verdict // null),
  merge_sha: (.predicate.merge.sha // null),
  payloadType: (.dsseEnvelope.payloadType // null)
}' "$att_file")
att_name=$(basename -- "$att_file")
# shellcheck disable=SC2016 # backticks in note string are literal Markdown code spans
payload=$(jq -cn --arg sha "$sha" --arg digest "$att_sha256" --arg name "$att_name" --arg meta "$meta" \
  '{note: ("lights-out provenance attestation\n\n- subject_sha: `" + $sha + "`\n- sha256: `" + $digest + "`\n- file: `" + $name + "`\n- metadata: `" + $meta + "`")}')

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
code=$(api -o "$tmp" -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
  -d "$payload" "$GITLAB_PROJECT/repository/commits/$enc_sha/comments") || code=000
case "$code" in
  200 | 201 | 202) : ;;
  *)
    body=$(tr '\n' ' ' < "$tmp" 2>/dev/null || true)
    printf 'attest-gitlab: POST commit comment on %s returned %s%s\n' "$sha" "$code" "${body:+: $body}" >&2
    exit 1 ;;
esac

commit_url="$GITLAB_URL/$GITLAB_OWNER/$GITLAB_REPO/-/commit/$enc_sha"
if [ -n "${GITLAB_PROJECT_PATH:-}" ]; then
  commit_url="$GITLAB_URL/$GITLAB_PROJECT_PATH/-/commit/$enc_sha"
fi
case "$att_file" in
  *.bundle) vcmd="cosign verify-blob --key .crew/cosign.pub --insecure-ignore-tlog --bundle $att_file ${att_file%.bundle}" ;;
  *) vcmd="python3 core/provenance.py verify --statement $att_file" ;;
esac
jq -cn --arg sha "$sha" --arg digest "sha256:$att_sha256" --arg curl "$commit_url" --arg vcmd "$vcmd" \
  '((try input catch {}) | if type == "object" then . else {} end) as $resp |
   {
     stored: "remote",
     id: ($resp.id // null),
     url: ($resp.web_url // $resp.url // $curl),
     sha: $sha,
     digest: $digest,
     verify_cmd: $vcmd
   }' < "$tmp"
