#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# lights-out entrypoint. The only things the host needs are a container
# engine, git and this shell. Everything else runs inside the workbench image
# built from workbench/Containerfile (docs/adr/0004-containerised-toolchain.md).
#
#   ./go help           list the make targets
#   ./go start          guided setup wizard (interactive or --non-interactive); writes non-secret plan, checks doctor, prints E2E commands (ADR 0103)
#   ./go next           inspect repo, contract and state; print next E2E command (MODE=junior|expert, --json) (ADR 0103)
#   ./go cli <args>     unified noun-verb CLI over flat commands and JSON envelopes (ADR 0112)
#   ./go top            interactive terminal cockpit over the logbook: [LOGBOOK_FILE=] [URL=] [ONCE=1] [JSON=1] (ADR 0114)
#   ./go ci             repo self-checks, no engine socket inside
#   ./go up             start the L0 plane in compose and wait for it
#   ./go smoke          run the workload's happy path through Envoy
#   ./go demo           the demo: a step per line, the run's own console at the end (LIGHTS_OUT_VERBOSE=1 prints everything; ADR 0093)
#   ./go demo-rollback  release a failing canary, prove it rolls back on its own
#   ./go demo-hold      same release, kill switch on: prove verify GO holds the canary at 25%
#   ./go demo-dast      prove the passive DAST scan at staging: the stable against itself GO, a leaky canary rolled back
#   ./go demo-gates     prove every PR gate GO and NO-GO on the committed fixtures
#   ./go demo-seam      prove attach and the Workload port on workloads/minimal as its own repository
#   ./go demo-seam-plain prove attach and proxy SLO rollback on uninstrumented workloads/plain
#   ./go attach <path>  make a repository of yours the workload (its .lights-out/ is the contract, if it has one)
#   ./go try <path>     your repository in one command: its contract drafted, the eight gates on its last commit, how to keep the draft (ADR 0093)
#   ./go init [<path>] [--apply] [--branch <name>] [--commit] [--force] [--workspace] [--baseline-mode full|incremental] [--baseline-max-files <n>]  draft .lights-out/ in the clone (or zero-copy into <path> with --apply), baseline measured (ADR 0042, 0113)
#   ./go genesis        scaffold a greenfield repository from a brief with ADR 0001, starter tickets, tests and .lights-out/ (ADR 0118)
#   ./go epics          manage multi-ticket epic DAGs (create|status|unblock) and monorepo workspaces (workspace) (ADR 0119)
#   ./go gates          judge a range of the workload: BASE=<ref> HEAD=<ref>, default the last commit; no plane needed
#   ./go adr-check      check ADR lifecycle compliance on a range: [BASE=<ref> HEAD=<ref>] (ADR 0115)
#   ./go migration-safety  check database expand/contract migration safety: [BASE=<ref> HEAD=<ref>] [TICKET_FILE=] (ADR 0123)
#   ./go agents         what the contract gives the agents: role files for the reviewers, MCP servers for the dev-worker (ADR 0073)
#   ./go demo-init      prove attach, init and gates on the two example repositories, without the plane
#   ./go corpus         verify the unknown-repository corpus (init, light gates, contracts): [CORPUS_FULL=1] (ADR 0106)
#   ./go detach         drop the attached repository; the workload is app/ again
#   ./go demo-network   prove a network of members on one ticket, governed end to end (ADR 0036)
#   ./go e2e            run the full tower: ticket → PR → CI → merge → canary → verify NO-GO → rollback, audited
#   ./go audit          rebuild the chain from a ticket's state: TICKET=<id>
#   ./go evidence       the evidence pack of a release and its frozen console, signed with the plane's key and verified: VERSION=<v> (ADR 0081, 0089)
#   ./go provenance     build, sign and verify an in-toto provenance attestation: [VERSION=<v>] [TICKET=<id>] [PROVENANCE_DIR=] (ADR 0117)
#   ./go resume         request again the step a ticket failed, history kept: TICKET=<id>
#   ./go request        start a ticket at requested: the product role asks or confirms, then the spec gate: TICKET=<id> [REQUEST_BY=<who>]
#   ./go answer         answer a ticket's open question, then the product role again: TICKET=<id> ANSWER=<text>
#   ./go screen-clear   clear a text the screen stopped, after reading it, and run its step again: TICKET=<id> (ADR 0091)
#   ./go demo-rework    prove a NO-GO goes back to the worker with its verdict, then GO and merged
#   ./go demo-mainline  prove main stays green without a queue: unjudged disjoint merges, an overlap reworked, a breaking merge bisected and reverted
#   ./go assign         turn a ticket into a PR: TICKET=<id>
#   ./go demo-crew      prove ticket 0001 becomes a PR that passes the M4 gates
#   ./go record         run a ticket for real at L1 and save the fixture: TICKET=<id>
#   ./go triage         run the ops-triage role on an alert: ALERT=<file>
#   ./go demo-triage    prove a fake alert becomes an incident with ranked hypotheses and a proposal
#   ./go incident       build an incident document from the release record: NAME= FROM= TO= [LOGS=] [TICKET=]
#   ./go postmortem     run the ops-triage role on an incident document: INCIDENT=<file>, one ticket per action item
#   ./go demo-postmortem prove the incident captured from demo-rollback becomes a post mortem and a fix ticket, no plane
#   ./go watch          continuous tower orchestrator draining EVENT_BUS: [ONCE=1] [POLL_S=<sec>] (ADR 0109)
#   ./go alert-webhook  normalize an alert JSON file or serve alert webhook on localhost:8097: [ALERT_FILE=<json>] (ADR 0109)
#   ./go auto-incident  build a deterministic incident document from rolled-back .crew/release.json (ADR 0109)
#   ./go board          the pipeline explained, live when the plane is up, on localhost:8099
#   ./go console        the client's console on localhost:8098: the record, and go-for-launch from a browser (ADR 0047)
#   ./go console-build  bundle modular console/src/ into self-contained console/index.html: [--check] (ADR 0124)
#   ./go logbook        write the logbook, the one versioned record the console reads: [LOGBOOK_FILE=<file>]
#   ./go network        one ticket's agent network in text, the console's #network in the same words: [TICKET=] [ATTEMPT=]
#   ./go dora           the four delivery measures (DORA) over the last WINDOW_DAYS days, from the logbook (ADR 0082)
#   ./go spend          cost and time per ticket, per role and per day, from the tower's spend ledger (ADR 0083)
#   ./go scoreboard     per provider and harness: merged at the first attempt, reworks, NO-GOs, budget stops, cost; every row sourced (ADR 0088)
#   ./go demo-console   prove go-for-launch pressed from the console: held canary, POST GO → 100%, then the un-promote
#   ./go promote        go-for-launch: the brief, the human answer, then 100% or rollback
#   ./go demo-launch    prove go-for-launch: held canary, brief, GO → 100%, then the un-promote
#   ./go record-evals   record the ADK roles' responses at L1 (PROFILE=l1)
#   ./go record-decisions  record the screens and the perimeter decisions L0 replays, at L1 (PROFILE=l1, ADR 0091)
#   ./go record-postmortem  record the ops-triage role's post mortem of fixtures/incidents/ at L1 (PROFILE=l1)
#   ./go harness-conformance  every CLI harness against a scripted model, no key: [CONFORMANCE_HARNESS=<name>] (ADR 0041)
#   ./go argo-conformance  the Delivery suite on adapters/argo-rollouts, a local k3s beside the plane (ADR 0051)
#   ./go flagger-conformance  the Delivery suite on adapters/flagger, the same k3s with Flagger (ADR 0095)
#   ./go flagger-proof  Flagger's own rollback on bad metrics and its promotion held at the gate, on that cluster (ADR 0095)
#   ./go codehost-conformance  ten cases on the CODE_HOST set: Gitea with the plane up, or CODE_HOST=github on an attached repository (ADR 0043)
#   ./go gitlab-conformance  the same ten cases on adapters/gitlab, GitLab CE in a container beside the plane, no plane needed (ADR 0058)
#   ./go registry-conformance  six-case Registry suite on REGISTRY (zot or oci-registry) (ADR 0107)
#   ./go flags-conformance  six-case Flags suite on FLAGS (flags-file daemonless JSON, flags-http OFREP, or flagd) (ADR 0107)
#   ./go tracker-conformance  six-case Tracker suite on TRACKER (file, github-issues, linear, or outside/*) (ADR 0116)
#   ./go charts-conformance  five-case Charts suite on CHARTS (grafana, cloud-monitoring, or outside/*) (ADR 0122)
#   ./go engine-compat  host-side engine gate: versions, then up and smoke
#   ./go doctor         engine, versions, ports, the key: one line each, then GO or NO-GO (host-side)
#   ./go doctor --level <l0|l1|l2|l3>   what that level still lacks, one line per item (host-side, ADR 0057)
#   ./go production-readiness  GO only with a conformant delivery, your metrics, no secrets in .env and a human gate (ADR 0057)
#   ./go package        build the full lights-out image, localhost/lights-out:dev (host-side)
#   ./go package-gates  build the gates image, localhost/lights-out-gates:dev, what the wrapper and the Action run (host-side)
#   ./go demo-package   prove the wrapper: init, gates, test-proof and test-integrity from the gates image on workloads/minimal
#   ./go demo-jenkins   prove Jenkins as a surface: Jenkinsfile Runner posts the gates line on a local Gitea PR (ADR 0058)
#   ./go demo-gitlab-ci  prove GitLab CI as a surface: a real gitlab-runner posts the gates line on a local GitLab merge request (ADR 0095)
#   ./go demo-circleci  prove the CircleCI recipe as far as runs without an account: config validate, its step against a local Gitea PR (ADR 0095)
#   ./go cloud-setup CLOUD=<provider> [SLO_MODE=proxy|otel]  stage Terraform + OIDC starter for a cloud Delivery plugin into .crew/cloud/<provider>/ (ADR 0104, 0107)
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
LIGHTS_OUT_T0=${LIGHTS_OUT_T0:-$(date +%s)}
export LIGHTS_OUT_T0

die() { printf 'go: %s\n' "$*" >&2; exit 1; }

# `./go demo` for a person (docs/adr/0093 §2): the run itself is this
# script again, its output to .crew/demo/run.log through core/progress.sh,
# which shows a line per step and the verdict; the last line written into
# the pipe is the run's exit code. LIGHTS_OUT_VERBOSE=1 streams everything.
# On GO with a terminal, the run's frozen console opens (§4).
if [ "$cmd" = demo ] && [ -z "${LIGHTS_OUT_DEMO_INNER:-}" ]; then
  mkdir -p "$here/.crew/demo"
  rc=0
  if [ "${LIGHTS_OUT_VERBOSE:-}" = 1 ]; then
    LIGHTS_OUT_DEMO_INNER=1 "$here/go" demo "$@" || rc=$?
  else
    { r=0; LIGHTS_OUT_DEMO_INNER=1 "$here/go" demo "$@" 2>&1 </dev/null || r=$?; printf '[exit] %s\n' "$r"; } \
      | sh "$here/core/progress.sh" "$here/.crew/demo" || rc=$?
  fi
  page="$here/.crew/demo/console.html"
  if [ "$rc" -eq 0 ] && [ -t 1 ] && [ "${LIGHTS_OUT_OPEN:-1}" != 0 ] && [ -f "$page" ]; then
    case "$(uname -s)" in
      Darwin) open "$page" 2>/dev/null || : ;;
      *) if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then xdg-open "$page" >/dev/null 2>&1 || :; fi ;;
    esac
  fi
  exit "$rc"
fi

# Attach (docs/adr/0037 §1): on the host, with git only, because the
# user's repository may be anywhere the workbench cannot see. It is cloned
# here, at the Makefile's ATTACHED, and only ever read; the contract check
# then runs in workbench, as `make attached`.
attached="$here/.attached"
init_apply=0
init_target=""
init_cli_args=""
init_make_args=""
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
    init_usage="usage: ./go init [<path>] [--apply] [--branch <name>] [--commit] [--force] [--workspace] [--baseline-mode full|incremental] [--baseline-max-files <n>] [--service <subdir>]"
    init_path=""
    init_sep=$(printf '\037')
    while [ $# -gt 0 ]; do
      case "$1" in
        --apply)
          init_apply=1
          init_cli_args="$init_cli_args${init_cli_args:+$init_sep}--apply"
          shift ;;
        --apply-to=*)
          init_apply=1
          [ -z "$init_path" ] || die "$init_usage"
          init_path=${1#--apply-to=}
          init_cli_args="$init_cli_args${init_cli_args:+$init_sep}--apply"
          shift ;;
        --apply-to)
          [ $# -ge 2 ] || die "$init_usage"
          [ -z "$init_path" ] || die "$init_usage"
          init_apply=1
          init_path=$2
          init_cli_args="$init_cli_args${init_cli_args:+$init_sep}--apply"
          shift 2 ;;
        --commit|--force|--workspace)
          init_cli_args="$init_cli_args${init_cli_args:+$init_sep}$1"
          shift ;;
        --branch=*|--baseline-mode=*|--baseline-max-files=*|--service=*)
          init_cli_args="$init_cli_args${init_cli_args:+$init_sep}$1"
          shift ;;
        --branch|--baseline-mode|--baseline-max-files|--service)
          [ $# -ge 2 ] || die "$init_usage"
          init_cli_args="$init_cli_args${init_cli_args:+$init_sep}$1${init_sep}$2"
          shift 2 ;;
        [A-Za-z_][A-Za-z0-9_]*=*)
          init_make_args="$init_make_args${init_make_args:+$init_sep}$1"
          shift ;;
        -*)
          die "$init_usage" ;;
        *)
          [ -z "$init_path" ] || die "$init_usage"
          init_path=$1
          shift ;;
      esac
    done
    if [ "$init_apply" = 1 ]; then
      [ -n "$init_path" ] || die "init --apply requires <path to a git repository>"
      init_target=$(git -C "$init_path" rev-parse --show-toplevel 2>/dev/null) || die "$init_path is not a git repository"
      init_target=$(CDPATH='' cd -- "$init_target" && pwd -P)
      init_make_args="WORKLOAD=$init_target${init_make_args:+$init_sep}$init_make_args"
    else
      if [ -n "$init_path" ]; then
        sh "$here/core/attach.sh" "$init_path" "$attached" || exit 1
      fi
      [ -d "$attached/.git" ] || die "no repository is attached; ./go init <path>, or ./go attach <path> first"
    fi ;;
  try)
    # docs/adr/0093 §5: attach a clone, then `make try` drafts, judges,
    # copies the draft out and detaches. It uses the attach slot, so it
    # refuses one in use for something else.
    [ $# -eq 1 ] || die "usage: ./go try <path to a git repository of yours>"
    src=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || die "$1 is not a git repository"
    if [ -e "$attached" ]; then
      die "a repository is attached ($(git -C "$attached" remote get-url origin 2>/dev/null || echo "$attached")); ./go try uses the same place: ./go detach first"
    fi
    sh "$here/core/attach.sh" "$src" "$attached" || exit 1
    shift
    try_src=$src ;;
  detach)
    rm -rf "$attached" "$here/.crew/remote.env"
    printf 'go: detached; the workload is app/ again (run ./go down first if the plane still serves the attached one)\n' >&2
    exit 0 ;;
  demo)
    [ ! -e "$attached" ] || die "a repository is attached ($(git -C "$attached" remote get-url origin 2>/dev/null || echo "$attached")); ./go demo runs the bundled app: ./go detach first, then ./go attach again"
    ;;
esac

# doctor diagnoses the engine itself, so it runs before ./go requires one
# (docs/adr/0045 §4).
if [ "$cmd" = doctor ]; then
  exec sh "$here/core/doctor.sh" "$@"
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
# NO-GO. A podman machine that exists but is stopped is started (docs/adr/
# 0093 §1a); creating one is left to the person, with the commands.
if ! "$engine" info >/dev/null 2>&1; then
  CONTAINER_ENGINE="$engine" sh "$here/core/preflight.sh" machine || exit 1
fi
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

# 1.3. The preflight (docs/adr/0093 §1): memory, disk and the plane's
# ports, a busy one moved, before anything is built. Once per ./go demo:
# its recursive ./go up does not run it again.
if { [ "$cmd" = up ] || [ "$cmd" = demo ]; } && [ -z "${LIGHTS_OUT_PREFLIGHT_DONE:-}" ]; then
  [ "$cmd" != demo ] || printf '[step] preflight\n' >&2
  CONTAINER_ENGINE="$engine" sh "$here/core/preflight.sh" || exit 1
  LIGHTS_OUT_PREFLIGHT_DONE=1
  export LIGHTS_OUT_PREFLIGHT_DONE
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

# The lights-out image and its acceptance (docs/adr/0045 §1, §8) run on
# the host: the image is built by the engine itself, and the wrapper a
# stranger runs drives the engine from the host, never from a container.
if [ "$cmd" = package ]; then
  exec sh "$here/package/build.sh" "$engine" "${LIGHTS_OUT_IMAGE:-localhost/lights-out:dev}"
fi
if [ "$cmd" = package-gates ]; then
  PACKAGE_FLAVOUR=gates exec sh "$here/package/build.sh" "$engine" "${LIGHTS_OUT_IMAGE:-localhost/lights-out-gates:dev}"
fi
if [ "$cmd" = demo-package ]; then
  CONTAINER_ENGINE="$engine" exec sh "$here/package/demo.sh"
fi
# Another CI as a surface (docs/adr/0058 §7): Jenkinsfile Runner judges a PR
# on a Gitea of its own, through the wrapper and the host engine, as a
# Jenkins agent would.
if [ "$cmd" = demo-jenkins ]; then
  CONTAINER_ENGINE="$engine" exec sh "$here/surfaces/jenkins/demo.sh"
fi
# GitLab CI and CircleCI as surfaces (docs/adr/0095 §7-§8), host-side too.
if [ "$cmd" = demo-gitlab-ci ]; then
  CONTAINER_ENGINE="$engine" exec sh "$here/surfaces/gitlab-ci/demo.sh"
fi
if [ "$cmd" = demo-circleci ]; then
  CONTAINER_ENGINE="$engine" exec sh "$here/surfaces/circleci/demo.sh"
fi

# 2. Build the workbench image once per Containerfile revision. A run
# that has to build it is a first run: demo reports its time instead of
# judging it, unless CI is set (docs/adr/0013, amendment 2026-11-04).
first_run=0
[ "$cmd" != demo ] || printf '[step] toolchain\n' >&2
if ! "$engine" image inspect "$image" >/dev/null 2>&1; then
  printf 'go: building workbench with %s, the first run takes a few minutes\n' "$engine" >&2
  "$engine" build -t "$image" -f "$containerfile" "$here/workbench"
  first_run=1
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

# The repository's checks beside the plane (docs/adr/0093 §3): for demo,
# `make ci` starts now in a container of its own, with no engine socket as
# under ./go ci, its output and then its exit code in .crew/demo, where
# `make demo` waits for them. Removed if this run ends before they do.
ci_dir="$here/.crew/demo"
ci_name=""
if [ "$cmd" = demo ]; then
  mkdir -p "$ci_dir"
  rm -f "$ci_dir/ci.out" "$ci_dir/ci.err" "$ci_dir/ci.rc"
  ci_name="lights-out-demo-ci-$$"
  # shellcheck disable=SC2086 # engine_flags is deliberately word-split
  ( r=0
    "$engine" run --rm --init --name "$ci_name" -v "$here:$here$label" -w "$here" \
      -e PROFILE="${PROFILE:-l0}" -e CONTAINER_ENGINE="$engine" -e WORKBENCH_IMAGE="$image" -e LIGHTS_OUT_CI="${CI:-}" \
      $engine_flags "$image" make ci >"$ci_dir/ci.out" 2>"$ci_dir/ci.err" </dev/null || r=$?
    printf '%s\n' "$r" > "$ci_dir/ci.rc" ) &
  trap '[ -s "$ci_dir/ci.rc" ] || "$engine" rm -f "$ci_name" >/dev/null 2>&1 || :' EXIT
  printf '[demo] ci: the repository'"'"'s checks started beside the plane, in %s (docs/adr/0093 §3)\n' "$ci_name" >&2
fi

# engine-compat is the one gate that runs on the host, because its subject
# is the host engine (ADR 0004 point 4, ADR 0012 §5). It calls ./go up and
# ./go smoke itself. demo runs it first, then continues inside workbench.
if [ "$cmd" = engine-compat ] || [ "$cmd" = demo ]; then
  [ "$cmd" != demo ] || printf '[step] plane\n' >&2
  CONTAINER_ENGINE="$engine" sh "$here/ci/engine-compat.sh" || exit 1
  [ "$cmd" = demo ] || exit 0
fi


# 4. Targets that drive the engine get its socket. This hands workbench full
# control of the engine; the README says so. For gates, init and
# mutation-baseline the socket is optional (docs/adr/0042 §4): a workload
# whose toolchain is the workbench never needs it, and one that does gets
# a NO-GO naming it inside, never a silent in-process run.
socket_flags=""
socket_optional=""
case "$cmd" in gates|init|try|mutation-baseline|demo-init|console|logbook|network|dora|watch|cli|registry-conformance) socket_optional=1 ;; esac
case "$cmd" in
  up|down|test|demo|release|demo-rollback|demo-hold|demo-dast|demo-seam|demo-seam-plain|demo-network|e2e|resume|shell|gates|demo-gates|mutation-baseline|assign|demo-crew|record|demo-launch|review|demo-review|record-review|delivery-conformance|argo-conformance|flagger-conformance|flagger-proof|harness-conformance|codehost-conformance|gitlab-conformance|registry-conformance|unsigned-refused|board|demo-rework|demo-mainline|init|try|demo-init|console|logbook|network|dora|demo-console|request|answer|watch|cli)
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
fi
if [ "$cmd" = alert-webhook ]; then
  alert_file=${ALERT_FILE:-}
  for a in "$@"; do
    case "$a" in
      ALERT_FILE=*) alert_file=${a#ALERT_FILE=} ;;
    esac
  done
  if [ -z "$alert_file" ]; then
    publish=${ALERT_WEBHOOK_PUBLISH:-127.0.0.1}
    port_flags="-p $publish:${ALERT_WEBHOOK_PORT:-8097}:8097 -e WEBHOOK_PORT=8097 -e WEBHOOK_BIND=0.0.0.0"
  fi
fi
# -e NAME, no value: the engine copies it from this environment, so a
# value with spaces is never split. An unset one is not passed. The
# console's settings go to the console and to production-readiness, which
# judges them (docs/adr/0057 §10); the secret keys .env.example declares
# go to every command, so production can keep them out of .env (§10(c)).
# A value in .env still wins inside make.
if [ "$cmd" = console ] || [ "$cmd" = production-readiness ]; then
  for v in CONSOLE_AUTH CONSOLE_IDENTITY_HEADER CONSOLE_LAUNCHERS CONSOLE_SNAPSHOT CONSOLE_HOSTS CONSOLE_POLL_S \
           CONSOLE_VERIFY_JWT CONSOLE_JWT_HEADER CONSOLE_JWKS_URL CONSOLE_JWKS_FILE CONSOLE_JWKS_TTL_S \
           CONSOLE_JWKS_REFRESH_COOLDOWN_S CONSOLE_JWT_ISSUER CONSOLE_JWT_AUDIENCE CONSOLE_JWT_LEEWAY_S \
           CONSOLE_JWT_ALGS CONSOLE_JWT_SECRET CONSOLE_IDENTITY_CLAIM CONSOLE_RBAC CONSOLE_DEFAULT_ROLE \
           CONSOLE_VIEWERS CONSOLE_DEVELOPERS CONSOLE_OPERATORS CONSOLE_ROLE_CLAIM CONSOLE_ROLE_MAP \
           CONSOLE_AUDIT_LOG; do
    if env | grep -q "^$v="; then port_flags="$port_flags -e $v"; fi
  done
fi
# shellcheck disable=SC2013 # variable names, one word each
for v in $(awk '/^[A-Za-z0-9_]*(_KEY|_TOKEN|_SECRET|_PASSWORD)=/ || /^METRICS_AUTH_HEADER=/ { sub(/=.*/, ""); print }' "$here/.env.example" | sort -u); do
  if env | grep -q "^$v="; then port_flags="$port_flags -e $v"; fi
done
for v in POLL_S ONCE ALERT_FILE ALERT_WEBHOOK_TOKEN ALERT_WEBHOOK_OUT WEBHOOK_ARGS \
         AUTO_INCIDENT_ON_ROLLBACK AUTO_POSTMORTEM_ON_ROLLBACK \
         SLO_PROMQL SLO_MODE INIT_SERVICE SMOKE_AUTH_HEADER \
         FLAGS_AUTH_HEADER OCI_REGISTRY_TOKEN OCI_REGISTRY_PASSWORD \
         LOGBOOK_FILE LOGBOOK_URL URL JSON VIEW WIDTH HEIGHT NO_COLOR TICKET_FILE \
         PROVENANCE_DIR COSIGN_KEY COSIGN_PUB RELEASES_LOG SUBJECT_SHA256 IMAGE \
         TRACKER GITHUB_API_URL LINEAR_TEAM_ID LINEAR_API_URL LINEAR_TOKEN \
         CHARTS CHARTS_URL GRAFANA_URL GRAFANA_TOKEN \
         MONITORING_API_URL MONITORING_ACCESS_TOKEN MONITORING_AUTH_HEADER \
         SSE_HEARTBEAT_S CONSOLE_SSE_HEARTBEAT_S; do
  if env | grep -q "^$v="; then port_flags="$port_flags -e $v"; fi
done
if [ "$cmd" = top ] && env | grep -q "^TERM="; then
  port_flags="$port_flags -e TERM"
fi
if env | grep -q "^CORPUS_FULL="; then port_flags="$port_flags -e CORPUS_FULL"; fi

# Adapters outside the repository (docs/adr/0058 §1): ADAPTERS_PATH, from
# this environment, else .env, else .crew/start-plan.env (for non-excluded
# commands, docs/adr/0108), else the profile, is mounted read-only at
# its own path, where make finds it and core/adapters.py links from it.
skip_plan=""
case "$cmd" in
  demo|demo-*|ci|ci-*|e2e|corpus|init|genesis|try|gates|test-proof|test-integrity|adr-check|migration-safety|mutation-baseline|attached) skip_plan=1 ;;
esac
effective_profile=${PROFILE:-}
if [ -z "$effective_profile" ] && [ -z "$skip_plan" ] && [ -f "$here/.crew/start-plan.env" ]; then
  effective_profile=$(awk -F= '/^START_PROFILE=/ { sub(/^START_PROFILE=/, ""); v = $0 } END { print v }' "$here/.crew/start-plan.env")
fi
[ -n "$effective_profile" ] || effective_profile=l0

adapters_path=${ADAPTERS_PATH:-}
for f in "$here/.env" "$here/.crew/start-plan.env" "$here/profiles/${effective_profile}.env"; do
  [ -z "$adapters_path" ] && [ -f "$f" ] || continue
  [ -n "$skip_plan" ] && [ "$f" = "$here/.crew/start-plan.env" ] && continue
  adapters_path=$(awk -F= '/^ADAPTERS_PATH=/ { sub(/^ADAPTERS_PATH=/, ""); v = $0 } END { print v }' "$f")
done
if [ -n "$adapters_path" ]; then
  case "$adapters_path" in /*) ;; *) die "ADAPTERS_PATH=$adapters_path is not an absolute path (docs/adr/0058 §1)" ;; esac
  [ -d "$adapters_path" ] || die "ADAPTERS_PATH=$adapters_path is not a directory"
  port_flags="$port_flags -e ADAPTERS_PATH=$adapters_path -v $adapters_path:$adapters_path:ro$(printf '%s' "$label" | tr ':' ',')"
fi
if [ -n "${PROFILE:-}" ]; then
  port_flags="$port_flags -e PROFILE"
fi

# shellcheck disable=SC2086 # flag strings are deliberately word-split
runc() {
  ${run_exec:-} "$engine" run --rm --init $tty \
    -v "$here:$here$label" -w "$here" \
    -e CONTAINER_ENGINE="$engine" -e WORKBENCH_IMAGE="$image" \
    -e LIGHTS_OUT_T0="$LIGHTS_OUT_T0" -e LIGHTS_OUT_FIRST_RUN="$first_run" -e LIGHTS_OUT_CI="${CI:-}" \
    $engine_flags $socket_flags $port_flags \
    "$image" "$@"
}
run() { run_exec="exec"; runc "$@"; }

# demo: the checks' directory and the plane already up (docs/adr/0093 §3);
# not exec'd, so the trap above can remove the checks' container. try: the
# repository's path on the host, for the commands that keep the draft (§5).
if [ "$cmd" = demo ]; then
  port_flags="$port_flags -e LIGHTS_OUT_CI_DIR=.crew/demo -e LIGHTS_OUT_PLANE_UP=1"
  rc=0; runc make demo "$@" || rc=$?
  exit "$rc"
fi
if [ "$cmd" = try ]; then
  TRY_SOURCE=$try_src
  export TRY_SOURCE
  port_flags="$port_flags -e TRY_SOURCE"
fi
if [ "$cmd" = init ] && [ "$init_apply" = 1 ] && [ -n "$init_target" ]; then
  if [ "$init_target" != "$here" ]; then
    port_flags="$port_flags -v $init_target:$init_target:rw$(printf '%s' "$label" | tr ':' ',')"
  fi
  printf 'go: mounting %s read-write for zero-copy init --apply\n' "$init_target" >&2
fi

# For `init`, `start`, `next`, `top`, and `cli`, forward CLI flags (--apply,
# --non-interactive, --json, --once, etc.) via LIGHTS_OUT_CLI_ARGS so `make`
# never tries to parse them, while keeping KEY=VALUE overrides as make arguments
# (for `cli`, only CLI_ARGS/WORKLOAD/ATTACHED are make arguments; command
# KEY=VALUE pairs go to core/cli.py). For `start`, pre-run host-side
# core/doctor.sh so workbench receives real host engine and port status.
make_args=""
if [ "$cmd" = init ]; then
  make_args=$init_make_args
  if [ -n "$init_cli_args" ]; then
    LIGHTS_OUT_CLI_ARGS=$init_cli_args
    export LIGHTS_OUT_CLI_ARGS
    port_flags="$port_flags -e LIGHTS_OUT_CLI_ARGS"
  fi
elif [ "$cmd" = cli ]; then
  cli_sep=$(printf '\037')
  cli_args=""
  for a in "$@"; do
    case "$a" in
      CLI_ARGS=*|WORKLOAD=*|ATTACHED=*) make_args="$make_args${make_args:+$cli_sep}$a" ;;
      *) cli_args="$cli_args${cli_args:+$cli_sep}$a" ;;
    esac
  done
  if [ -n "$cli_args" ]; then
    LIGHTS_OUT_CLI_ARGS=$cli_args
    export LIGHTS_OUT_CLI_ARGS
    port_flags="$port_flags -e LIGHTS_OUT_CLI_ARGS"
  fi
elif [ "$cmd" = genesis ]; then
  cli_sep=$(printf '\037')
  cli_args=""
  genesis_repo=""
  genesis_brief_file=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo=*)
        genesis_repo=${1#--repo=}
        cli_args="$cli_args${cli_args:+$cli_sep}$1"
        shift ;;
      --repo)
        [ $# -ge 2 ] || die "genesis: --repo requires a path"
        genesis_repo=$2
        cli_args="$cli_args${cli_args:+$cli_sep}--repo${cli_sep}$2"
        shift 2 ;;
      --brief-file=*)
        genesis_brief_file=${1#--brief-file=}
        cli_args="$cli_args${cli_args:+$cli_sep}$1"
        shift ;;
      --brief-file)
        [ $# -ge 2 ] || die "genesis: --brief-file requires a path"
        genesis_brief_file=$2
        cli_args="$cli_args${cli_args:+$cli_sep}--brief-file${cli_sep}$2"
        shift 2 ;;
      --brief|--stack|--name|--port|--tickets-count|--today|--stacks)
        [ $# -ge 2 ] || die "genesis: $1 requires a value"
        cli_args="$cli_args${cli_args:+$cli_sep}$1${cli_sep}$2"
        shift 2 ;;
      [A-Za-z_][A-Za-z0-9_]*=*)
        case "$1" in
          REPO=*) genesis_repo=${1#REPO=} ;;
          BRIEF_FILE=*) genesis_brief_file=${1#BRIEF_FILE=} ;;
        esac
        make_args="$make_args${make_args:+$cli_sep}$1"
        shift ;;
      -*)
        cli_args="$cli_args${cli_args:+$cli_sep}$1"
        shift ;;
      *)
        if [ -z "$genesis_repo" ]; then
          genesis_repo=$1
          cli_args="$cli_args${cli_args:+$cli_sep}--repo${cli_sep}$1"
        else
          cli_args="$cli_args${cli_args:+$cli_sep}$1"
        fi
        shift ;;
    esac
  done
  if [ -n "$genesis_repo" ]; then
    g_mount=""
    if [ -d "$genesis_repo" ]; then
      g_mount=$(CDPATH='' cd -- "$genesis_repo" && pwd -P)
    elif [ -d "$(dirname -- "$genesis_repo")" ]; then
      g_mount=$(CDPATH='' cd -- "$(dirname -- "$genesis_repo")" && pwd -P)
    fi
    if [ -n "$g_mount" ] && [ "$g_mount" != / ]; then
      case "$g_mount" in
        "$here"|"$here"/*) ;;
        *)
          port_flags="$port_flags -v $g_mount:$g_mount:rw$(printf '%s' "$label" | tr ':' ',')"
          printf 'go: mounting %s read-write for genesis\n' "$g_mount" >&2 ;;
      esac
    fi
  fi
  if [ -n "$genesis_brief_file" ] && [ -f "$genesis_brief_file" ]; then
    bf_abs=$(CDPATH='' cd -- "$(dirname -- "$genesis_brief_file")" && pwd -P)/$(basename -- "$genesis_brief_file")
    case "$bf_abs" in
      "$here"/*) ;;
      *) port_flags="$port_flags -v $bf_abs:$bf_abs:ro$(printf '%s' "$label" | tr ':' ',')" ;;
    esac
  fi
  for gv in REPO BRIEF BRIEF_FILE STACK SERVICE_NAME PORT TICKETS_COUNT NO_COMMIT JSON GENESIS_ARGS; do
    if env | grep -q "^$gv="; then port_flags="$port_flags -e $gv"; fi
  done
  if [ -n "$cli_args" ]; then
    LIGHTS_OUT_CLI_ARGS=$cli_args
    export LIGHTS_OUT_CLI_ARGS
    port_flags="$port_flags -e LIGHTS_OUT_CLI_ARGS"
  fi
elif [ "$cmd" = start ] || [ "$cmd" = next ] || [ "$cmd" = top ] || [ "$cmd" = epics ]; then
  cli_sep=$(printf '\037')
  cli_args=""
  for a in "$@"; do
    case "$a" in
      [A-Za-z_][A-Za-z0-9_]*=*) make_args="$make_args${make_args:+$cli_sep}$a" ;;
      *) cli_args="$cli_args${cli_args:+$cli_sep}$a" ;;
    esac
  done
  if [ -n "$cli_args" ]; then
    LIGHTS_OUT_CLI_ARGS=$cli_args
    export LIGHTS_OUT_CLI_ARGS
    port_flags="$port_flags -e LIGHTS_OUT_CLI_ARGS"
  fi
  if [ "$cmd" = start ]; then
    doc_out=$(mktemp)
    doc_err=$(mktemp)
    doc_rc=0
    sh "$here/core/doctor.sh" >"$doc_out" 2>"$doc_err" || doc_rc=$?
    LIGHTS_OUT_DOCTOR_RC=$doc_rc
    LIGHTS_OUT_DOCTOR_OUT=$(cat "$doc_out")
    LIGHTS_OUT_DOCTOR_ERR=$(cat "$doc_err")
    rm -f "$doc_out" "$doc_err"
    export LIGHTS_OUT_DOCTOR_RC LIGHTS_OUT_DOCTOR_OUT LIGHTS_OUT_DOCTOR_ERR
    port_flags="$port_flags -e LIGHTS_OUT_DOCTOR_RC -e LIGHTS_OUT_DOCTOR_OUT -e LIGHTS_OUT_DOCTOR_ERR"
  fi
fi

if [ "$cmd" = cloud-setup ]; then
  cs_sep=$(printf '\037')
  cs_args=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --slo-mode=*) cs_args="$cs_args${cs_args:+$cs_sep}SLO_MODE=${1#--slo-mode=}"; shift ;;
      --slo-mode)
        [ $# -ge 2 ] || die "cloud-setup: --slo-mode requires a value (proxy|otel)"
        cs_args="$cs_args${cs_args:+$cs_sep}SLO_MODE=$2"; shift 2 ;;
      *) cs_args="$cs_args${cs_args:+$cs_sep}$1"; shift ;;
    esac
  done
  if [ -n "$cs_args" ]; then
    old_ifs=$IFS
    IFS=$(printf '\037')
    set -f
    # shellcheck disable=SC2086 # deliberate unit-separator splitting for cloud-setup args
    set -- $cs_args
    set +f
    IFS=$old_ifs
  fi
fi

if [ "$cmd" = console-build ]; then
  cb_flags=""
  cb_make=""
  cb_sep=$(printf '\037')
  for a in "$@"; do
    case "$a" in
      [A-Za-z_][A-Za-z0-9_]*=*) cb_make="$cb_make${cb_make:+$cb_sep}$a" ;;
      *) cb_flags="$cb_flags${cb_flags:+ }$a" ;;
    esac
  done
  if [ -n "$cb_flags" ]; then
    cb_make="CONSOLE_BUILD_ARGS=$cb_flags${cb_make:+$cb_sep}$cb_make"
  fi
  if [ -n "$cb_make" ]; then
    old_ifs=$IFS
    IFS=$(printf '\037')
    set -f
    # shellcheck disable=SC2086 # deliberate unit-separator splitting for console-build args
    set -- $cb_make
    set +f
    IFS=$old_ifs
  fi
fi

if [ "$cmd" = shell ]; then
  run sh
elif [ "$cmd" = init ] || [ "$cmd" = start ] || [ "$cmd" = next ] || [ "$cmd" = top ] || [ "$cmd" = cli ] || [ "$cmd" = genesis ] || [ "$cmd" = epics ]; then
  if [ -n "$make_args" ]; then
    old_ifs=$IFS
    IFS=$(printf '\037')
    set -f
    # shellcheck disable=SC2086 # deliberate unit-separator splitting for KEY=VALUE args
    set -- $make_args
    set +f
    IFS=$old_ifs
  else
    set --
  fi
  run make "$cmd" "$@"
else
  run make "$cmd" "$@"
fi
