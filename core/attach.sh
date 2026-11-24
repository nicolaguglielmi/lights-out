#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-attach (docs/adr/0037 §1): clones a repository of the user's into
# <dest>, the directory the Makefile then selects as the workload. Runs on
# the host under `./go attach`, with git and sh only, and inside workbench
# under demo-seam. The source is only read: every worktree and fetch the
# platform makes happens in the clone. A repository without
# .lights-out/workload.env committed at HEAD is attached too, for ./go
# init to draft one in the clone (docs/adr/0042 §6): the contract is read
# from there and nowhere else, so until then there is none. Not a gate:
# one stderr line and exit 1 on failure, one stderr line on success.
set -eu
src=${1:?usage: attach.sh <repository> <dest>}
dest=${2:?usage: attach.sh <repository> <dest>}

top=$(git -C "$src" rev-parse --show-toplevel 2>/dev/null) \
  || { printf 'attach: %s is not a git repository\n' "$src" >&2; exit 1; }
git -C "$top" rev-parse -q --verify HEAD >/dev/null 2>&1 \
  || { printf 'attach: %s has no commit yet; lights-out attaches a HEAD\n' "$top" >&2; exit 1; }
contract=yes
git -C "$top" cat-file -e HEAD:.lights-out/workload.env 2>/dev/null || contract=no
# Only a clone of ours is ever replaced, and never one holding a draft the
# user has not copied out yet (docs/adr/0042 resolved 9).
if [ -e "$dest" ] && [ ! -d "$dest/.git" ]; then
  printf 'attach: %s exists and is not a clone lights-out made; not touching it\n' "$dest" >&2
  exit 1
fi
# A draft is a file added or changed under the clone's .lights-out/ (a
# deletion left by a check is not one).
if [ -d "$dest/.git" ] && git -C "$dest" status --porcelain -- .lights-out 2>/dev/null | grep -qv '^.D \|^D '; then
  printf 'attach: %s/.lights-out has changes lights-out drafted (./go init) that are not in a repository yet; copy them into yours, or ./go detach to drop them\n' "$dest" >&2
  exit 1
fi
[ -z "$(git -C "$top" status --porcelain)" ] \
  || printf 'attach: %s has uncommitted changes; lights-out attaches its HEAD, not them\n' "$top" >&2

rm -rf "$dest"
git clone -q "$top" "$dest" \
  || { printf 'attach: git clone %s %s failed\n' "$top" "$dest" >&2; exit 1; }
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
remote_out=${REMOTE_ENV_OUT:-}
if [ -z "$remote_out" ]; then
  # shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
  dest_parent=$(CDPATH= cd -- "$(dirname -- "$dest")" && pwd -P)
  if [ "$dest_parent" = "$here" ]; then
    remote_out="$here/.crew/remote.env"
  fi
fi
if [ -n "$remote_out" ] && command -v python3 >/dev/null 2>&1; then
  if detected=$(sh "$here/core/remote-env.sh" "$top" "$remote_out"); then
    if [ -n "$detected" ]; then
      summary=$(printf '%s\n' "$detected" | tr '\n' ' ' | sed 's/ $//')
      printf 'attach: detected remote code host (%s) from origin\n' "$summary" >&2
    fi
  else
    printf 'attach: remote detection failed for %s\n' "$top" >&2
  fi
fi
if [ "$contract" = yes ]; then
  printf 'attach: %s at %s is the workload (clone: %s)\n' "$top" "$(git -C "$dest" rev-parse --short HEAD)" "$dest" >&2
else
  printf 'attach: %s at %s is the workload (clone: %s); it has no .lights-out/workload.env at HEAD, ./go init drafts one\n' "$top" "$(git -C "$dest" rev-parse --short HEAD)" "$dest" >&2
fi
