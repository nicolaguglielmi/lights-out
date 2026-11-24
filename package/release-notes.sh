#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# package/release-notes.sh (docs/adr/0075 §4): the notes of a draft release,
# markdown on stdout, from the facts the release job has.
#
#   lights-out VERSION GATES_DIGEST FULL_DIGEST
#       docs/releases/vVERSION.md first when it exists (the release commit's
#       prose), then both images by tag and by digest, one cosign verify per
#       image, the PyPI line and the two Action repositories. A digest is
#       sha256:<64 hex>, or the word unpublished for a dry run.
#   action NAME VERSION
#       the notes of NAME's own repository: its uses line and the link to
#       lights-out VERSION's release.
#
# Run from the lights-out repository's root. A malformed argument: exit 1,
# nothing on stdout.
# shellcheck disable=SC2086 # each pair is split into its name and digest on purpose
set -eu
usage='usage: package/release-notes.sh lights-out VERSION GATES_DIGEST FULL_DIGEST | action NAME VERSION'
repo=https://github.com/nicolaguglielmi
identity='^https://github.com/nicolaguglielmi/lights-out/.github/workflows/package.yml@refs/tags/v'

semver() { printf '%s\n' "$1" | awk 'END { exit !(NR == 1 && /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/) }'; }
digest() {
  [ "$1" = unpublished ] && return 0
  printf '%s\n' "$1" | awk 'END { exit !(NR == 1 && /^sha256:[0-9a-f]+$/ && length($0) == 71) }'
}

case "${1:-}" in
  lights-out)
    [ $# = 4 ] || { echo "$usage" >&2; exit 2; }
    v=$2 gates=$3 full=$4
    semver "$v" || { echo "release-notes: '$v' is not a version X.Y.Z" >&2; exit 1; }
    for d in "$gates" "$full"; do
      digest "$d" || { echo "release-notes: '$d' is not sha256:<64 hex> or unpublished" >&2; exit 1 # a malformed digest
      }
    done
    prose=docs/releases/v$v.md
    if [ -f "$prose" ]; then cat "$prose"; echo; fi
    echo "**Images**, amd64 and arm64, signed keyless with Sigstore by this repository's \`package.yml\` at the tag:"
    echo
    for pair in "lights-out-gates $gates" "lights-out $full"; do
      set -- $pair
      ref=ghcr.io/nicolaguglielmi/$1
      [ "$2" = unpublished ] && by="$ref@<digest, set at the tag>" || by="$ref@$2"
      echo "- \`$ref:$v\`, by digest \`$by\`"
    done
    echo
    echo '```sh'
    for pair in "lights-out-gates $gates" "lights-out $full"; do
      set -- $pair
      ref=ghcr.io/nicolaguglielmi/$1
      [ "$2" = unpublished ] && by="$ref@<digest>" || by="$ref@$2"
      echo "cosign verify $by \\"
      echo "  --certificate-identity-regexp '$identity' \\"
      echo "  --certificate-oidc-issuer https://token.actions.githubusercontent.com"
    done
    echo '```'
    echo
    echo "**One install on PyPI**, [lights-out-gates $v](https://pypi.org/project/lights-out-gates/$v/), uploaded by trusted publishing: \`uv tool install lights-out-gates==$v\` puts both \`lights-out\` (the container wrapper) and \`lights-out-gates\` on your PATH (or run the light tier without installing via \`uvx lights-out-gates@$v --light\`)."
    echo
    echo "**The two standalone Actions**, at the same version:"
    echo
    for a in test-proof test-integrity; do
      echo "- [\`nicolaguglielmi/$a@v$v\`]($repo/$a/releases/tag/v$v)"
    done
    echo
    echo "Apache-2.0."
    ;;
  action)
    [ $# = 3 ] || { echo "$usage" >&2; exit 2; }
    a=$2 v=$3
    semver "$v" || { echo "release-notes: '$v' is not a version X.Y.Z" >&2; exit 1; }
    echo "One GO or NO-GO check on a pull request, at lights-out v$v."
    echo
    echo '```yaml'
    echo "  - uses: nicolaguglielmi/$a@v$v"
    echo '```'
    echo
    echo "The changes are in [lights-out $v]($repo/lights-out/releases/tag/v$v). Apache-2.0."
    ;;
  *) echo "$usage" >&2; exit 2 ;;
esac
