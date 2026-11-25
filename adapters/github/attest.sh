#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# attest-github: attaches an in-toto statement or Sigstore bundle to the
# GitHub repository's Attestations API (POST /repos/<owner>/<repo>/attestations,
# API version 2022-11-28, docs/adr/0134). Optional CodeHost verb: requires
# non-empty <subject-sha>, readable JSON <statement-or-bundle-file>,
# WORKLOAD_ATTACHED, GITHUB_REPO and GITHUB_TOKEN (via adapters/github/env.sh).
# Passes the token strictly on curl stdin (-H @-) via github_api; never prints
# the token. Emits one JSON receipt line on stdout or one stderr line and
# exit 1 on refusal.
set -eu
sha=${1:-}
att_file=${2:-}
[ -n "$sha" ] && [ -n "$att_file" ] \
  || { printf 'usage: attest.sh <subject-sha> <statement-or-bundle-file>\n' >&2; exit 1; }
case "$sha" in
  *[!A-Za-z0-9:._-]* | -* | .*)
    printf 'attest-github: invalid subject sha %s\n' "$sha" >&2
    exit 1 ;;
esac
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck source=adapters/github/env.sh
. "$here/adapters/github/env.sh"

[ -n "${WORKLOAD_ATTACHED:-}" ] \
  || { printf 'attest-github: CODE_HOST=github serves an attached repository: ./go attach <your clone> (docs/adr/0043 §3)\n' >&2; exit 1; }
[ -f "$att_file" ] && [ -r "$att_file" ] && [ -s "$att_file" ] \
  || { printf 'attest-github: attestation file missing or unreadable: %s\n' "$att_file" >&2; exit 1; }
jq -e 'type == "object"' "$att_file" >/dev/null 2>&1 \
  || { printf 'attest-github: %s is not a valid JSON object\n' "$att_file" >&2; exit 1; }

payload=$(jq -c 'if (has("bundle") and (.bundle | type == "object")) then . else {bundle: .} end' "$att_file")
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
code=$(github_api POST "/repos/$GITHUB_REPO/attestations" "$tmp" "$payload")
case "$code" in
  200 | 201 | 202) : ;;
  *)
    msg=$(jq -r '.message // empty' "$tmp" 2>/dev/null | tr '\n' ' ' || true)
    printf 'attest-github: POST attestations on %s returned %s%s\n' "$GITHUB_REPO" "$code" "${msg:+: $msg}" >&2
    exit 1 ;;
esac

if jq -e 'has("dsseEnvelope") or has("messageSignature") or has("mediaType")' "$att_file" >/dev/null 2>&1; then
  verify_cmd="gh attestation verify --repo $GITHUB_REPO --bundle $att_file"
else
  verify_cmd="gh attestation verify --repo $GITHUB_REPO $att_file"
fi
jq -cn --arg repo "$GITHUB_REPO" --arg api "$GITHUB_API" --arg sha "$sha" --arg vcmd "$verify_cmd" \
  '((try input catch {}) | if type == "object" then . else {} end) as $resp |
   {
     stored: "remote",
     id: ($resp.id // null),
     url: ($resp.url // $resp.html_url // (if ($resp.id // null) != null then "https://github.com/\($repo)/attestations/\($resp.id)" else "\($api)/repos/\($repo)/attestations" end)),
     sha: $sha,
     verify_cmd: $vcmd
   }' < "$tmp"
