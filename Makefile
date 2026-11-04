# SPDX-License-Identifier: Apache-2.0
#
# Runs inside the workbench container. Use ./go <target> from the host.
# Profiles select the adapters (docs/adr/0001-profiles-and-single-makefile.md).

SHELL := /bin/sh
.DEFAULT_GOAL := help

PROFILE ?= l0
include profiles/$(PROFILE).env
-include .env

# The Workload port (docs/adr/0030 §1): a workload is a directory: not the
# profile's choice, the same at every level. ./go attach <path> clones a
# repository of the user's into ATTACHED (docs/adr/0037 §1); while that
# holds a clone, it is the workload. ./go knows the same path.
ATTACHED ?= .attached
WORKLOAD ?= $(if $(wildcard $(ATTACHED)/.git),$(ATTACHED),app)
# A workload in a git repository other than this one is attached: its
# contract is in its .lights-out/ and nowhere else, no fallback. One inside
# this repository keeps it in its own directory, as app/ does (docs/adr/0037
# §2). Every read of the contract goes through WORKLOAD_CONTRACT. include
# stops make, naming the file, if it is missing.
WORKLOAD_REPO := $(shell git -C '$(WORKLOAD)' rev-parse --show-toplevel 2>/dev/null)
ifeq ($(WORKLOAD_REPO),$(shell git rev-parse --show-toplevel 2>/dev/null))
WORKLOAD_CONTRACT := $(WORKLOAD)
WORKLOAD_ATTACHED :=
else
WORKLOAD_CONTRACT := $(WORKLOAD)/.lights-out
WORKLOAD_ATTACHED := 1
endif
# `init` is the one goal that may start without a contract: it drafts one
# (docs/adr/0042 §6). Every other goal stops here, naming the file.
ifeq ($(MAKECMDGOALS),init)
-include $(WORKLOAD_CONTRACT)/workload.env
else
include $(WORKLOAD_CONTRACT)/workload.env
endif
export

# COMPOSE_FILE merges the workload's compose file after the plane's; these
# are := and exported, never ?=, because demo-seam and demo-gates re-enter
# make with a different WORKLOAD and export is what hands the parent's
# values down to that recursive call.
COMPOSE_FILE := compose.yaml:$(WORKLOAD_CONTRACT)/compose.yaml
export COMPOSE_FILE
FLAGS_FILE := $(WORKLOAD_CONTRACT)/flags/flags.json
export FLAGS_FILE
# A code host that ships a git credential helper gets it as environment-
# scoped config (docs/adr/0043 §2): core's clone and fetches and the
# adapter's push authenticate through it, the token never in a URL, an
# argv or a .git/config. Appended at index 1: index 0 is the workbench
# image's safe.directory, which overwriting would lose. ci/codehost.sh
# checks both indices.
ifneq ($(wildcard adapters/$(CODE_HOST)/credential.sh),)
GIT_CONFIG_COUNT := 2
GIT_CONFIG_KEY_1 := credential.helper
GIT_CONFIG_VALUE_1 := !sh '$(CURDIR)/adapters/$(CODE_HOST)/credential.sh'
export GIT_CONFIG_COUNT GIT_CONFIG_KEY_1 GIT_CONFIG_VALUE_1
endif

# The file Tracker's directory (docs/adr/0037 §2): this checkout's tickets/
# for a bundled workload, the attached repository's own otherwise.
TICKETS_DIR := $(if $(WORKLOAD_ATTACHED),$(WORKLOAD_CONTRACT)/tickets,tickets)
# The dev-worker's image is built on the workload's toolchain (docs/adr/
# 0042 §8): the image reference when TOOLCHAIN_IMAGE is one, the workbench
# for `workbench` and for a compose: service, which has no image of its
# own to lend. compose.yaml's gemini-cli service takes it as a build arg.
HARNESS_BASE_IMAGE := $(if $(filter workbench compose:%,$(TOOLCHAIN_IMAGE)),$(WORKBENCH_IMAGE),$(or $(TOOLCHAIN_IMAGE),$(WORKBENCH_IMAGE)))
export HARNESS_BASE_IMAGE
export TICKETS_DIR

# docker-compose over the podman socket builds through the classic /build
# API; pinning this avoids compose spawning a buildkitd container
# (docs/adr/0010).
DOCKER_BUILDKIT := 0
export DOCKER_BUILDKIT

COMPOSE := docker-compose

# Workbench reaches a port compose publishes on the engine host through this
# alias (docs/adr/0011, 0012 §6); it is the one place the hostname is written.
PLANE_HOST ?= host.containers.internal
# The host port the edge publishes the app on (compose.yaml, service envoy);
# inside the plane it stays 8080. Set APP_PORT in .env when 8080 is taken
# on the machine — found closing M12, where another project held it.
APP_PORT ?= 8080
APP_URL ?= http://$(PLANE_HOST):$(APP_PORT)
export PLANE_HOST APP_PORT APP_URL

# Seconds each adapters/<name>/ready.sh polls before giving up. 4x the 15 s a
# cold plane took to answer at M2 close (docs/adr/0012 §6, HANDOFF) was 60;
# raised to 180 on 2026-11-02 after GitHub's launch job measured
# envoy-canary still not ready at 60 s on a cold runner. It is a ceiling,
# not a delay: a plane that answers sooner is not slowed down.
READY_TIMEOUT ?= 180
export READY_TIMEOUT

# Adapters that run as plane services at this profile, in readiness order.
PLANE_ADAPTERS := $(CODE_HOST) $(DELIVERY) $(METRICS) $(FLAGS) $(REGISTRY) $(EVENT_BUS) $(STATE_STORE)

# The gemini-cli worker's compose service writes into its workspace bind
# mount as this uid:gid, so workbench (running as the host user under
# docker, root under rootless podman) can still read and commit what it
# wrote (docs/adr/0017 §3).
CREW_UID := $(shell id -u)
CREW_GID := $(shell id -g)
export CREW_UID CREW_GID

# M3 release controller (docs/adr/0013, 0014). Workbench reaches the Envoy
# admin API the same way it reaches every other plane port (PLANE_HOST).
ENVOY_ADMIN_URL ?= http://$(PLANE_HOST):9901
export ENVOY_ADMIN_URL

# The image `up` builds, publishes and signs as stable (docs/adr/0025 §1,
# §3): the current checkout. CANARY_TAG is what a release deploys as
# canary; it defaults to the same tag, so demo-rollback and demo-hold keep
# releasing "the stable image under a different reported version" exactly
# as before M8 — a real release from core/steps/release.sh overrides it to
# the merge commit's own published tag. CANARY_IMAGE has no meaningful
# default of its own (deploy.sh always sets it via core/launchpad.sh's
# verified digest); the one here only keeps `docker-compose config -q`
# (ci-compose) clean with no release in flight, the same reason
# compose.yaml's own CANARY_VERSION default exists.
APP_TAG ?= sha-$(shell git -C '$(WORKLOAD)' rev-parse HEAD 2>/dev/null)
export APP_TAG
CANARY_TAG ?= $(APP_TAG)
export CANARY_TAG
CANARY_IMAGE ?= localhost:5050/lights-out-backend:$(APP_TAG)
export CANARY_IMAGE

# Seconds launchpad holds a shift before calling verify/slo.sh: equal to the
# slo.promql window, so a stage's numbers describe only this stage's canary
# (docs/adr/0013 §2, §5).
STAGE_SECONDS ?= 60
export STAGE_SECONDS

# Where launchpad writes the decision and its numbers, and what promote
# reads (docs/adr/0020 §4). Under .crew/, gitignored.
RELEASE_RECORD ?= .crew/release.json
export RELEASE_RECORD
# Every final record and every go-for-launch answer, appended (docs/adr/
# 0047 §2, P2): launchpad and promote write it, the logbook reads it.
RELEASES_LOG ?= .crew/releases.jsonl
export RELEASES_LOG

# Plan §2's ceiling on `./go demo` reaching the automatic rollback, timed by
# ./go from before engine-compat, where the cold pulls happen (docs/adr/0013
# §7). 0 disables the verdict; the elapsed time is still printed.
DEMO_BUDGET_SECONDS ?= 900

# The PR gates (docs/adr/0015 §2), cheap and stable ones first, the ones
# needing a fresh image build or a live network last; `gates` stops at the
# first NO-GO. `secrets` reuses the existing repo-wide ci/secrets.sh (it
# already covers history plus the working tree for any range); every other
# name is gates/<name>.sh.
GATES := risk secrets deps sast test mutation

# Where a gate run's data beyond its one-line verdict lands: risk.json,
# mutation.json, and gates' own verdict.env (docs/adr/0015 §2). Gitignored;
# a fresh gates run wipes and recreates it.
GATES_OUT ?= .gates-out
export GATES_OUT

# Milestone stubs. A target whose milestone has not arrived fails loudly.
define not_yet
	@printf 'NO-GO: %s arrives with %s. Nothing runs yet.\n' "$(1)" "$(2)" >&2; exit 1
endef

.PHONY: help
help: ## List the targets
	@printf 'lights-out  (profile: %s)\n\n' "$(PROFILE)"
	@awk 'BEGIN{FS=":.*## "} /^[a-z][a-z0-9-]*:.*## /{printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@for c in $(CI_CHECKS); do printf '  %-16s %s\n' "ci-$$c" "one self-check, see ci/$$c.sh"; done
	@printf '\n  Host side, via ./go only:\n  %-16s %s\n  %-16s %s\n  %-16s %s\n  %-16s %s\n  %-16s %s\n  %-16s %s\n  %-16s %s\n  %-16s %s\n' \
		engine-compat "host-side engine gate: versions, then up and smoke (ci/engine-compat.sh)" \
		doctor "engine, versions, ports, the key: one line each, then GO or NO-GO (docs/adr/0045 §4)" \
		package "build the lights-out image, localhost/lights-out:dev, for the lights-out wrapper (docs/adr/0045 §1)" \
		demo-package "prove the wrapper: init and gates from the image on workloads/minimal as its own repository (docs/adr/0045 §8)" \
		attach "clone a repository and make it the workload; with a .lights-out/ contract it is checked: ./go attach <path> (docs/adr/0037, 0042)" \
		detach "drop the attached clone; the workload is app/ again" \
		shell "interactive shell inside workbench" clean "remove the workbench image"

# Self-checks run by `ci`, in order. engine-compat runs on the host (ADR 0004, ADR 0012 §5), never here.
CI_CHECKS := secrets lint shell help readme gitea-compat profiles workloads attach toolchain init core-tests vendor-free app-free model-free codehost hooks harness harness-shape network tower perimeter review prompts evals promote board logbook roles postmortem metrics package contract

.PHONY: ci
ci: $(addprefix ci-,$(CI_CHECKS)) ## Repo self-checks (ADR 0002), same on laptop and CI
	@printf 'GO: ci\n'

.PHONY: ci-%
ci-%:
	@sh ci/$*.sh

.PHONY: demo
demo: ## The demo, as far as the current milestone takes it (ADR 0003, 0013 §7)
	@printf '[demo] engine-compat: done on the host by ./go before this run\n' >&2
	@printf '[demo] ci (M2): running\n'
	@$(MAKE) --no-print-directory ci
	@$(MAKE) --no-print-directory up
	@printf '[demo] up: L0 plane, %s\n' "$(PLANE_ADAPTERS)" >&2
	@$(MAKE) --no-print-directory smoke
	@printf '[demo] smoke: the happy path passed through Envoy at %s\n' "$(APP_URL)" >&2
	@$(MAKE) --no-print-directory verify METRICS_URL=
	@printf '[demo] verify: metric shape only; canary vs stable is evaluated inside demo-rollback below\n' >&2
	@$(MAKE) --no-print-directory demo-rollback
	@printf '[demo] deploy, verify (canary vs stable), rollback: proved by demo-rollback above (docs/adr/0013, 0014)\n' >&2
	@$(MAKE) --no-print-directory e2e
	@printf '[demo] e2e: ticket → PR → CI → merge → canary → verify NO-GO → rollback, audited (docs/adr/0019)\n' >&2
	@printf 'lights-out: open the app at http://localhost:%s, Gitea at http://localhost:3000, Prometheus at http://localhost:9090\n' "$(APP_PORT)" >&2
	@now=$$(date +%s); t0=$${LIGHTS_OUT_T0:-$$now}; elapsed=$$((now - t0)); \
	if [ "$$elapsed" -lt 0 ]; then \
		printf 'GO: demo, milestone M9, rollback reached but not timed: the engine clock is %ss behind the host, budget %ss unverified (plan §2)\n' "$$((0 - elapsed))" "$(DEMO_BUDGET_SECONDS)"; \
		exit 0; \
	fi; \
	if [ "$(DEMO_BUDGET_SECONDS)" -gt 0 ] && [ "$$elapsed" -gt "$(DEMO_BUDGET_SECONDS)" ]; then \
		printf 'NO-GO: demo — rollback reached, but %ss > %ss budget (plan §2)\n' "$$elapsed" "$(DEMO_BUDGET_SECONDS)"; \
		exit 1; \
	fi; \
	printf 'GO: demo, milestone M9, %ss to rollback (budget %ss, plan §2)\n' "$$elapsed" "$(DEMO_BUDGET_SECONDS)"

.PHONY: up
# The cosign key is generated before `compose up`: compose bind-mounts
# ./.crew, and a rootful docker engine creates a missing mount source owned
# by root, which the workbench (the host user under docker) then cannot
# write the key into. GitHub's engine-compat and seam jobs failed this way
# from M8 on (docs/adr/0025 §3).
up: ## Start the L0 plane, wait until every adapter is ready, then publish the signed stable image (docs/adr/0012 §6, 0025 §3)
	@[ -f "$(WORKLOAD_CONTRACT)/compose.yaml" ] || { printf 'NO-GO: up — %s has no compose.yaml: this workload has the gates half of the contract only (docs/adr/0042 §7); ./go gates runs without the plane, docs/use-on-your-repo.md says what the plane half needs\n' "$(WORKLOAD_CONTRACT)"; exit 1; }
	@[ -f .crew/cosign.key ] || { mkdir -p .crew && COSIGN_PASSWORD= cosign generate-key-pair --output-key-prefix .crew/cosign </dev/null >&2; }
	@$(COMPOSE) up -d --build >&2 || { \
		printf 'lights-out: compose up failed (see above). If a port is already taken: the plane publishes %s, 3000, 5050, 8016, 8085, 8086, 9090 and 9901 on the host; only the app port (%s) moves, with APP_PORT=<port> in .env.\n' "$(APP_PORT)" "$(APP_PORT)" >&2; \
		exit 1; }
	@for a in $(PLANE_ADAPTERS); do \
		[ -f "adapters/$$a/ready.sh" ] || { printf 'lights-out: adapters/%s/ready.sh is missing\n' "$$a" >&2; exit 1; }; \
		printf 'lights-out: waiting for %s\n' "$$a" >&2; \
		sh "adapters/$$a/ready.sh" || { $(COMPOSE) ps -a >&2; exit 1; }; \
	done
	@$(MAKE) --no-print-directory publish TAG=$(APP_TAG)
	@printf 'lights-out: plane is up, app at http://localhost:%s\n' "$(APP_PORT)" >&2

.PHONY: board
board: ## The flight readiness board on localhost:8099: the pipeline explained, and the live plane when it is up (docs/adr/0035)
	@python3 board/serve.py

# The logbook (docs/adr/0047 §2): the one versioned document the console
# and any consumer read, assembled from the tower, the release log, the
# post mortems, the policy files and the plane. LOGBOOK_FILE moves it.
.PHONY: logbook
logbook: ## Write the logbook, the record a client's console reads: [LOGBOOK_FILE=.crew/logbook.json] (docs/adr/0047 §2)
	@sh core/logbook.sh

# The console (docs/adr/0047): the client's five screens over the logbook
# and the one write, go-for-launch through core/promote.sh. ./go publishes
# it on the host's loopback, CONSOLE_PORT to move it; CONSOLE_SNAPSHOT=<file>
# serves a logbook file with no plane behind it.
.PHONY: console
console: ## The console on localhost:8098: tickets, one ticket's chain, post mortems, go-for-launch, thresholds (docs/adr/0047)
	@python3 console/serve.py

# M11's acceptance (docs/adr/0047 §4): demo-launch's sequence, but the
# human answer is an HTTP POST to a running console instead of
# GO_FOR_LAUNCH=GO, then demo-launch's own checks, the record's `via:
# console`, and the un-promote. Not a `demo` stage (ADR 0003): plan §2's
# criterion is the rollback. The console runs inside this recipe on a
# loopback port of the workbench, like a browser on the same machine.
DEMO_CONSOLE_PORT ?= 18098
.PHONY: demo-console
demo-console: ## Prove go-for-launch pressed from the console: held canary, brief, POST GO → 100%, via console, then the un-promote (docs/adr/0047 §4)
	@backup=$$(mktemp); \
	cp "$(FLAGS_FILE)" "$$backup"; \
	restore() { [ -f "$$backup" ] || return 0; cat "$$backup" > "$(FLAGS_FILE)"; rm -f "$$backup"; }; \
	console_pid=""; \
	trap 'rc=$$?; [ -z "$$console_pid" ] || kill "$$console_pid" 2>/dev/null; restore; [ "$$rc" -eq 0 ] || sh "adapters/$(DELIVERY)/rollback.sh" >&2' EXIT; \
	trap 'exit 130' INT TERM; \
	url="http://127.0.0.1:$(DEMO_CONSOLE_PORT)"; \
	mkdir -p .crew; \
	CONSOLE_LISTEN=$(DEMO_CONSOLE_PORT) CONSOLE_PORT=$(DEMO_CONSOLE_PORT) CONSOLE_AUTH=loopback python3 console/serve.py 2>.crew/demo-console.log & \
	console_pid=$$!; \
	$(MAKE) --no-print-directory kill-switch || exit 1; \
	stable=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(APP_URL)/healthz" | jq -r .version); \
	printf '[demo-console] stable is %s; releasing %s with %s off\n' "$$stable" "$(CANARY_VERSION)" "$(WORKLOAD_KILL_SWITCH)" >&2; \
	if ! $(MAKE) --no-print-directory release CANARY_VERSION=$(CANARY_VERSION); then \
		printf 'NO-GO: demo-console — release rolled back; expected a hold at 25%%\n'; exit 1; \
	fi; \
	post() { curl -sS --max-time 600 -X POST "$$url/v1/go-for-launch" -H 'Content-Type: application/json' -H "Origin: $$url" -d "$$1"; }; \
	launch() { curl -fsS --max-time 10 "$$url/v1/logbook.json" | jq -c .launch; }; \
	i=0; l=""; \
	while [ "$$i" -lt 60 ]; do \
		l=$$(launch 2>/dev/null) || l=""; \
		[ "$$(printf '%s' "$$l" | jq -r '.record.verdict // empty' 2>/dev/null)" = held ] && break; \
		i=$$((i + 1)); sleep 1; \
	done; \
	[ "$$(printf '%s' "$$l" | jq -r .preparable)" = true ] \
		|| { printf 'NO-GO: demo-console — the console offers no brief to prepare: %s\n' "$$(printf '%s' "$$l" | jq -r '.disabled_reason // "no logbook"')"; cat .crew/demo-console.log >&2; exit 1; }; \
	v=$$(printf '%s' "$$l" | jq -r .record.version); held=$$(printf '%s' "$$l" | jq -r .record.held_at); \
	r=$$(post "{\"answer\":\"PREPARE\",\"version\":\"$$v\",\"held_at\":$$held}"); \
	printf '[demo-console] prepare the brief: %s\n' "$$(printf '%s' "$$r" | jq -r '.line // .error')" >&2; \
	l=$$(launch); \
	[ "$$(printf '%s' "$$l" | jq -r .pressable)" = true ] \
		|| { printf 'NO-GO: demo-console — the button is disabled: %s\n' "$$(printf '%s' "$$l" | jq -r .disabled_reason)"; exit 1; }; \
	sha=$$(printf '%s' "$$l" | jq -r .brief.sha256); \
	printf '[demo-console] the brief for %s, sha256 %s, by %s; pressing GO\n' "$$v" "$$sha" "$$(printf '%s' "$$l" | jq -r .brief.author)" >&2; \
	r=$$(post "{\"answer\":\"GO\",\"version\":\"$$v\",\"held_at\":$$held,\"brief_sha256\":\"$$sha\"}"); \
	line=$$(printf '%s' "$$r" | jq -r '.line // ("refused: " + .error)'); \
	[ "$$line" = "GO: go-for-launch" ] || { printf 'NO-GO: demo-console — the console answered: %s\n' "$$line"; cat .crew/demo-console.log >&2; exit 1; }; \
	via=$$(jq -r '.promoted.via // empty' "$(RELEASE_RECORD)"); \
	[ "$$via" = console ] || { printf 'NO-GO: demo-console — the record says promoted via %s, want console\n' "$${via:-nothing}"; exit 1; }; \
	logged=$$(tail -n 1 "$(RELEASES_LOG)" | jq -r '"\(.kind) \(.answer) \(.via)"'); \
	[ "$$logged" = "answer GO console" ] || { printf 'NO-GO: demo-console — the release log ends with %s, want the console'"'"'s GO\n' "$$logged"; exit 1; }; \
	canary_w=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(ENVOY_ADMIN_URL)/runtime" | jq -r '.entries["weights.canary"].final_value // "none"'); \
	[ "$$canary_w" = "100" ] || { printf 'NO-GO: demo-console — Envoy reports weights.canary=%s after the press, want 100\n' "$$canary_w"; exit 1; }; \
	ok=0; total=10; i=0; \
	while [ "$$i" -lt "$$total" ]; do \
		hv=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(APP_URL)/healthz" | jq -r .version); \
		[ "$$hv" = "$(CANARY_VERSION)" ] && ok=$$((ok + 1)); \
		i=$$((i + 1)); \
	done; \
	[ "$$ok" -eq "$$total" ] || { printf 'NO-GO: demo-console — only %s/%s healthz through Envoy report %s at 100%%\n' "$$ok" "$$total" "$(CANARY_VERSION)"; exit 1; }; \
	printf '[demo-console] %s at 100%% after the console'"'"'s GO: %s/%s healthz samples; now the un-promote\n' "$(CANARY_VERSION)" "$$ok" "$$total" >&2; \
	sh adapters/$(DELIVERY)/rollback.sh >&2; \
	canary_w=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(ENVOY_ADMIN_URL)/runtime" | jq -r '.entries["weights.canary"].final_value // "none"'); \
	[ "$$canary_w" = "0" ] || { printf 'NO-GO: demo-console — Envoy reports weights.canary=%s after the un-promote, want 0\n' "$$canary_w"; exit 1; }; \
	printf 'GO: demo-console\n'

.PHONY: publish
publish: ## Build, push and sign the image at TAG (docs/adr/0025 §1-§3)
	@sh "adapters/$(REGISTRY)/publish.sh" "$(TAG)"

.PHONY: smoke
smoke: ## Run the workload's happy path through Envoy (its contract's smoke.sh)
	@sh "$(WORKLOAD_CONTRACT)/smoke.sh"

.PHONY: down
down: ## Stop the app, including a held canary revision if one is running
	@$(COMPOSE) --profile test --profile canary --profile crew down --remove-orphans

.PHONY: test
test: ## Run the backend test suite against compose Postgres (docs/adr/0010)
	$(COMPOSE) --profile test run --rm --build backend-test

.PHONY: verify
verify: ## Check verify/slo.promql: against the live app's scrape, or with METRICS_URL=<endpoint> CANARY_VERSION=<v> STABLE_VERSION=<s> against any Prometheus-compatible endpoint (docs/adr/0044 §8)
	@if [ -n "$(METRICS_URL)" ]; then \
		METRICS=promql METRICS_URL="$(METRICS_URL)" CANARY_VERSION="$(CANARY_VERSION)" STABLE_VERSION="$(STABLE_VERSION)" sh verify/slo.sh; \
	else \
		APP_URL="$(APP_URL)" sh verify/metric-shape.sh; \
	fi

# Each gate's own stdout line is the verdict; a gate's stderr (compose
# builds, pytest, bandit, cosmic-ray) streams straight through, live, never
# captured here. A NO-GO's own details (nogo's DETAILS lines, e.g. the
# surviving mutants) also land in $(GATES_OUT)/<gate>.details, which the CI
# step hands to the rework loop (docs/adr/0038 §5). `gates` writes $(GATES_OUT)/verdict.env in the exact shape
# gates/fixtures/*/expect uses, so demo-gates can diff the two instead of
# re-parsing anything (docs/adr/0015 §2, §6).
# With no range given, the last commit is judged (docs/adr/0042 §5): the
# defaults live in this recipe only, never as make variables, so the cold
# ci/contract.sh run of every gate still sees no range. A root commit's
# base is the empty tree: the whole tree is the diff, which is what init
# measured.
.PHONY: gates
gates: ## Judge a PR: BASE=<ref> HEAD=<ref> (default: the last commit) — risk, secrets, deps, sast, test, mutation (docs/adr/0015, 0016, 0042)
	@rm -rf "$(GATES_OUT)" && mkdir -p "$(GATES_OUT)"
	@base="$(BASE)"; head="$(HEAD)"; \
	[ -n "$$head" ] || head=$$(git -C "$(WORKLOAD)" rev-parse HEAD); \
	if [ -z "$$base" ]; then \
		base=$$(git -C "$(WORKLOAD)" rev-parse --verify -q "$$head~1" 2>/dev/null) \
			|| base=$$(git -C "$(WORKLOAD)" hash-object -t tree /dev/null); \
		printf '[gates] no BASE/HEAD given: judging the last commit, %s..%s\n' "$$(printf '%s' "$$base" | cut -c1-7)" "$$(git -C "$(WORKLOAD)" rev-parse --short "$$head")" >&2; \
	fi; \
	failed=""; failreason=""; \
	for g in $(GATES); do \
		case "$$g" in secrets) script=ci/secrets.sh ;; *) script=gates/$$g.sh ;; esac; \
		out=$$(BASE="$$base" HEAD="$$head" NOGO_DETAILS="$(GATES_OUT)/$$g.details" sh "$$script"); \
		case "$$out" in \
			"GO: $$g"|"GO: $$g — "*) ;; \
			*) failed=$$g; failreason=$$out; break ;; \
		esac; \
	done; \
	level=unknown; decision=unknown; \
	if [ -f "$(GATES_OUT)/risk.json" ]; then \
		level=$$(jq -r .level "$(GATES_OUT)/risk.json"); \
		decision=$$(jq -r .decision "$(GATES_OUT)/risk.json"); \
	fi; \
	mt=""; \
	if [ -f "$(GATES_OUT)/mutation.json" ]; then \
		mt=$$(jq -r 'if .total == 0 then "zero" else "some" end' "$(GATES_OUT)/mutation.json"); \
	fi; \
	if [ -n "$$failed" ]; then \
		{ printf 'GATES=NO-GO\n'; printf 'FAILED=%s\n' "$$failed"; \
		  printf 'RISK_LEVEL=%s\n' "$$level"; printf 'RISK_DECISION=%s\n' "$$decision"; \
		  if [ -n "$$mt" ]; then printf 'MUTATION_TOTAL=%s\n' "$$mt"; fi; \
		} > "$(GATES_OUT)/verdict.env"; \
		printf 'NO-GO: gates — %s: %s\n' "$$failed" "$$failreason"; \
		exit 1; \
	fi; \
	{ printf 'GATES=GO\n'; printf 'RISK_LEVEL=%s\n' "$$level"; printf 'RISK_DECISION=%s\n' "$$decision"; \
	  if [ -n "$$mt" ]; then printf 'MUTATION_TOTAL=%s\n' "$$mt"; fi; \
	} > "$(GATES_OUT)/verdict.env"; \
	where=""; [ ! -f "$(GATES_OUT)/toolchain.where" ] || where=", in $$(cat "$(GATES_OUT)/toolchain.where")"; \
	printf 'GO: gates — risk %s, %s%s\n' "$$level" "$$decision" "$$where"

# Proves every gate can both GO and NO-GO, on committed fixtures, not just
# once in a build log (docs/adr/0015 §6; the demo-hold lesson, docs/adr/0014:
# a verify wired to always pass -- or fail -- would pass its own demo too).
# Each fixture is a real commit applied by `git am` in its own throwaway
# worktree off the workload repository's HEAD, judged as the range
# HEAD..<applied>, then discarded. A bundled workload's worktree carries
# this checkout, so make runs inside it, as before; an attached one's
# carries only the target, so make runs here with WORKLOAD pointing at it
# (docs/adr/0037 §4).
.PHONY: demo-gates
demo-gates: ## Prove every PR gate GO and NO-GO on the committed fixtures (docs/adr/0015 §6)
	@git -C "$(WORKLOAD)" worktree prune
	@main_sha=$$(git -C "$(WORKLOAD)" rev-parse HEAD); \
	if [ -z "$(WORKLOAD_ATTACHED)" ] && [ -n "$$(git status --porcelain -- gates ci verify policy core Makefile)" ]; then \
		printf '[demo-gates] note: uncommitted edits under gates/, ci/, verify/, policy/, core/ or the Makefile are not judged here; the fixtures run against HEAD\n' >&2; \
	fi; \
	tmp1=$$(mktemp); tmp2=$$(mktemp); wtd=""; wt=""; \
	drop_wt() { [ -z "$$wt" ] || git -C "$(WORKLOAD)" worktree remove --force "$$wt" >/dev/null 2>&1 || :; [ -z "$$wtd" ] || rm -rf "$$wtd"; wt=""; wtd=""; }; \
	trap 'drop_wt; rm -f "$$tmp1" "$$tmp2"' EXIT; \
	trap 'exit 130' INT TERM; \
	found=0; \
	for f in "$(WORKLOAD_CONTRACT)/fixtures/gates"/*/; do \
		[ -d "$$f" ] || continue; \
		found=$$((found + 1)); \
		name=$$(basename "$$f"); \
		f=$$(CDPATH= cd -- "$$f" && pwd)/; \
		wtd=$$(mktemp -d); wt=$$wtd/wt; \
		err=$$(git -C "$(WORKLOAD)" worktree add --detach "$$wt" HEAD 2>&1 >/dev/null) || { \
			printf 'NO-GO: demo-gates — %s: git worktree add failed: %s\n' "$$name" "$$err"; exit 1; \
		}; \
		if ! git -C "$$wt" -c user.name=lights-out-fixtures -c user.email=fixtures@lights-out.invalid \
				am "$$f"*.patch >/dev/null 2>&1; then \
			git -C "$$wt" am --abort >/dev/null 2>&1 || true; \
			drop_wt; \
			printf 'NO-GO: demo-gates — %s does not apply; regenerate\n' "$$name"; \
			exit 1; \
		fi; \
		head_sha=$$(git -C "$$wt" rev-parse HEAD); \
		if [ -n "$(WORKLOAD_ATTACHED)" ]; then \
			verdict="$$wtd/gates-out/verdict.env"; \
			out=$$($(MAKE) --no-print-directory gates WORKLOAD="$$wt" GATES_OUT="$$wtd/gates-out" BASE="$$main_sha" HEAD="$$head_sha"); \
		else \
			verdict="$$wt/$(GATES_OUT)/verdict.env"; \
			out=$$( (cd "$$wt" && $(MAKE) --no-print-directory gates BASE="$$main_sha" HEAD="$$head_sha") ); \
		fi; \
		printf '[demo-gates] %s: %s\n' "$$name" "$$out" >&2; \
		sort "$$verdict" > "$$tmp1" 2>/dev/null || : > "$$tmp1"; \
		sort "$$f/expect" > "$$tmp2"; \
		if ! diff -q "$$tmp1" "$$tmp2" >/dev/null 2>&1; then \
			printf '[demo-gates] %s: verdict mismatch\n  got:  %s\n  want: %s\n' "$$name" \
				"$$(tr '\n' ' ' < "$$verdict" 2>/dev/null || echo '(no verdict.env)')" \
				"$$(tr '\n' ' ' < "$$f/expect")" >&2; \
			drop_wt; \
			printf 'NO-GO: demo-gates — %s does not match expect\n' "$$name"; \
			exit 1; \
		fi; \
		drop_wt; \
	done; \
	[ "$$found" -gt 0 ] || { printf 'NO-GO: demo-gates — no fixture in %s/fixtures/gates/\n' "$(WORKLOAD_CONTRACT)"; exit 1; }; \
	printf 'GO: demo-gates\n'

.PHONY: assign
assign: ## Turn a ticket into a PR: TICKET=<id> [ROLE=dev-worker] (docs/adr/0017 §4)
	@TICKET="$(TICKET)" ROLE="$(ROLE)" sh core/crew.sh

# Shared by demo-crew and record (docs/adr/0017 §6): runs assign, then
# the CI step the tower uses (core/steps/ci.sh, docs/adr/0019 §1) on the
# range .crew/<ticket>/pr-opened.env records — fetch from CODE_HOST,
# throwaway worktree, `make gates`, verdict posted as a commit status —
# leaving the verdict at $(GATES_OUT)/verdict.env at the repo root; the
# caller decides what a GO or a NO-GO means. Always exits 0 past assign;
# a gates NO-GO is data, not a Makefile failure.
.PHONY: crew-run
crew-run:
	@rm -f "$(GATES_OUT)/verdict.env"
	@role="$(ROLE)"; [ -n "$$role" ] || role=dev-worker; \
	$(MAKE) --no-print-directory assign TICKET="$(TICKET)" ROLE="$$role" >&2 || { \
		printf 'NO-GO: crew-run — assign did not produce a PR\n'; exit 1; \
	}; \
	. ".crew/$(TICKET)/pr-opened.env"; \
	TICKET="$(TICKET)" PR_NUMBER="$$PR_NUMBER" BASE_SHA="$$BASE_SHA" HEAD_SHA="$$HEAD_SHA" \
		sh core/steps/ci.sh >/dev/null || { \
		printf 'NO-GO: crew-run — the CI step did not reach a verdict\n'; exit 1; \
	}

.PHONY: demo-crew
demo-crew: ## Prove ticket 0001 becomes a PR that passes the M4 gates (docs/adr/0017; the M5 acceptance command)
	@$(MAKE) --no-print-directory up
	@$(MAKE) --no-print-directory crew-run TICKET=0001 ROLE=dev-worker
	@expect=fixtures/0001/dev-worker/expect; \
	if [ ! -f "$$expect" ]; then \
		printf 'NO-GO: demo-crew — no %s; run ./go record TICKET=0001 PROFILE=l1 first\n' "$$expect"; exit 1; \
	fi; \
	tmp1=$$(mktemp); tmp2=$$(mktemp); \
	sort "$(GATES_OUT)/verdict.env" > "$$tmp1" 2>/dev/null || : > "$$tmp1"; \
	sort "$$expect" > "$$tmp2"; \
	if ! diff -q "$$tmp1" "$$tmp2" >/dev/null 2>&1; then \
		printf '[demo-crew] verdict mismatch\n  got:  %s\n  want: %s\n' \
			"$$(tr '\n' ' ' < "$(GATES_OUT)/verdict.env" 2>/dev/null || echo '(no verdict.env)')" \
			"$$(tr '\n' ' ' < "$$expect")" >&2; \
		rm -f "$$tmp1" "$$tmp2"; \
		printf 'NO-GO: demo-crew — verdict does not match fixtures/0001/dev-worker/expect\n'; exit 1; \
	fi; \
	rm -f "$$tmp1" "$$tmp2"; \
	printf 'GO: demo-crew\n'

# What record and record-review need of the run they save (docs/adr/0041
# §10, §7), $(1) the target: AGENT_HARNESS is not replay, which would replay
# the fixture it is trying to create; it is a CLI harness, an adapters/<name>/
# with run.sh, Containerfile and package-lock.json (the same discovery as
# gates/harness-conformance.sh); and MODEL_BASE_URL is unset, so a run
# against a scripted or proxied endpoint can never become a fixture.
define record_guard
	@if [ "$(AGENT_HARNESS)" = "replay" ]; then \
		printf 'NO-GO: $(1) — recording a replay is circular; PROFILE=l1\n'; exit 1; \
	fi
	@if [ ! -f "adapters/$(AGENT_HARNESS)/run.sh" ] || [ ! -f "adapters/$(AGENT_HARNESS)/Containerfile" ] || [ ! -f "adapters/$(AGENT_HARNESS)/package-lock.json" ]; then \
		printf 'NO-GO: $(1) — AGENT_HARNESS=%s is not a CLI harness (an adapters/<name>/ with run.sh, Containerfile and package-lock.json)\n' "$(AGENT_HARNESS)"; exit 1; \
	fi
	@if [ -n "$(MODEL_BASE_URL)" ]; then \
		printf 'NO-GO: $(1) — MODEL_BASE_URL is set: a run against a scripted or proxied endpoint is never a fixture (docs/adr/0041 §7)\n'; exit 1; \
	fi
endef

# Records the one real L1 run this ADR calls for (docs/adr/0017 §6): the
# fixture L0 replays forever after. Refuses at L0 ("recording a replay is
# circular" — AGENT_HARNESS=replay would replay the fixture it is trying to
# create) and refuses a run that does not pass the gates (kept under
# .crew/<ticket> for study; the attempt is not silently retried here, and
# HANDOFF records how many attempts the recorded run took). Any CLI harness
# records (docs/adr/0041 §10): gemini-cli's run is the fixture L0 replays, in
# fixtures/<ticket>/<role>/; another harness's goes one directory deeper, in
# fixtures/<ticket>/<role>/<harness>/, where replay does not read it (the
# owner's measurement: cost, served model, budgets).
.PHONY: record
record: ## Run a ticket for real at L1 and save the fixture: TICKET=<id> [ROLE=dev-worker] [AGENT_HARNESS=claude-code MODEL_PROVIDER=anthropic MODEL=…] (docs/adr/0017 §6, 0041 §10)
	$(call record_guard,record)
	@$(MAKE) --no-print-directory up
	@role="$(ROLE)"; [ -n "$$role" ] || role=dev-worker; \
	$(MAKE) --no-print-directory crew-run TICKET="$(TICKET)" ROLE="$$role" || { \
		printf 'NO-GO: record — ticket %s did not reach the gates; see the crew-run output above\n' "$(TICKET)"; \
		exit 1; \
	}; \
	gates_verdict=$$(grep '^GATES=' "$(GATES_OUT)/verdict.env" 2>/dev/null | cut -d= -f2); \
	if [ "$$gates_verdict" != "GO" ]; then \
		printf 'NO-GO: record — ticket %s did not pass the gates (GATES=%s); kept under .crew/%s for study\n' \
			"$(TICKET)" "$${gates_verdict:-unknown}" "$(TICKET)"; \
		exit 1; \
	fi; \
	dest="fixtures/$(TICKET)/$$role"; \
	[ "$(AGENT_HARNESS)" = "gemini-cli" ] || dest="$$dest/$(AGENT_HARNESS)"; \
	mkdir -p "$$dest"; \
	cp .crew/$(TICKET)/out/* "$$dest/" 2>/dev/null || true; \
	cp "$(GATES_OUT)/verdict.env" "$$dest/expect"; \
	printf 'lights-out: fixture written to %s\n' "$$dest" >&2; \
	printf 'GO: record\n'

# Not a gate: a measurement. MUTATION_LINES=all tells tests/mutation/run.sh
# to skip the line filter and mutate the whole package, so this is the
# number policy/merge.env's MUTATION_BASELINE cites, with its provenance
# comment (commit, date, tool version, mutant count, wall time) written by
# hand after reading this target's output (docs/adr/0015 §3).
.PHONY: mutation-baseline
mutation-baseline: ## Whole-scope mutation score of the workload, where its TOOLCHAIN_IMAGE says, for MUTATION_BASELINE's provenance (docs/adr/0015 §3, 0042 §6)
	@sh core/mutation-baseline.sh

.PHONY: release
release: ## Deploy CANARY_VERSION at CANARY_TAG and run the controller through staging, 5%, 25% (docs/adr/0013 §4, 0025 §6)
	@sh "$(WORKLOAD_CONTRACT)/load.sh" & \
	load_pid=$$!; \
	CANARY_VERSION="$(CANARY_VERSION)" CANARY_TAG="$(CANARY_TAG)" STAGE_SECONDS="$(STAGE_SECONDS)" sh core/launchpad.sh; \
	rc=$$?; \
	kill "$$load_pid" 2>/dev/null || true; \
	wait "$$load_pid" 2>/dev/null || true; \
	exit "$$rc"

.PHONY: demo-rollback
demo-rollback: ## Release the failing default and prove the canary rolls back on its own (docs/adr/0013 §4)
	@stable=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(APP_URL)/healthz" | jq -er .version) \
		|| { printf 'NO-GO: demo-rollback — no stable version at %s/healthz; run ./go up\n' "$(APP_URL)"; exit 1; }; \
	printf '[demo-rollback] stable is %s; releasing %s (%s targeted on)\n' "$$stable" "$(CANARY_VERSION)" "$(WORKLOAD_KILL_SWITCH)" >&2; \
	rm -f "$(RELEASE_RECORD)"; \
	if $(MAKE) --no-print-directory release CANARY_VERSION=$(CANARY_VERSION); then \
		printf 'NO-GO: demo-rollback — release passed; expected a NO-GO at 5%%\n'; \
		exit 1; \
	fi; \
	failed=$$(jq -r '.stages[-1].verdict // empty' "$(RELEASE_RECORD)" 2>/dev/null); \
	case "$$failed" in \
		"NO-GO: slo — "*) : ;; \
		*) printf 'NO-GO: demo-rollback — the release stopped before verify judged the canary [%s]\n' "$$failed"; exit 1 ;; \
	esac; \
	canary_w=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(ENVOY_ADMIN_URL)/runtime" | jq -r '.entries["weights.canary"].final_value // "none"'); \
	if [ "$$canary_w" != "0" ]; then \
		printf 'NO-GO: demo-rollback — Envoy still reports weights.canary=%s after rollback\n' "$$canary_w"; \
		exit 1; \
	fi; \
	ok=0; total=10; i=0; \
	while [ "$$i" -lt "$$total" ]; do \
		v=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(APP_URL)/healthz" | jq -r .version); \
		[ "$$v" = "$$stable" ] && ok=$$((ok + 1)); \
		i=$$((i + 1)); \
	done; \
	if [ "$$ok" -ne "$$total" ]; then \
		printf 'NO-GO: demo-rollback — only %s/%s healthz through Envoy report the stable version\n' "$$ok" "$$total"; \
		exit 1; \
	fi; \
	printf 'GO: demo-rollback\n'

# demo-hold rolls back its own held canary, so every rollback here goes
# through unhold: a `held` record turns `rolled-back` (stages kept, plus
# rolled_back_by), never left describing a canary the plane no longer
# holds (ticket 0008).
.PHONY: demo-hold
demo-hold: ## Same release, kill switch on: verify GO end to end, canary held at 25% for the human gate (docs/adr/0014)
	@backup=$$(mktemp); \
	cp "$(FLAGS_FILE)" "$$backup"; \
	restore() { [ -f "$$backup" ] || return 0; cat "$$backup" > "$(FLAGS_FILE)"; rm -f "$$backup"; }; \
	unhold() { \
		sh "adapters/$(DELIVERY)/rollback.sh" >&2 || return 1; \
		[ "$$(jq -r .verdict "$(RELEASE_RECORD)" 2>/dev/null)" = held ] || return 0; \
		rec=$$(jq -c '. + {verdict: "rolled-back", held_at: null, rolled_back_by: "demo-hold"}' "$(RELEASE_RECORD)") \
			&& printf '%s\n' "$$rec" > "$(RELEASE_RECORD)"; \
	}; \
	trap 'rc=$$?; restore; [ "$$rc" -eq 0 ] || unhold' EXIT; \
	trap 'exit 130' INT TERM; \
	$(MAKE) --no-print-directory kill-switch || exit 1; \
	stable=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(APP_URL)/healthz" | jq -r .version); \
	printf '[demo-hold] stable is %s; releasing %s with %s off\n' "$$stable" "$(CANARY_VERSION)" "$(WORKLOAD_KILL_SWITCH)" >&2; \
	if ! $(MAKE) --no-print-directory release CANARY_VERSION=$(CANARY_VERSION); then \
		printf 'NO-GO: demo-hold — release rolled back; expected GO through 25%%\n'; \
		exit 1; \
	fi; \
	canary_w=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(ENVOY_ADMIN_URL)/runtime" | jq -r '.entries["weights.canary"].final_value // "none"'); \
	if [ "$$canary_w" != "25" ]; then \
		printf 'NO-GO: demo-hold — Envoy reports weights.canary=%s, want 25\n' "$$canary_w"; \
		exit 1; \
	fi; \
	seen_canary=0; seen_stable=0; total=40; i=0; \
	while [ "$$i" -lt "$$total" ]; do \
		v=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(APP_URL)/healthz" | jq -r .version); \
		[ "$$v" = "$(CANARY_VERSION)" ] && seen_canary=1; \
		[ "$$v" = "$$stable" ] && seen_stable=1; \
		i=$$((i + 1)); \
	done; \
	if [ "$$seen_canary" -ne 1 ] || [ "$$seen_stable" -ne 1 ]; then \
		printf 'NO-GO: demo-hold — %s healthz samples through Envoy did not see both versions (canary seen=%s, stable seen=%s)\n' "$$total" "$$seen_canary" "$$seen_stable"; \
		exit 1; \
	fi; \
	unhold; \
	printf '[demo-hold] cleanup: rollback to 100/0, record now rolled-back (not a verdict — the controller held at 25%%, this is hygiene for the next run)\n' >&2; \
	printf 'GO: demo-hold\n'

# The three targets below go through adapters/$(FLAGS)/set.sh and
# evaluate.sh (docs/adr/0026 §3) instead of writing FLAGS_FILE
# themselves — the same seam L2's flagd-on-Cloud-Run uses (docs/adr/0027
# §8). set.sh's own header has the in-place-write story (why not a
# temp-file-plus-rename): flagd's fileinfo poller, reproduced live
# 2026-09-14 on v0.16.3, intermittently keeps serving a stale evaluation
# after a rename, with no error.
.PHONY: kill-switch
kill-switch: ## Turn the workload's kill switch off for every version, live within seconds, no deploy (docs/adr/0013 §6, 0030 §2)
	@sh "adapters/$(FLAGS)/set.sh" "$(WORKLOAD_KILL_SWITCH)" off; \
	i=0; v=""; \
	while [ "$$i" -lt "$(READY_TIMEOUT)" ]; do \
		v=$$(sh "adapters/$(FLAGS)/evaluate.sh" "$(WORKLOAD_KILL_SWITCH)" "$(CANARY_VERSION)" 2>/dev/null) || v=""; \
		[ "$$v" = "false" ] && break; \
		i=$$((i + 1)); sleep 1; \
	done; \
	if [ "$$v" != "false" ]; then \
		printf 'NO-GO: kill-switch — %s still evaluates %s for %s after %ss\n' "$(WORKLOAD_KILL_SWITCH)" "$${v:-no answer}" "$(CANARY_VERSION)" "$(READY_TIMEOUT)"; \
		exit 1; \
	fi; \
	printf 'lights-out: %s is off for every version (%s edited; git checkout it to undo)\n' "$(WORKLOAD_KILL_SWITCH)" "$(FLAGS_FILE)" >&2; \
	printf 'GO: kill-switch\n'

.PHONY: freeze
freeze: ## Turn release-freeze on: launchpad refuses to deploy or shift (docs/adr/0013 §6)
	@sh "adapters/$(FLAGS)/set.sh" release-freeze on; \
	printf 'lights-out: release-freeze is on (%s edited; git checkout it to undo)\n' "$(FLAGS_FILE)" >&2; \
	printf 'GO: freeze\n'

.PHONY: unfreeze
unfreeze: ## Turn release-freeze off (docs/adr/0013 §6)
	@sh "adapters/$(FLAGS)/set.sh" release-freeze off; \
	printf 'lights-out: release-freeze is off\n' >&2; \
	printf 'GO: unfreeze\n'

# The M6 acceptance command (docs/adr/0019 §7). A replay on a per-`up`
# plane: the ticket's document is deleted first so the machine starts at
# `opened` every time — a production tower never deletes. Then: open
# (document + the `assign` event), the inproc orchestrator until the
# queue is empty, the final state asserted as rolled-back (the done-when
# ends in a rollback; the demo-rollback precedent), and the audit. Order
# inside `demo`: after demo-rollback, so ≥ 97 s pass between its rollback
# and this canary's 5% verify and the 1m window (docs/adr/0013 §2) holds
# only this stage's canary; put e2e first and the two runs share a window.
TICKET ?= 0001

.PHONY: e2e
e2e: ## Golden path at L0: ticket → PR → CI → merge → canary → verify NO-GO → rollback, audited (docs/adr/0019)
	@$(MAKE) --no-print-directory up
	@printf '[e2e] replay: deleting the document of ticket %s before opening it again\n' "$(TICKET)" >&2
	@sh adapters/$(STATE_STORE)/delete.sh tickets "$(TICKET)" || { printf 'NO-GO: e2e — could not reset the document\n'; exit 1; }
	@sh core/tower.sh open "$(TICKET)" || { printf 'NO-GO: e2e — could not open ticket %s\n' "$(TICKET)"; exit 1; }
	@sh adapters/$(ORCHESTRATOR)/run.sh || { printf 'NO-GO: e2e — a step failed; see the [tower] lines above\n'; exit 1; }
	@state=$$(sh adapters/$(STATE_STORE)/get.sh tickets "$(TICKET)" | jq -r .state); \
	if [ "$$state" != "rolled-back" ]; then \
		printf 'NO-GO: e2e — ticket %s ended at %s, expected rolled-back\n' "$(TICKET)" "$$state"; exit 1; \
	fi
	@$(MAKE) --no-print-directory audit TICKET="$(TICKET)" >&2 || { printf 'NO-GO: e2e — audit did not pass\n'; exit 1; }
	@printf 'GO: e2e\n'

# The network (docs/adr/0036): ticket 0012's two members, `impl` and
# `tests`, through the same golden path as e2e, then the facts only a
# network run has. At L0 the members replay hand-written fixtures, and
# this target says so on stderr; the tower records model null. Not in
# demo's fixed list (docs/adr/0003).
.PHONY: demo-network
demo-network: ## Prove a network of members on ticket 0012: one PR, gated, merged, canary rolled back, audited (docs/adr/0036)
	@$(MAKE) --no-print-directory e2e TICKET=0012 CREW_HARNESS=network >&2 \
		|| { printf 'NO-GO: demo-network — the tower did not carry ticket 0012 from network to rollback\n'; exit 1; }
	@doc=$$(sh adapters/$(STATE_STORE)/get.sh tickets 0012) || { printf 'NO-GO: demo-network — no document for ticket 0012\n'; exit 1; }; \
	run=.crew/0012/out/run.json; \
	harness=$$(printf '%s' "$$doc" | jq -r '.task.harness'); \
	[ "$$harness" = network ] || { printf 'NO-GO: demo-network — task.harness is %s, want network\n' "$$harness"; exit 1; }; \
	members=$$(jq -r '[.members[].id] | join(",")' "$$run" 2>/dev/null); \
	[ "$$members" = impl,tests ] || { printf 'NO-GO: demo-network — run.json members are [%s], want impl,tests\n' "$$members"; exit 1; }; \
	base=$$(printf '%s' "$$doc" | jq -r .pr.base_sha); head=$$(printf '%s' "$$doc" | jq -r .pr.head_sha); \
	commits=$$(git -C .crew/0012/workspace rev-list --count "$$base..$$head" 2>/dev/null) || commits=0; \
	[ "$$commits" -eq 2 ] || { printf 'NO-GO: demo-network — the PR holds %s commits, want one per member\n' "$$commits"; exit 1; }; \
	ci=$$(printf '%s' "$$doc" | jq -r '"\(.ci.verdict)/\(.ci.risk_level)/\(.ci.decision)"'); \
	[ "$$ci" = GO/low/auto-merge ] || { printf 'NO-GO: demo-network — gates %s, want GO/low/auto-merge\n' "$$ci"; exit 1; }; \
	hand=$$(jq -r '[.members[] | select(.run.recorded.hand_written == true) | .id] | join(",")' "$$run"); \
	[ -z "$$hand" ] || printf '[demo-network] members %s replayed hand-written fixtures: no model ran (docs/adr/0036 §7)\n' "$$hand" >&2; \
	printf 'GO: demo-network\n'

.PHONY: audit
audit: ## Rebuild the chain deploy → merge → PR → ticket → spec from the state store and the code host: TICKET=<id> (docs/adr/0019 §6)
	@TICKET="$(TICKET)" sh core/audit.sh

# A ticket whose last step failed stays at that step's starting state
# with nothing queued (docs/adr/0034). resume asks the tower to request
# that step again — refused unless the last history entry is a failed
# attempt — then runs the orchestrator until the queue is empty. The
# document is never reset: the history keeps the failed attempt, the
# resume and the new attempt.
.PHONY: resume
resume: ## Request again the step a ticket failed, keeping its history: TICKET=<id> (docs/adr/0034)
	@sh core/tower.sh resume "$(TICKET)" || { printf 'NO-GO: resume — ticket %s was not resumed; see the [tower] line above\n' "$(TICKET)"; exit 1; }
	@sh adapters/$(ORCHESTRATOR)/run.sh || { printf 'NO-GO: resume — a step failed again; see the [tower] lines above\n'; exit 1; }
	@state=$$(sh adapters/$(STATE_STORE)/get.sh tickets "$(TICKET)" | jq -r .state); \
	printf '[resume] ticket %s is at %s\n' "$(TICKET)" "$$state" >&2
	@printf 'GO: resume\n'

# The rework loop live at L0 (docs/adr/0038 §10). Ticket 0013's attempt 1
# is the committed fake-test change, NO-GO at mutation; the tower sends the
# verdict back to the worker, attempt 2 answers it, GO, merge, and the
# release rolls back as in e2e: after merge nothing loops. Both attempts
# are hand-written fixtures (README, "What is real"); the gates, the tower
# and the loop are real. The assertions read the document and the input
# the worker was actually given, not a log line.
.PHONY: demo-rework
demo-rework: TICKET := 0013
demo-rework: ## Prove the rework loop: ticket 0013 NO-GO at the gates, reworked with the verdict, GO and merged (docs/adr/0038)
	@$(MAKE) --no-print-directory up
	@printf '[demo-rework] replay: deleting the document of ticket %s before opening it again\n' "$(TICKET)" >&2
	@sh adapters/$(STATE_STORE)/delete.sh tickets "$(TICKET)" || { printf 'NO-GO: demo-rework — could not reset the document\n'; exit 1; }
	@sh core/tower.sh open "$(TICKET)" || { printf 'NO-GO: demo-rework — could not open ticket %s\n' "$(TICKET)"; exit 1; }
	@sh adapters/$(ORCHESTRATOR)/run.sh || { printf 'NO-GO: demo-rework — a step failed; see the [tower] lines above\n'; exit 1; }
	@doc=$$(sh adapters/$(STATE_STORE)/get.sh tickets "$(TICKET)") || { printf 'NO-GO: demo-rework — no document for ticket %s\n' "$(TICKET)"; exit 1; }; \
	trail=$$(printf '%s' "$$doc" | jq -r '[.history[] | "\(.step):\(.state):\(.attempt // "-")"] | join(" ")'); \
	want='open:opened:- assign:pr-opened:1 ci:ci-failed:1 rework:pr-opened:2 ci:ci-passed:2 merge:merged:2 release:rolled-back:2'; \
	[ "$$trail" = "$$want" ] || { printf 'NO-GO: demo-rework — history is %s, want %s\n' "$$trail" "$$want"; exit 1; }; \
	line=$$(printf '%s' "$$doc" | jq -r '.rework[0].line // empty'); \
	case "$$line" in "NO-GO: gates — mutation"*) : ;; \
		*) printf 'NO-GO: demo-rework — attempt 1 was not sent back by the mutation gate: %s\n' "$${line:-no rework entry}"; exit 1 ;; esac; \
	input=".crew/$(TICKET)/input.md"; \
	grep -qF -- "$$line" "$$input" || { printf 'NO-GO: demo-rework — %s does not carry attempt 1'"'"'s verdict\n' "$$input"; exit 1; }; \
	details=$$(printf '%s' "$$doc" | jq -r '.rework[0].details[]'); \
	[ -n "$$details" ] || { printf 'NO-GO: demo-rework — attempt 1'"'"'s verdict has no gate details\n'; exit 1; }; \
	printf '%s\n' "$$details" | while IFS= read -r d; do grep -qF -- "$$d" "$$input" || exit 1; done \
		|| { printf 'NO-GO: demo-rework — %s does not carry the gate'"'"'s details\n' "$$input"; exit 1; }; \
	printf '[demo-rework] attempt 1: %s\n' "$$line" >&2; \
	printf '%s\n' "$$details" | sed 's/^/[demo-rework]   /' >&2; \
	printf '[demo-rework] attempt 2: %s\n' "$$(printf '%s' "$$doc" | jq -r .ci.line)" >&2
	@$(MAKE) --no-print-directory audit TICKET="$(TICKET)" >&2 || { printf 'NO-GO: demo-rework — audit did not pass\n'; exit 1; }
	@printf 'GO: demo-rework\n'

.PHONY: review
review: ## Judge an opened PR outside the tower: TICKET=<id> [ROLE=test-reviewer] (docs/adr/0021)
	@role="$(ROLE)"; [ -n "$$role" ] || role=test-reviewer; \
	. ".crew/$(TICKET)/pr-opened.env"; \
	TICKET="$(TICKET)" PR_NUMBER="$$PR_NUMBER" BASE_SHA="$$BASE_SHA" HEAD_SHA="$$HEAD_SHA" ROLE="$$role" \
		sh core/steps/review.sh

# TICKET defaults to 0005 here, not the golden path's 0001: ticket 0001 is
# low-risk (docs/adr/0016 §4) and never enters review at all. A command-line
# TICKET=<id> still wins over this default (docs/adr/0021 §9).
.PHONY: demo-review
demo-review: TICKET := 0005
demo-review: ## Prove a medium/high-risk PR goes through the review step to a traceable verdict: TICKET=<id> (docs/adr/0021)
	@$(MAKE) --no-print-directory up
	@printf '[demo-review] replay: deleting the document of ticket %s before opening it again\n' "$(TICKET)" >&2
	@sh adapters/$(STATE_STORE)/delete.sh tickets "$(TICKET)" || { printf 'NO-GO: demo-review — could not reset the document\n'; exit 1; }
	@sh core/tower.sh open "$(TICKET)" || { printf 'NO-GO: demo-review — could not open ticket %s\n' "$(TICKET)"; exit 1; }
	@sh adapters/$(ORCHESTRATOR)/run.sh || { printf 'NO-GO: demo-review — a step failed; see the [tower] lines above\n'; exit 1; }
	@doc=$$(sh adapters/$(STATE_STORE)/get.sh tickets "$(TICKET)") || { printf 'NO-GO: demo-review — no document for ticket %s\n' "$(TICKET)"; exit 1; }; \
	state=$$(printf '%s' "$$doc" | jq -r .state); \
	verdict=$$(printf '%s' "$$doc" | jq -r '.review.verdict // empty'); \
	reason=$$(printf '%s' "$$doc" | jq -r '.review.reason // empty'); \
	matches=$$(printf '%s' "$$doc" | jq -r 'if .review.input_matches_recorded == null then "" else (.review.input_matches_recorded | tostring) end'); \
	if [ -z "$$verdict" ] || [ -z "$$reason" ]; then \
		printf 'NO-GO: demo-review — ticket %s carries no review verdict/reason (state %s)\n' "$(TICKET)" "$$state"; exit 1; \
	fi; \
	case "$$verdict" in \
		approved) case "$$state" in \
			ci-passed|merged|held|rolled-back) : ;; \
			*) printf 'NO-GO: demo-review — approved but state is %s, expected past ci-passed\n' "$$state"; exit 1 ;; \
		esac ;; \
		changes-requested) \
			case "$$state" in ci-failed|needs-human) : ;; \
				*) printf 'NO-GO: demo-review — changes-requested but state is %s, expected ci-failed or needs-human (docs/adr/0038)\n' "$$state"; exit 1 ;; esac; \
			merge_sha=$$(printf '%s' "$$doc" | jq -r '.merge.sha // empty'); \
			[ -z "$$merge_sha" ] || { printf 'NO-GO: demo-review — changes-requested but merge.sha is %s\n' "$$merge_sha"; exit 1; } ;; \
		*) printf 'NO-GO: demo-review — unexpected verdict %s\n' "$$verdict"; exit 1 ;; \
	esac; \
	[ "$$matches" = "true" ] || { printf 'NO-GO: demo-review — recorded review input does not match this diff (input_matches_recorded=%s)\n' "$$matches"; exit 1; }; \
	printf '[demo-review] test-reviewer: %s — %s\n' "$$verdict" "$$reason" >&2
	@printf '[demo-review] second reviewer, architect (exercised once, not wired into the tower):\n' >&2
	@$(MAKE) --no-print-directory review TICKET="$(TICKET)" ROLE=architect >&2 || { printf 'NO-GO: demo-review — architect run failed\n'; exit 1; }
	@printf 'GO: demo-review\n'

# The one real L1 run this ADR calls for beyond dev-worker's (docs/adr/0021
# §9): the reviewer fixture L0 replays forever after. record's own
# precedent: refuses at L0, requires .crew/<ticket>/pr-opened.env from a
# prior `record TICKET=<id>` (the PR the reviewer reads). Takes any CLI
# harness and keeps record's layout (docs/adr/0041 §10).
.PHONY: record-review
record-review: ## Record a role's review verdict as a fixture: TICKET=<id> [ROLE=test-reviewer] (docs/adr/0021 §9, 0041 §10)
	$(call record_guard,record-review)
	@$(MAKE) --no-print-directory up
	@role="$(ROLE)"; [ -n "$$role" ] || role=test-reviewer; \
	$(MAKE) --no-print-directory review TICKET="$(TICKET)" ROLE="$$role" >&2 || { \
		printf 'NO-GO: record-review — the review step did not reach a verdict\n'; exit 1; \
	}; \
	dest="fixtures/$(TICKET)/$$role"; \
	[ "$(AGENT_HARNESS)" = "gemini-cli" ] || dest="$$dest/$(AGENT_HARNESS)"; \
	mkdir -p "$$dest"; \
	cp ".crew/$(TICKET)/review/input.md" "$$dest/input.md"; \
	cp .crew/$(TICKET)/review/out/* "$$dest/" 2>/dev/null || true; \
	printf 'lights-out: fixture written to %s\n' "$$dest" >&2; \
	printf 'GO: record-review\n'

# The ADK roles (docs/adr/0020). triage and promote need no engine socket:
# the roles' tools touch nothing, the drivers read files and, for promote,
# the Envoy admin port through PLANE_HOST.
.PHONY: triage
triage: ## Run the ops-triage role on an alert: ALERT=<file> → .crew/triage/<name>/incident.json (docs/adr/0020 §3)
	@ALERT="$(ALERT)" sh core/triage.sh

# The M7 acceptance for the alert half of the done-when: every alert case
# of the ops-triage evalset, through `triage`, with the plane unreachable
# (PLANE_HOST=nowhere.invalid), each incident's proposal compared with
# the tool the evalset expects. A triage that touched the plane could
# not pass; a replay keyed on anything but the eval's text could not
# either (docs/adr/0020 §3).
.PHONY: demo-triage
demo-triage: ## Prove a fake alert becomes an incident with ranked hypotheses and a proposal, no plane (docs/adr/0020 §3)
	@evalset=adapters/adk/agents/ops_triage/evals/alerts.evalset.json; \
	[ -f "$$evalset" ] || { printf 'NO-GO: demo-triage — no %s\n' "$$evalset"; exit 1; }; \
	mkdir -p .crew/triage-alerts; \
	for id in $$(jq -r '.eval_cases[].eval_id' "$$evalset"); do \
		alert=".crew/triage-alerts/$$id.json"; \
		jq -r --arg id "$$id" '.eval_cases[] | select(.eval_id == $$id) | .conversation[0].user_content.parts[0].text' "$$evalset" > "$$alert"; \
		want=$$(jq -r --arg id "$$id" '.eval_cases[] | select(.eval_id == $$id) | .conversation[0].intermediate_data.tool_uses[-1].name' "$$evalset"); \
		case "$$want" in propose_rollback) want=rollback ;; propose_hold) want=hold ;; esac; \
		out=$$(PLANE_HOST=nowhere.invalid ALERT="$$alert" sh core/triage.sh) || { \
			printf '[demo-triage] %s: %s\n' "$$id" "$$out" >&2; \
			printf 'NO-GO: demo-triage — %s did not produce an incident\n' "$$id"; exit 1; }; \
		got=$$(jq -r .proposal.action ".crew/triage/$$id/incident.json"); \
		n=$$(jq '.hypotheses | length' ".crew/triage/$$id/incident.json"); \
		printf '[demo-triage] %s: %s hypotheses, proposal %s (expected %s)\n' "$$id" "$$n" "$$got" "$$want" >&2; \
		[ "$$got" = "$$want" ] || { printf 'NO-GO: demo-triage — %s proposed %s, expected %s\n' "$$id" "$$got" "$$want"; exit 1; }; \
	done; \
	printf 'GO: demo-triage\n'

# Ops without the plane (docs/adr/0044). `incident` builds the document
# the ops-triage role reads from a release record, a window and a log
# excerpt; `postmortem` runs the role on it and files each action item as a
# ticket. Neither needs the engine socket or the plane (TICKET= on
# `incident` is the one option that reads the state store).
.PHONY: incident
incident: ## Build an incident document from the release record: NAME=<slug> FROM=<utc> TO=<utc> [LOGS=<file>] [TICKET=<id>, the one option that needs the plane] (docs/adr/0044 §2)
	@NAME="$(NAME)" FROM="$(FROM)" TO="$(TO)" LOGS="$(LOGS)" TICKET="$(if $(filter command line,$(origin TICKET)),$(TICKET))" sh core/incident.sh

.PHONY: postmortem
postmortem: ## Run the ops-triage role on an incident document: INCIDENT=<file> → .crew/postmortem/<name>/postmortem.md and one ticket per action item (docs/adr/0044 §5)
	@INCIDENT="$(INCIDENT)" sh core/postmortem.sh

# The incident captured from a real `demo-rollback` run is committed under
# fixtures/incidents/ (its README says when and how). The role answers from
# the recording `record-postmortem` makes at L1; until it exists this says
# so and names the command, and a hand-written answer is never a stand-in
# (docs/adr/0044 §7). The tickets go to a copy of tickets/, so the checkout
# stays clean and the next ticket number stays the owner's; the second run
# proves a re-run files nothing. Not a `demo` stage (ADR 0003).
.PHONY: demo-postmortem
demo-postmortem: ## Prove the incident captured from demo-rollback becomes a post mortem and a fix ticket, no plane (docs/adr/0044 §7)
	@incident=fixtures/incidents/demo-rollback.json; \
	[ -f "$$incident" ] || { printf 'NO-GO: demo-postmortem — no %s\n' "$$incident"; exit 1; }; \
	work=.crew/demo-postmortem; rm -rf "$$work"; mkdir -p "$$work/tickets"; cp tickets/*.md "$$work/tickets/"; \
	run() { PLANE_HOST=nowhere.invalid TICKETS_DIR="$$work/tickets" POSTMORTEM_OUT="$$work/out" INCIDENT="$$incident" sh core/postmortem.sh 2>"$$work/stderr"; }; \
	out=$$(run) || { \
		cat "$$work/stderr" >&2; \
		if grep -q 'no recorded responses' "$$work/stderr"; then \
			printf 'NO-GO: demo-postmortem — no recording of the ops-triage role for %s; at L0 it replays fixtures/adk/ops-triage, record it with ./go record-postmortem PROFILE=l1\n' "$$incident"; \
		else \
			printf 'NO-GO: demo-postmortem — %s\n' "$${out#NO-GO: postmortem — }"; \
		fi; exit 1; }; \
	cat "$$work/stderr" >&2; printf '[demo-postmortem] %s\n' "$$out" >&2; \
	name=$$(jq -r .name "$$incident"); pm="$$work/out/$$name/postmortem.json"; \
	jq -e '[.timeline[].anchor | startswith("release:")] | any' "$$pm" >/dev/null || { printf 'NO-GO: demo-postmortem — no timeline entry is anchored to the release\n'; exit 1; }; \
	for id in $$(jq -r '.actions[].ticket' "$$pm"); do \
		f="$$work/tickets/$$id.md"; \
		grep -q "^- Origin: postmortem $$name action " "$$f" || { printf 'NO-GO: demo-postmortem — ticket %s carries no origin line\n' "$$id"; exit 1; }; \
		printf '[demo-postmortem] fix ticket %s\n' "$$f" >&2; \
	done; \
	before=$$(ls "$$work/tickets" | wc -l); ids=$$(jq -r '[.actions[].ticket] | join(" ")' "$$pm"); \
	out=$$(run) || { printf 'NO-GO: demo-postmortem — the second run did not pass: %s\n' "$$out"; exit 1; }; \
	[ "$$(ls "$$work/tickets" | wc -l)" = "$$before" ] && [ "$$(jq -r '[.actions[].ticket] | join(" ")' "$$pm")" = "$$ids" ] \
		|| { printf 'NO-GO: demo-postmortem — a re-run filed new tickets\n'; exit 1; }; \
	printf 'GO: demo-postmortem\n'

# Records the post mortem of every incident under fixtures/incidents/ at L1
# (docs/adr/0044 §7): writes the evalset that holds them, one case per
# file, records the ops-triage role through `record-evals` (which also
# re-records the alert cases under the current prompt), and keeps the
# evalset only if the eval passes. Refuses at L0 as `record-evals` does.
.PHONY: record-postmortem
record-postmortem: ## Record the ops-triage role's post mortem of fixtures/incidents/*.json at L1 and keep it only if the eval passes (docs/adr/0044 §7)
	@if [ "$(AGENT_HARNESS)" = "replay" ]; then \
		printf 'NO-GO: record-postmortem — recording a replay is circular; PROFILE=l1\n'; exit 1; \
	fi
	@ls fixtures/incidents/*.json >/dev/null 2>&1 || { printf 'NO-GO: record-postmortem — no incident under fixtures/incidents/\n'; exit 1; }
	@evalset=adapters/adk/agents/ops_triage/evals/postmortem.evalset.json; \
	for f in fixtures/incidents/*.json; do jq -nc --arg id "$$(jq -r .name "$$f")" --rawfile text "$$f" \
		'{eval_id: $$id, conversation: [{invocation_id: $$id, user_content: {role: "user", parts: [{text: $$text}]}, intermediate_data: {tool_uses: [{name: "file_postmortem", args: {}}], intermediate_responses: []}}], session_input: {app_name: "ops_triage", user_id: "lights-out", state: {}}}'; done \
		| jq -s '{eval_set_id: "postmortems", name: "postmortems", description: "The ops-triage role: the post mortem of an incident document captured from a real release (fixtures/incidents/, docs/adr/0044 §7). The expected trajectory is one file_postmortem call, written from the ADR.", eval_cases: .}' > "$$evalset"; \
	if $(MAKE) --no-print-directory record-evals ROLES=ops_triage >&2; then \
		printf 'GO: record-postmortem\n'; \
	else \
		rm -f "$$evalset"; printf 'NO-GO: record-postmortem — the eval did not pass; the evalset was removed (recordings under .crew/adk/)\n'; exit 1; \
	fi

.PHONY: promote
promote: ## go-for-launch: the brief, then the human answer — GO_FOR_LAUNCH=GO|NO-GO or the terminal — then 100% or rollback (docs/adr/0020 §4)
	@sh core/promote.sh

# The go-for-launch proof (docs/adr/0020 §4): a release that verify passes
# (kill switch on, the demo-hold setup) is held at 25%%, promoted with the
# human answered by GO_FOR_LAUNCH=GO, checked at 100%% of traffic, then
# rolled back — the un-promote — and checked at 0. Not a `demo` stage:
# plan §2's criterion is the rollback (docs/adr/0014, 0020 §4).
.PHONY: demo-launch
demo-launch: ## Prove go-for-launch: held canary, brief, GO → 100%, then the un-promote (docs/adr/0020 §4)
	@backup=$$(mktemp); \
	cp "$(FLAGS_FILE)" "$$backup"; \
	restore() { [ -f "$$backup" ] || return 0; cat "$$backup" > "$(FLAGS_FILE)"; rm -f "$$backup"; }; \
	trap 'rc=$$?; restore; [ "$$rc" -eq 0 ] || sh "adapters/$(DELIVERY)/rollback.sh" >&2' EXIT; \
	trap 'exit 130' INT TERM; \
	$(MAKE) --no-print-directory kill-switch || exit 1; \
	stable=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(APP_URL)/healthz" | jq -r .version); \
	printf '[demo-launch] stable is %s; releasing %s with %s off\n' "$$stable" "$(CANARY_VERSION)" "$(WORKLOAD_KILL_SWITCH)" >&2; \
	if ! $(MAKE) --no-print-directory release CANARY_VERSION=$(CANARY_VERSION); then \
		printf 'NO-GO: demo-launch — release rolled back; expected a hold at 25%%\n'; exit 1; \
	fi; \
	if ! GO_FOR_LAUNCH=GO $(MAKE) --no-print-directory promote; then \
		printf 'NO-GO: demo-launch — go-for-launch did not promote; see the brief and the reason above\n'; exit 1; \
	fi; \
	canary_w=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(ENVOY_ADMIN_URL)/runtime" | jq -r '.entries["weights.canary"].final_value // "none"'); \
	[ "$$canary_w" = "100" ] || { printf 'NO-GO: demo-launch — Envoy reports weights.canary=%s after promote, want 100\n' "$$canary_w"; exit 1; }; \
	ok=0; total=10; i=0; \
	while [ "$$i" -lt "$$total" ]; do \
		v=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(APP_URL)/healthz" | jq -r .version); \
		[ "$$v" = "$(CANARY_VERSION)" ] && ok=$$((ok + 1)); \
		i=$$((i + 1)); \
	done; \
	[ "$$ok" -eq "$$total" ] || { printf 'NO-GO: demo-launch — only %s/%s healthz through Envoy report %s at 100%%\n' "$$ok" "$$total" "$(CANARY_VERSION)"; exit 1; }; \
	printf '[demo-launch] %s at 100%%: %s/%s healthz samples; now the un-promote\n' "$(CANARY_VERSION)" "$$ok" "$$total" >&2; \
	sh adapters/$(DELIVERY)/rollback.sh; \
	canary_w=$$(curl -fsS --connect-timeout 1 --max-time 3 "$(ENVOY_ADMIN_URL)/runtime" | jq -r '.entries["weights.canary"].final_value // "none"'); \
	[ "$$canary_w" = "0" ] || { printf 'NO-GO: demo-launch — Envoy reports weights.canary=%s after the un-promote, want 0\n' "$$canary_w"; exit 1; }; \
	printf 'GO: demo-launch\n'

# The M12 acceptance command (docs/adr/0030 §7), made M13's (docs/adr/0037
# §10), outside `./go demo` (ADR 0003): workloads/minimal copied into a
# fresh git repository outside this checkout, attached the way a user
# attaches theirs, and run through the same steps. demo-rollback already
# proves a canary rolls back on *some* NO-GO; this additionally proves the
# failing stage is specifically an error_rate NO-GO — "no traffic" would
# also make demo-rollback pass (the ADR 0014 lesson) but would mean the two
# versions' metrics were never actually compared. --remove-orphans on both
# `down` calls: a later `up` must not re-mount an anonymous volume
# Prometheus already wrote into, or verify could join two workloads'
# backend:8000 series (docs.docker.com, verified 2026-09-23). WORKLOAD is
# passed down explicitly, and the image tags dropped from the environment:
# this make computed them from this checkout before the clone existed, and
# export would hand those values to every child.
.PHONY: demo-seam
demo-seam: ## Prove attach and the Workload port on workloads/minimal, as a repository of its own (docs/adr/0030 §7, 0037 §10)
	@if [ -e "$(ATTACHED)" ]; then \
		printf 'NO-GO: demo-seam — %s is attached at %s; demo-seam attaches its own repository there: ./go detach first\n' \
			"$$(git -C "$(ATTACHED)" remote get-url origin 2>/dev/null || echo 'a repository')" "$(ATTACHED)"; \
		exit 1; \
	fi
	@rm -f "$(RELEASE_RECORD)"
	@src=$$(mktemp -d); \
	trap 'rm -rf "$$src" "$(ATTACHED)"' EXIT; \
	trap 'exit 130' INT TERM; \
	cp -R workloads/minimal/. "$$src/" && \
	git -C "$$src" init -q -b main && git -C "$$src" add -A && \
	git -C "$$src" -c user.name=lights-out-demo -c user.email=demo@lights-out.invalid -c commit.gpgsign=false \
		commit -q -m 'workloads/minimal, as a repository of its own' || { printf 'NO-GO: demo-seam — could not make a repository of workloads/minimal\n'; exit 1; }; \
	printf '[demo-seam] workloads/minimal is now the repository %s, outside this checkout\n' "$$src" >&2; \
	sh core/attach.sh "$$src" "$(ATTACHED)" || { printf 'NO-GO: demo-seam — attach refused %s\n' "$$src"; exit 1; }; \
	for step in attached down up smoke verify demo-rollback demo-gates; do \
		env -u APP_TAG -u CANARY_TAG -u CANARY_IMAGE \
			$(MAKE) --no-print-directory "$$step" WORKLOAD="$(ATTACHED)" || { printf 'NO-GO: demo-seam — %s failed on the attached repository\n' "$$step"; exit 1; }; \
	done; \
	verdict=$$(jq -r '.stages[-1].verdict // empty' "$(RELEASE_RECORD)" 2>/dev/null); \
	case "$$verdict" in \
		"NO-GO: slo — error_rate "*) : ;; \
		*) printf 'NO-GO: demo-seam — last release stage was [%s], want an error_rate NO-GO\n' "$$verdict"; exit 1 ;; \
	esac; \
	$(MAKE) --no-print-directory down WORKLOAD="$(ATTACHED)" || exit 1; \
	printf 'GO: demo-seam\n'

# The M20 acceptance (docs/adr/0042 §10), outside `./go demo` (ADR 0003):
# each example repository copied into a fresh git repository outside the
# checkout, attached, judged on its committed contract (the compose or
# image form, through the engine), drafted by init and judged again on the
# draft with DOCKER_HOST aimed at a path that does not exist, so a gate
# that reached for the engine would NO-GO instead of passing in silence.
# Init plus the draft's gates are timed against INIT_BUDGET_SECONDS: plan
# §16's second golden-rule measure, 300 s, an owner decision (2026-11-03)
# to be re-measured at the first run; 0 disables the verdict.
INIT_BUDGET_SECONDS ?= 300

.PHONY: demo-init
demo-init: ## Prove attach, init and gates without the plane on workloads/minimal and workloads/minimal-js, timed (docs/adr/0042 §10)
	@if [ -e "$(ATTACHED)" ]; then \
		printf 'NO-GO: demo-init — %s is attached at %s; demo-init attaches its own repositories there: ./go detach first\n' \
			"$$(git -C "$(ATTACHED)" remote get-url origin 2>/dev/null || echo 'a repository')" "$(ATTACHED)"; \
		exit 1; \
	fi
	@src=$$(mktemp -d); \
	trap 'rm -rf "$$src" "$(ATTACHED)"' EXIT; \
	trap 'exit 130' INT TERM; \
	report=""; \
	for w in minimal minimal-js; do \
		rm -rf "$$src/$$w" "$(ATTACHED)"; mkdir -p "$$src/$$w"; \
		cp -R "workloads/$$w/." "$$src/$$w/" && \
		git -C "$$src/$$w" init -q -b main && git -C "$$src/$$w" add -A && \
		git -C "$$src/$$w" -c user.name=lights-out-demo -c user.email=demo@lights-out.invalid -c commit.gpgsign=false \
			commit -q -m "workloads/$$w, as a repository of its own" || { printf 'NO-GO: demo-init — could not make a repository of workloads/%s\n' "$$w"; exit 1; }; \
		printf '[demo-init] workloads/%s is now the repository %s, outside this checkout\n' "$$w" "$$src/$$w" >&2; \
		sh core/attach.sh "$$src/$$w" "$(ATTACHED)" || { printf 'NO-GO: demo-init — attach refused %s\n' "$$src/$$w"; exit 1; }; \
		out=$$($(MAKE) --no-print-directory attached WORKLOAD="$(ATTACHED)") || { printf 'NO-GO: demo-init — %s: the committed contract fails its check: %s\n' "$$w" "$$out"; exit 1; }; \
		printf '[demo-init] %s: %s\n' "$$w" "$$out" >&2; \
		out=$$($(MAKE) --no-print-directory gates WORKLOAD="$(ATTACHED)" GATES_OUT="$$src/$$w-gates-committed") || { printf 'NO-GO: demo-init — %s: gates on the committed contract: %s\n' "$$w" "$$out"; exit 1; }; \
		printf '[demo-init] %s, committed contract: %s\n' "$$w" "$$out" >&2; \
		committed=$$out; \
		start=$$(date +%s); \
		out=$$($(MAKE) --no-print-directory init WORKLOAD="$(ATTACHED)") || { printf 'NO-GO: demo-init — %s: init: %s\n' "$$w" "$$out"; exit 1; }; \
		printf '[demo-init] %s: %s\n' "$$w" "$$out" >&2; \
		out=$$(env DOCKER_HOST=unix:///nonexistent/lights-out-no-engine.sock \
			$(MAKE) --no-print-directory gates WORKLOAD="$(ATTACHED)" GATES_OUT="$$src/$$w-gates-draft") || { printf 'NO-GO: demo-init — %s: gates on the draft, no engine: %s\n' "$$w" "$$out"; exit 1; }; \
		elapsed=$$(( $$(date +%s) - start )); \
		printf '[demo-init] %s, draft, no engine: %s (%ss for init and gates)\n' "$$w" "$$out" "$$elapsed" >&2; \
		where=$$(cat "$$src/$$w-gates-draft/toolchain.where" 2>/dev/null || echo unknown); \
		[ "$$where" = workbench ] || { printf 'NO-GO: demo-init — %s: the draft ran in %s, want workbench\n' "$$w" "$$where"; exit 1; }; \
		if [ "$(INIT_BUDGET_SECONDS)" -gt 0 ] && [ "$$elapsed" -gt "$(INIT_BUDGET_SECONDS)" ]; then \
			printf 'NO-GO: demo-init — %s: init and gates took %ss, over the %ss budget (plan §16)\n' "$$w" "$$elapsed" "$(INIT_BUDGET_SECONDS)"; exit 1; \
		fi; \
		report="$$report$${report:+, }$$w $${elapsed}s in workbench (committed contract: $${committed#GO: gates — })"; \
		rm -rf "$(ATTACHED)"; \
	done; \
	printf 'GO: demo-init — %s\n' "$$report"

# What `./go init [<path>]` runs once the clone exists (docs/adr/0042 §6):
# the gates half of .lights-out/ drafted into the clone, every value with
# its provenance, MUTATION_BASELINE measured, then the contract check.
.PHONY: init
init: ## Draft the attached repository's .lights-out/ in the clone, baseline measured, never in your repository: ./go init <path> (docs/adr/0042 §6)
	@sh core/init.sh

# What `./go attach <path>` runs once the clone exists (docs/adr/0037 §1,
# §7): the attached repository's contract, checked the way ci/workloads.sh
# checks the ones in this checkout.
.PHONY: attached
attached: ## Check the attached repository's .lights-out/ contract (docs/adr/0037 §7)
	@[ -n "$(WORKLOAD_ATTACHED)" ] || { printf 'NO-GO: attached — WORKLOAD=%s is not an attached repository; ./go attach <path> first\n' "$(WORKLOAD)"; exit 1; }
	@out=$$(sh ci/workloads.sh "$(WORKLOAD)") || { printf 'NO-GO: attached — %s\n' "$${out#NO-GO: workloads — }"; exit 1; }; \
	printf 'GO: attached — %s at %s, contract in %s\n' \
		"$$(git -C "$(WORKLOAD)" remote get-url origin 2>/dev/null || echo "$(WORKLOAD)")" \
		"$$(git -C "$(WORKLOAD)" rev-parse --short HEAD)" "$(WORKLOAD_CONTRACT)"

# The CodeHost conformance suite (docs/adr/0043 §5): ten cases on
# whichever CODE_HOST is set, a real branch, PR, statuses and merge on it.
# Like delivery-conformance, the suite refuses to run unless this target
# sets CONFORMANCE=1, so ci/contract.sh's cold run stays instant. At L0
# against Gitea (ci.yml's launch job, plane up); with CODE_HOST=github
# against the attached throwaway repository (the nightly).
.PHONY: codehost-conformance
codehost-conformance: ## Ten-case CodeHost conformance suite on the CODE_HOST set: Gitea at L0 (plane up), CODE_HOST=github on an attached throwaway repository (docs/adr/0043 §5)
	@CONFORMANCE=1 sh gates/codehost-conformance.sh

# The M8 acceptance command (docs/adr/0026 §4): the suite refuses to run by
# accident (ci/contract.sh's cold run of every gates/*.sh sees CONFORMANCE
# unset and NO-GOes instantly), so this target is the one place CONFORMANCE=1
# is set. Not a ci-% self-check and not a demo-* stage: it stages real
# releases on the plane, four to five minutes (docs/adr/0026 §7), and the
# acceptance command is `./go delivery-conformance` (PROFILE=l2 at L2), never
# a bare `make`/`sh gates/…` invocation.
.PHONY: delivery-conformance
delivery-conformance: ## Six-case Delivery/Flags conformance suite, real releases on the plane (docs/adr/0026)
	@CONFORMANCE=1 sh gates/delivery-conformance.sh

# Every CLI harness against a scripted model (docs/adr/0041 §8): its image is
# built, its run.sh driven nine ways, no key, no account. CONFORMANCE=1 for
# the same reason as above. The compose view is the root file plus the
# smallest workload fragment that defines `backend` (the plane services
# depend on it, so the root file alone does not load: a correction to the
# ADR) and names no harness service, so running a harness starts nothing
# beside it; the worker image is built on the workbench, not the workload's
# toolchain. CONFORMANCE_HARNESS=<name> runs one. The suite is proved first,
# on a made-up harness and a stand-in for compose, to accept a good harness
# and refuse seventeen wrong ones (gates/harness-conformance/proof.sh): a
# suite that cannot fail would say GO to everything.
.PHONY: harness-conformance
harness-conformance: ## Every CLI harness against a scripted model, nine cases, no key: [CONFORMANCE_HARNESS=<name>] (docs/adr/0041 §8)
	@sh gates/harness-conformance/proof.sh
	@CONFORMANCE=1 \
		WORKLOAD=workloads/minimal WORKLOAD_CONTRACT=workloads/minimal/.lights-out \
		COMPOSE_FILE=compose.yaml:workloads/minimal/.lights-out/compose.yaml \
		HARNESS_BASE_IMAGE=$(WORKBENCH_IMAGE) \
		sh gates/harness-conformance.sh

# Records the ADK roles' model responses at L1 (docs/adr/0020 §2), one
# role at a time: ci/evals.sh with ADK_RECORD=1 writes fixtures/adk/<role>/
# while the eval runs; a role whose eval fails has its new recordings
# moved to .crew/adk/<role>/ for study, never committed. Refuses at L0
# (recording a replay is circular, the `record` precedent) and refuses a
# fixture that contains a key: each of the three provider keys that is
# set and not the placeholder (docs/adr/0040 §7; an empty key would match
# every file). MODEL_PROVIDER and MODEL come from the profile, .env or
# the command line: `./go record-evals PROFILE=l1 MODEL_PROVIDER=anthropic
# MODEL=claude-sonnet-5-5` records the ADK roles on Claude.
.PHONY: record-evals
record-evals: ## Record the ADK roles' responses at L1 and keep them only if the eval passes: [ROLES="ops_triage release"] [MODEL_PROVIDER=… MODEL=…] (docs/adr/0020 §2, 0040 §7)
	@if [ "$(AGENT_HARNESS)" = "replay" ]; then \
		printf 'NO-GO: record-evals — recording a replay is circular; PROFILE=l1\n'; exit 1; \
	fi
	@roles="$(ROLES)"; [ -n "$$roles" ] || roles=$$(ls -d adapters/adk/agents/*/evals | awk -F/ '{print $$4}'); \
	failed=""; \
	for r in $$roles; do \
		role=$$(printf '%s' "$$r" | tr _ -); \
		before=$$(mktemp); ls fixtures/adk/$$role 2>/dev/null | sort > "$$before"; \
		out=$$(ADK_RECORD=1 ROLES="$$r" sh ci/evals.sh) || true; \
		printf '[record-evals] %s: %s\n' "$$role" "$$out" >&2; \
		for key in "$$GEMINI_API_KEY" "$$ANTHROPIC_API_KEY" "$$OPENAI_API_KEY"; do \
			[ -n "$$key" ] && [ "$$key" != replace-me ] || continue; \
			if grep -rq -- "$$key" fixtures/adk/$$role 2>/dev/null; then \
				rm -rf fixtures/adk/$$role; printf 'NO-GO: record-evals — a key appeared in a %s recording; deleted\n' "$$role"; exit 1; \
			fi; \
		done; \
		case "$$out" in \
			"GO: evals") ;; \
			*) mkdir -p .crew/adk/$$role; \
			   for f in $$(ls fixtures/adk/$$role 2>/dev/null); do \
			     grep -qx "$$f" "$$before" && git diff --quiet -- fixtures/adk/$$role/$$f 2>/dev/null || mv fixtures/adk/$$role/$$f .crew/adk/$$role/; \
			   done; \
			   git checkout -q -- fixtures/adk/$$role 2>/dev/null || true; \
			   failed="$$failed $$role" ;; \
		esac; \
		rm -f "$$before"; \
	done; \
	[ -z "$$failed" ] || { printf 'NO-GO: record-evals — eval failed for:%s (recordings kept under .crew/adk/)\n' "$$failed"; exit 1; }; \
	printf 'GO: record-evals\n'
