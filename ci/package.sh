#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-package: docs/adr/0045 §8, docs/adr/0057 §3-§8, and docs/adr/0105,
# engine-free and network-free (the engine half is `./go demo-package`);
# written from the ADRs, not from the wrapper.
#   wrapper    lights-out is an executable #!/bin/sh file, clean under
#              `shellcheck -s sh` (ci/shell.sh does not list it), one semver
#              version= line, no --env-file or :z/:Z in its code; the
#              subcommands are 24 (8 read-only/host: init gates test-proof
#              test-integrity triage postmortem doctor version; 4 state-only:
#              next dora spend cloud-setup; 12 opt-in plane/workflow: start
#              intake answer intake-confirm assign up down smoke release
#              promote console evidence); an unknown subcommand, disallowed
#              flag, or unknown KEY=VALUE is refused before any engine is
#              touched; with no engine on PATH an engine subcommand exits
#              non-zero with one stderr line and nothing on stdout; `version`
#              and `--version` work without touching an engine and match
#              `pyproject.toml`'s `[project].version` (ADR 0099); behavioral
#              fake-engine checks prove read-only/gates and state-only
#              commands NEVER pass a socket mount or DOCKER_HOST, while
#              opt-in plane commands DO (with stderr notice, platform/crew
#              mirroring, console port forwarding, and clean missing-socket
#              refusal).
#   allowlist  the wrapper's KEY=* case arms equal package/entrypoint.sh's and
#              each is taken.
#   yaml       (PyYAML) the Action is action.yml at the repository root, and
#              action/action.yml is gone: composite, runs $GITHUB_ACTION_PATH/
#              lights-out (no /../) with the subcommand chosen by its `gates`
#              input (default all), posts summary.md, calls no REST path but
#              check-runs; jobs that use ./ hold at most contents: read +
#              checks: write, and none still uses ./action; packages: write
#              and id-token: write only package.yml's publish (v* tag or
#              workflow_dispatch), which runs a keyless cosign sign, and
#              id-token: write also its pypi job (ADR 0075 §2); every job
#              has the github.com guard (ADR 0039).
#   first run  (docs/adr/0064 §2, §4) the first-run reason, `the repository has no
#              .lights-out/workload.env committed; lights-out init drafts one`,
#              is in both package/entrypoint.sh and action.yml outside the
#              comments (the Action matches on the entrypoint's exact text), and
#              so is the removal reason, `the change removes .lights-out/
#              workload.env, which its base has; a change cannot remove the
#              contract that judges it`, in the entrypoint; action.yml uses
#              actions/upload-artifact at v7.0.2's pin cf430e0 and no other ref
#              of it in the Action or its shims, with include-hidden-files: true and overwrite:
#              true; no new permission (the yaml checks above, kept: check-runs
#              the only REST path, at most contents: read and checks: write).
#   shims      actions/test-proof and actions/test-integrity: composite, a
#              README.md beside, name, description, branding, exactly one step
#              `uses: nicolaguglielmi/lights-out@v<the wrapper's version=>`
#              with `gates: <the directory's name>`, every declared input
#              passed through as ${{ inputs.<same name> }}, and every name
#              there an input of the root action.yml.
#   doctor     core/doctor.sh with no engine: one `NO-GO: doctor —` line naming
#              the engine, exit 1, no ANSI. With --level (ADR 0057 §8): l2 and
#              l3 are one `NO-GO: doctor — <level> lacks ` line naming
#              engine and toolchain; l0 and l1 do not name
#              toolchain; --level l9 and an unknown argument are one NO-GO
#              line; with a fake engine that answers, plain doctor and
#              --level l0 are GO, so the last two are refused for the
#              argument, not for the engine.
# Each judge is then run on copies of the real files with one defect each (the
# edits name the real text; if it moves, the defect is not applied and this
# says it cannot fail). The --level judge, whose real subject may not do
# --level yet, is run on a small stand-in doctor written from the ADR: it must
# pass that one, and fail on each copy of it with one defect.
# shellcheck disable=SC2016,SC2086 # edits are text; word lists are split on purpose
set -eu
name=package
. ci/lib.sh

for t in shellcheck python3; do command -v "$t" >/dev/null 2>&1 || nogo "$t is not on PATH (./go ci runs in the workbench)"; done
python3 -c 'import yaml' 2>/dev/null || nogo "python3 cannot import yaml (PyYAML, workbench/uv.lock)"
for f in lights-out pyproject.toml package/entrypoint.sh action.yml .github/workflows/package.yml core/doctor.sh; do
  [ -e "$f" ] || nogo "$f not found"
done

root=$(pwd -P)
sh_bin=$(command -v sh)
w=$(mktemp -d)
trap 'rm -rf "$w"' EXIT
# tools: a PATH of plain tools and no engine. trip: a podman and docker that only leave a mark.
mkdir "$w/tools" "$w/trip" "$w/repo" "$w/fake" "$w/beh"
for t in git awk sed grep tr sort head tail cut cksum cat uname basename dirname mkdir rm mktemp id env wc stat; do
  p=$(command -v "$t" 2>/dev/null || true)
  case "$p" in /*) ln -s "$p" "$w/tools/$t" ;; esac
done
printf '#!/bin/sh\n: > %s/tripped\n' "$w" > "$w/trip/podman"
cp "$w/trip/podman" "$w/trip/docker"
chmod +x "$w/trip/podman" "$w/trip/docker"
# fake: a podman that answers every call, for a doctor whose only failing item is the one under test.
printf '#!/bin/sh\ncase "$*" in *RemoteSocket*) echo true ;; *Client.Version*) echo 5.0.0 ;; esac\nexit 0\n' > "$w/fake/podman"
chmod +x "$w/fake/podman"
# beh: recording fake podman/docker engines that log `run` argv for behavioral socket-boundary checks.
cat > "$w/beh/podman" <<SH
#!/bin/sh
case "\$1" in
  run)
    shift
    : > "$w/beh.run"
    for a do printf '%s\n' "\$a" >> "$w/beh.run"; done
    exit 0 ;;
esac
case "\$*" in
  *RemoteSocket.Path*) printf '%s\n' "\${FAKE_REMOTE_SOCK:-$w/fake-podman.sock}"; exit 0 ;;
  *RemoteSocket.Exists*) printf '%s\n' "\${FAKE_REMOTE_EXISTS:-true}"; exit 0 ;;
  *Client.Version*) printf '5.0.0\n'; exit 0 ;;
esac
exit 0
SH
cp "$w/beh/podman" "$w/beh/docker"
chmod +x "$w/beh/podman" "$w/beh/docker"
python3 -c 'import socket, sys; s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.bind(sys.argv[1]); s.close()' "$w/beh.sock"
git init -q "$w/repo" >/dev/null 2>&1 || nogo "git init failed in a temporary directory"

lines() { awk 'END{print NR}' "$1"; }
has_all() { hf=$1; shift; for hx; do grep -qw -- "$hx" "$hf" || return 1; done; }
# run PATH CMD...: CMD in an empty environment but PATH; stdout in $w/o, stderr in $w/e, status in $rc.
run() { rp=$1; shift; rm -f "$w/tripped" "$w/beh.run"; (cd "$w/repo" && env -i PATH="$rp" HOME="$w" "$@") >"$w/o" 2>"$w/e" && rc=0 || rc=$?; }
run_env() { rp=$1; shift; rm -f "$w/tripped" "$w/beh.run"; (cd "$w/repo" && env -i PATH="$rp" HOME="$w" "$@") >"$w/o" 2>"$w/e" && rc=0 || rc=$?; }
# keys FILE: the KEY of each KEY=* case arm outside comments, sorted.
keys() { grep -Ev '^[[:space:]]*#' "$1" | grep -oE '[A-Z][A-Z0-9_]*=/?\*' | sed 's/=.*//' | LC_ALL=C sort -u | tr '\n' ' '; }

READONLY_CMDS="init gates test-proof test-integrity triage postmortem"
STATE_CMDS="next dora spend cloud-setup"
PLANE_CMDS="start intake answer intake-confirm assign up down smoke release promote console evidence provenance"
ALL_CMDS="$READONLY_CMDS doctor version $STATE_CMDS $PLANE_CMDS"

# wrapper_flaws ROOT [MODE]: what is wrong with ROOT/lights-out, one line each.
# MODE=full checks all 29 keys and all 24 subcommands; MODE=fast checks the
# subset exercised by try()'s planted-defect self-tests.
wrapper_flaws() {
  f=$1/lights-out
  wf_mode=${2:-fast}
  k=$(keys "$1/package/entrypoint.sh")
  v=$(sed -n 's/^version=//p' "$f")
  { [ -x "$f" ] && [ "$(head -n 1 "$f")" = '#!/bin/sh' ]; } || echo "the wrapper is not an executable #!/bin/sh file"
  shellcheck -s sh --color=never "$f" >"$w/sc" 2>&1 || { echo "shellcheck -s sh reports problems in the wrapper"; cat "$w/sc"; }
  { [ "$(grep -c '^version=' "$f")" = 1 ] && printf '%s\n' "$v" | grep -Eq '^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){2}$'; } \
    || echo "the wrapper needs exactly one version= line, semver X.Y.Z"
  ! grep -Ev '^[[:space:]]*#' "$f" | grep -qE -- '--env-file|[:,][zZ]([^A-Za-z0-9_]|$)' \
    || echo "the wrapper names --env-file or a :z/:Z mount"
  { [ -n "$k" ] && [ "$k" = "$(keys "$f")" ]; } || echo "the allowlists differ: the wrapper takes [$(keys "$f")], the entrypoint [$k]"
  run "$w/trip:$w/tools" "$f" frobnicate
  { [ "$rc" -ne 0 ] && [ ! -e "$w/tripped" ] && [ ! -s "$w/o" ] && has_all "$w/e" $ALL_CMDS; } \
    || echo "an unknown subcommand is not refused on stderr, before any engine, naming all 24"
  for a in "init NOPE=1" "gates NOPE=1" "triage BASEX=1" "postmortem base=x" "next NOPE=1" "up NOPE=1" "start base=x"; do
    run "$w/trip:$w/tools" "$f" $a
    { [ "$rc" -ne 0 ] && [ ! -e "$w/tripped" ] && [ ! -s "$w/o" ] && has_all "$w/e" $k; } || echo "'$a' is not refused before any engine, naming $k"
  done
  for a in "test-proof NOPE=1" "test-integrity NOPE=1" "test-proof base=x" "test-integrity BASEX=1" "gates --json" "up --non-interactive" "next --bogus" "start --preset" "up --engine" "next --engine" "doctor --engine"; do
    run "$w/trip:$w/tools" "$f" $a
    { [ "$rc" -ne 0 ] && [ ! -e "$w/tripped" ] && [ ! -s "$w/o" ]; } || echo "'$a' is not refused before any engine"
  done
  if [ "$wf_mode" = full ]; then
    check_keys=$k
    check_state=$STATE_CMDS
    check_plane=$PLANE_CMDS
    check_engine_gates="init gates test-proof test-integrity"
  else
    check_keys="BASE INCIDENT"
    check_state="next"
    check_plane="up console"
    check_engine_gates="gates"
  fi
  for x in $check_keys; do
    run "$w/trip:$w/tools" "$f" gates "$x=x"
    [ -e "$w/tripped" ] || echo "$x= is in the entrypoint's allowlist and the wrapper refuses it"
  done
  for s in test-proof test-integrity; do
    run "$w/trip:$w/tools" "$f" "$s" BASE=x HEAD=y
    [ -e "$w/tripped" ] || echo "$s refuses BASE= HEAD=, the range it judges"
  done
  if [ "$wf_mode" = full ]; then
    for a in init gates test-proof test-integrity "triage ALERT=a" "postmortem INCIDENT=i" $STATE_CMDS $PLANE_CMDS; do
      run "$w/tools" "$f" $a
      { [ "$rc" -ne 0 ] && [ ! -s "$w/o" ] && [ "$(lines "$w/e")" = 1 ] && grep -qi engine "$w/e"; } \
        || echo "'$a' with no engine: want non-zero, nothing on stdout, one stderr line naming the engine"
    done
  else
    for a in init gates test-proof test-integrity next up; do
      run "$w/tools" "$f" "$a"
      { [ "$rc" -ne 0 ] && [ ! -s "$w/o" ] && [ "$(lines "$w/e")" = 1 ] && grep -qi engine "$w/e"; } \
        || echo "'$a' with no engine: want non-zero, nothing on stdout, one stderr line naming the engine"
    done
    for a in "triage ALERT=a" "postmortem INCIDENT=i"; do
      run "$w/tools" "$f" $a
      { [ "$rc" -ne 0 ] && [ ! -s "$w/o" ] && [ "$(lines "$w/e")" = 1 ] && grep -qi engine "$w/e"; } \
        || echo "'$a' with no engine: want non-zero, nothing on stdout, one stderr line naming the engine"
    done
  fi
  # Behavioral fake-engine socket boundary checks (ADR 0105, 0108):
  if [ "$wf_mode" = full ]; then
    for a in init gates test-proof test-integrity "triage ALERT=a" "postmortem INCIDENT=i"; do
      run "$w/beh:$w/tools" "$f" $a
      { [ "$rc" -eq 0 ] && [ -s "$w/beh.run" ]; } || { echo "read-only '$a' failed under fake engine: $(cat "$w/e")"; continue; }
      ! grep -qF '.sock' "$w/beh.run" || echo "read-only '$a' mounted a socket"
      ! grep -qE '^DOCKER_HOST' "$w/beh.run" || echo "read-only '$a' passed DOCKER_HOST"
      ! grep -qE '^LIGHTS_OUT_PLATFORM_DIR=' "$w/beh.run" || echo "read-only '$a' passed LIGHTS_OUT_PLATFORM_DIR"
      ! grep -qE '^LIGHTS_OUT_ENGINE_GATES=' "$w/beh.run" || echo "read-only '$a' passed LIGHTS_OUT_ENGINE_GATES"
    done
  else
    run "$w/beh:$w/tools" "$f" gates
    if [ "$rc" -eq 0 ] && [ -s "$w/beh.run" ]; then
      ! grep -qF '.sock' "$w/beh.run" || echo "read-only 'gates' mounted a socket"
      ! grep -qE '^DOCKER_HOST' "$w/beh.run" || echo "read-only 'gates' passed DOCKER_HOST"
      ! grep -qE '^LIGHTS_OUT_PLATFORM_DIR=' "$w/beh.run" || echo "read-only 'gates' passed LIGHTS_OUT_PLATFORM_DIR"
      ! grep -qE '^LIGHTS_OUT_ENGINE_GATES=' "$w/beh.run" || echo "read-only 'gates' passed LIGHTS_OUT_ENGINE_GATES"
    else
      echo "read-only 'gates' failed under fake engine: $(cat "$w/e")"
    fi
  fi
  for s in $check_engine_gates; do
    run_env "$w/beh:$w/tools" LIGHTS_OUT_OUT="$w/out-eng-$s" "$f" "$s" --engine
    { [ "$rc" -eq 0 ] && [ -s "$w/beh.run" ]; } || { echo "engine-gates '$s --engine' failed under fake engine: $(cat "$w/e")"; continue; }
    grep -qF "$w/fake-podman.sock:/var/run/docker.sock" "$w/beh.run" || echo "engine-gates '$s --engine' did not mount the engine socket"
    grep -qx 'DOCKER_HOST=unix:///var/run/docker.sock' "$w/beh.run" || echo "engine-gates '$s --engine' did not pass DOCKER_HOST"
    grep -qx 'LIGHTS_OUT_ENGINE_GATES=1' "$w/beh.run" || echo "engine-gates '$s --engine' did not pass LIGHTS_OUT_ENGINE_GATES=1"
    grep -qE '^LIGHTS_OUT_PLATFORM_DIR=' "$w/beh.run" || echo "engine-gates '$s --engine' did not pass LIGHTS_OUT_PLATFORM_DIR"
    grep -qE '^LIGHTS_OUT_CREW_DIR=' "$w/beh.run" || echo "engine-gates '$s --engine' did not pass LIGHTS_OUT_CREW_DIR"
    grep -qF "ghcr.io/nicolaguglielmi/lights-out:${v:-?}" "$w/beh.run" || echo "engine-gates '$s --engine' did not use full image"
    [ "$(grep -ci 'socket' "$w/e")" = 1 ] || echo "engine-gates '$s --engine' did not print a 1-line opt-in socket notice on stderr"
  done
  run_env "$w/beh:$w/tools" LIGHTS_OUT_OUT="$w/out-eng-env" LIGHTS_OUT_ENGINE_GATES=1 "$f" gates
  { [ "$rc" -eq 0 ] && grep -qF "$w/fake-podman.sock:/var/run/docker.sock" "$w/beh.run" && grep -qx 'LIGHTS_OUT_ENGINE_GATES=1' "$w/beh.run"; } \
    || echo "LIGHTS_OUT_ENGINE_GATES=1 gates did not opt into engine socket"
  run_env "$w/beh:$w/tools" CONTAINER_ENGINE=docker DOCKER_HOST="unix://$w/beh.sock" LIGHTS_OUT_OUT="$w/out-eng-docker" "$f" gates --engine
  { [ "$rc" -eq 0 ] && grep -qF "$w/beh.sock:/var/run/docker.sock" "$w/beh.run" && grep -qx -- '--group-add' "$w/beh.run"; } \
    || echo "docker 'gates --engine' did not mount DOCKER_HOST socket with --group-add"
  run_env "$w/beh:$w/tools" FAKE_REMOTE_EXISTS=false "$f" gates --engine
  { [ "$rc" -ne 0 ] && [ ! -s "$w/beh.run" ] && [ ! -s "$w/o" ] && [ "$(lines "$w/e")" = 1 ] && grep -qi 'socket' "$w/e"; } \
    || echo "missing podman socket on 'gates --engine' did not fail cleanly with 1 stderr line naming socket"
  for s in $check_state; do
    run "$w/beh:$w/tools" "$f" "$s"
    { [ "$rc" -eq 0 ] && [ -s "$w/beh.run" ]; } || { echo "state-only '$s' failed under fake engine: $(cat "$w/e")"; continue; }
    ! grep -qF '.sock' "$w/beh.run" || echo "state-only '$s' mounted a socket"
    ! grep -qE '^DOCKER_HOST' "$w/beh.run" || echo "state-only '$s' passed DOCKER_HOST"
    ! grep -qE '^LIGHTS_OUT_PLATFORM_DIR=' "$w/beh.run" || echo "state-only '$s' passed LIGHTS_OUT_PLATFORM_DIR"
    grep -qF ':/opt/lights-out/.crew' "$w/beh.run" || echo "state-only '$s' did not mount .crew"
  done
  for s in $check_plane; do
    run_env "$w/beh:$w/tools" LIGHTS_OUT_OUT="$w/out-$s" "$f" "$s"
    { [ "$rc" -eq 0 ] && [ -s "$w/beh.run" ]; } || { echo "plane '$s' failed under fake engine: $(cat "$w/e")"; continue; }
    grep -qF "$w/fake-podman.sock:/var/run/docker.sock" "$w/beh.run" || echo "plane '$s' did not mount the engine socket"
    grep -qx 'DOCKER_HOST=unix:///var/run/docker.sock' "$w/beh.run" || echo "plane '$s' did not pass DOCKER_HOST"
    grep -qE '^LIGHTS_OUT_PLATFORM_DIR=' "$w/beh.run" || echo "plane '$s' did not pass LIGHTS_OUT_PLATFORM_DIR"
    grep -qE '^LIGHTS_OUT_CREW_DIR=' "$w/beh.run" || echo "plane '$s' did not pass LIGHTS_OUT_CREW_DIR"
    grep -qx 'label=disable' "$w/beh.run" || echo "plane '$s' did not pass label=disable"
    [ "$(grep -ci 'socket' "$w/e")" = 1 ] || echo "plane '$s' did not print a 1-line opt-in socket notice on stderr"
  done
  run_env "$w/beh:$w/tools" LIGHTS_OUT_OUT="$w/out-console" "$f" console
  { [ "$rc" -eq 0 ] && grep -qx '127.0.0.1:8098:8098' "$w/beh.run"; } || echo "console did not publish 127.0.0.1:8098:8098"
  run_env "$w/beh:$w/tools" CONTAINER_ENGINE=docker DOCKER_HOST="unix://$w/beh.sock" LIGHTS_OUT_OUT="$w/out-docker" "$f" up
  { [ "$rc" -eq 0 ] && grep -qF "$w/beh.sock:/var/run/docker.sock" "$w/beh.run" && grep -qx -- '--group-add' "$w/beh.run"; } \
    || echo "docker plane 'up' did not mount DOCKER_HOST socket with --group-add"
  run_env "$w/beh:$w/tools" FAKE_REMOTE_EXISTS=false "$f" up
  { [ "$rc" -ne 0 ] && [ ! -s "$w/beh.run" ] && [ ! -s "$w/o" ] && [ "$(lines "$w/e")" = 1 ] && grep -qi 'socket' "$w/e"; } \
    || echo "missing podman socket on 'up' did not fail cleanly with 1 stderr line naming socket"
  run "$w/tools" "$f" version
  { [ "$rc" -eq 0 ] && grep -qF -- "${v:-?}" "$w/o"; } || echo "version does not work with no engine"
  cp "$w/o" "$w/ver.out"
  run "$w/tools" "$f" --version
  { [ "$rc" -eq 0 ] && [ ! -s "$w/e" ] && cmp -s "$w/o" "$w/ver.out"; } \
    || echo "--version does not match lights-out version with no engine"
  run "$w/trip:$w/tools" "$f" --version
  { [ "$rc" -eq 0 ] && [ ! -e "$w/tripped" ] && cmp -s "$w/o" "$w/ver.out"; } \
    || echo "--version touched the engine or failed"
  pv=$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb")).get("project", {}).get("version", ""))' "$1/pyproject.toml" 2>/dev/null || true)
  { [ -n "$pv" ] && [ "$v" = "$pv" ]; } \
    || echo "lights-out version=$v does not match pyproject.toml version=$pv"
  :
}

# The two reasons ADR 0064 §2 fixes; the Action matches on the first, exactly.
FIRST_RUN='the repository has no .lights-out/workload.env committed; lights-out init drafts one'
REMOVAL='the change removes .lights-out/workload.env, which its base has; a change cannot remove the contract that judges it'
# code FILE: the lines that are not comments.
code() { grep -Ev '^[[:space:]]*#' "$1"; }
# first_run_flaws ROOT: the reasons as text, one line each.
first_run_flaws() {
  for f in package/entrypoint.sh action.yml; do
    code "$1/$f" | grep -qF -- "$FIRST_RUN" \
      || echo "$f does not hold the first-run reason '$FIRST_RUN' outside the comments: the entrypoint prints it and the Action matches on it (ADR 0064 §4)"
  done
  code "$1/package/entrypoint.sh" | grep -qF -- "$REMOVAL" \
    || echo "package/entrypoint.sh does not hold the removal reason '$REMOVAL' outside the comments (ADR 0064 §2)"
  :
}

# doctor_flaws ROOT: ROOT/core/doctor.sh, run from the repository root with no engine on PATH.
doctor_flaws() {
  (cd "$root" && env -i PATH="$w/tools" HOME="$w" "$sh_bin" "$1/core/doctor.sh") >"$w/o" 2>"$w/e" && rc=0 || rc=$?
  { [ "$rc" -eq 1 ] && [ "$(lines "$w/o")" = 1 ] && grep -qE '^NO-GO: doctor — .*engine' "$w/o" && ! grep -q "$(printf '\033')" "$w/o"; } \
    || echo "core/doctor.sh with no engine: want exit 1 and the one line NO-GO: doctor — naming the engine, no ANSI; got exit $rc: $(cat "$w/o")"
  :
}

# dlevel PATH DOCTOR ARGS...: DOCTOR from the repository root with ARGS, PATH as given; stdout in $w/o, status in $rc.
dlevel() { dp=$1; dd=$2; shift 2; (cd "$root" && env -i PATH="$dp" HOME="$w" "$sh_bin" "$dd" "$@") >"$w/o" 2>"$w/e" && rc=0 || rc=$?; }
# shape: the last dlevel exited 1 with one stdout line and no ANSI.
shape() { [ "$rc" -eq 1 ] && [ "$(lines "$w/o")" = 1 ] && ! grep -q "$(printf '\033')" "$w/o"; }
# doctor_level_flaws ROOT: ROOT/core/doctor.sh --level, docs/adr/0057 §8; what is wrong, one line each.
doctor_level_flaws() {
  dr=$1/core/doctor.sh
  for lv in l2 l3; do
    dlevel "$w/tools" "$dr" --level "$lv"
    { shape && grep -q "^NO-GO: doctor — $lv lacks " "$w/o" && grep -q engine "$w/o" && grep -q toolchain "$w/o"; } \
      || echo "doctor --level $lv with no engine: want exit 1 and the one line NO-GO: doctor — $lv lacks <n>: <items> naming engine and toolchain, no ANSI; got exit $rc: $(cat "$w/o")"
  done
  for lv in l0 l1; do
    dlevel "$w/tools" "$dr" --level "$lv"
    { shape && grep -q "^NO-GO: doctor — $lv lacks " "$w/o" && grep -q engine "$w/o" && ! grep -q toolchain "$w/o"; } \
      || echo "doctor --level $lv with no engine: want exit 1 and the one line NO-GO: doctor — $lv lacks <n>: <items> naming the engine and not toolchain, no ANSI; got exit $rc: $(cat "$w/o")"
  done
  for a in "--level l9" "--frobnicate"; do
    dlevel "$w/tools" "$dr" $a
    { shape && grep -q '^NO-GO: doctor — ' "$w/o"; } \
      || echo "doctor $a with no engine: want exit 1 and the one line NO-GO: doctor —, no ANSI; got exit $rc: $(cat "$w/o")"
  done
  # With an engine that answers, only the argument can fail.
  dlevel "$w/fake:$w/tools" "$dr"
  { [ "$rc" -eq 0 ] && [ "$(lines "$w/o")" = 1 ] && grep -q '^GO: doctor' "$w/o"; } \
    || echo "doctor with a fake engine that answers: want the one line GO: doctor, exit 0; got exit $rc: $(cat "$w/o")"
  dlevel "$w/fake:$w/tools" "$dr" --level l0
  { [ "$rc" -eq 0 ] && [ "$(lines "$w/o")" = 1 ] && grep -q '^GO: doctor — l0 has what its profile names' "$w/o"; } \
    || echo "doctor --level l0 with a fake engine that answers: want the one line GO: doctor — l0 has what its profile names, exit 0; got exit $rc: $(cat "$w/o")"
  for a in "--level l9" "--frobnicate"; do
    dlevel "$w/fake:$w/tools" "$dr" $a
    { shape && grep -q '^NO-GO: doctor — ' "$w/o"; } \
      || echo "doctor $a with a fake engine that answers: want exit 1 and the one line NO-GO: doctor — (the argument is refused, not the engine); got exit $rc: $(cat "$w/o")"
  done
  :
}
doctor_all_flaws() { doctor_flaws "$1"; doctor_level_flaws "$1"; }

# A doctor that does what docs/adr/0057 §8 says of the exit and the lines, no more:
# the judge above must pass it, and fail on a copy with `mut` set to each defect.
cat > "$w/doctor.ok" <<'SH'
#!/bin/sh
mut=NONE
lv=""
while [ $# -gt 0 ]; do
  case "$1" in
    --level) [ $# -ge 2 ] || { echo "NO-GO: doctor — --level needs a level"; exit 1; }; lv=$2; shift 2 ;;
    *) [ "$mut" = ignores-unknown ] && { shift; continue; }; echo "NO-GO: doctor — $1 is not an argument"; exit 1 ;;
  esac
done
bad=""
add() { bad="$bad${bad:+, }$1"; }
command -v podman >/dev/null 2>&1 || command -v docker >/dev/null 2>&1 || [ "$mut" = ignores-engine ] || add engine
case "$lv" in
  "" | l0 | l1) [ "$mut" = toolchain-at-l0 ] && add toolchain ;;
  l2 | l3)
    [ "$mut" = no-toolchain-item ] || add toolchain
    [ "$mut" = l2-is-go ] && bad="" ;;
  *) [ "$mut" = accepts-l9 ] || { echo "NO-GO: doctor — $lv has no profile"; exit 1; } ;;
esac
tail=""
[ "$mut" = ansi ] && tail=$(printf '\033[0m')
if [ -n "$bad" ]; then
  if [ -z "$lv" ]; then
    echo "NO-GO: doctor — $bad"
  else
    word=lacks
    [ "$mut" = wrong-words ] && word=missing
    echo "NO-GO: doctor — $lv $word $(echo "$bad" | awk -F', ' '{ print NF }'): $bad$tail"
  fi
  [ "$mut" = nogo-exit0 ] && exit 0
  [ "$mut" = two-lines ] && echo "and more"
  exit 1
fi
if [ -z "$lv" ]; then echo "GO: doctor"
elif [ "$mut" = other-go-line ]; then echo "GO: doctor — $lv is fine"
else echo "GO: doctor — $lv has what its profile names"; fi
SH
mkdir -p "$w/ok/core"
cp "$w/doctor.ok" "$w/ok/core/doctor.sh"

cat > "$w/chk.py" <<'PY'
import copy, glob, json, os, re, sys, yaml

root, mode = sys.argv[1], sys.argv[2]
PKG, ACT, OLD = ".github/workflows/package.yml", "action.yml", "action/action.yml"
SHIMS = ("actions/test-proof", "actions/test-integrity")
S = "github.server_url == 'https://github.com'"
TAG, DISP = r"startsWith\(github\.ref, 'refs/tags/v'\)", r"github\.event_name == 'workflow_dispatch'"
PUB = re.compile(re.escape(S) + r" && \((%s \|\| %s|%s \|\| %s)\)" % (TAG, DISP, DISP, TAG))
UP = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"  # v7.0.2, ADR 0064
AP = r"(\$GITHUB_ACTION_PATH|\$\{GITHUB_ACTION_PATH\}|\$\{\{\s*github\.action_path\s*\}\})"
names = glob.glob(".github/workflows/*.y*ml", root_dir=root) + [ACT, OLD] + [s + "/action.yml" for s in SHIMS]
files = [s + "/README.md" for s in SHIMS]
m = re.search(r"^version=(.*)$", open(os.path.join(root, "lights-out")).read(), re.M)
d0 = {
    "yaml": {n: yaml.safe_load(open(os.path.join(root, n))) for n in names if os.path.isfile(os.path.join(root, n))},
    "version": m.group(1) if m else "",
    "files": {f for f in files if os.path.isfile(os.path.join(root, f))},
}
Y = lambda d: d["yaml"]


def uses(j):
    return any(str(s.get("uses", "")).strip() == "./" for s in j.get("steps") or [])


def stale(j):
    return any(str(s.get("uses", "")).strip() == "./action" or str(s.get("uses", "")).startswith("./action/") for s in j.get("steps") or [])


def perm(p, k):
    return p == "write-all" or (isinstance(p, dict) and p.get(k) == "write")


def passes(v, i):
    return re.fullmatch(r"\$\{\{\s*inputs\.%s\s*\}\}" % re.escape(i), str(v).strip()) is not None


def flaws(d):
    y = Y(d)
    a = y.get(ACT)
    root_inputs = (a or {}).get("inputs") or {} if isinstance(a, dict) else {}
    if OLD in y:
        yield "action/action.yml exists: the Action is action.yml at the repository root, and action/ is removed (ADR 0057 §6)"
    if not isinstance(a, dict) or (a.get("runs") or {}).get("using") != "composite":
        yield "action.yml (repository root) is missing or is not a composite action"
    else:
        steps = a["runs"].get("steps") or []
        run = "\n".join(str(s.get("run", "")) for s in steps)
        if re.search(AP + r"/\.\./", run):
            yield "action.yml reaches $GITHUB_ACTION_PATH/../: the wrapper is at the Action's own root (ADR 0057 §6)"
        elif not re.search(AP + r"/lights-out\"?(\s|$)", run):
            yield "action.yml does not run $GITHUB_ACTION_PATH/lights-out"
        if not re.search(r"inputs\.gates|inputs\['gates'\]", json.dumps(steps)):
            yield "action.yml does not choose the subcommand from its gates input"
        g = root_inputs.get("gates")
        if not isinstance(g, dict) or str(g.get("default")) != "all":
            yield "action.yml has no input gates whose default is all"
        if "summary.md" not in json.dumps(steps):
            yield "action.yml does not post the summary.md the image writes (ADR 0057 §6)"
        for p in re.findall(r"(?:GITHUB_API_URL\}?|api\.github\.com)(/[^\s\"'?]*)", run) + re.findall(r"\bgh\s+api\s+(\S+)", run):
            if not p.endswith("check-runs"):
                yield "action.yml calls %s, a permission beyond checks: write" % p
        # ADR 0064 §4: the draft is uploaded at one pin, hidden files in, replacing a re-run's.
        ups = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
        if not ups:
            yield "action.yml does not upload the draft with %s (ADR 0064 §4)" % UP
        for s in ups:
            w = s.get("with") or {}
            if str(w.get("include-hidden-files")).lower() != "true":
                yield "action.yml: the upload-artifact step lacks include-hidden-files: true; the draft is .lights-out/, hidden (ADR 0064 §4)"
            if str(w.get("overwrite")).lower() != "true":
                yield "action.yml: the upload-artifact step lacks overwrite: true; a re-run redrafts (ADR 0064 §4)"
    # No other ref of upload-artifact in the Action or its shims (ci.yml's own uploads are the repository's, not the Action's).
    for path, doc in y.items():
        if path.startswith(".github/workflows/"):
            continue
        for ref in re.findall(r"actions/upload-artifact@([^\s\"']+)", json.dumps(doc)):
            if ref != UP.split("@")[1]:
                yield "%s uses actions/upload-artifact@%s, want %s (ADR 0064 §4)" % (path, ref, UP)
    for path, doc in y.items():
        if not path.startswith(".github/workflows/"):
            continue
        for n, j in (doc.get("jobs") or {}).items():
            p = j.get("permissions", doc.get("permissions"))
            for k in ("packages", "id-token"):
                # ADR 0075 §2: the pypi job holds id-token: write too, for trusted publishing; never packages: write.
                # ADR 0128 §2: cloud-conformance.yml's conformance job holds id-token: write for keyless OIDC; never packages: write.
                if perm(p, k) and not (
                    (path == PKG and (n == "publish" or (n == "pypi" and k == "id-token")))
                    or (path == ".github/workflows/cloud-conformance.yml" and n == "conformance" and k == "id-token")
                ):
                    # The one other id-token: write is a GitHub Pages deploy (pages: write, site.yml), which signs nothing.
                    if k == "id-token" and path != PKG and isinstance(p, dict) and p.get("pages") == "write":
                        continue
                    yield "%s: job %s holds %s: write; only package.yml's publish may" % (path, n, k)
            if stale(j):
                yield "%s: job %s uses ./action, which is gone: the Action is ./ (ADR 0057 §6)" % (path, n)
            if uses(j) and not (isinstance(p, dict) and all(v == "none" or (k, v) in (("contents", "read"), ("checks", "write")) for k, v in p.items())):
                yield "%s: job %s uses ./ with permissions %s, want at most contents: read and checks: write" % (path, n, p)
    jobs = (y.get(PKG) or {}).get("jobs") or {}
    if "publish" not in jobs:
        yield "package.yml has no publish job"
    for n, j in jobs.items():
        c = re.sub(r"^\$\{\{(.*)\}\}$", r"\1", str(j.get("if", "")).strip()).strip()
        if n == "publish":
            if not PUB.fullmatch(c):
                yield "package.yml: job publish: if: %r is not the github.com guard and a v* tag or workflow_dispatch" % c
            if not perm(j.get("permissions"), "packages"):
                yield "package.yml: job publish has no packages: write"
            if not perm(j.get("permissions"), "id-token"):
                yield "package.yml: job publish has no id-token: write"
            pr = "\n".join(str(s.get("run", "")) for s in j.get("steps") or [])
            if not re.search(r"\bcosign\s+sign\b", pr):
                yield "package.yml: job publish does not run cosign sign (ADR 0057 §4)"
            if re.search(r"(^|\s)--key\b", pr, re.M) or re.search(r"COSIGN_PRIVATE_KEY|COSIGN_PASSWORD", json.dumps(j)):
                yield "package.yml: job publish signs with a key; the signing is keyless (ADR 0057 §4)"
        elif not re.fullmatch(re.escape(S) + r"( && [^|]*)?", c):
            yield "package.yml: job %s: if: %r is not the github.com guard (ADR 0039)" % (n, c)
    for s in SHIMS:
        base, p = os.path.basename(s), s + "/action.yml"
        sh = y.get(p)
        if not isinstance(sh, dict):
            yield "%s is missing" % p
            continue
        if (sh.get("runs") or {}).get("using") != "composite":
            yield "%s is not a composite action" % p
        if s + "/README.md" not in d["files"]:
            yield "%s/README.md is missing" % s
        for k in ("name", "description"):
            if not str(sh.get(k) or "").strip():
                yield "%s has no %s" % (p, k)
        b = sh.get("branding")
        if not (isinstance(b, dict) and b.get("icon") and b.get("color")):
            yield "%s has no branding (icon and color)" % p
        st = (sh.get("runs") or {}).get("steps") or []
        if len(st) != 1:
            yield "%s: want exactly one step, it has %d" % (p, len(st))
        else:
            want = "nicolaguglielmi/lights-out@v" + d["version"]
            if str(st[0].get("uses", "")) != want:
                yield "%s: the step uses %r, want %r (v + the wrapper's version=)" % (p, st[0].get("uses"), want)
            w = st[0].get("with") or {}
            if str(w.get("gates")) != base:
                yield "%s: the step's with: gates is %r, want %r" % (p, w.get("gates"), base)
            for i in sh.get("inputs") or {}:
                if not passes(w.get(i, ""), i):
                    yield "%s: input %s is not passed through as ${{ inputs.%s }}" % (p, i, i)
            for i in w:
                if i not in root_inputs:
                    yield "%s: the step passes %s, which is not an input of the root action.yml" % (p, i)
        for i in sh.get("inputs") or {}:
            if i not in root_inputs:
                yield "%s: input %s is not an input of the root action.yml" % (p, i)


J = lambda d: Y(d)[PKG]["jobs"]
A = lambda d: next(j for j in J(d).values() if uses(j))
Q = lambda d: next(j for n, j in J(d).items() if n != "publish" and not uses(j))
SP, ST = SHIMS[0] + "/action.yml", SHIMS[1] + "/action.yml"


def other(d, **perms):
    Y(d)[".github/workflows/o.yml"] = {"jobs": {"x": {"permissions": perms, "steps": []}}}


def no_perms(d):
    A(d).pop("permissions")
    Y(d)[PKG].pop("permissions", None)


def runs(d, a, b):
    for s in Y(d)[ACT]["runs"]["steps"]:
        s["run"] = str(s.get("run", "")).replace(a, b)


def text(d, p, a, b):
    Y(d)[p] = json.loads(json.dumps(Y(d)[p]).replace(a, b))


def signs_with_key(d):
    for s in J(d)["publish"]["steps"]:
        s["run"] = str(s.get("run", "")).replace("cosign sign", "cosign sign --key cosign.key")


def upload_step(d):
    return next(s for s in Y(d)[ACT]["runs"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@"))


def declare(d, p, name="zz", root_too=False, pass_as="${{ inputs.%s }}"):
    Y(d)[p].setdefault("inputs", {})[name] = {"description": "x", "required": False, "default": ""}
    if root_too:
        Y(d)[ACT].setdefault("inputs", {})[name] = {"description": "x", "required": False, "default": ""}
    if pass_as:
        Y(d)[p]["runs"]["steps"][0].setdefault("with", {})[name] = pass_as % name if "%s" in pass_as else pass_as


CASES = [
    ("a job that uses ./ holds packages: write", lambda d: A(d)["permissions"].update(packages="write"), "uses ./ with"),
    ("a job that uses ./ sets no permissions", no_perms, "uses ./ with"),
    ("a job that uses ./ holds contents: write", lambda d: A(d)["permissions"].update(contents="write"), "uses ./ with"),
    ("a job still uses ./action", lambda d: A(d)["steps"].append({"uses": "./action"}), "which is gone"),
    ("another job holds packages: write", lambda d: Q(d).update(permissions={"packages": "write"}), "holds packages: write; only package.yml's publish"),
    ("another workflow holds packages: write", lambda d: other(d, packages="write"), "holds packages: write; only package.yml's publish"),
    ("another job holds id-token: write", lambda d: Q(d).update(permissions={"id-token": "write"}), "holds id-token: write; only package.yml's publish"),
    ("another workflow holds id-token: write", lambda d: other(d, **{"id-token": "write"}), "holds id-token: write; only package.yml's publish"),
    ("a package.yml job holds pages: write and id-token: write", lambda d: Q(d).update(permissions={"pages": "write", "id-token": "write"}), "holds id-token: write; only package.yml's publish"),
    ("another workflow's job holds id-token: write and nothing for Pages", lambda d: other(d, **{"id-token": "write", "contents": "read"}), "holds id-token: write; only package.yml's publish"),
    ("pypi holds packages: write", lambda d: J(d).setdefault("pypi", {"if": S, "steps": []}).update(permissions={"id-token": "write", "packages": "write"}), "job pypi holds packages: write; only package.yml's publish"),
    ("a job named pypi in another workflow holds id-token: write", lambda d: Y(d).update({".github/workflows/o.yml": {"jobs": {"pypi": {"permissions": {"id-token": "write"}, "steps": []}}}}), "job pypi holds id-token: write; only package.yml's publish"),
    ("a workflow holds write-all", lambda d: Y(d)[PKG].update(permissions="write-all"), "only package.yml's publish"),
    ("publish runs on every push", lambda d: J(d)["publish"].update({"if": S}), "job publish: if"),
    ("publish: && and || ungrouped", lambda d: J(d)["publish"].update({"if": S + " && startsWith(github.ref, 'refs/tags/v') || github.event_name == 'workflow_dispatch'"}), "job publish: if"),
    ("publish without packages: write", lambda d: J(d)["publish"]["permissions"].pop("packages"), "no packages: write"),
    ("publish without id-token: write", lambda d: J(d)["publish"]["permissions"].pop("id-token"), "no id-token: write"),
    ("publish does not run cosign sign", lambda d: text(d, PKG, "cosign sign", "cosign verify"), "does not run cosign sign"),
    ("publish signs with a key", signs_with_key, "keyless"),
    ("a job without the github.com guard", lambda d: Q(d).pop("if"), "ADR 0039"),
    ("a job whose guard has an ||", lambda d: Q(d).update({"if": S + " || github.event_name == 'push'"}), "ADR 0039"),
    ("action.yml is not composite", lambda d: Y(d)[ACT]["runs"].update(using="node20"), "composite"),
    ("action.yml is gone", lambda d: Y(d).pop(ACT), "missing or is not a composite"),
    ("action/action.yml is back", lambda d: Y(d).update({OLD: {"runs": {"using": "composite"}}}), "action/action.yml exists"),
    ("action.yml reaches ../lights-out", lambda d: runs(d, "/lights-out", "/../lights-out"), "reaches $GITHUB_ACTION_PATH/../"),
    ("action.yml does not run its own lights-out", lambda d: runs(d, "/lights-out", "/x"), "does not run $GITHUB_ACTION_PATH/lights-out"),
    ("action.yml ignores its gates input", lambda d: text(d, ACT, "inputs.gates", "inputs.base"), "does not choose the subcommand"),
    ("action.yml has no gates input", lambda d: Y(d)[ACT]["inputs"].pop("gates"), "default is all"),
    ("action.yml's gates defaults to test-proof", lambda d: Y(d)[ACT]["inputs"]["gates"].update(default="test-proof"), "default is all"),
    ("action.yml does not post summary.md", lambda d: text(d, ACT, "summary.md", "details.txt"), "summary.md"),
    ("action.yml calls another REST path", lambda d: runs(d, "check-runs", "issues/1/comments"), "beyond"),
    ("the upload is a different ref", lambda d: upload_step(d).update(uses="actions/upload-artifact@v4"), "want actions/upload-artifact@cf430e0"),
    ("the upload is a tag of the same action", lambda d: upload_step(d).update(uses=UP.replace("cf430e030ddbb5b0abf93d22962f4752f3646cd9", "v7")), "want actions/upload-artifact@cf430e0"),
    ("a shim uploads at another ref", lambda d: Y(d)[SP]["runs"]["steps"].append({"uses": "actions/upload-artifact@v4"}), "actions/test-proof/action.yml uses actions/upload-artifact@v4"),
    ("the upload skips hidden files", lambda d: upload_step(d)["with"].pop("include-hidden-files"), "include-hidden-files"),
    ("the upload sets include-hidden-files: false", lambda d: upload_step(d)["with"].update({"include-hidden-files": False}), "include-hidden-files"),
    ("the upload does not overwrite", lambda d: upload_step(d)["with"].pop("overwrite"), "overwrite"),
    ("action.yml uploads nothing", lambda d: Y(d)[ACT]["runs"].update(steps=[s for s in Y(d)[ACT]["runs"]["steps"] if not str(s.get("uses", "")).startswith("actions/upload-artifact@")]), "does not upload the draft"),
    ("a job that uses ./ holds actions: read", lambda d: A(d)["permissions"].update(actions="read"), "uses ./ with"),
    ("a job that uses ./ holds pull-requests: write", lambda d: A(d)["permissions"].update({"pull-requests": "write"}), "uses ./ with"),
    ("action.yml calls the artifacts API", lambda d: Y(d)[ACT]["runs"]["steps"].append({"shell": "sh", "run": "gh api repos/o/r/actions/artifacts"}), "beyond"),
    ("test-proof's action.yml is gone", lambda d: Y(d).pop(SP), "actions/test-proof/action.yml is missing"),
    ("test-integrity's action.yml is gone", lambda d: Y(d).pop(ST), "actions/test-integrity/action.yml is missing"),
    ("test-proof is not composite", lambda d: Y(d)[SP]["runs"].update(using="node20"), "not a composite"),
    ("test-proof has no README", lambda d: d["files"].discard(SHIMS[0] + "/README.md"), "actions/test-proof/README.md is missing"),
    ("test-integrity has no README", lambda d: d["files"].discard(SHIMS[1] + "/README.md"), "actions/test-integrity/README.md is missing"),
    ("test-proof has no name", lambda d: Y(d)[SP].pop("name"), "has no name"),
    ("test-integrity has no description", lambda d: Y(d)[ST].pop("description"), "has no description"),
    ("test-proof has no branding", lambda d: Y(d)[SP].pop("branding"), "has no branding"),
    ("test-integrity has a branding with no color", lambda d: Y(d)[ST]["branding"].pop("color"), "has no branding"),
    ("test-proof has two steps", lambda d: Y(d)[SP]["runs"]["steps"].append({"run": "true", "shell": "sh"}), "exactly one step"),
    ("test-integrity has no step", lambda d: Y(d)[ST]["runs"].update(steps=[]), "exactly one step"),
    ("test-proof uses another version", lambda d: Y(d)[SP]["runs"]["steps"][0].update(uses="nicolaguglielmi/lights-out@v0.0.1"), "want 'nicolaguglielmi/lights-out@v"),
    ("test-integrity uses a branch", lambda d: Y(d)[ST]["runs"]["steps"][0].update(uses="nicolaguglielmi/lights-out@main"), "want 'nicolaguglielmi/lights-out@v"),
    ("test-proof uses the old path", lambda d: Y(d)[SP]["runs"]["steps"][0].update(uses="nicolaguglielmi/lights-out/action@v" + d["version"]), "want 'nicolaguglielmi/lights-out@v"),
    ("the wrapper's version moved on, the shims did not", lambda d: d.update(version="9.9.9"), "want 'nicolaguglielmi/lights-out@v9.9.9'"),
    ("test-proof passes gates: all", lambda d: Y(d)[SP]["runs"]["steps"][0]["with"].update(gates="all"), "with: gates is 'all'"),
    ("test-integrity passes test-proof's gates", lambda d: Y(d)[ST]["runs"]["steps"][0]["with"].update(gates="test-proof"), "with: gates is 'test-proof'"),
    ("test-proof declares an input it does not pass", lambda d: declare(d, SP, root_too=True, pass_as=None), "input zz is not passed through"),
    ("test-integrity passes an input as a literal", lambda d: declare(d, ST, root_too=True, pass_as="literal"), "input zz is not passed through"),
    ("test-proof passes another input's value", lambda d: declare(d, SP, root_too=True, pass_as="${{ inputs.base }}"), "input zz is not passed through"),
    ("test-proof declares an input the root has not", lambda d: declare(d, SP), "is not an input of the root action.yml"),
    ("test-integrity passes an input it did not declare", lambda d: Y(d)[ST]["runs"]["steps"][0]["with"].update(qq="${{ inputs.qq }}"), "the step passes qq"),
]
if mode == "check":
    print("\n".join(flaws(d0)))
else:
    for desc, mutate, want in CASES:
        d = copy.deepcopy(d0)
        mutate(d)
        got = "\n".join(flaws(d))
        if want not in got:
            print("it cannot fail: %s -> [%s]" % (desc, got[:160]))
            break
PY

# judge WHAT FLAWS: the first flaw is the one line, all of them go to stderr.
judge() { [ -z "$2" ] || nogo "$1: $(printf '%s\n' "$2" | head -n 1)" "$2"; }
judge wrapper "$(wrapper_flaws "$root" full)"
judge doctor "$(doctor_all_flaws "$root")"
judge first-run "$(first_run_flaws "$root")"
judge yaml "$(python3 -I "$w/chk.py" "$root" check)"

# --- the checks must be able to fail ----------------------------------------------
# try JUDGE WANT EDIT: JUDGE on a copy of the real files with EDIT applied names WANT.
try() {
  rm -rf "$w/m"
  mkdir -p "$w/m/package" "$w/m/core"
  cp lights-out pyproject.toml "$w/m/"
  cp package/entrypoint.sh "$w/m/package/"
  cp action.yml "$w/m/"
  cp core/doctor.sh "$w/m/core/"
  (cd "$w/m" && eval "$3")
  got=$($1 "$w/m")
  case "$got" in
    *"$2"*) ;;
    "") nogo "ci/package.sh cannot fail: it accepted: $3" ;;
    *) nogo "ci/package.sh named the wrong problem for: $3" "wanted: $2" "got: $got" ;;
  esac
}
try wrapper_flaws "--env-file" 'sed -i "1a : --env-file .env" lights-out'
try wrapper_flaws ":z" 'sed -i "1a : -v x:/repo:ro,z" lights-out'
try wrapper_flaws "not an executable" 'chmod -x lights-out'
try wrapper_flaws "not an executable" 'sed -i "1s|.*|#!/bin/bash|" lights-out'
try wrapper_flaws "shellcheck" 'sed -i "1a [[ -n x ]] || :" lights-out'
try wrapper_flaws "semver" 'sed -i "s/^version=.*/version=1.2/" lights-out'
try wrapper_flaws "semver" 'sed -i "/^version=/p" lights-out'
try wrapper_flaws "unknown subcommand is not refused" 'sed -i "s/^  \*) die .unknown command.*/  *) ;;/" lights-out'
try wrapper_flaws "is not refused before any engine, naming" 'sed -i "s/^      BASE=\*|/      *|BASE=*|/" lights-out'
try wrapper_flaws "is not refused before any engine, naming" 'sed -i "s/one of \$all_keys/bad args/" lights-out'
try wrapper_flaws "unknown subcommand is not refused" 'sed -i "1a podman info" lights-out'
try wrapper_flaws "with no engine" 'sed -i "s/^  die .no container engine found.*/  echo no engine found; exit 1/" lights-out'
try wrapper_flaws "with no engine" 'sed -i "s/^  die .no container engine found.*/  echo no engine found >\&2; exit 0/" lights-out'
try wrapper_flaws "version does not work" 'sed -i "s/= version \]/= nonesuch ]/" lights-out'
try wrapper_flaws "allowlists differ" 'sed -i "s/INCIDENT=\*|//" lights-out'
try wrapper_flaws "allowlists differ" 'printf "case x in FOO=*) ;; esac\n" >> package/entrypoint.sh'
# Subcommand presence & socket-boundary planted defects (ADR 0057 §5, ADR 0105):
for s in test-proof test-integrity next up; do
  try wrapper_flaws "naming all 24" "sed -i \"/^[[:space:]]*#/!s/\\\\b$s\\\\b/${s}x/g\" lights-out"
done
for s in test-proof test-integrity; do
  try wrapper_flaws "'$s' with no engine" "sed -i \"/^[[:space:]]*#/!s/$s/${s}x/g\" lights-out"
done
try wrapper_flaws "'test-proof NOPE=1' is not refused" 'sed -i "s/^      BASE=\*|/      *|BASE=*|/" lights-out'
try wrapper_flaws "'test-integrity BASEX=1' is not refused" 'sed -i "s/^      BASE=\*|/      *|BASE=*|/" lights-out'
try wrapper_flaws "test-proof refuses BASE= HEAD=" 'sed -i "s/^      BASE=\*|//" lights-out'
try wrapper_flaws "test-integrity refuses BASE= HEAD=" 'sed -i "s/^      BASE=\*|//" lights-out'
try wrapper_flaws "read-only 'gates' mounted a socket" 'sed -i "s|set -- \"\$@\" -v \"\$here:/repo:ro\" -v \"\$out:/out\"|set -- \"\$@\" -v \"\$here:/repo:ro\" -v \"\$out:/out\" -v /tmp/leak.sock:/var/run/docker.sock|" lights-out'
try wrapper_flaws "state-only 'next' passed DOCKER_HOST" 'sed -i "s|set -- \"\$@\" -v \"\$here:/repo:ro\" -v \"\$out/crew:/opt/lights-out/.crew\"|set -- \"\$@\" -v \"\$here:/repo:ro\" -v \"\$out/crew:/opt/lights-out/.crew\" -e DOCKER_HOST=unix:///tmp/leak.sock|" lights-out'
try wrapper_flaws "plane 'up' did not mount the engine socket" 'sed -i "s|-v \"\$sock:/var/run/docker.sock\"||" lights-out'
try wrapper_flaws "plane 'up' did not pass DOCKER_HOST" 'sed -i "s|-e DOCKER_HOST=unix:///var/run/docker.sock||" lights-out'
try wrapper_flaws "plane 'up' did not pass LIGHTS_OUT_PLATFORM_DIR" 'sed -i "s|-e LIGHTS_OUT_PLATFORM_DIR=\"\$out/platform\"||" lights-out'
try wrapper_flaws "plane 'up' did not print a 1-line opt-in socket notice" 'sed -i "/mounting container engine socket/d" lights-out'
try wrapper_flaws "engine-gates 'gates --engine' did not mount the engine socket" 'sed -i "s/\[ \"\$a\" != --engine \] || engine_gates=1/:/" lights-out'
try wrapper_flaws "engine-gates 'gates --engine' did not pass LIGHTS_OUT_ENGINE_GATES=1" 'sed -i "s/-e LIGHTS_OUT_ENGINE_GATES=1//" lights-out'
try wrapper_flaws "'up --engine' is not refused before any engine" 'sed -i "s/init|gates|test-proof|test-integrity) ;; \*) die/init|gates|test-proof|test-integrity|up) ;; *) die/" lights-out'
try wrapper_flaws "missing podman socket on 'gates --engine' did not fail cleanly" 'sed -i "s/\[ \"\$tier\" = plane \] || \[ \"\$tier\" = engine-gates \]/[ \"\$tier\" = plane ]/" lights-out'
try wrapper_flaws "console did not publish" 'sed -i "s|-p \"\$publish:\$cport:8098\"||" lights-out'
try wrapper_flaws "--version does not match lights-out version" 'sed -i "s/--version)/--nonesuch)/" lights-out'
try wrapper_flaws "--version does not match lights-out version" 'sed -i "s/--version) cmd=version/--version) printf \"lights-out %s (other)\\n\" \"\$version\"; exit 0/" lights-out'
try wrapper_flaws "--version touched the engine" 'sed -i "s/--version)/--version) podman info;/" lights-out'
try wrapper_flaws "does not match pyproject.toml version" 'sed -i "s/^version = .*/version = \"9.9.9\"/" pyproject.toml'
try doctor_flaws "want exit 1" 'sed -i "/^\. ci\/lib.sh/a echo second" core/doctor.sh'
# The reasons as text (ADR 0064 §2, §4): either copy changed by a word, or only a comment.
try first_run_flaws "package/entrypoint.sh does not hold the first-run reason" 'sed -i "s/lights-out init drafts one/lights-out init drafts it/" package/entrypoint.sh'
try first_run_flaws "action.yml does not hold the first-run reason" 'sed -i "s/lights-out init drafts one/lights-out init drafts it/" action.yml'
try first_run_flaws "package/entrypoint.sh does not hold the first-run reason" 'sed -i "/lights-out init drafts one/s/^/# /" package/entrypoint.sh'
try first_run_flaws "action.yml does not hold the first-run reason" 'sed -i "/lights-out init drafts one/s/^/# /" action.yml'
try first_run_flaws "does not hold the removal reason" 'sed -i "s/a change cannot remove the contract that judges it/a change cannot remove it/" package/entrypoint.sh'
try first_run_flaws "does not hold the removal reason" 'sed -i "/a change cannot remove the contract/s/^/# /" package/entrypoint.sh'
try doctor_flaws "want exit 1" 'printf "#!/bin/sh\necho NO-GO: doctor — key\nexit 1\n" > core/doctor.sh'
try doctor_flaws "want exit 1" 'printf "#!/bin/sh\necho GO: doctor\nexit 0\n" > core/doctor.sh'
try doctor_flaws "want exit 1" 'printf "#!/bin/sh\nprintf \"\\033[31mNO-GO: doctor — engine\\033[0m\\n\"\nexit 1\n" > core/doctor.sh'
try doctor_flaws "want exit 1" 'printf "#!/bin/sh\necho NO-GO: doctor — engine\nexit 0\n" > core/doctor.sh'
# --level (ADR 0057 §8), on the stand-in doctor: it passes, and each defect is found.
got=$(doctor_level_flaws "$w/ok")
[ -z "$got" ] || nogo "ci/package.sh refuses a doctor that does what ADR 0057 §8 says (its own stand-in)" "$got"
dmut() { try doctor_level_flaws "$1" "cp \"\$w/doctor.ok\" core/doctor.sh && sed -i \"s/^mut=NONE/mut=$2/\" core/doctor.sh"; }
for m in ansi nogo-exit0 two-lines wrong-words ignores-engine no-toolchain-item l2-is-go; do dmut "doctor --level l2 with no engine" "$m"; done
dmut "doctor --level l0 with no engine" toolchain-at-l0
dmut "doctor --level l0 with no engine" ignores-engine
dmut "with a fake engine that answers: want exit 1" accepts-l9
dmut "with a fake engine that answers: want exit 1" ignores-unknown
dmut "doctor --level l0 with a fake engine" other-go-line
st=$(python3 -I "$w/chk.py" "$root" selftest) || nogo "the YAML self-test crashed; see stderr"
[ -z "$st" ] || nogo "ci/package.sh $st"

go
