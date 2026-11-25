#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-codehost: the cold half of the CodeHost proof (docs/adr/0043 §5); the
# live half is `./go codehost-conformance`. No network, no engine and no
# fake GitHub: no HTTP answer is simulated anywhere below. Written from the
# ADR and the port's argument lists, not from what the adapters do. It checks
# five things:
#   (1) every adapters/*/ with a clone-url.sh is a code host and ships all
#       nine scripts (the eight verbs and ready.sh); found, never listed;
#   (2) the credential helper of any adapter that has one answers
#       username/password on `get` for its own host, nothing for another
#       host or protocol, nothing on `store` and `erase`, nothing when the
#       token is empty, and never writes the token on stderr;
#   (3) the Makefile hands git the helper as environment-scoped config at
#       index 1 and leaves the workbench image's safe.directory at index 0;
#       with the code host that ships no helper, the config stays as the
#       image has it. It is probed through make's own recipe environment,
#       and git itself is asked for a credential through that environment;
#   (4) every GitHub verb, run with a dummy token and recorders for curl and
#       git first on PATH (they log argv, GIT_CONFIG_* and stdin, then fail):
#       the token is in no argv and no GIT_CONFIG_* value, reaches curl only
#       on stdin as `-H @-`, and is on no verb's stdout or stderr; with the
#       token empty or unset, or the repository unset, every verb refuses
#       in one stderr line naming .env and makes no curl or git call. A
#       token copied into another variable first is caught here, not by a
#       grep. The verbs also see a `date` whose clock moves one second
#       between any two reads: a polling verb (await, ready) must call the
#       API before it reads its deadline, or a second that turns between
#       its two reads leaves it with no call at all (2026-11-07, main red
#       at 373da51 on `ready` with READY_TIMEOUT=1);
#   (5) no code host adapter runs `git config`, `git remote add` or
#       `git remote set-url` (comment lines are not code).
#
# A check that cannot fail is not one: after judging this tree, the script
# judges copies of a good one with a single defect each, and every defect
# must be named. The self-tests build their defects themselves; only the
# Makefile's are sed edits of the real file, and each must change it.
# shellcheck disable=SC2016 # the self-tests write scripts with printf; their $-expressions are the written scripts', not this one's
set -eu
name=codehost
. ci/lib.sh

for f in Makefile workbench/Containerfile adapters/github/credential.sh; do
  [ -f "$f" ] || nogo "$f not found"
done
for tool in jq git make; do
  command -v "$tool" >/dev/null 2>&1 || nogo "$tool is not on PATH; run this via ./go ci"
done

# The temp repositories below are committed to: a GIT_DIR inherited from a
# hook would aim that at this checkout instead.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
export GIT_AUTHOR_NAME=ci GIT_AUTHOR_EMAIL=ci@localhost GIT_COMMITTER_NAME=ci GIT_COMMITTER_EMAIL=ci@localhost

t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT
mkdir "$t/home"
# A made-up token, distinctive enough that finding it anywhere means a leak.
tok=dummy-codehost-token-5e1c0a9d
sha=0123456789abcdef0123456789abcdef01234567
verbs="bootstrap clone-url push open-pr status merge pr await ready"

# Every *_flaw function prints the first thing wrong, as one line, or
# nothing, and always returns 0. None calls nogo: they run in $(...).

# --- (1) the surface ------------------------------------------------------------
surface_flaw() {  # ROOT
  seen=""
  for d in "$1"/adapters/*/; do
    [ -f "${d}clone-url.sh" ] || continue
    h=$(basename "$d")
    seen="$seen $h"
    for v in $verbs; do
      [ -s "${d}$v.sh" ] || { printf 'adapters/%s has a clone-url.sh, so it is a code host, but no %s.sh' "$h" "$v"; return 0; }
    done
  done
  [ -n "$seen" ] || printf 'no code host under adapters/ (a directory with a clone-url.sh)'
  return 0
}

# --- (2) the credential helper --------------------------------------------------
# cred_run SCRIPT VERB INPUT-FILE [VAR=value...]: the helper, run as git runs
# it, in a clean environment; stdout in $t/cred.out, stderr in $t/cred.err.
cred_run() {
  cr_script=$1 cr_verb=$2 cr_in=$3
  shift 3
  env -i PATH="$PATH" "$@" sh "$cr_script" "$cr_verb" <"$cr_in" >"$t/cred.out" 2>"$t/cred.err" && cred_rc=0 || cred_rc=$?
}

credential_flaw() {  # ROOT
  croot=$1
  for c in "$croot"/adapters/*/credential.sh; do
    [ -f "$c" ] || continue
    h=$(basename "$(dirname "$c")")
    case "$h" in
      github) proto=https host=github.com user=x-access-token var=GITHUB_TOKEN ;;
      gitlab) proto=https host=gitlab.com user=oauth2 var=GITLAB_TOKEN ;;
      *) printf 'adapters/%s/credential.sh: ci/codehost.sh does not know the host and token variable it serves; add them to its table' "$h"; return 0 ;;
    esac
    printf 'protocol=%s\nhost=%s\n\n' "$proto" "$host" > "$t/in-right"
    cred_run "$c" get "$t/in-right" "$var=$tok"
    if [ "$cred_rc" -ne 0 ] || ! grep -qxF "username=$user" "$t/cred.out" || ! grep -qxF "password=$tok" "$t/cred.out"; then
      printf '%s: get for %s://%s did not answer username=%s and password=<token>' "$h" "$proto" "$host" "$user"; return 0
    fi
    if grep -qF "$tok" "$t/cred.err"; then
      printf '%s: get wrote the token on stderr' "$h"; return 0
    fi
    for other in "https example.com" "https $host.example.test" "https api.$host" "http $host"; do
      printf 'protocol=%s\nhost=%s\n\n' "${other%% *}" "${other#* }" > "$t/in-other"
      cred_run "$c" get "$t/in-other" "$var=$tok"
      if [ "$cred_rc" -ne 0 ] || [ -s "$t/cred.out" ]; then
        printf '%s: get for %s://%s answered, want silence (a credential for the wrong host is a leak)' "$h" "${other%% *}" "${other#* }"; return 0
      fi
    done
    printf 'protocol=%s\nhost=%s\nusername=%s\npassword=other\n\n' "$proto" "$host" "$user" > "$t/in-store"
    for cv in store erase; do
      cred_run "$c" "$cv" "$t/in-store" "$var=$tok"
      if [ "$cred_rc" -ne 0 ] || [ -s "$t/cred.out" ]; then
        printf '%s: %s answered, want silence' "$h" "$cv"; return 0
      fi
    done
    cred_run "$c" get "$t/in-right" "$var="
    if [ -s "$t/cred.out" ]; then printf '%s: get with an empty token answered' "$h"; return 0; fi
    cred_run "$c" get "$t/in-right"
    if [ -s "$t/cred.out" ]; then printf '%s: get with no token variable answered' "$h"; return 0; fi
  done
  return 0
}

# --- (3) the Makefile's env-scoped entry ----------------------------------------
# The Containerfile's own config is the premise: index 0 is safe.directory.
grep -q 'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=\*' workbench/Containerfile \
  || nogo "workbench/Containerfile no longer sets GIT_CONFIG_COUNT=1 with safe.directory=* at index 0; update the Makefile's index and this check (docs/adr/0043 §2)"

# make_env MAKEFILE HOST: the environment make gives a recipe, in $t/make.env.
# The workbench image's variables are written out, the token and repository
# are command-line variables so a developer's .env cannot replace them.
make_env() {
  env -i PATH="$PATH" HOME="$t/home" GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*' \
    make -f "$1" --no-print-directory -s ATTACHED="$t/never-attached" CODE_HOST="$2" \
      GITHUB_TOKEN="$tok" GITHUB_REPO=o/r \
      --eval 'lo-codehost-probe: ; @env' lo-codehost-probe >"$t/make.env" 2>"$t/make.err"
}
# make_fill MAKEFILE HOST: git itself asked for the credential of https://HOST
# through that environment, no network; output in $t/make.fill. Asked from a
# directory that is no repository, so no repository config can answer.
make_fill() {
  env -i PATH="$PATH" HOME="$t/home" GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*' \
    make -f "$1" --no-print-directory -s ATTACHED="$t/never-attached" CODE_HOST=github \
      GITHUB_TOKEN="$tok" GITHUB_REPO=o/r LO_FILL_HOST="$2" LO_FILL_DIR="$t/home" \
      --eval 'lo-fill-probe: ; @printf "protocol=https\nhost=$(LO_FILL_HOST)\n\n" | git -C "$(LO_FILL_DIR)" credential fill' lo-fill-probe >"$t/make.fill" 2>"$t/make.err"
}
gv() { sed -n "s/^$1=//p" "$t/make.env"; }

makefile_flaw() {  # MAKEFILE
  mf=$1
  if ! make_env "$mf" github; then
    printf 'make CODE_HOST=github failed: %s' "$(tail -n 3 "$t/make.err" | tr '\n' ' ')"; return 0
  fi
  [ "$(gv GIT_CONFIG_COUNT)" = 2 ] \
    || { printf 'CODE_HOST=github: GIT_CONFIG_COUNT is "%s", want 2 (safe.directory at 0, the helper at 1)' "$(gv GIT_CONFIG_COUNT)"; return 0; }
  if [ "$(gv GIT_CONFIG_KEY_0)" != safe.directory ] || [ "$(gv GIT_CONFIG_VALUE_0)" != '*' ]; then
    printf 'CODE_HOST=github: index 0 is "%s=%s": the image'\''s safe.directory=* was overwritten' "$(gv GIT_CONFIG_KEY_0)" "$(gv GIT_CONFIG_VALUE_0)"; return 0
  fi
  [ "$(gv GIT_CONFIG_KEY_1)" = credential.helper ] \
    || { printf 'CODE_HOST=github: GIT_CONFIG_KEY_1 is "%s", want credential.helper' "$(gv GIT_CONFIG_KEY_1)"; return 0; }
  v1=$(gv GIT_CONFIG_VALUE_1)
  case "$v1" in
    '!sh '*adapters/github/credential.sh*) ;;
    *) printf 'CODE_HOST=github: GIT_CONFIG_VALUE_1 is "%s", want a `!sh` command naming adapters/github/credential.sh' "$v1"; return 0 ;;
  esac
  helper=${v1#'!sh '}
  helper=${helper#\'}
  helper=${helper%\'}
  [ -f "$helper" ] || { printf 'CODE_HOST=github: GIT_CONFIG_VALUE_1 names %s, which is not a file' "$helper"; return 0; }
  if ! make_env "$mf" gitea; then
    printf 'make CODE_HOST=gitea failed: %s' "$(tail -n 3 "$t/make.err" | tr '\n' ' ')"; return 0
  fi
  [ "$(gv GIT_CONFIG_COUNT)" = 1 ] && [ -z "$(gv GIT_CONFIG_KEY_1)" ] && [ -z "$(gv GIT_CONFIG_VALUE_1)" ] \
    || { printf 'CODE_HOST=gitea ships no credential helper: GIT_CONFIG_COUNT is "%s" and index 1 is "%s", want 1 and nothing' "$(gv GIT_CONFIG_COUNT)" "$(gv GIT_CONFIG_KEY_1)"; return 0; }
  if ! make_fill "$mf" github.com || ! grep -qxF 'username=x-access-token' "$t/make.fill" || ! grep -qxF "password=$tok" "$t/make.fill"; then
    printf 'git, given only the GIT_CONFIG_* variables make exports for CODE_HOST=github, got no token for https://github.com'; return 0
  fi
  if make_fill "$mf" example.com || grep -qF "$tok" "$t/make.fill"; then
    printf 'git, given only the GIT_CONFIG_* variables make exports for CODE_HOST=github, got a credential for https://example.com'; return 0
  fi
  return 0
}

# --- (4) the recorders ----------------------------------------------------------
# Recorders for curl and git: argv, GIT_CONFIG_* and stdin of every call, in
# $REC_DIR, then exit 1. Every verb's own curl and git calls reach them.
rec_bin=$t/rec-bin
mkdir "$rec_bin"
cat > "$rec_bin/curl" <<'REC'
#!/bin/sh
tool=$(basename "$0")
{ printf '%s' "$tool"; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\n'; } >> "$REC_DIR/$tool.argv"
env | grep '^GIT_CONFIG_' >> "$REC_DIR/$tool.env" || true
cat >> "$REC_DIR/$tool.stdin"
exit 1
REC
cp "$rec_bin/curl" "$rec_bin/git"
# The clock: `date +%s` moves one second at every read, from $CLOCK, so the
# second that turns between two reads, a race on a real clock, happens on
# every pair. Any other `date` is the real one.
cat > "$rec_bin/date" <<'CLOCK'
#!/bin/sh
[ "$*" = +%s ] || exec "$REAL_DATE" "$@"
n=$(cat "$CLOCK" 2>/dev/null || echo 1000000000)
n=$((n + 1))
printf '%s\n' "$n" > "$CLOCK"
printf '%s\n' "$n"
CLOCK
real_date=$(command -v date)
chmod +x "$rec_bin/curl" "$rec_bin/git" "$rec_bin/date"
# The workload an attached bootstrap would read, and a harness's pr.json.
git init -q -b main "$t/wl"
git -C "$t/wl" -c commit.gpgsign=false commit -q --allow-empty -m wl
printf '{"title":"t","body":"b"}\n' > "$t/pr.json"
printf '{"_type":"https://in-toto.io/Statement/v1","subject":[{"name":"wl","digest":{"gitCommit":"%s"}}]}\n' "$sha" > "$t/att.json"

# vrun ROOT OUTDIR VERB ARGS...: one GitHub verb of ROOT in a clean environment,
# the recorders first on PATH, the GIT_CONFIG_* the Makefile would give it, no
# stdin. $v_token ($v_token_unset=1: no variable at all), $v_repo and
# $v_attached are the knobs. Stdout and stderr in OUTDIR/out and err, the
# recorders' logs in OUTDIR/rec; the exit code in $vrc.
# shellcheck disable=SC2086 # $tokenarg is one word or nothing
vrun() {
  vroot=$1 vout=$2 vverb=$3
  shift 3
  mkdir -p "$vout/rec"
  tokenarg="GITHUB_TOKEN=$v_token"
  [ -z "$v_token_unset" ] || tokenarg=""
  env -i PATH="$rec_bin:$PATH" HOME="$t/home" LC_ALL=C REC_DIR="$vout/rec" \
    CLOCK="$vout/clock" REAL_DATE="$real_date" \
    GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*' \
    GIT_CONFIG_KEY_1=credential.helper GIT_CONFIG_VALUE_1="!sh '$vroot/adapters/github/credential.sh'" \
    GITHUB_REPO="$v_repo" WORKLOAD="$t/wl" WORKLOAD_ATTACHED="$v_attached" \
    AWAIT_TIMEOUT=1 AWAIT_INTERVAL=1 READY_TIMEOUT=1 \
    $tokenarg sh "$vroot/adapters/github/$vverb.sh" "$@" </dev/null >"$vout/out" 2>"$vout/err" && vrc=0 || vrc=$?
}
vcall() {  # ROOT OUTDIR VERB: the verb with arguments of the shape adapters/gitea/ takes
  case "$3" in
    push) vrun "$1" "$2" push "$t/wl" ticket/0001 ;;
    open-pr) vrun "$1" "$2" open-pr ticket/0001 "$t/pr.json" ;;
    status) vrun "$1" "$2" status "$sha" success gates "one line" ;;
    await) vrun "$1" "$2" await "$sha" gates ;;
    pr) vrun "$1" "$2" pr 7 ;;
    merge) vrun "$1" "$2" merge 7 "$sha" ;;
    attest) vrun "$1" "$2" attest "$sha" "$t/att.json" ;;
    *) vrun "$1" "$2" "$3" ;;
  esac
}
# leak_flaw DIR: the token may be in one place only, a curl's stdin when the
# verb is meant to be calling the API.
leak_flaw() {
  leaks=$(grep -rlF -- "$tok" "$1" | grep -v '/ok/[^/]*/rec/curl\.stdin$' || true)
  [ -z "$leaks" ] || printf 'the token is in %s' "$(printf '%s\n' "$leaks" | sed "s#^$1/##" | tr '\n' ' ')"
  return 0
}

recorder_flaw() {  # ROOT VERBS: the verbs of ROOT's adapters/github/ to run
  root=$1 run_verbs=$2
  w=$t/runs
  rm -rf "$w"
  mkdir "$w"
  v_attached=1 v_repo=o/r v_token=$tok v_token_unset=""

  # With a token: where does it go?
  for v in $run_verbs; do vcall "$root" "$w/ok/$v" "$v"; done
  flaw=$(leak_flaw "$w")
  [ -z "$flaw" ] || { printf '%s' "$flaw"; return 0; }
  for v in $run_verbs; do
    d=$w/ok/$v
    case "$v" in
      bootstrap | open-pr | status | await | pr | merge | ready | attest)
        grep -qxF "Authorization: Bearer $tok" "$d/rec/curl.stdin" 2>/dev/null \
          || { printf '%s: no curl call carried "Authorization: Bearer <token>" on stdin; the check proved nothing' "$v"; return 0; }
        grep -qF '[-H] [@-]' "$d/rec/curl.argv" 2>/dev/null \
          || { printf '%s: curl was not given the header as -H @-' "$v"; return 0; }
        ;;
      push)
        grep -qF '[push]' "$d/rec/git.argv" 2>/dev/null \
          || { printf 'push: no git push was recorded; the check proved nothing'; return 0; }
        ;;
    esac
  done
  if grep -lE '://[^] /]*@' "$w"/ok/*/rec/*.argv >/dev/null 2>&1; then
    printf 'a curl or git argv holds a URL with credentials in it'; return 0
  fi

  # Refusals: no token, an empty one, no repository, no attached repository.
  for mode in empty unset norepo; do
    v_attached=1 v_repo=o/r v_token="" v_token_unset=""
    case "$mode" in
      unset) v_token_unset=1 ;;
      norepo) v_token=$tok v_repo="" ;;
    esac
    for v in $run_verbs; do
      d=$w/$mode/$v
      vcall "$root" "$d" "$v"
      [ -z "$(ls -A "$d/rec")" ] || { printf '%s with %s: made a curl or git call' "$v" "$mode"; return 0; }
      [ "$vrc" -eq 1 ] || { printf '%s with %s: exit %s, want 1' "$v" "$mode" "$vrc"; return 0; }
      [ "$(awk 'END{print NR}' "$d/err")" -eq 1 ] || { printf '%s with %s: stderr is not exactly one line' "$v" "$mode"; return 0; }
      grep -q '\.env' "$d/err" || { printf '%s with %s: the refusal does not name .env' "$v" "$mode"; return 0; }
      [ ! -s "$d/out" ] || { printf '%s with %s: wrote on stdout' "$v" "$mode"; return 0; }
    done
  done
  case " $run_verbs " in
    *" bootstrap "*)
      v_attached="" v_repo=o/r v_token=$tok v_token_unset=""
      d=$w/unattached/bootstrap
      vcall "$root" "$d" bootstrap
      [ "$vrc" -eq 1 ] && [ "$(awk 'END{print NR}' "$d/err")" -eq 1 ] && grep -q 'attach' "$d/err" && [ -z "$(ls -A "$d/rec")" ] \
        || { printf 'bootstrap on a repository that is not attached: want one stderr line naming ./go attach, exit 1 and no call'; return 0; }
      ;;
  esac
  case " $run_verbs " in
    *" attest "*)
      v_attached="" v_repo=o/r v_token=$tok v_token_unset=""
      d=$w/unattached/attest
      vcall "$root" "$d" attest
      [ "$vrc" -eq 1 ] && [ "$(awk 'END{print NR}' "$d/err")" -eq 1 ] && [ ! -s "$d/out" ] && [ -z "$(ls -A "$d/rec")" ] \
        || { printf 'attest on a repository that is not attached: want one stderr line, empty stdout, exit 1 and no call'; return 0; }
      ;;
  esac
  flaw=$(leak_flaw "$w")
  [ -z "$flaw" ] || { printf '%s' "$flaw"; return 0; }
  return 0
}

# --- (5) no git config, no remote edits -----------------------------------------
grep_flaw() {  # ROOT
  for d in "$1"/adapters/*/; do
    [ -f "${d}clone-url.sh" ] || continue
    hits=$(grep -nE 'git([[:space:]]+-[Cc][[:space:]]+[^[:space:]]+)*[[:space:]]+config|remote[[:space:]]+(add|set-url)' "${d}"*.sh \
      | grep -vE '^[^:]*:[0-9]+:[[:space:]]*#' || true)
    [ -z "$hits" ] || { printf 'a code host adapter edits git config or a remote: %s' "$(printf '%s' "$hits" | head -n 1)"; return 0; }
  done
  return 0
}

# ==================================================================================
flaw=$(surface_flaw .)
[ -z "$flaw" ] || nogo "$flaw"
flaw=$(credential_flaw .)
[ -z "$flaw" ] || nogo "$flaw"
flaw=$(makefile_flaw Makefile)
[ -z "$flaw" ] || nogo "$flaw"
opt_verbs=""
[ ! -f adapters/github/attest.sh ] || opt_verbs=" attest"
flaw=$(recorder_flaw . "$verbs$opt_verbs")
[ -z "$flaw" ] || nogo "$flaw"
flaw=$(grep_flaw .)
[ -z "$flaw" ] || nogo "$flaw"

# --- the check must be able to fail ----------------------------------------------
# A good tree: the code hosts' adapters, copied. Then one defect at a time; each
# must be named, and a defect that goes unnoticed is a NO-GO of its own.
good=$t/good
bad=$t/bad
mkdir -p "$good/adapters"
for d in adapters/*/; do
  if [ -f "${d}clone-url.sh" ]; then cp -R "$d" "$good/adapters/$(basename "$d")"; fi
done
fresh() { rm -rf "$bad"; cp -R "$good" "$bad"; }
gh=$bad/adapters/github
# expect NAME WANT FLAW: FLAW, what a *_flaw printed for the defect, must hold WANT.
expect() {
  case "$3" in
    *"$2"*) ;;
    "") nogo "ci/codehost.sh cannot fail: it accepted the defect '$1'" ;;
    *) nogo "ci/codehost.sh named the wrong problem for '$1'" "wanted a message with: $2" "got: $3" ;;
  esac
}

# The controls: the copy and a helper written from the ADR are judged good.
cat > "$t/helper-good.sh" <<'HELPER'
#!/bin/sh
[ "${1:-}" = get ] || exit 0
p="" h=""
while IFS= read -r l; do
  [ -n "$l" ] || break
  case "$l" in
    protocol=*) p=${l#protocol=} ;;
    host=*) h=${l#host=} ;;
  esac
done
[ "$p" = https ] || exit 0
[ "$h" = github.com ] || exit 0
[ -n "${GITHUB_TOKEN:-}" ] || exit 0
printf 'username=x-access-token\npassword=%s\n' "$GITHUB_TOKEN"
HELPER
fresh
control=$(surface_flaw "$bad")$(grep_flaw "$bad")$(recorder_flaw "$bad" "pr push attest")
[ -z "$control" ] || nogo "the self-test's good tree is not judged good: $control"
cp "$t/helper-good.sh" "$gh/credential.sh"
control=$(credential_flaw "$bad")
[ -z "$control" ] || nogo "the self-test's good credential helper is not judged good: $control"

# (1) a code host missing a script.
fresh
rm "$bad/adapters/gitea/merge.sh"
expect "a code host without merge.sh" "no merge.sh" "$(surface_flaw "$bad")"
fresh
rm "$bad/adapters/github/ready.sh"
expect "a code host without ready.sh" "no ready.sh" "$(surface_flaw "$bad")"
fresh
: > "$bad/adapters/github/await.sh"
expect "an empty await.sh" "no await.sh" "$(surface_flaw "$bad")"

# (2) a helper with one defect.
fresh
grep -vF '[ "$h" = github.com ]' "$t/helper-good.sh" > "$gh/credential.sh"
expect "a helper that answers for any host" "wrong host" "$(credential_flaw "$bad")"
fresh
sed 's/^\[ "\$h" = github.com \] || exit 0$/case "$h" in github.com*) ;; *) exit 0 ;; esac/' "$t/helper-good.sh" > "$gh/credential.sh"
if cmp -s "$t/helper-good.sh" "$gh/credential.sh"; then nogo "the self-test 'a helper that matches a host by prefix' no longer changes the helper"; fi
expect "a helper that matches a host by prefix" "wrong host" "$(credential_flaw "$bad")"
fresh
grep -vF '[ "$p" = https ]' "$t/helper-good.sh" > "$gh/credential.sh"
expect "a helper that answers over http" "http://github.com" "$(credential_flaw "$bad")"
fresh
grep -vF '[ "${1:-}" = get ]' "$t/helper-good.sh" > "$gh/credential.sh"
expect "a helper that answers on store and erase" "answered, want silence" "$(credential_flaw "$bad")"
fresh
grep -vF '[ -n "${GITHUB_TOKEN:-}" ]' "$t/helper-good.sh" > "$gh/credential.sh"
expect "a helper that answers with an empty token" "empty token" "$(credential_flaw "$bad")"
fresh
{ cat "$t/helper-good.sh"; printf 'printf "%%s\\n" "$GITHUB_TOKEN" >&2\n'; } > "$gh/credential.sh"
expect "a helper that writes the token on stderr" "stderr" "$(credential_flaw "$bad")"

# (3) a Makefile with one defect.
mutate() {  # NAME SED-SCRIPT: $t/Makefile.NAME is the Makefile with the edit; it must differ
  sed "$2" Makefile > "$t/Makefile.$1"
  if cmp -s Makefile "$t/Makefile.$1"; then nogo "the self-test '$1' no longer changes the Makefile; update its sed expression"; fi
}
mutate helper-at-0 's/GIT_CONFIG_KEY_1/GIT_CONFIG_KEY_0/g; s/GIT_CONFIG_VALUE_1/GIT_CONFIG_VALUE_0/g'
expect "the helper written over safe.directory at index 0" "overwritten" "$(makefile_flaw "$t/Makefile.helper-at-0")"
mutate count-1 's/^GIT_CONFIG_COUNT := 2$/GIT_CONFIG_COUNT := 1/'
expect "a count that leaves the helper out" "GIT_CONFIG_COUNT" "$(makefile_flaw "$t/Makefile.count-1")"
mutate every-host 's/^ifneq (\$(wildcard adapters\/\$(CODE_HOST)\/credential.sh),)$/ifneq (x,)/'
expect "a helper entry for a code host that ships none" "CODE_HOST=gitea" "$(makefile_flaw "$t/Makefile.every-host")"
mutate wrong-path "s|/credential\\.sh'\$|/nope.sh'|"
expect "a helper entry naming a file that is not there" "GIT_CONFIG_VALUE_1" "$(makefile_flaw "$t/Makefile.wrong-path")"
mutate not-a-helper 's/^GIT_CONFIG_KEY_1 := credential.helper$/GIT_CONFIG_KEY_1 := credential.username/'
expect "an entry that is not a credential helper" "GIT_CONFIG_KEY_1" "$(makefile_flaw "$t/Makefile.not-a-helper")"

# (4) an adapter with one defect, appended to env.sh (the last definition
# wins) or written from scratch.
fresh
cat >> "$gh/env.sh" <<'DEFECT'
github_api() {
  _method=$1 _path=$2 _out=$3
  _code=$(curl -sS -o "$_out" -w '%{http_code}' -H "Authorization: Bearer $GITHUB_TOKEN" -X "$_method" "$GITHUB_API$_path") || _code=000
  printf '%s' "${_code:-000}"
}
DEFECT
expect "the token in curl's argv" "curl.argv" "$(recorder_flaw "$bad" "pr push")"
fresh
printf 'GITHUB_CLONE_URL="https://x-access-token:$GITHUB_TOKEN@github.com/$GITHUB_REPO.git"\n' >> "$gh/env.sh"
expect "the token copied into a variable, then into a git URL" "git.argv" "$(recorder_flaw "$bad" "pr push")"
fresh
printf 'GIT_CONFIG_VALUE_1="!f() { echo password=$GITHUB_TOKEN; }; f"\nexport GIT_CONFIG_VALUE_1\n' >> "$gh/env.sh"
expect "the token in a GIT_CONFIG_* value" "git.env" "$(recorder_flaw "$bad" "pr push")"
fresh
printf 'printf "token: %%s\\n" "$GITHUB_TOKEN" >&2\n' >> "$gh/env.sh"
expect "the token on stderr" "/err" "$(recorder_flaw "$bad" "pr push")"
fresh
printf 'printf "%%s\\n" "$GITHUB_TOKEN"\n' >> "$gh/env.sh"
expect "the token on stdout" "/out" "$(recorder_flaw "$bad" "pr push")"
fresh
cat > "$gh/env.sh" <<'DEFECT'
GITHUB_API=https://api.github.com
GITHUB_CLONE_URL="https://github.com/${GITHUB_REPO:-}.git"
github_api() {
  _method=$1 _path=$2 _out=$3
  _code=$(printf 'Authorization: Bearer %s\n' "$GITHUB_TOKEN" | curl -sS -o "$_out" -w '%{http_code}' -H @- -X "$_method" "$GITHUB_API$_path") || _code=000
  printf '%s' "${_code:-000}"
}
DEFECT
expect "no refusal when the token is empty" "empty" "$(recorder_flaw "$bad" "pr push")"
fresh
cat > "$gh/env.sh" <<'DEFECT'
[ -n "${GITHUB_TOKEN:-}" ] || { printf 'no token\n' >&2; exit 1; }
GITHUB_API=https://api.github.com
GITHUB_CLONE_URL="https://github.com/${GITHUB_REPO:-}.git"
github_api() {
  _method=$1 _path=$2 _out=$3
  _code=$(printf 'Authorization: Bearer %s\n' "$GITHUB_TOKEN" | curl -sS -o "$_out" -w '%{http_code}' -H @- -X "$_method" "$GITHUB_API$_path") || _code=000
  printf '%s' "${_code:-000}"
}
DEFECT
expect "a refusal that does not name .env" "does not name .env" "$(recorder_flaw "$bad" "pr push")"
fresh
printf '#!/bin/sh\nset -eu\nhere=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)\n. "$here/adapters/github/env.sh"\ntmp=$(mktemp)\ngithub_api GET "/repos/$GITHUB_REPO" "$tmp" >/dev/null\n' > "$gh/bootstrap.sh"
expect "a bootstrap that serves a repository that is not attached" "not attached" "$(recorder_flaw "$bad" "bootstrap")"
fresh
printf '#!/bin/sh\nset -eu\nhere=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)\n. "$here/adapters/github/env.sh"\ntmp=$(mktemp)\ngithub_api POST "/repos/$GITHUB_REPO/attestations" "$tmp" "{}" >/dev/null\n' > "$gh/attest.sh"
expect "an attest.sh that serves a repository that is not attached" "not attached" "$(recorder_flaw "$bad" "attest")"
# A poller that reads its deadline before its first call (373da51's shape).
for pv in ready await; do
  fresh
  cat > "$gh/$pv.sh" <<'DEFECT'
#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$here/adapters/github/env.sh"
tmp=$(mktemp)
start=$(date +%s)
while [ $(( $(date +%s) - start )) -lt 1 ]; do
  [ "$(github_api GET "/repos/$GITHUB_REPO" "$tmp")" = 200 ] && exit 0
  sleep 1
done
printf 'timed out\n' >&2
exit 1
DEFECT
  expect "a $pv.sh that reads its deadline before its first call" "$pv: no curl call" "$(recorder_flaw "$bad" "$pv")"
done

# (5) an adapter that edits git config or a remote.
fresh
printf '# not git config, not remote add: a comment is not code\n' >> "$gh/push.sh"
control=$(grep_flaw "$bad")
[ -z "$control" ] || nogo "the self-test's comment about git config is judged code: $control"
printf 'git -C "$workspace" config credential.helper store\n' >> "$gh/push.sh"
expect "git config in a verb" "edits git config" "$(grep_flaw "$bad")"
fresh
printf 'git -C "$workspace" remote add origin "$GITHUB_CLONE_URL"\n' >> "$gh/push.sh"
expect "git remote add in a verb" "edits git config" "$(grep_flaw "$bad")"
fresh
printf 'git remote set-url origin "$GITHUB_CLONE_URL"\n' >> "$bad/adapters/gitea/push.sh"
expect "git remote set-url in the reference adapter" "edits git config" "$(grep_flaw "$bad")"

go
