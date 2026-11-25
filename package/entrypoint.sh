#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The lights-out image's entrypoint (docs/adr/0045 §3, 0105), what the
# `lights-out` wrapper runs: /repo is the user's repository, read-only;
# /out is used by read-only/gates commands; state-only commands mount
# /opt/lights-out/.crew; opt-in plane/workflow commands mirror /opt/lights-out
# into LIGHTS_OUT_PLATFORM_DIR with .crew -> LIGHTS_OUT_CREW_DIR.
#
#   init [--engine] [--apply [--branch <name>] [--commit] [--force]] [--workspace] [--baseline-mode full|incremental] [--baseline-max-files <n>] [HEAD=]  attach /repo, draft .lights-out/ (or apply directly with --apply), copy it to /out/.lights-out, its summary to /out/init
#   genesis [--repo <dir>] [--brief <text> | --brief-file <path>] [--stack <name>] [--name <name>] [--port <n>] [--no-commit]  scaffold a greenfield project into /repo
#   gates [--engine] [BASE= HEAD=]      attach /repo, judge the range (default the last commit)
#   gates STAGED=1           ... with what /repo has staged as one more commit, judged alone
#   gates ... JUDGED=<shas>  ... mutation skips the lines unchanged from those judged trees (docs/adr/0074)
#   test-proof [--engine] [BASE= HEAD=]     the gates test and test-proof only: the tests a change adds fail on its base
#   test-integrity [--engine] [BASE= HEAD=] the gates test-weakening and mutation only: no test weakened, the changed lines' mutants killed
#   adr-check [--engine] [BASE= HEAD=]      judge ADR lifecycle compliance against policy
#   migration-safety [--engine] [BASE= HEAD=] judge database migration expand/contract safety
#   triage ALERT=<file>      a path under /repo (the full image only)
#   postmortem INCIDENT=<file>   a path under /repo; tickets go to /out/tickets (the full image only)
#   next [MODE=junior|expert] [--json]       inspect repo, contract and state; print next E2E command
#   dora [WINDOW_DAYS=30]                    compute the four DORA measures from the logbook
#   spend                                    cost and time per ticket, role and day
#   cloud-setup CLOUD=<provider>             stage Terraform + OIDC starter into .crew/cloud
#   start [--non-interactive ...]            guided setup wizard or preset plan
#   intake [TICKET=] TEXT=<text>             start an intake interview
#   answer TICKET=<id> ANSWER=<text>         answer an intake or ticket question
#   intake-confirm TICKET=<id> DECISIONS=... confirm, edit or drop proposed intake tickets
#   assign TICKET=<id>                       turn a ticket into a PR
#   up                                       start the L0 compose plane and publish stable image
#   down                                     stop the compose plane
#   smoke                                    run the workload's smoke path through the edge
#   release [CANARY_VERSION= CANARY_TAG=]    deploy canary and run SLO verification controller
#   promote [GO_FOR_LAUNCH=GO|NO-GO REASON=] go-for-launch brief and human promotion gate
#   console                                  serve the console on CONSOLE_PUBLISH:CONSOLE_PORT
#   evidence VERSION=<v>                     build, sign and verify release evidence pack
#   provenance [VERSION= TICKET= PROVENANCE_DIR=]  build, sign and verify in-toto provenance attestation (ADR 0117)
#   map [MAP_FILE=]                          build the project map of /repo
#   network [TICKET= ATTEMPT=]               one ticket's agent network in text
#   scoreboard                               per provider and harness: first-attempt merges, reworks, cost
#   auto-incident [RELEASE_RECORD=]          incident document from a rolled-back release record
#   incident NAME= FROM= TO= [LOGS=]         incident document from the release record (TICKET= needs the plane)
#   alert-webhook ALERT_FILE=<path>          normalize an alert JSON file under /repo
#   top [LOGBOOK_FILE= URL= ONCE=1 --json]   interactive terminal cockpit over the logbook
#   try                                      draft the contract and judge the last commit, keep the draft
#   watch [ONCE=1] [POLL_S=<sec>] [RUNNER_WORKERS=<1..8>]  continuous tower orchestrator draining the event bus
#   request TICKET=<id> [REQUEST_BY=]        start a ticket at requested
#   resume TICKET=<id>                       request again the step a ticket failed
#   audit TICKET=<id>                        rebuild the chain deploy to spec from the state store
#   epics [--id <id>] [--title <text>] [--tickets <list>] [--baseline-mode full|incremental]  manage epics DAG and monorepo workspace manifest
#   spec-split <check|split|compose> <file>  autonomous ## Network spec-split planner and validator (ADR 0120)
#   m24-check [evaluate|ingest-logbook ...]  deterministic M24 benchmark evaluator and logbook ingester (ADR 0120)
#   production-readiness                     GO only with a conformant delivery, metrics and a human gate
#   version
#
# Grammar (docs/adr/0112): <domain> <verb> [--flag value | KEY=VALUE ...] with domains repo, ticket,
# release, ops and config is the same command as its flat name; --flag value is KEY=VALUE. --json and
# --jsonl wrap a non-gate command's one stdout line in the envelope of core/cli.py and exit 0, 1, 3 or 4.
#
# One stdout line in the gate output contract (CLAUDE.md), the make
# target's own; the rest on stderr. make exits 2 when a recipe fails: the
# contract's NO-GO is 1, so every non-zero exit leaves here as 1.
set -eu
image_root=${LIGHTS_OUT_IMAGE_ROOT:-/opt/lights-out}
repo_dir=${LIGHTS_OUT_REPO_DIR:-/repo}
cd "$image_root"

lead=""
while [ $# -gt 0 ]; do
  case "$1" in --json|--jsonl) lead="$lead $1"; shift ;; *) break ;; esac
done
sub=${1:-help}
[ $# -gt 0 ] && shift
name=$sub
# shellcheck source=ci/lib.sh disable=SC1091
. ci/lib.sh

member() {
  case "$1" in ""|*[[:space:]]*) return 1 ;; esac
  case " $2 " in *" $1 "*) return 0 ;; esac
  return 1
}

verbs_of() {
  case "$1" in
    repo) echo "init try gates test-proof test-integrity adr-check migration-safety doctor map corpus genesis" ;;
    ticket) echo "intake request answer intake-confirm assign resume network audit epics spec-split" ;;
    release) echo "up down smoke deploy promote evidence provenance production-readiness" ;;
    ops) echo "watch alert-webhook triage auto-incident incident postmortem dora spend top charts-conformance m24-check" ;;
    config) echo "start next cloud-setup scoreboard agents tracker-conformance console-build" ;;
  esac
}

# The keys beyond the 29 a command takes (docs/adr/0112 §2), same as the wrapper's.
extra_keys_for() {
  case "$1" in
    migration-safety) echo "TICKET_FILE" ;;
    provenance) echo "PROVENANCE_DIR" ;;
    intake|answer) echo "INTAKE_NETWORK_SPLIT" ;;
    assign) echo "NETWORK_AUTO_SPLIT" ;;
    resume) echo "INTAKE_NETWORK_SPLIT NETWORK_AUTO_SPLIT" ;;
    request) echo "REQUEST_BY REQUEST_VIA" ;;
    network) echo "ATTEMPT" ;;
    watch) echo "ONCE POLL_S RUNNER_WORKERS WORKERS" ;;
    alert-webhook) echo "ALERT_FILE" ;;
    auto-incident) echo "RELEASE_RECORD" ;;
    incident) echo "NAME FROM TO LOGS" ;;
    map) echo "MAP_FILE" ;;
    cloud-setup) echo "SLO_MODE" ;;
    top) echo "LOGBOOK_FILE LOGBOOK URL ONCE JSON POLL_S VIEW WIDTH HEIGHT" ;;
  esac
}

native_flag() {
  case "$1:$2" in
    init:--branch|init:--baseline-mode|init:--baseline-max-files|start:--preset|start:--provider|start:--harness|start:--delivery|start:--profile|start:--ticket-budget-usd|start:--day-budget-usd|start:--mode|next:--mode) return 0 ;;
    genesis:--repo|genesis:--brief|genesis:--brief-file|genesis:--stack|genesis:--name|genesis:--port|genesis:--tickets-count|genesis:--today|genesis:--stacks) return 0 ;;
    epics:--id|epics:--title|epics:--tickets|epics:--tickets-dir|epics:--state-dir|epics:--repo|epics:--out|epics:--manifest|epics:--stacks|epics:--today|epics:--baseline-mode|epics:--changed) return 0 ;;
    spec-split:--in-place) return 0 ;;
    m24-check:--data|m24-check:--logbook|m24-check:--logbooks|m24-check:--min-providers|m24-check:--allow-incomplete|m24-check:--out|m24-check:--handoff|m24-check:--scope|m24-check:--repository|m24-check:--kind|m24-check:--phase|m24-check:--replace) return 0 ;;
  esac
  return 1
}

# <domain> <verb> is the flat command (docs/adr/0112 §1); release is a domain only when a verb follows.
case "$sub" in
  repo|ticket|release|ops|config)
    if [ "$sub" != release ] || { [ $# -gt 0 ] && member "$1" "$(verbs_of release)"; }; then
      domain=$sub
      # shellcheck disable=SC2034 # consumed by nogo in sourced ci/lib.sh
      name=lights-out
      verb=${1:-}
      case "$verb" in ""|-*) nogo "$domain needs a verb: $(verbs_of "$domain")" ;; esac
      member "$verb" "$(verbs_of "$domain")" || nogo "unknown $domain verb '$verb'; one of: $(verbs_of "$domain")"
      shift
      case "$domain:$verb" in
        release:deploy) sub=release ;;
        repo:corpus|config:agents|config:tracker-conformance|ops:charts-conformance|config:console-build) nogo "$domain $verb needs a clone of lights-out, not the image: run ./go $verb" ;;
        *) sub=$verb ;;
      esac
      # shellcheck disable=SC2034 # consumed by nogo in sourced ci/lib.sh
      name=$sub
    fi ;;
esac

# --json / --jsonl on a non-gate command: run it again, capture its stdout, and wrap that
# in the envelope of core/cli.py (docs/adr/0112 §4); its exit code is the taxonomy's.
for f in $lead; do set -- "$@" "$f"; done
json_cmds="triage postmortem dora spend cloud-setup network scoreboard auto-incident incident alert-webhook intake answer intake-confirm assign up down smoke release promote evidence provenance watch request resume audit genesis epics spec-split m24-check"
json_mode=""
if [ -z "${LIGHTS_OUT_JSON_INNER:-}" ] && member "$sub" "$json_cmds"; then
  nargs=$#
  i=0
  while [ "$i" -lt "$nargs" ]; do
    a=$1
    shift
    i=$((i + 1))
    case "$a" in
      --json|--jsonl)
        [ -z "$json_mode" ] || [ "$json_mode" = "$a" ] || nogo "--json and --jsonl are mutually exclusive"
        json_mode=$a ;;
      *) set -- "$@" "$a" ;;
    esac
  done
fi
if [ -n "$json_mode" ]; then
  rc=0
  inner=$(LIGHTS_OUT_JSON_INNER=1 sh "$0" "$sub" "$@") || rc=$?
  rc_env=0
  if [ -n "$inner" ]; then printf '%s\n' "$inner"; fi | python3 core/cli.py envelope "$json_mode" --command "$sub" --rc "$rc" -- "$@" || rc_env=$?
  exit "$rc_env"
fi

case "$sub" in
  init|gates|test-proof|test-integrity|adr-check|migration-safety) ;;
  genesis|epics|spec-split|m24-check|triage|postmortem|next|dora|spend|cloud-setup|map|network|scoreboard|auto-incident|incident|alert-webhook|top|start|intake|answer|intake-confirm|assign|up|down|smoke|release|promote|console|evidence|provenance|try|watch|request|resume|audit|production-readiness)
    # The gates image has no ADK, compose, cosign, or full platform runtime (docs/adr/0057 §3, 0105).
    [ "${LIGHTS_OUT_FLAVOUR:-full}" != gates ] \
      || nogo "this is the gates image; $sub runs in ghcr.io/nicolaguglielmi/lights-out:${LIGHTS_OUT_VERSION:-<version>} (LIGHTS_OUT_IMAGE)" ;;
  version)
    printf 'lights-out %s (%s, %s)\n' "${LIGHTS_OUT_VERSION:-?}" "${LIGHTS_OUT_FLAVOUR:-full}" "${LIGHTS_OUT_REVISION:-?}"
    exit 0 ;;
  *)
    sed -n 's/^#   //p' "$0" >&2
    # shellcheck disable=SC2034 # consumed by nogo in sourced ci/lib.sh
    name=lights-out
    nogo "unknown command '$sub'; one of: init genesis gates test-proof test-integrity adr-check migration-safety triage postmortem version next dora spend cloud-setup map network scoreboard auto-incident incident alert-webhook top epics spec-split m24-check start intake answer intake-confirm assign up down smoke release promote console evidence provenance try watch request resume audit production-readiness; or a domain (repo ticket release ops config) and a verb" ;;
esac

host_out=${LIGHTS_OUT_HOST_OUT:-/out}
out_root=${LIGHTS_OUT_CONTAINER_OUT:-/out}
# This image is the workbench. For plane commands the wrapper sets
# WORKBENCH_IMAGE and mounts the socket; otherwise compose render only needs a name.
WORKBENCH_IMAGE=${WORKBENCH_IMAGE:-ghcr.io/nicolaguglielmi/lights-out:${LIGHTS_OUT_VERSION:-0.0.0}}
export WORKBENCH_IMAGE

staged=""
cli_args=""
cli_sep=$(printf '\037')
has_non_interactive=0
want_non_interactive=0
init_apply=0
has_genesis_repo=0
has_model_provider=0
provider_val=""
reason_val=""

resolve_repo_path() {
  case "$1" in
    .) printf '%s' "$repo_dir" ;;
    /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$repo_dir" "$1" ;;
  esac
}

# POSIX flags to KEY=VALUE (docs/adr/0112 §2); the flags a command owns pass through untouched.
nargs=$#
i=0
while [ "$i" -lt "$nargs" ]; do
  a=$1
  shift
  i=$((i + 1))
  case "$a" in
    --json|--jsonl|--engine|--non-interactive|--apply|--commit|--force|--workspace|--no-commit|--no-publish|--in-place|--allow-incomplete|--replace) set -- "$@" "$a" ;;
    --staged) set -- "$@" STAGED=1 ;;
    --once) set -- "$@" ONCE=1 ;;
    --*=*)
      flag_name=${a%%=*}
      if native_flag "$sub" "$flag_name"; then
        set -- "$@" "$a"
      else
        flag_name=${flag_name#--}
        set -- "$@" "$(printf '%s' "$flag_name" | tr 'a-z-' 'A-Z_')=${a#*=}"
      fi ;;
    --*)
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1
      shift
      i=$((i + 1))
      if native_flag "$sub" "$a"; then
        set -- "$@" "$a" "$v"
      else
        flag_name=${a#--}
        set -- "$@" "$(printf '%s' "$flag_name" | tr 'a-z-' 'A-Z_')=$v"
      fi ;;
    *) set -- "$@" "$a" ;;
  esac
done

nargs=$#
i=0
while [ "$i" -lt "$nargs" ]; do
  a=$1
  shift
  i=$((i + 1))
  case "$a" in
    STAGED=*) staged=${a#STAGED=}; continue ;;
    ALERT=/*|INCIDENT=/*) ;;
    ALERT=*) a="ALERT=$repo_dir/${a#ALERT=}" ;;
    INCIDENT=*) a="INCIDENT=$repo_dir/${a#INCIDENT=}" ;;
    "ALERT_FILE="/*) ;;
    "ALERT_FILE="*) a="ALERT_FILE=$repo_dir/${a#ALERT_FILE=}" ;;
    "LOGBOOK_FILE="/*) ;;
    "LOGBOOK_FILE="*)
      lpath=${a#LOGBOOK_FILE=}
      [ ! -e "$repo_dir/$lpath" ] || a="LOGBOOK_FILE=$repo_dir/$lpath" ;;
    "LOGBOOK="/*) ;;
    "LOGBOOK="*)
      lpath=${a#LOGBOOK=}
      [ ! -e "$repo_dir/$lpath" ] || a="LOGBOOK=$repo_dir/$lpath" ;;
    "TICKET_FILE="/*) ;;
    "TICKET_FILE="*)
      tpath=${a#TICKET_FILE=}
      [ ! -e "$repo_dir/$tpath" ] || a="TICKET_FILE=$repo_dir/$tpath" ;;
    DECISIONS=@/*) ;;
    DECISIONS=@*)
      dpath=${a#DECISIONS=@}
      if [ -e "$repo_dir/$dpath" ]; then a="DECISIONS=@$repo_dir/$dpath"; fi ;;
    MODEL_PROVIDER=*) has_model_provider=1 ;;
    PROVIDER=*)
      provider_val=${a#PROVIDER=}
      if [ "$sub" = start ]; then
        cli_args="$cli_args${cli_args:+$cli_sep}--provider${cli_sep}${provider_val}"
        want_non_interactive=1
      fi ;;
    PRESET=*)
      if [ "$sub" = start ]; then
        cli_args="$cli_args${cli_args:+$cli_sep}--preset${cli_sep}${a#PRESET=}"
        want_non_interactive=1
      fi ;;
    AGENT_HARNESS=*)
      if [ "$sub" = start ]; then
        cli_args="$cli_args${cli_args:+$cli_sep}--harness${cli_sep}${a#AGENT_HARNESS=}"
        want_non_interactive=1
      fi ;;
    DELIVERY=*)
      if [ "$sub" = start ]; then
        cli_args="$cli_args${cli_args:+$cli_sep}--delivery${cli_sep}${a#DELIVERY=}"
        want_non_interactive=1
      fi ;;
    PROFILE=*)
      if [ "$sub" = start ]; then
        cli_args="$cli_args${cli_args:+$cli_sep}--profile${cli_sep}${a#PROFILE=}"
      fi ;;
    MODE=*)
      if [ "$sub" = start ] || [ "$sub" = next ]; then
        cli_args="$cli_args${cli_args:+$cli_sep}--mode${cli_sep}${a#MODE=}"
      fi ;;
    SPEND_BUDGET_TICKET_USD=*)
      if [ "$sub" = start ]; then
        cli_args="$cli_args${cli_args:+$cli_sep}--ticket-budget-usd${cli_sep}${a#SPEND_BUDGET_TICKET_USD=}"
      fi ;;
    SPEND_BUDGET_DAY_USD=*)
      if [ "$sub" = start ]; then
        cli_args="$cli_args${cli_args:+$cli_sep}--day-budget-usd${cli_sep}${a#SPEND_BUDGET_DAY_USD=}"
      fi ;;
    REASON=*) reason_val=${a#REASON=} ;;
    BASE=*|HEAD=*|JUDGED=*|MODEL=*|TICKET=*|TEXT=*|ANSWER=*|DECISIONS=*|VERSION=*|CANARY_VERSION=*|CANARY_TAG=*|GO_FOR_LAUNCH=*|CLOUD=*|CODE_HOST=*|ADAPTERS_PATH=*|WINDOW_DAYS=*) ;;
    --json)
      case "$sub" in start|next|top) cli_args="$cli_args${cli_args:+$cli_sep}$a"; continue ;; esac
      nogo "'$a' is not offered for $sub: a gate keeps its one-line GO/NO-GO contract, and start, next, and top keep their native --json (docs/adr/0112 §5)" ;;
    --jsonl)
      nogo "'$a' is not offered for $sub: a gate keeps its one-line GO/NO-GO contract, and start, next, and top keep their native --json (docs/adr/0112 §5)" ;;
    --mode=*)
      case "$sub" in start|next) cli_args="$cli_args${cli_args:+$cli_sep}$a"; continue ;; esac
      nogo "'$a' is only valid with start or next" ;;
    --mode)
      case "$sub" in start|next) ;; *) nogo "'$a' is only valid with start or next" ;; esac
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --non-interactive)
      [ "$sub" = start ] || nogo "'$a' is only valid with start"
      has_non_interactive=1
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --preset=*|--provider=*|--harness=*|--delivery=*|--profile=*|--ticket-budget-usd=*|--day-budget-usd=*)
      [ "$sub" = start ] || nogo "'$a' is only valid with start"
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --preset|--provider|--harness|--delivery|--profile|--ticket-budget-usd|--day-budget-usd)
      [ "$sub" = start ] || nogo "'$a' is only valid with start"
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --apply|--commit|--force|--workspace)
      [ "$sub" = init ] || nogo "'$a' is only valid with init"
      [ "$a" != --apply ] || init_apply=1
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --branch=*|--baseline-max-files=*)
      [ "$sub" = init ] || nogo "'$a' is only valid with init"
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --branch|--baseline-max-files)
      [ "$sub" = init ] || nogo "'$a' is only valid with init"
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --baseline-mode=*)
      case "$sub" in init|epics) ;; *) nogo "'$a' is only valid with init or epics" ;; esac
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --baseline-mode)
      case "$sub" in init|epics) ;; *) nogo "'$a' is only valid with init or epics" ;; esac
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --no-commit|--brief=*|--stack=*|--name=*|--port=*|--tickets-count=*)
      [ "$sub" = genesis ] || nogo "'$a' is only valid with genesis"
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --brief-file=*)
      [ "$sub" = genesis ] || nogo "'$a' is only valid with genesis"
      v=$(resolve_repo_path "${a#--brief-file=}")
      cli_args="$cli_args${cli_args:+$cli_sep}--brief-file=$v"
      continue ;;
    --brief|--stack|--name|--port|--tickets-count)
      [ "$sub" = genesis ] || nogo "'$a' is only valid with genesis"
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --brief-file)
      [ "$sub" = genesis ] || nogo "'$a' is only valid with genesis"
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$(resolve_repo_path "$1"); shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --no-publish|--id=*|--title=*|--tickets=*|--tickets-dir=*|--state-dir=*|--manifest=*|--changed=*)
      [ "$sub" = epics ] || nogo "'$a' is only valid with epics"
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --id|--title|--tickets|--tickets-dir|--state-dir|--manifest|--changed)
      [ "$sub" = epics ] || nogo "'$a' is only valid with epics"
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --out=*)
      case "$sub" in epics|m24-check) ;; *) nogo "'$a' is only valid with epics or m24-check" ;; esac
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --out)
      case "$sub" in epics|m24-check) ;; *) nogo "'$a' is only valid with epics or m24-check" ;; esac
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --in-place)
      [ "$sub" = spec-split ] || nogo "'$a' is only valid with spec-split"
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --allow-incomplete|--replace|--min-providers=*|--handoff=*|--scope=*|--repository=*|--kind=*|--phase=*)
      [ "$sub" = m24-check ] || nogo "'$a' is only valid with m24-check"
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --min-providers|--handoff|--scope|--repository|--kind|--phase)
      [ "$sub" = m24-check ] || nogo "'$a' is only valid with m24-check"
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --data=*|--logbook=*|--logbooks=*)
      [ "$sub" = m24-check ] || nogo "'$a' is only valid with m24-check"
      name=${a%%=*}; v=${a#*=}
      [ ! -e "$repo_dir/$v" ] || v=$(resolve_repo_path "$v")
      cli_args="$cli_args${cli_args:+$cli_sep}$name=$v"
      continue ;;
    --data|--logbook|--logbooks)
      [ "$sub" = m24-check ] || nogo "'$a' is only valid with m24-check"
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      [ ! -e "$repo_dir/$v" ] || v=$(resolve_repo_path "$v")
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --repo=*)
      case "$sub" in genesis|epics) ;; *) nogo "'$a' is only valid with genesis or epics" ;; esac
      v=${a#--repo=}
      if [ "$sub" = genesis ]; then
        has_genesis_repo=1
        v=$(resolve_repo_path "$v")
      fi
      cli_args="$cli_args${cli_args:+$cli_sep}--repo=$v"
      continue ;;
    --repo)
      case "$sub" in genesis|epics) ;; *) nogo "'$a' is only valid with genesis or epics" ;; esac
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      if [ "$sub" = genesis ]; then
        has_genesis_repo=1
        v=$(resolve_repo_path "$v")
      fi
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --today=*|--stacks=*)
      case "$sub" in genesis|epics) ;; *) nogo "'$a' is only valid with genesis or epics" ;; esac
      cli_args="$cli_args${cli_args:+$cli_sep}$a"
      continue ;;
    --today|--stacks)
      case "$sub" in genesis|epics) ;; *) nogo "'$a' is only valid with genesis or epics" ;; esac
      [ "$i" -lt "$nargs" ] || nogo "'$a' requires a value"
      v=$1; shift; i=$((i + 1))
      cli_args="$cli_args${cli_args:+$cli_sep}$a${cli_sep}$v"
      continue ;;
    --engine)
      case "$sub" in
        init|gates|test-proof|test-integrity|adr-check|migration-safety)
          LIGHTS_OUT_ENGINE_GATES=1
          export LIGHTS_OUT_ENGINE_GATES
          continue ;;
        *) nogo "'$a' is only valid with init, gates, test-proof, test-integrity, adr-check, or migration-safety" ;;
      esac ;;
    [A-Z]*=*)
      member "${a%%=*}" "$(extra_keys_for "$sub")" \
        || nogo "'${a%%=*}=' is not an argument lights-out takes; one of BASE= HEAD= JUDGED= STAGED= ALERT= INCIDENT= PROFILE= MODEL_PROVIDER= MODEL= TICKET= TEXT= ANSWER= DECISIONS= VERSION= CANARY_VERSION= CANARY_TAG= GO_FOR_LAUNCH= REASON= MODE= PRESET= CLOUD= DELIVERY= CODE_HOST= AGENT_HARNESS= PROVIDER= ADAPTERS_PATH= WINDOW_DAYS= SPEND_BUDGET_TICKET_USD= SPEND_BUDGET_DAY_USD= $(extra_keys_for "$sub")" ;;
    -*) nogo "'${a%%=*}' is not an argument lights-out takes; one of BASE= HEAD= JUDGED= STAGED= ALERT= INCIDENT= PROFILE= MODEL_PROVIDER= MODEL= TICKET= TEXT= ANSWER= DECISIONS= VERSION= CANARY_VERSION= CANARY_TAG= GO_FOR_LAUNCH= REASON= MODE= PRESET= CLOUD= DELIVERY= CODE_HOST= AGENT_HARNESS= PROVIDER= ADAPTERS_PATH= WINDOW_DAYS= SPEND_BUDGET_TICKET_USD= SPEND_BUDGET_DAY_USD= $(extra_keys_for "$sub")" ;;
    *)
      case "$sub" in
        genesis)
          has_genesis_repo=1
          v=$(resolve_repo_path "$a")
          cli_args="$cli_args${cli_args:+$cli_sep}$v"
          continue ;;
        epics)
          cli_args="$cli_args${cli_args:+$cli_sep}$a"
          continue ;;
        spec-split)
          v=$a
          case "$a" in
            check|split|compose) ;;
            *) [ ! -e "$repo_dir/$a" ] || v=$(resolve_repo_path "$a") ;;
          esac
          cli_args="$cli_args${cli_args:+$cli_sep}$v"
          continue ;;
        m24-check)
          v=$a
          case "$a" in
            evaluate|ingest-logbook) ;;
            *) [ ! -e "$repo_dir/$a" ] || v=$(resolve_repo_path "$a") ;;
          esac
          cli_args="$cli_args${cli_args:+$cli_sep}$v"
          continue ;;
        *) nogo "'${a%%=*}' is not an argument lights-out takes; one of BASE= HEAD= JUDGED= STAGED= ALERT= INCIDENT= PROFILE= MODEL_PROVIDER= MODEL= TICKET= TEXT= ANSWER= DECISIONS= VERSION= CANARY_VERSION= CANARY_TAG= GO_FOR_LAUNCH= REASON= MODE= PRESET= CLOUD= DELIVERY= CODE_HOST= AGENT_HARNESS= PROVIDER= ADAPTERS_PATH= WINDOW_DAYS= SPEND_BUDGET_TICKET_USD= SPEND_BUDGET_DAY_USD= $(extra_keys_for "$sub")" ;;
      esac ;;
  esac
  set -- "$@" "$a"
done

if [ "${LIGHTS_OUT_ENGINE_GATES:-0}" = 1 ] && [ "${LIGHTS_OUT_FLAVOUR:-full}" = gates ]; then
  nogo "this is the gates image; $sub --engine runs in ghcr.io/nicolaguglielmi/lights-out:${LIGHTS_OUT_VERSION:-<version>} (LIGHTS_OUT_IMAGE)"
fi

if [ "$sub" = start ] && [ "$want_non_interactive" = 1 ] && [ "$has_non_interactive" = 0 ]; then
  cli_args="--non-interactive${cli_args:+$cli_sep}$cli_args"
fi
if [ -n "$cli_args" ]; then
  LIGHTS_OUT_CLI_ARGS=$cli_args
  export LIGHTS_OUT_CLI_ARGS
fi
if [ -n "$provider_val" ] && [ "$has_model_provider" = 0 ]; then
  set -- "$@" "MODEL_PROVIDER=$provider_val"
fi
if [ "$sub" = promote ] && [ -n "$reason_val" ]; then
  set -- "$@" "GO_FOR_LAUNCH_REASON=$reason_val"
fi

# At L0 the roles replay lights-out's own recordings only (docs/adr/0045
# §3): on any other alert or incident the NO-GO says what runs it live.
role() {
  out=$(make --no-print-directory "$@") && { printf '%s\n' "$out"; exit 0; }
  if [ "${PROFILE:-l0}" = l0 ]; then
    out="$out; at L0 only lights-out's own recordings replay: on yours, PROFILE=l1 with GEMINI_API_KEY in your environment (ANTHROPIC_API_KEY, OPENAI_API_KEY or ZAI_API_KEY with MODEL_PROVIDER=anthropic, openai or zai)"
  fi
  printf '%s\n' "$out"
  exit 1
}

case "$sub" in
  triage)
    role triage TRIAGE_OUT=/out/triage "$@" ;;
  postmortem)
    mkdir -p /out/tickets
    role postmortem POSTMORTEM_OUT=/out/postmortem TICKETS_DIR=/out/tickets "$@" ;;
esac

run_make() {
  rc=0
  make --no-print-directory "$@" || rc=$?
  [ "$rc" = 0 ] && exit 0
  exit 1
}

# Host-mirrored platform staging (docs/adr/0105 §3, 0108 §2): when LIGHTS_OUT_PLATFORM_DIR
# is set, populate it from $image_root when .platform-revision differs, link
# .crew -> LIGHTS_OUT_CREW_DIR, cd into it, attach $repo_dir, and run make.
gate_attached=""
if [ -n "${LIGHTS_OUT_PLATFORM_DIR:-}" ]; then
  plat=$LIGHTS_OUT_PLATFORM_DIR
  crew=${LIGHTS_OUT_CREW_DIR:-$plat/.crew}
  rev="${LIGHTS_OUT_VERSION:-?}:${LIGHTS_OUT_REVISION:-?}:${LIGHTS_OUT_FLAVOUR:-full}"
  mkdir -p "$plat" "$crew"
  cur_rev=$(cat "$plat/.platform-revision" 2>/dev/null || true)
  if [ "$cur_rev" != "$rev" ]; then
    for p in "$plat"/* "$plat"/.[!.]* "$plat"/..?*; do
      [ -e "$p" ] || [ -L "$p" ] || continue
      case "$(basename "$p")" in
        .crew|.attached|.gates-attached) continue ;;
      esac
      rm -rf "$p"
    done
    (cd "$image_root" && tar cf - --exclude='./.crew' --exclude='./.attached' --exclude='./.gates-attached' --exclude='./.gates-out' .) \
      | (cd "$plat" && tar xf -)
    printf '%s\n' "$rev" > "$plat/.platform-revision"
  fi
  rm -rf "$plat/.crew"
  ln -s "$crew" "$plat/.crew"
  cd "$plat"

  case "$sub" in
    init|gates|test-proof|test-integrity|adr-check|migration-safety)
      gate_attached="$plat/.gates-attached"
      REMOTE_ENV_OUT=/tmp/lights-out/gates-remote.env
      LIGHTS_OUT_NO_START_PLAN=1
      export REMOTE_ENV_OUT LIGHTS_OUT_NO_START_PLAN ;;
    *)
      if [ "$sub" = cloud-setup ]; then
        run_make cloud-setup "$@"
      fi

      attached="$plat/.attached"
      has_repo=0
      if sh core/await-head.sh "$repo_dir" 15; then
        has_repo=1
      else
        case "$sub" in
          down|console) ;;
          *) nogo "the current directory is not a git repository with a commit" ;;
        esac
      fi

      if [ "$has_repo" = 1 ]; then
        saved_tickets=""
        if [ -d "$attached/.git" ] && [ -d "$attached/.lights-out/tickets" ]; then
          untracked=$(git -C "$attached" ls-files --others --exclude-standard -- .lights-out/tickets 2>/dev/null || true)
          if [ -n "$untracked" ]; then
            saved_tickets=$(mktemp -d)
            for tf in $untracked; do
              mkdir -p "$saved_tickets/$(dirname "$tf")"
              cp "$attached/$tf" "$saved_tickets/$tf"
            done
          fi
        fi
        rm -rf "$attached"
        sh core/attach.sh "$repo_dir" "$attached" || {
          [ -z "$saved_tickets" ] || rm -rf "$saved_tickets"
          nogo "could not clone the repository (see above)"
        }
        if [ -n "$saved_tickets" ]; then
          for sf in "$saved_tickets"/.lights-out/tickets/*; do
            [ -f "$sf" ] || continue
            base_sf=$(basename "$sf")
            [ -e "$attached/.lights-out/tickets/$base_sf" ] || {
              mkdir -p "$attached/.lights-out/tickets"
              cp "$sf" "$attached/.lights-out/tickets/$base_sf"
            }
          done
          rm -rf "$saved_tickets"
        fi
      fi

      if [ "$has_repo" = 1 ] && [ -f "$attached/.lights-out/workload.env" ]; then
        run_make "$sub" WORKLOAD=.attached ATTACHED=.attached "$@"
      else
        case "$sub" in
          start|try)
            run_make "$sub" WORKLOAD=.attached ATTACHED=.attached "$@" ;;
          down|console|promote|evidence|provenance|production-readiness|incident)
            run_make "$sub" ATTACHED=/tmp/lights-out/no-attached WORKLOAD=app "$@" ;;
          *)
            nogo "the repository has no .lights-out/workload.env committed; lights-out init drafts one" ;;
        esac
      fi ;;
  esac
fi

# State-only commands (docs/adr/0105 §1): no socket, /opt/lights-out/.crew
# mounted from $out/crew. cloud-setup skips attach; next/dora/spend attach
# $repo_dir when available.
case "$sub" in
  cloud-setup|scoreboard|auto-incident|incident|alert-webhook|top|m24-check)
    run_make "$sub" "$@" ;;
  next|dora|spend|map|network|epics|spec-split)
    attached=/tmp/lights-out/attached
    mkdir -p /tmp/lights-out
    if sh core/await-head.sh "$repo_dir" 15; then
      rm -rf "$attached"
      sh core/attach.sh "$repo_dir" "$attached" || nogo "could not clone the repository (see above)"
      if member "$sub" "next map epics spec-split" || [ -f "$attached/.lights-out/workload.env" ]; then
        run_make "$sub" WORKLOAD="$attached" ATTACHED="$attached" "$@"
      fi
      run_make "$sub" WORKLOAD=app ATTACHED=/tmp/lights-out/no-attached "$@"
    else
      ! member "$sub" "next map" || nogo "the current directory is not a git repository with a commit"
      run_make "$sub" WORKLOAD=app ATTACHED=/tmp/lights-out/no-attached "$@"
    fi ;;
esac

# Greenfield genesis writes directly into the mounted read-write $repo_dir before any commit exists.
if [ "$sub" = genesis ]; then
  if [ "$has_genesis_repo" = 0 ]; then
    cli_args="--repo${cli_sep}$repo_dir${cli_args:+$cli_sep}$cli_args"
    LIGHTS_OUT_CLI_ARGS=$cli_args
    export LIGHTS_OUT_CLI_ARGS
  fi
  LIGHTS_OUT_NO_START_PLAN=1
  export LIGHTS_OUT_NO_START_PLAN
  run_make genesis WORKLOAD=app ATTACHED=/tmp/lights-out/no-attached "$@"
fi

# init and gates: the repository's HEAD, cloned where only this container
# sees it (docs/adr/0037 §1: lights-out reads the repository, never writes it).
# Always ignore .crew/start-plan.env so planned cloud adapters do not trip ADMIT.
# On a podman machine /repo can lag the host for a few seconds after a
# commit or a git switch: core/await-head.sh waits for HEAD, bounded.
LIGHTS_OUT_NO_START_PLAN=1
export LIGHTS_OUT_NO_START_PLAN
sh core/await-head.sh "$repo_dir" 15 \
  || nogo "the current directory is not a git repository with a commit"
attached=${gate_attached:-/tmp/lights-out/attached}
if [ "$sub" = init ] && [ "$init_apply" = 1 ]; then
  # Zero-copy init --apply: wrapper mounted $repo_dir read-write; skip clone and apply directly.
  rm -rf "$out_root/init"
  from=$(git -C "$repo_dir" rev-parse --short HEAD)
  out=$(make --no-print-directory init WORKLOAD="$repo_dir" ATTACHED="$repo_dir") && rc=0 || rc=$?
  if [ -d "$repo_dir/.lights-out" ] && { [ -d "$out_root" ] || mkdir -p "$out_root" 2>/dev/null; }; then
    rm -rf "$out_root/.lights-out" 2>/dev/null || :
    cp -R "$repo_dir/.lights-out" "$out_root/.lights-out" 2>/dev/null || :
  fi
  line=$(printf '%s\n' "$out" | tail -n 1 | sed -e "s|$repo_dir/\.lights-out|.lights-out|g" -e "s|$repo_dir|the repository|g")
  if mkdir -p "$out_root/init" 2>/dev/null; then
    # shellcheck disable=SC2016 # the backticks are Markdown, not a command
    {
      printf '### lights-out init --apply, from %s\n\n`%s`\n' "$from" "$line"
      if [ "$rc" != 0 ]; then
        printf '\nno draft was applied.\n'
      fi
    } > "$out_root/init/summary.md" 2>/dev/null || :
  fi
  printf '%s\n' "$out" | sed -e "s|$repo_dir/\.lights-out|.lights-out|g" -e "s|$repo_dir|the repository|g"
  [ "$rc" = 0 ] || exit 1
  exit 0
fi
mkdir -p "$(dirname "$attached")"
rm -rf "$attached"
sh core/attach.sh "$repo_dir" "$attached" || nogo "could not clone the repository (see above)"

if [ "$sub" = init ]; then
  # HEAD=<commit> drafts from that commit (docs/adr/0064 §3): the Action
  # drafts from the base, the commit whose contract would judge.
  rm -rf "$out_root/init"
  for a do
    case "$a" in
      HEAD=*)
        h=${a#HEAD=}
        git -C "$attached" checkout -q --detach "$h^{commit}" 2>/dev/null \
          || nogo "HEAD=$h is not a commit of the repository (a shallow checkout? fetch-depth: 0)" ;;
    esac
  done
  from=$(git -C "$attached" rev-parse --short HEAD)
  out=$(make --no-print-directory init WORKLOAD="$attached" ATTACHED="$attached") && rc=0 || rc=$?
  if [ -d "$attached/.lights-out" ]; then
    rm -rf "$out_root/.lights-out"
    cp -R "$attached/.lights-out" "$out_root/.lights-out"
  fi
  # init/summary.md, what the Action posts (docs/adr/0064 §3): the line and,
  # on GO, the three small files and the runners by size. No path of this
  # container or of the host: the clone is the repository, the draft .lights-out.
  line=$(printf '%s\n' "$out" | tail -n 1 | sed -e "s|$attached/\.lights-out|.lights-out|g" -e "s|$attached|the repository|g")
  mkdir -p "$out_root/init"
  # shellcheck disable=SC2016 # the backticks are Markdown, not a command
  {
    printf '### lights-out init, drafted from %s\n\n`%s`\n' "$from" "$line"
    if [ "$rc" = 0 ]; then
      for f in workload.env policy.env worker.md; do
        [ -f "$out_root/.lights-out/$f" ] || continue
        # A fence one backtick longer than any run in the file: it cannot close early.
        fence=$(awk '{ while (match($0, /`+/)) { if (RLENGTH > n) n = RLENGTH; $0 = substr($0, RSTART + RLENGTH) } }
          END { n = (n < 3 ? 3 : n + 1); while (n--) printf "`" }' "$out_root/.lights-out/$f")
        printf '\n`.lights-out/%s`:\n\n%s\n' "$f" "$fence"
        cat "$out_root/.lights-out/$f"
        printf '%s\n' "$fence"
      done
      if [ -f "$out_root/.lights-out/compose.yaml" ]; then
        printf '\nPlane half (`.lights-out/`):\n\n'
        for f in compose.yaml flags/flags.json smoke.sh load.sh; do
          [ -f "$out_root/.lights-out/$f" ] || continue
          printf -- '- `.lights-out/%s`, %s bytes\n' "$f" "$(wc -c < "$out_root/.lights-out/$f" | tr -d ' ')"
        done
      fi
      if [ -d "$out_root/.lights-out/runners" ]; then
        stack=${line#GO: init — }
        printf '\n`.lights-out/runners/`, the %s stack'"'"'s runners as lights-out ships them:\n\n' "${stack%%,*}"
        (cd "$out_root/.lights-out" && find runners -type f | LC_ALL=C sort | while read -r f; do
          printf -- '- `.lights-out/%s`, %s bytes\n' "$f" "$(wc -c < "$f" | tr -d ' ')"
        done)
      fi
    else
      printf '\nno draft is offered.\n'
    fi
  } > "$out_root/init/summary.md" 2>/dev/null || :
  printf '%s\n' "$out" | sed -e "s|$attached/.lights-out|$host_out/.lights-out|g" -e "s|$attached|the repository|g"
  [ "$rc" = 0 ] || exit 1
  exit 0
fi

base=""; head=""
for a do
  case "$a" in BASE=*) base=${a#BASE=} ;; HEAD=*) head=${a#HEAD=} ;; esac
done
if [ -n "$head" ]; then
  git -C "$attached" checkout -q --detach "$head" 2>/dev/null \
    || nogo "HEAD=$head is not a commit of the repository (a shallow checkout? fetch-depth: 0)"
fi
# No contract at the head (docs/adr/0064 §2): a change that removes the one
# its base has is refused as such, with no draft; with none at the base
# either, or no base, it is the first run, and the Action drafts on that
# line, matched exactly. STAGED=1 judges on HEAD: its base is HEAD itself.
if ! git -C "$attached" cat-file -e HEAD:.lights-out/workload.env 2>/dev/null; then
  if [ -z "$staged" ] || [ "$staged" = 0 ]; then
    from=$base
    [ -n "$from" ] || from=$(git -C "$attached" rev-parse -q --verify HEAD~1 2>/dev/null) || from=""
    if [ -n "$from" ] && git -C "$attached" cat-file -e "$from:.lights-out/workload.env" 2>/dev/null; then
      nogo "the change removes .lights-out/workload.env, which its base has; a change cannot remove the contract that judges it"
    fi
  fi
  nogo "the repository has no .lights-out/workload.env committed; lights-out init drafts one"
fi

if [ -n "$staged" ] && [ "$staged" != 0 ]; then
  [ -z "$base$head" ] || nogo "STAGED=1 judges the staged changes on HEAD; it takes no BASE or HEAD"
  patch=/tmp/lights-out/staged.patch
  # The wrapper writes the patch with the host's git (a mounted .git/index
  # can be stale right after `git add`); without one, read it here.
  if [ -f "$out_root/staged.patch" ]; then
    cp "$out_root/staged.patch" "$patch"
  else
    git -C "$repo_dir" diff --cached --binary > "$patch"
  fi
  [ -s "$patch" ] || nogo "STAGED=1, and nothing is staged"
  git -C "$attached" apply --index "$patch" >&2 || nogo "the staged changes do not apply on HEAD"
  git -C "$attached" -c user.name=lights-out -c user.email=lights-out@localhost.invalid -c commit.gpgsign=false \
    commit -q --no-verify -m "lights-out: the staged changes, judged before they are committed" \
    || nogo "could not commit the staged changes in the clone"
  printf '[lights-out] judging the staged changes as one commit on %s\n' "$(git -C "$repo_dir" rev-parse --short HEAD)" >&2
fi

# A range is judged by its base (docs/adr/0045 §3, ADR 0033): with a
# contract at the base, its workload.env, policy.env and runners/ replace
# the head's, no-overlay, so a change cannot loosen what judges it. The
# base is the one make gates would pick: BASE, else the parent of HEAD.
if [ -n "$base" ]; then
  git -C "$attached" rev-parse -q --verify "$base^{commit}" >/dev/null 2>&1 \
    || nogo "BASE=$base is not a commit of the repository (a shallow checkout? fetch-depth: 0)"
  judge=$base
else
  judge=$(git -C "$attached" rev-parse -q --verify HEAD~1 2>/dev/null) || judge=""
fi
if [ -n "$judge" ] && git -C "$attached" cat-file -e "$judge:.lights-out/workload.env" 2>/dev/null; then
  present=""
  for j in .lights-out/workload.env .lights-out/policy.env .lights-out/runners; do
    if git -C "$attached" cat-file -e "$judge:$j" 2>/dev/null || [ -e "$attached/$j" ]; then
      present="$present $j"
    fi
  done
  # shellcheck disable=SC2086 # a list of paths, split on purpose
  git -C "$attached" restore --source="$judge" --worktree -- $present \
    || nogo "could not take the contract from the base $judge"
  printf '[lights-out] the contract that judges is the base'"'"'s, %s\n' "$(git -C "$attached" rev-parse --short "$judge")" >&2
else
  printf '[lights-out] no contract at the base: the head'"'"'s own .lights-out/ judges it\n' >&2
fi

case "$sub" in
  gates|test-proof|test-integrity)
    toolchain=$(sed -n 's/^TOOLCHAIN_IMAGE=//p' "$attached/.lights-out/workload.env" | tail -1)
    if [ "$toolchain" != workbench ] && [ "${LIGHTS_OUT_ENGINE_GATES:-0}" != 1 ]; then
      nogo "TOOLCHAIN_IMAGE=$toolchain needs the engine socket, which lights-out omits by default (docs/adr/0045 §3, 0108); run lights-out gates --engine, ./go gates from a clone, or set TOOLCHAIN_IMAGE=workbench"
    fi ;;
esac

# gates runs the eight; test-proof and test-integrity (docs/adr/0057 §5) the
# same target on a subset, its one line renamed for the subcommand: GO joins
# the gates' own details, NO-GO names the gate that stopped it. test runs
# before test-proof: a test-proof GO (the changed tests fail on the base)
# proves nothing when they fail on the head too.
case "$sub" in
  gates) subset=$(sed -n 's/^GATES := //p' Makefile) ;;
  test-proof) subset="test test-proof" ;;
  test-integrity) subset="test-weakening mutation" ;;
  adr-check) subset="adr-check" ;;
  migration-safety) subset="migration-safety" ;;
esac
out=$(make --no-print-directory gates WORKLOAD="$attached" ATTACHED="$attached" GATES_OUT="$out_root/gates" GATES="$subset" "$@") && rc=0 || rc=$?
case "$out" in
  "GO: gates"*) [ "$rc" = 0 ] || out="NO-GO: gates — the gates exited $rc after a GO line: $out" ;;
  "NO-GO: gates — "*) ;;
  *) out="NO-GO: gates — the gates gave no verdict: $out" ;;
esac
if [ "$sub" = gates ]; then
  line=$out
else
  case "$out" in
    "NO-GO: gates — "*)
      # "<gate>: NO-GO: <gate> — <reason>" reads "<gate>: <reason>".
      line="NO-GO: $sub — $(printf '%s' "${out#NO-GO: gates — }" | sed 's/^\([a-z-]*\): NO-GO: \1 — /\1: /')" ;;
    *)
      details=$(sed -n 's/^GO: [a-z-]* — //p' "$out_root/gates/lines" 2>/dev/null | awk 'NR > 1 { printf "; " } { printf "%s", $0 }')
      line="GO: $sub — ${details:-$subset GO}" ;;
  esac
fi

# The summary the Action posts (docs/adr/0057 §6): the line, one line per
# gate in the order run, the gates not run, and the first 20 lines of a
# NO-GO's details (20: a decision, the survivors a changed-line run leaves).
mkdir -p "$out_root/gates" 2>/dev/null || :
# shellcheck disable=SC2016 # the backticks are Markdown, not a command
{
  printf '### lights-out %s\n\n`%s`\n\n' "$sub" "$line"
  for g in $subset; do
    l=$(grep -E "^(NO-)?GO: $g( |\$)" "$out_root/gates/lines" 2>/dev/null | head -n 1) || l=""
    if [ -n "$l" ]; then printf -- '- `%s`\n' "$l"; else printf -- '- %s: not run, stopped at the first NO-GO\n' "$g"; fi
  done
  for f in "$out_root/gates"/*.details; do
    [ -s "$f" ] || continue
    printf '\n%s, details (first 20 lines):\n\n```\n' "$(basename "$f" .details)"
    head -n 20 "$f"
    printf '```\n'
  done
} > "$out_root/gates/summary.md" 2>/dev/null || :

printf '%s\n' "$line"
case "$line" in GO:*) exit 0 ;; *) exit 1 ;; esac
