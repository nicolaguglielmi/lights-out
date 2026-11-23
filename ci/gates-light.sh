#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-gates-light: proves docs/adr/0061 §9 cold, no engine, no network.
#   (a) each app/fixtures/gates fixture of `make demo-gates`, applied with
#       `git am` in a throwaway worktree of HEAD (its base/ patches first,
#       their commit the base), is judged by the light
#       tier (`python3 -m core gates --light`) against the fixture's
#       committed `expect`: RISK_LEVEL and RISK_DECISION always equal; where
#       expect failed at a gate the light tier ran, the light tier fails at
#       that gate; otherwise the light tier fails only at a gate after
#       expect's FAILED in the Makefile's GATES order, or not at all (with
#       this fixture set: GO). test and test-proof do not run here (the
#       app's TEST_CMD needs the compose Postgres); core/tests/test_light.py
#       proves them on scratch repositories.
#   (b) the light tier on this checkout's last commit is the one line of §5,
#       never `GO: gates`, and takes under 10 s.
#   (c) pyproject.toml maps `lights_out` onto core/ (§1), ships
#       `script-files = ["lights-out"]` (ADR 0099 §1), and no core/*.py
#       imports itself as `core.x` (§2).
#   (d) the comparison and the scan are shown to fail on a planted wrong
#       expect, a planted `from core.x import y` and a planted wrong
#       pyproject, each through the same functions the real checks use.
set -eu
name=gates-light
. ci/lib.sh

unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
root=$(pwd)
order=$(sed -n 's/^GATES := //p' Makefile)
[ -n "$order" ] || nogo "no GATES := line in the Makefile"
t=$(mktemp -d)
trap 'rm -rf "$t"; git worktree prune >/dev/null 2>&1 || :' EXIT
git worktree prune

# env_of FILE KEY: the value of KEY in a KEY=VALUE file, empty when absent.
env_of() { sed -n "s/^$2=//p" "$1" | tail -n 1; }

# position GATE: its place in the Makefile's GATES, 0 when it is not there.
position() {
  i=0
  for g in $order; do
    i=$((i + 1))
    [ "$g" != "$1" ] || { echo "$i"; return; }
  done
  echo 0
}

# verdict_flaw EXPECT VERDICT: what in the light tier's VERDICT disagrees with
# the fixture's EXPECT (the full verdict), or nothing when it agrees.
verdict_flaw() {
  for key in RISK_LEVEL RISK_DECISION; do
    [ "$(env_of "$1" "$key")" = "$(env_of "$2" "$key")" ] \
      || { printf '%s is %s, want %s' "$key" "$(env_of "$2" "$key")" "$(env_of "$1" "$key")"; return; }
  done
  want_failed=$(env_of "$1" FAILED)
  got=$(env_of "$2" GATES)
  got_failed=$(env_of "$2" FAILED)
  if [ -n "$want_failed" ] && env_of "$2" RAN | tr -s ', ' '\n' | grep -qx -- "$want_failed"; then
    { [ "$got" = NO-GO ] && [ "$got_failed" = "$want_failed" ]; } \
      || printf 'the light tier ran %s, where the full verdict failed, and says %s %s' "$want_failed" "$got" "$got_failed"
  elif [ "$got" = NO-GO ]; then
    [ "$(position "$want_failed")" -gt 0 ] && [ "$(position "$got_failed")" -gt "$(position "$want_failed")" ] \
      || printf 'the light tier fails at %s, which the full verdict did not fail at or before' "$got_failed"
  elif [ "$got" != GO ]; then
    printf 'GATES is %s' "$got"
  fi
}

# judge_fixture DIR: the flaw in the light tier's verdict on DIR's patch against
# DIR/expect, with got and want, or nothing.
judge_fixture() {
  jf=$(basename "$1")
  jf_flaw=""
  git worktree add --detach "$t/wt-$jf" HEAD >/dev/null 2>"$t/$jf.err" \
    || { printf 'git worktree add failed: %s' "$(cat "$t/$jf.err")"; return; }
  # A base/ directory's patches first: the commit they make is the base
  # (docs/adr/0072 §1, as make demo-gates).
  jf_applied=1
  if [ -d "$1/base" ]; then
    git -C "$t/wt-$jf" -c user.name=lights-out-fixtures -c user.email=fixtures@lights-out.invalid \
      -c commit.gpgsign=false am "$1"/base/*.patch >/dev/null 2>"$t/$jf.err" || jf_applied=0
  fi
  jf_base=$(git -C "$t/wt-$jf" rev-parse HEAD)
  if [ "$jf_applied" = 1 ] && git -C "$t/wt-$jf" -c user.name=lights-out-fixtures -c user.email=fixtures@lights-out.invalid \
      -c commit.gpgsign=false am "$1"/*.patch >/dev/null 2>"$t/$jf.err"; then
    jf_head=$(git -C "$t/wt-$jf" rev-parse HEAD)
    (cd "$t/wt-$jf" && PYTHONPATH="$root" PYTHONSAFEPATH=1 python3 -m core gates --light \
      BASE="$jf_base" HEAD="$jf_head" WORKLOAD=app GATES_OUT="$t/out-$jf") >/dev/null 2>"$t/$jf.err" </dev/null || :
    if [ -f "$t/out-$jf/verdict.env" ]; then
      jf_flaw=$(verdict_flaw "$1/expect" "$t/out-$jf/verdict.env")
      [ -z "$jf_flaw" ] || jf_flaw="$jf_flaw (got: $(tr '\n' ' ' <"$t/out-$jf/verdict.env"); want: $(tr '\n' ' ' <"$1/expect"))"
    else
      jf_flaw="the light tier wrote no verdict.env"
    fi
  else
    jf_flaw="the patch does not apply"
  fi
  printf '%s' "$jf_flaw"
  git worktree remove --force "$t/wt-$jf" >/dev/null 2>&1 || :
}

# scan_imports DIR: the core/*.py lines of DIR that import the package as `core`.
scan_imports() {
  grep -nE '^[[:space:]]*(from core\.|import core([^[:alnum:]_]|$))' "$1"/*.py || :
}

# pyproject_flaw FILE: what in FILE breaks the mapping of §1, or nothing.
pyproject_flaw() {
  python3 - "$1" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    p = tomllib.load(f)
project, setuptools = p.get("project", {}), p.get("tool", {}).get("setuptools", {})
mapping = setuptools.get("package-dir", {})
shipped = [g for globs in setuptools.get("package-data", {}).values() for g in globs]
for ok, why in [
    (project.get("name") == "lights-out-gates", "project name is not lights-out-gates"),
    (project.get("dependencies") == [], "dependencies is not empty"),
    (project.get("scripts", {}) == {"lights-out-gates": "lights_out.__main__:gates_main"},
     "the scripts are not exactly lights-out-gates = lights_out.__main__:gates_main"),
    (setuptools.get("script-files") == ["lights-out"],
     'tool.setuptools.script-files is not ["lights-out"]'),
    (mapping.get("lights_out") == "core", "package-dir does not map lights_out to core"),
    (mapping.get("lights_out.platform_policy") == "policy",
     "package-dir does not map lights_out.platform_policy to policy"),
    ("perimeter.sh" in shipped, "package-data does not ship perimeter.sh"),
    ("merge.env" in shipped, "package-data does not ship merge.env"),
]:
    if not ok:
        print(why)
        break
PY
}

# (a)
found=0
for dir in app/fixtures/gates/*/; do
  [ -d "$dir" ] || continue
  found=$((found + 1))
  dir=${dir%/}
  flaw=$(judge_fixture "$root/$dir")
  [ -z "$flaw" ] || nogo "fixture $(basename "$dir"): $flaw" "$(cat "$t/$(basename "$dir").err")"
done
[ "$found" -gt 0 ] || nogo "no fixture in app/fixtures/gates/"

# (b) Provenance: PLAN M35's acceptance, the light tier answers in under 10 s
# (docs/adr/0061 §9). date +%s is whole seconds, so 10 s or more is a NO-GO.
t0=$(date +%s)
python3 -m core gates --light >"$t/last.out" 2>"$t/last.err" </dev/null || :
t1=$(date +%s)
[ "$((t1 - t0))" -lt 10 ] || nogo "the light tier took $((t1 - t0)) s on the last commit, the budget is under 10 s" "$(cat "$t/last.err")"
[ "$(awk 'END{print NR}' "$t/last.out")" -eq 1 ] || nogo "the light tier's stdout is not one line: $(cat "$t/last.out")" "$(cat "$t/last.err")"
last=$(cat "$t/last.out")
case "$last" in
  "GO: gates-light — "*"secrets, deps, sast, mutation need the image"*) ;;
  "NO-GO: gates-light — "*) ;;
  *) nogo "the light tier's line on the last commit is not GO or NO-GO gates-light naming the image: $last" "$(cat "$t/last.err")" ;;
esac

# (c)
flaw=$(pyproject_flaw pyproject.toml)
[ -z "$flaw" ] || nogo "pyproject.toml: $flaw"
grep -q '^def main(' core/__main__.py || nogo "core/__main__.py defines no def main("
flaw=$(scan_imports core)
[ -z "$flaw" ] || nogo "core/*.py imports itself as core.x, ADR 0061 §2: $flaw"

# (d) honest-fix with an expect that says it fails test-weakening, honest-fix
# with a wrong risk, and assertion-removed with an expect that says GO; the
# same planted import and pyproject, caught by the same functions.
plant() {  # NAME PATCHDIR EXPECT...
  p_dir=$t/plant-$1
  mkdir "$p_dir"
  cp "$2"/*.patch "$p_dir/"
  shift 2
  printf '%s\n' "$@" >"$p_dir/expect"
}
fixtures=app/fixtures/gates
plant says-test-weakening "$fixtures/honest-fix" GATES=NO-GO FAILED=test-weakening RISK_LEVEL=low RISK_DECISION=auto-merge
plant says-high-risk "$fixtures/honest-fix" GATES=GO RISK_LEVEL=high RISK_DECISION=review
plant says-go "$fixtures/assertion-removed" GATES=GO RISK_LEVEL=low RISK_DECISION=auto-merge
for planted in "$t"/plant-*/; do
  [ -n "$(judge_fixture "${planted%/}")" ] || nogo "the comparison accepted a planted wrong expect: $(basename "$planted")"
done

mkdir "$t/core-planted"
cp core/risk.py "$t/core-planted/risk.py"
[ -z "$(scan_imports "$t/core-planted")" ] || nogo "the scan flags core/risk.py as it is: $(scan_imports "$t/core-planted")"
printf 'from core.x import y\n' >>"$t/core-planted/risk.py"
[ -n "$(scan_imports "$t/core-planted")" ] || nogo "the scan missed a planted from core.x import y"

cat >"$t/good.toml" <<'TOML'
[project]
name = "lights-out-gates"
dependencies = []
[project.scripts]
lights-out-gates = "lights_out.__main__:gates_main"
[tool.setuptools]
script-files = ["lights-out"]
[tool.setuptools.package-dir]
lights_out = "core"
"lights_out.platform_policy" = "policy"
[tool.setuptools.package-data]
lights_out = ["perimeter.sh"]
"lights_out.platform_policy" = ["merge.env"]
TOML
[ -z "$(pyproject_flaw "$t/good.toml")" ] || nogo "the pyproject check flags a good pyproject: $(pyproject_flaw "$t/good.toml")"
for broken in 's/^name = .*/name = "other"/' 's/^dependencies = .*/dependencies = ["requests"]/' \
  's/^lights_out = "core"/lights_out = "src"/' 's/\["merge.env"\]/["other.env"]/' \
  's/^lights-out-gates = .*/lights-out = "lights_out.__main__:main"/' 's/^name = .*/name = "lights-out"/' \
  's/^script-files = .*/script-files = []/' 's/^script-files = .*/script-files = ["lights-out", "other"]/' \
  '/^script-files = /d'; do
  sed "$broken" "$t/good.toml" >"$t/bad.toml"
  [ -n "$(pyproject_flaw "$t/bad.toml")" ] || nogo "the pyproject check accepted a planted wrong pyproject ($broken)"
done

go
