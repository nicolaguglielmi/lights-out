#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# lights-out entrypoint. The only things the host needs are a container
# engine, git and this shell. Everything else runs inside the workbench image
# built from workbench/Containerfile (docs/adr/0004-containerised-toolchain.md).
#
#   ./go help           list the make targets
#   ./go ci             repo self-checks, no engine socket inside
#   ./go up             start the L0 plane in compose and wait for it
#   ./go smoke          run the workload's happy path through Envoy
#   ./go demo           the demo, as far as the current milestone takes it
#   ./go demo-rollback  release a failing canary, prove it rolls back on its own
#   ./go demo-hold      same release, kill switch on: prove verify GO holds the canary at 25%
#   ./go demo-gates     prove every PR gate GO and NO-GO on the committed fixtures
#   ./go demo-seam      prove attach and the Workload port on workloads/minimal as its own repository
#   ./go attach <path>  make a repository of yours the workload (its .lights-out/ is the contract, if it has one)
#   ./go init [<path>]  draft the attached repository's .lights-out/ in the clone, baseline measured (ADR 0042)
#   ./go gates          judge a range of the workload: BASE=<ref> HEAD=<ref>, default the last commit; no plane needed
#   ./go demo-init      prove attach, init and gates on the two example repositories, without the plane
#   ./go detach         drop the attached repository; the workload is app/ again
#   ./go demo-network   prove a network of members on one ticket, governed end to end (ADR 0036)
#   ./go e2e            run the full tower: ticket → PR → CI → merge → canary → verify NO-GO → rollback, audited
#   ./go audit          rebuild the chain from a ticket's state: TICKET=<id>
#   ./go resume         request again the step a ticket failed, history kept: TICKET=<id>
#   ./go demo-rework    prove a NO-GO goes back to the worker with its verdict, then GO and merged
#   ./go assign         turn a ticket into a PR: TICKET=<id>
#   ./go demo-crew      prove ticket 0001 becomes a PR that passes the M4 gates
#   ./go record         run a ticket for real at L1 and save the fixture: TICKET=<id>
#   ./go triage         run the ops-triage role on an alert: ALERT=<file>
#   ./go demo-triage    prove a fake alert becomes an incident with ranked hypotheses and a proposal
#   ./go incident       build an incident document from the release record: NAME= FROM= TO= [LOGS=] [TICKET=]
#   ./go postmortem     run the ops-triage role on an incident document: INCIDENT=<file>, one ticket per action item
#   ./go demo-postmortem prove the incident captured from demo-rollback becomes a post mortem and a fix ticket, no plane
#   ./go board          the pipeline explained, live when the plane is up, on localhost:8099
#   ./go console        the client's console on localhost:8098: the record, and go-for-launch from a browser (ADR 0047)
#   ./go logbook        write the logbook, the one versioned record the console reads: [LOGBOOK_FILE=<file>]
#   ./go demo-console   prove go-for-launch pressed from the console: held canary, POST GO → 100%, then the un-promote
#   ./go promote        go-for-launch: the brief, the human answer, then 100% or rollback
#   ./go demo-launch    prove go-for-launch: held canary, brief, GO → 100%, then the un-promote
#   ./go record-evals   record the ADK roles' responses at L1 (PROFILE=l1)
#   ./go record-postmortem  record the ops-triage role's post mortem of fixtures/incidents/ at L1 (PROFILE=l1)
#   ./go harness-conformance  every CLI harness against a scripted model, no key: [CONFORMANCE_HARNESS=<name>] (ADR 0041)
#   ./go codehost-conformance  ten cases on the CODE_HOST set: Gitea with the plane up, or CODE_HOST=github on an attached repository (ADR 0043)
#   ./go engine-compat  host-side engine gate: versions, then up and smoke
#   ./go doctor         engine, versions, ports, the key: one line each, then GO or NO-GO (host-side)
#   ./go package        build the lights-out image, localhost/lights-out:dev, for the lights-out wrapper (host-side)
#   ./go demo-package   prove the wrapper: init and gates from the image on workloads/minimal as its own repository
#   ./go shell          interactive shell inside workbench
#   ./go clean          remove the workbench image
#
# Any other argument is passed to make inside workbench.
set -eu

# Inside workbench there is nothing to wrap: run make directly.
if [ "${LIGHTS_OUT_WORKBENCH:-}" = "1" ]; then
  exec make "$@"
fi

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
# Host-side scripts (ci/engine-compat.sh) source ci/lib.sh by relative path:
# run from the repository root wherever ./go was called from.
cd "$here" || exit 1
containerfile="$here/workbench/Containerfile"
# One tag per revision of the image's inputs: the Containerfile and the
# Python lock it installs (docs/adr/0020 §5).
image="localhost/lights-out-workbench:$(cat "$containerfile" "$here/workbench/pyproject.toml" "$here/workbench/uv.lock" | cksum | cut -d' ' -f1)"
cmd="${1:-help}"
[ $# -gt 0 ] && shift

# Captured here, before engine-compat, where demo's cold pulls happen, so
# the elapsed-time verdict on demo (docs/adr/0013 §7) covers them.
LIGHTS_OUT_T0=$(date +%s)

die() { printf 'go: %s\n' "$*" >&2; exit 1; }

# Attach (docs/adr/0037 §1): on the host, with git only, because the
# user's repository may be anywhere the workbench cannot see. It is cloned
# here, at the Makefile's ATTACHED, and only ever read; the contract check
# then runs in workbench, as `make attached`.
attached="$here/.attached"
case "$cmd" in
  attach)
    [ $# -eq 1 ] || die "usage: ./go attach <path to a git repository>"
    sh "$here/core/attach.sh" "$1" "$attached" || exit 1
    shift
    # A repository without a contract is attached for ./go init to draft
    # one (docs/adr/0042 §6); there is nothing to check yet.
    if git -C "$attached" cat-file -e HEAD:.lights-out/workload.env 2>/dev/null; then
      cmd=attached
    else
      printf 'go: %s has no .lights-out/ at HEAD; ./go init drafts one in the clone\n' "$(git -C "$attached" remote get-url origin)" >&2
      exit 0
    fi ;;
  init)
    [ $# -le 1 ] || die "usage: ./go init [<path to a git repository>]"
    if [ $# -eq 1 ]; then
      sh "$here/core/attach.sh" "$1" "$attached" || exit 1
      shift
    fi
    [ -d "$attached/.git" ] || die "no repository is attached; ./go init <path>, or ./go attach <path> first" ;;
  detach)
    rm -rf "$attached"
    printf 'go: detached; the workload is app/ again (run ./go down first if the plane still serves the attached one)\n' >&2
    exit 0 ;;
  demo)
    [ ! -e "$attached" ] || die "a repository is attached ($(git -C "$attached" remote get-url origin 2>/dev/null || echo "$attached")); ./go demo runs the bundled app: ./go detach first, then ./go attach again"
    ;;
esac

# doctor diagnoses the engine itself, so it runs before ./go requires one
# (docs/adr/0045 §4).
if [ "$cmd" = doctor ]; then
  exec sh "$here/core/doctor.sh"
fi

# 1. Pick the engine: CONTAINER_ENGINE, then podman, then docker.
if [ -n "${CONTAINER_ENGINE:-}" ]; then
  engine="$CONTAINER_ENGINE"
  command -v "$engine" >/dev/null 2>&1 || die "CONTAINER_ENGINE=$engine is not on PATH"
elif command -v podman >/dev/null 2>&1; then
  engine=podman
elif command -v docker >/dev/null 2>&1; then
  engine=docker
else
  die "no container engine found. Install podman (reference) or docker, or set CONTAINER_ENGINE."
fi

if [ "$cmd" = clean ]; then
  "$engine" image rm -f "$image" >/dev/null 2>&1 || true
  printf 'go: removed %s\n' "$image"
  exit 0
fi

# 1.2. The engine must answer before anything is built: a stopped podman
# machine or docker daemon otherwise surfaces minutes later as an unrelated
# NO-GO.
if ! "$engine" info >/dev/null 2>&1; then
  case "$engine:$(uname -s)" in
    podman:Darwin|podman:MINGW*|podman:MSYS*)
      if [ -z "$(podman machine list --format '{{.Name}}' --noheading 2>/dev/null)" ]; then
        die "podman has no machine yet. Create and start one (4 CPUs and 6 GB of memory are comfortable for the plane):
  podman machine init --cpus 4 --memory 6144
  podman machine start"
      fi
      die "podman machine is not running. Start it: podman machine start" ;;
    podman:*)
      die "podman does not answer ('podman info' fails). Check your podman installation, then retry." ;;
    *)
      die "$engine does not answer ('$engine info' fails). Start the $engine daemon (Docker Desktop on macOS), then retry." ;;
  esac
fi

# 1.5. docs/adr/0025, amended 2026-09-20: verified live, not assumed.
# Rootless podman, unlike docker, does not treat localhost:5050 (Zot) as a
# plain-HTTP registry without configuration — `docker-compose push` fails
# with an opaque API 500. One-time and idempotent, only for podman, only
# before the first push (`up`, which `demo` and `engine-compat` reach
# through their own recursive `./go up`): a podman-machine VM (macOS,
# Windows) needs the entry written inside it, root, over ssh, because the
# daemon runs there, not on the host; native rootless podman (Linux, no
# machine) needs it in the user's own config, no root.
if [ "$engine" = podman ] && [ "$cmd" = up ]; then
  reg_conf=/etc/containers/registries.conf.d/999-lights-out-local.conf
  reg_tmp=$(mktemp)
  cat > "$reg_tmp" <<'EOF'
[[registry]]
location = "localhost:5050"
insecure = true
EOF
  # The default machine (podman marks it with a trailing *), else the first.
  machines=$(podman machine list --format '{{.Name}}' --noheading 2>/dev/null)
  machine=$(printf '%s\n' "$machines" | grep '\*$' | head -1)
  [ -n "$machine" ] || machine=$(printf '%s\n' "$machines" | head -1)
  machine=${machine%\*}
  if [ -n "$machine" ]; then
    if ! podman machine ssh "$machine" -- test -f "$reg_conf" 2>/dev/null; then
      podman machine ssh "$machine" -- sudo tee "$reg_conf" >/dev/null < "$reg_tmp" \
        || die "could not write $reg_conf inside podman machine '$machine'. Run once yourself: podman machine ssh -- 'printf \"[[registry]]\\nlocation = \\\"localhost:5050\\\"\\ninsecure = true\\n\" | sudo tee $reg_conf'"
      printf 'go: wrote %s inside podman machine %s — localhost:5050 (Zot) is an insecure registry for podman (docs/adr/0025)\n' "$reg_conf" "$machine" >&2
    fi
  else
    user_conf="${XDG_CONFIG_HOME:-$HOME/.config}/containers/registries.conf.d/999-lights-out-local.conf"
    if [ ! -f "$user_conf" ]; then
      mkdir -p "$(dirname "$user_conf")"
      cp "$reg_tmp" "$user_conf"
      printf 'go: wrote %s — localhost:5050 (Zot) is an insecure registry for podman (docs/adr/0025)\n' "$user_conf" >&2
    fi
  fi
  rm -f "$reg_tmp"
fi

# engine-compat is the one gate that runs on the host, because its subject
# is the host engine (ADR 0004 point 4, ADR 0012 §5). It calls ./go up and
# ./go smoke itself. demo runs it first, then continues inside workbench.
if [ "$cmd" = engine-compat ] || [ "$cmd" = demo ]; then
  CONTAINER_ENGINE="$engine" sh "$here/ci/engine-compat.sh" || exit 1
  [ "$cmd" = demo ] || exit 0
fi

# The lights-out image and its acceptance (docs/adr/0045 §1, §8) run on
# the host: the image is built by the engine itself, and the wrapper a
# stranger runs drives the engine from the host, never from a container.
if [ "$cmd" = package ]; then
  exec sh "$here/package/build.sh" "$engine" "${LIGHTS_OUT_IMAGE:-localhost/lights-out:dev}"
fi
if [ "$cmd" = demo-package ]; then
  CONTAINER_ENGINE="$engine" exec sh "$here/package/demo.sh"
fi

# 2. Build the workbench image once per Containerfile revision.
if ! "$engine" image inspect "$image" >/dev/null 2>&1; then
  printf 'go: building workbench with %s, the first run takes a few minutes\n' "$engine" >&2
  "$engine" build -t "$image" -f "$containerfile" "$here/workbench"
fi

# 3. Run flags. The repo is mounted at the same absolute path as on the host so
# that paths written by tools inside workbench are valid outside, and back.
tty="-i"
[ -t 0 ] && tty="-it"
label=""
[ -d /sys/fs/selinux ] && label=":z"

# Engine-specific flags. Two branches is the ceiling (ADR 0004).
engine_flags=""
if [ "$engine" = docker ]; then
  engine_flags="--user $(id -u):$(id -g) -e HOME=/tmp --add-host host.containers.internal:host-gateway"
fi

# 4. Targets that drive the engine get its socket. This hands workbench full
# control of the engine; the README says so. For gates, init and
# mutation-baseline the socket is optional (docs/adr/0042 §4): a workload
# whose toolchain is the workbench never needs it, and one that does gets
# a NO-GO naming it inside, never a silent in-process run.
socket_flags=""
host_sock_flag=""
socket_optional=""
case "$cmd" in gates|init|mutation-baseline|demo-init|console|logbook) socket_optional=1 ;; esac
case "$cmd" in
  up|down|test|demo|release|demo-rollback|demo-hold|demo-seam|demo-network|e2e|resume|shell|gates|demo-gates|mutation-baseline|assign|demo-crew|record|demo-launch|review|demo-review|record-review|delivery-conformance|harness-conformance|codehost-conformance|unsigned-refused|board|demo-rework|init|demo-init|console|logbook|demo-console)
    sock=""
    if [ "$engine" = podman ]; then
      sock=$(podman info --format '{{.Host.RemoteSocket.Path}}'); sock=${sock#unix://}
      if [ "$(podman info --format '{{.Host.RemoteSocket.Exists}}')" != true ] || [ -z "$sock" ]; then
        [ -n "$socket_optional" ] \
          || die "podman's API socket ${sock:-(none)} does not exist. Enable it: systemctl --user enable --now podman.socket"
        printf 'go: podman has no API socket; %s runs without the engine (a toolchain other than workbench will NO-GO)\n' "$cmd" >&2
        sock=""
      fi
    else
      sock=${DOCKER_HOST:-}; sock=${sock#unix://}
      [ -n "$sock" ] || sock=/var/run/docker.sock
      if [ ! -S "$sock" ]; then
        [ -n "$socket_optional" ] || die "$engine's socket $sock does not exist"
        printf 'go: no engine socket at %s; %s runs without the engine (a toolchain other than workbench will NO-GO)\n' "$sock" "$cmd" >&2
        sock=""
      fi
    fi
    if [ -n "$sock" ]; then
      # label=disable: the engine's SELinux (podman machine is Fedora CoreOS)
      # would otherwise deny the container access to the socket.
      socket_flags="-v $sock:/var/run/docker.sock -e DOCKER_HOST=unix:///var/run/docker.sock --security-opt label=disable"
      # Under docker the container runs as the host user (see engine_flags),
      # who reaches the socket through its group; carry that group in.
      if [ "$engine" = docker ] && [ -S "$sock" ]; then
        sock_gid=$(stat -c %g "$sock" 2>/dev/null || stat -f %g "$sock")
        socket_flags="$socket_flags --group-add $sock_gid"
      fi
      # The same host path, passed through so compose can give the Gitea
      # runner service its own copy of the socket (docs/adr/0015 §7, 0025
      # §4: the runner is the only *compose service* with it; this is a
      # separate mount from workbench's own above, not a second grant of it).
      host_sock_flag="-e HOST_DOCKER_SOCK=$sock"
    fi ;;
esac

# 5. The board is the one target that serves a port: on the host's
# loopback only, BOARD_PORT to move it (docs/adr/0035). It reads the plane
# through the Delivery port's status.sh, which needs the socket above.
port_flags=""
if [ "$cmd" = board ]; then
  port_flags="-p 127.0.0.1:${BOARD_PORT:-8099}:8099 -e BOARD_PORT=${BOARD_PORT:-8099}"
fi
# The console likewise (docs/adr/0047 §4, §5): on the host's loopback by
# default. It listens on every address inside the container, so it is told
# where the host publishes it, and refuses to start in loopback mode
# anywhere else. CONSOLE_* settings pass through from the host's
# environment; the Makefile also reads them from .env.
if [ "$cmd" = console ]; then
  publish=${CONSOLE_PUBLISH:-127.0.0.1}
  port_flags="-p $publish:${CONSOLE_PORT:-8098}:8098 -e CONSOLE_PORT=${CONSOLE_PORT:-8098} -e CONSOLE_BIND=0.0.0.0 -e CONSOLE_PUBLISH=$publish"
  # -e NAME, no value: the engine copies it from this environment, so a
  # value with spaces is never split. An unset one is not passed.
  for v in CONSOLE_AUTH CONSOLE_IDENTITY_HEADER CONSOLE_LAUNCHERS CONSOLE_SNAPSHOT CONSOLE_HOSTS CONSOLE_POLL_S; do
    if env | grep -q "^$v="; then port_flags="$port_flags -e $v"; fi
  done
fi

# shellcheck disable=SC2086 # flag strings are deliberately word-split
run() {
  exec "$engine" run --rm --init $tty \
    -v "$here:$here$label" -w "$here" \
    -e PROFILE="${PROFILE:-l0}" -e CONTAINER_ENGINE="$engine" -e WORKBENCH_IMAGE="$image" \
    -e LIGHTS_OUT_T0="$LIGHTS_OUT_T0" \
    $engine_flags $socket_flags $host_sock_flag $port_flags \
    "$image" "$@"
}

if [ "$cmd" = shell ]; then
  run sh
else
  run make "$cmd" "$@"
fi
