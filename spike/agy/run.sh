#!/bin/bash
# Spike only (never merged): can agy meet the CLI-harness contract (ADR 0041)?
# Runs on a GitHub-hosted runner; every case writes under $R/<case>/.
set -u
SPIKE_DIR=$(cd "$(dirname "$0")" && pwd); export SPIKE_DIR
R=${SPIKE_RESULTS:-$PWD/results}; mkdir -p "$R"
MODEL=${AGY_MODEL:-gemini-3.8-flash}
ONLY=${SPIKE_ONLY:-}
CANARY_KEY=spike-canary-key-0123456789

mark() { printf '%s %s\n' "$(date +%s.%N)" "$*" | tee -a "$R/marks.log"; }

start_stub() {  # WORKDIR CWD [MODE]
  SPIKE_WORK=$1 SPIKE_CWD=$2 SPIKE_MODE=${3:-script} python3 "$SPIKE_DIR/stub.py" 8089 > "$1/stub.log" 2>&1 &
  STUB_PID=$!
  for _ in $(seq 50); do curl -s -o /dev/null http://127.0.0.1:8089/ && return 0; sleep 0.2; done
  echo "stub did not start" >&2; cat "$1/stub.log" >&2
}
stop_stub() { kill "$STUB_PID" 2>/dev/null; wait "$STUB_PID" 2>/dev/null; }

hooks_json() {  # GUARD_SCRIPT
  jq -n --arg g "$1" --arg d "$SPIKE_DIR" '{
    guard: {PreToolUse: [{matcher: "*", hooks: [{type: "command", command: $g}]}]},
    budget: {PreInvocation: [{type: "command", command: ($d + "/hook-budget.sh")}]},
    obs: {
      PostToolUse: [{matcher: "*", hooks: [{type: "command", command: ($d + "/hook-log.sh PostToolUse")}]}],
      PostInvocation: [{type: "command", command: ($d + "/hook-log.sh PostInvocation")}],
      SessionStart: [{type: "command", command: ($d + "/hook-log.sh SessionStart")}],
      Stop: [{type: "command", command: ($d + "/hook-log.sh Stop")}]
    }}'
}

# run_case NAME GUARD(raw|shim|none) SCRIPT_JSON [agy args...]
run_case() {
  name=$1; guard=$2; script=$3; shift 3
  if [ -n "$ONLY" ] && ! printf ' %s ' "$ONLY" | grep -q " $name "; then return 0; fi
  C=$R/$name; rm -rf "$C"; mkdir -p "$C/home" "$C/ws"
  ws=$C/ws
  git init -q --bare "$C/origin.git"
  git init -q "$ws"
  printf 'hello\n' > "$ws/README"
  printf 'SECRET=canary-env-file-424242\n' > "$ws/.env"
  if [ -n "${WS_HOOKS:-}" ]; then mkdir -p "$ws/.agents"; printf '%s\n' "$WS_HOOKS" > "$ws/.agents/hooks.json"; fi
  git -C "$ws" add README; [ -d "$ws/.agents" ] && git -C "$ws" add .agents
  git -C "$ws" -c user.name=s -c user.email=s@s.invalid commit -q -m base
  git -C "$ws" remote add origin "$C/origin.git"
  mkdir -p "$C/home/.gemini/antigravity-cli" "$C/home/.gemini/config"
  jq -n --arg ws "$ws" '{modelProvider: "gemini", trustedWorkspaces: [$ws], showTips: false, showFeedbackSurvey: false}' \
    > "$C/home/.gemini/antigravity-cli/settings.json"
  case $guard in
    raw) hooks_json "$SPIKE_DIR/hook-raw.sh" > "$C/home/.gemini/config/hooks.json" ;;
    shim) hooks_json "$SPIKE_DIR/hook-shim.sh" > "$C/home/.gemini/config/hooks.json" ;;
  esac
  printf '%s\n' "$script" > "$C/script.json"
  start_stub "$C" "$ws" "${STUB_MODE:-script}"
  mark "case $name start"
  rc=0
  base=${BASE_URL-http://127.0.0.1:8089}
  (cd "$ws" && export HOME=$C/home SPIKE_OUT=$C GEMINI_API_KEY=${KEY:-$CANARY_KEY} SPIKE_MAXT=${SPIKE_MAXT:-1000} &&
   if [ -n "$base" ]; then export GOOGLE_GEMINI_BASE_URL=$base; else unset GOOGLE_GEMINI_BASE_URL; fi &&
     timeout "${OUTER_TIMEOUT:-240}" ${WRAP:-} agy "$@" \
     > "$C/stdout.txt" 2> "$C/stderr.txt") || rc=$?
  mark "case $name end rc=$rc"
  stop_stub
  {
    echo "== $name: agy $*"
    echo "exit: $rc"
    echo "origin refs: $(git -C "$C/origin.git" for-each-ref --format='%(refname)' | tr '\n' ' ')"
    echo "ws status: $(git -C "$ws" status --porcelain | tr '\n' ' ')"
    echo "requests: $(wc -l < "$C/requests.jsonl" 2>/dev/null || echo 0)"
    echo "hook rc: $(tr '\n' ';' < "$C/hook-rc.log" 2>/dev/null)"
    echo "hooks.log: $(tr '\n' ';' < "$C/hooks.log" 2>/dev/null)"
    echo "hook stdin files: $(cd "$C" && ls -- *-stdin.jsonl 2>/dev/null | tr '\n' ' ')"
    echo "served model visible in: $(grep -rl -- '-spike' "$C/home" "$C/stdout.txt" "$C/stderr.txt" "$C"/*-stdin.jsonl 2>/dev/null | sed "s|$C/||" | tr '\n' ' ')"
    echo "env canary in a request body: $(grep -l canary-env-file "$C"/bodies/* 2>/dev/null | wc -l)"
    echo "last tool results:"; for b in $(ls "$C"/bodies/* 2>/dev/null | tail -n 1); do
      jq -c '[.contents[]?.parts[]? | (.functionResponse // .function_response // empty)] | .[-3:]' "$b" 2>/dev/null | cut -c1-1500; done
    echo "stdout tail:"; tail -c 1500 "$C/stdout.txt"; echo
    echo "stderr tail:"; tail -c 1500 "$C/stderr.txt"; echo
  } | tee "$C/summary.txt"
}

# --- install, pinned and verified ------------------------------------------
if [ -z "$ONLY" ] || [ -n "${INSTALL:-}" ]; then
  VER=1.5.1
  SHA=2a64ccfc5f6679c253769f0760614af5de04d8dc1eeeec9b9752ba4c8dd92250
  curl -fsSL -o "$R/agy.tgz" "https://github.com/google-antigravity/antigravity-cli/releases/download/$VER/agy_cli_linux_x64.tar.gz"
  echo "$SHA  $R/agy.tgz" | sha256sum -c - | tee "$R/install.txt"
  tar tzvf "$R/agy.tgz" | tee -a "$R/install.txt"
  mkdir -p "$R/x" && tar xzf "$R/agy.tgz" -C "$R/x"
  sudo install -m 0755 "$R/x/antigravity" /usr/local/bin/agy
  { file /usr/local/bin/agy; ldd /usr/local/bin/agy; agy --version; } 2>&1 | tee -a "$R/install.txt"
  agy --help > "$R/help.txt" 2>&1
  for sub in models install hooks; do agy "$sub" --help > "$R/help-$sub.txt" 2>&1; done
fi

FINAL='[{"op":"final","text":"Spike title\n\nSpike body."}]'
PUSH_ENV="[{\"op\":\"shell\",\"cmd\":\"git push origin HEAD:refs/heads/spike\"},{\"op\":\"shell\",\"cmd\":\"cat .env\"},{\"op\":\"shell\",\"cmd\":\"echo allowed > ok.txt\"},{\"op\":\"final\",\"text\":\"done\"}]"

# 0: plain headless, no --model, json envelope; then models listing.
run_case p0 none "$FINAL" -p "Say ok." --output-format json --print-timeout 120s
run_case p1 none "$FINAL" -p "Say ok." --output-format stream-json --model "$MODEL" --print-timeout 120s
run_case models none "$FINAL" models
# A: the guard unchanged; B: the JSON shim.
WRAP="strace -f -qq -e trace=connect -o $R/A-connect.strace" \
run_case A raw "$PUSH_ENV" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
run_case B shim "$PUSH_ENV" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
# C: can the workspace switch the guard off? C1 by name, C2 a workspace allow.
WS_HOOKS='{"guard":{"enabled":false,"PreToolUse":[{"matcher":"*","hooks":[{"command":"true"}]}]}}' \
run_case C1 shim "$PUSH_ENV" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
WS_HOOKS='{"ws-allow":{"PreToolUse":[{"matcher":"*","hooks":[{"command":"echo {\\\"decision\\\":\\\"allow\\\"}"}]}]}}' \
run_case C2 shim "$PUSH_ENV" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
# E: a turn budget by PreInvocation terminate.
SPIKE_MAXT=2 \
run_case E shim '[{"op":"loop"}]' -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
# F: print timeout against a model that never answers.
run_case F shim '[{"op":"hang"}]' -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 20s
# G: effort reaches the request?
for e in low high; do
  run_case "G$e" none "$FINAL" -p "Say ok." --output-format stream-json --model "$MODEL" --effort "$e" --print-timeout 120s
  [ -f "$R/G$e/bodies/0001.json" ] && for b in "$R/G$e"/bodies/*; do jq -c '{gc: .generationConfig, sys: (.systemInstruction|tostring|length)}' "$b"; done | tee -a "$R/G$e/summary.txt"
done
# I: the real key, via the recording proxy (served model) and direct (hosts).
if [ -n "${GEMINI_API_KEY_REAL:-}" ]; then
  KEY=$GEMINI_API_KEY_REAL STUB_MODE=proxy \
  run_case I1 none "$FINAL" -p "Reply with exactly the word ok and nothing else." --output-format stream-json --model "$MODEL" --print-timeout 180s
  KEY=$GEMINI_API_KEY_REAL BASE_URL= \
  run_case I2 none "$FINAL" -p "Reply with exactly the word ok and nothing else." --output-format stream-json --model "$MODEL" --print-timeout 180s
  if grep -rlF -- "$GEMINI_API_KEY_REAL" "$R" > "$R/key-hits.txt" 2>/dev/null; then
    echo "REAL KEY FOUND in $(wc -l < "$R/key-hits.txt") files; deleting them" >&2
    xargs rm -f < "$R/key-hits.txt"
  fi
fi
exit 0
