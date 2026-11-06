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
  jq -n --arg g "$1" --arg d "$SPIKE_DIR" --arg gn "${GUARD_NAME:-guard}" '{
    ($gn): {PreToolUse: [{matcher: "*", hooks: [{type: "command", command: $g}]}]},
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
  jq -n --arg ws "$ws" --argjson extra "${SETTINGS_EXTRA:-{\}}" '{modelProvider: "gemini", trustedWorkspaces: [$ws], showTips: false, showFeedbackSurvey: false} + $extra' \
    > "$C/home/.gemini/antigravity-cli/settings.json"
  case $guard in
    raw) hooks_json "$SPIKE_DIR/hook-raw.sh" > "$C/home/.gemini/config/hooks.json" ;;
    shim) hooks_json "$SPIKE_DIR/hook-shim.sh" > "$C/home/.gemini/config/hooks.json" ;;
  esac
  if [ -n "${ROLE_MARKERS:-}" ]; then
    printf 'Rule M1: ROLE-MARKER-HOME-GEMINI-MD\n' > "$C/home/.gemini/GEMINI.md"
    printf 'Rule M2: ROLE-MARKER-AGY-GEMINI-MD\n' > "$C/home/.gemini/antigravity-cli/GEMINI.md"
    printf 'Rule M3: ROLE-MARKER-HOME-AGENTS-MD\n' > "$C/home/.gemini/AGENTS.md"
  fi
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
    echo "stub served/paths:"; jq -c '{n,method,path,served,op,tool_names: (.tool_names|length)}' "$C/requests.jsonl" 2>/dev/null | head -12
    echo "gen config:"; for b in "$C"/bodies/*; do jq -c '.generationConfig' "$b" 2>/dev/null; done | sort | uniq -c
    echo "db model strings:"; for db in "$C"/home/.gemini/antigravity-cli/conversations/*.db; do [ -f "$db" ] && python3 "$SPIKE_DIR/dbdump.py" "$db" && python3 "$SPIKE_DIR/pbdump.py" "$db" > "$C/pb.txt" 2>&1; done
    echo "key canary in home: $(grep -rlF -- "${KEY:-$CANARY_KEY}" "$C/home" 2>/dev/null | sed "s|$C/||" | tr '\n' ' ')"
    echo "tokens.log:"; cat "$C/tokens.log" 2>/dev/null
    for m in HOME-GEMINI-MD AGY-GEMINI-MD HOME-AGENTS-MD; do echo "marker $m in bodies: $(grep -l "ROLE-MARKER-$m" "$C"/bodies/* 2>/dev/null | wc -l)"; done
    echo "tool results:"; for b in "$C"/bodies/*; do jq -c '[.contents[]?.parts[]? | .functionResponse // empty] | .[-1] // empty' "$b" 2>/dev/null | cut -c1-600; done | sort -u
    echo "home tree:"; (cd "$C/home" && find . -type f | grep -v '/cache/' | head -30)
    for f in PreInvocation PostInvocation SessionStart Stop PostToolUse pretool; do [ -f "$C/$f-stdin.jsonl" ] && { echo "payload $f:"; head -n 1 "$C/$f-stdin.jsonl" | cut -c1-900; }; done
    echo "stdout tail:"; tail -c 1500 "$C/stdout.txt"; echo
    echo "stderr tail:"; tail -c 1500 "$C/stderr.txt"; echo
  } | tee "$C/summary.txt"
}

# --- install, pinned and verified ------------------------------------------
if [ -z "$ONLY" ] || [ -n "${INSTALL:-}" ]; then
  VER=1.5.1
  SHA=2a64ccfc5f6679c253769f0760614af5de04d8dc1eeeec9b9752ba4c8dd92250
  curl -fsSL -o "/tmp/agy.tgz" "https://github.com/google-antigravity/antigravity-cli/releases/download/$VER/agy_cli_linux_x64.tar.gz"
  echo "$SHA  /tmp/agy.tgz" | sha256sum -c - | tee "$R/install.txt"
  tar tzvf "/tmp/agy.tgz" | tee -a "$R/install.txt"
  mkdir -p /tmp/x && tar xzf /tmp/agy.tgz -C /tmp/x
  sudo install -m 0755 /tmp/x/antigravity /usr/local/bin/agy
  { file /usr/local/bin/agy; ldd /usr/local/bin/agy; agy --version; } 2>&1 | tee -a "$R/install.txt"
  agy --help > "$R/help.txt" 2>&1
  for sub in models install hooks; do agy "$sub" --help > "$R/help-$sub.txt" 2>&1; done
fi

if [ -n "$ONLY" ]; then agy --help; fi
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
# --- iteration 2 -------------------------------------------------------------
# C3: the model writes .agents/hooks.json mid-run, then pushes.
C3_SCRIPT=$(cat <<'JSON'
[{"op":"shell","cmd":"mkdir -p .agents && printf '{\"guard\":{\"enabled\":false}}' > .agents/hooks.json && cat .agents/hooks.json"},
 {"op":"shell","cmd":"git push origin HEAD:refs/heads/spike"},{"op":"final","text":"done"}]
JSON
)
run_case C3 shim "$C3_SCRIPT" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
# T: the network tools, through the guard, without and with permission deny rules.
T_SCRIPT='[{"op":"tool","name":"read_url_content","args":{"Url":"http://127.0.0.1:8089/exfil-T"}},{"op":"tool","name":"search_web","args":{"query":"spike","Query":"spike"}},{"op":"final","text":"done"}]'
for t in T1 T2 T3; do
  case $t in
    T1) x='{}' ;;
    T2) x='{"permissions":{"deny":["read_url_content","search_web"]}}' ;;
    T3) x='{"permissions":{"deny":["read_url_content(*)","search_web(*)"]}}' ;;
  esac
  SETTINGS_EXTRA=$x run_case "$t" shim "$T_SCRIPT" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 120s
  echo "$t GET /exfil seen: $(grep -c exfil "$R/$t/requests.jsonl" 2>/dev/null)" | tee -a "$R/$t/summary.txt"
  [ -f "$R/$t/tools.json" ] && jq -c '.[] | select(.name=="read_url_content" or .name=="search_web" or .name=="run_command" or .name=="view_file")' "$R/$t/tools.json" | cut -c1-700
done
# U: does a writable older binary update itself during a headless run?
if printf ' %s ' "$ONLY" | grep -q ' U '; then
  mkdir -p "$R/u/bin"
  curl -fsSL -o "$R/u/a.tgz" https://github.com/google-antigravity/antigravity-cli/releases/download/1.5.0/agy_cli_linux_x64.tar.gz
  echo "f65e365a67be49abdaf92f5669c3157bd4b9c569f6b9f448340b00ca261b0f01  $R/u/a.tgz" | sha256sum -c -
  tar xzf "$R/u/a.tgz" -C "$R/u" && install -m 0755 "$R/u/antigravity" "$R/u/bin/agy"
  echo "before: $("$R/u/bin/agy" --version) $(sha256sum < "$R/u/bin/agy" | cut -c1-16)"
  WRAP="env PATH=$R/u/bin:$PATH" run_case U none "$FINAL" -p "Say ok." --output-format stream-json --model "$MODEL" --print-timeout 120s
  sleep 30
  echo "after: $("$R/u/bin/agy" --version) $(sha256sum < "$R/u/bin/agy" | cut -c1-16)"
  find "$R/U/home" -type f -size +10M | head; ls -la "$R/u/bin"
fi
# --- iteration 3 -------------------------------------------------------------
for e in low medium high xhigh; do
  run_case "G$e" none "$FINAL" -p "Say ok." --output-format stream-json --model "$MODEL" --effort "$e" --print-timeout 120s
done
run_case S1 shim '[{"op":"tool","name":"search_web","args":{"query":"lights-out spike"}},{"op":"final","text":"done"}]' -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 120s
GUARD_NAME=guard-r4nd0m WS_HOOKS='{"guard":{"enabled":false},"guard-x":{"enabled":false},"ws-start":{"SessionStart":[{"type":"command","command":"touch ws-sessionstart-ran"}]},"ws-pre":{"PreToolUse":[{"matcher":"*","hooks":[{"command":"touch ws-pretool-ran"}]}]}}' \
run_case C4 shim "$PUSH_ENV" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
if printf ' %s ' "$ONLY" | grep -q ' K '; then
  echo "== K: strings in the binary"
  strings -n 6 /usr/local/bin/agy | grep -E '^(AGY|ANTIGRAVITY|JETSKI)_[A-Z0-9_]{3,}$' | sort -u | head -80
  strings -n 6 /usr/local/bin/agy | grep -iE 'auto.?update|disable.?update|update.?check|skip.?update|noupdate|workspace.?hooks|disable.?hooks|disableAllHooks|allowWorkspaceHooks' | sort -u | cut -c1-160 | head -60
fi
# E: which file is the user-level instruction; the file tools and search_web under permissions.deny.
E_SCRIPT=$(jq -nc --arg ws "$R/E2/ws" '[{op:"tool",name:"write_file",args:{Path:($ws+"/x.txt"),Content:"written\n"}},
  {op:"tool",name:"read_file",args:{Path:($ws+"/README")}},{op:"tool",name:"read_file",args:{Path:($ws+"/.env")}},
  {op:"tool",name:"search_web",args:{query:"lights-out spike"}},{op:"final",text:"done"}]')
ROLE_MARKERS=1 run_case E1 shim "$FINAL" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 120s
SETTINGS_EXTRA='{"permissions":{"deny":["search_web"]}}' run_case E2 shim "$E_SCRIPT" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 120s
echo "E2 x.txt: $(cat "$R/E2/ws/x.txt" 2>/dev/null)" | tee -a "$R/E2/summary.txt"
run_case E3 shim "$(printf %s "$E_SCRIPT" | sed "s#/E2/#/E3/#g")" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 120s
# E4: a command longer than WaitMsBeforeAsync's documented 10000 ms ceiling, asked to wait 60000.
E4_SCRIPT='[{"op":"tool","name":"run_command","args":{"CommandLine":"sleep 15; echo slept-15","toolSummary":"Sleep","WaitMsBeforeAsync":60000}},{"op":"tool","name":"run_command","args":{"CommandLine":"sleep 12; echo slept-12","toolSummary":"Sleep","WaitMsBeforeAsync":10000}},{"op":"final","text":"done"}]'
run_case E4 shim "$E4_SCRIPT" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
# E5: a backgrounded command, then manage_task until it is done.
E5_SCRIPT='[{"op":"tool","name":"run_command","args":{"CommandLine":"sleep 14; echo slept-14","toolSummary":"Sleep","WaitMsBeforeAsync":10000}},{"op":"taskstatus"},{"op":"taskstatus"},{"op":"taskstatus","action":"list"},{"op":"final","text":"done"}]'
run_case E5 shim "$E5_SCRIPT" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
cp "$R/E5/taskstatus.log" "$R/E5/taskstatus.txt" 2>/dev/null
# E6: a backgrounded command, then a text answer: what agy sends back when the task ends.
E6_SCRIPT='[{"op":"tool","name":"run_command","args":{"CommandLine":"sleep 12; echo slept-12-out","toolSummary":"Sleep","WaitMsBeforeAsync":10000}},{"op":"final","text":"Final title\n\nFinal body."}]'
run_case E6 shim "$E6_SCRIPT" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
cp "$R/E6/tail.log" "$R/E6/taskstatus.txt" 2>/dev/null
# D: the tool schemas and the gen_metadata fields; D2 the file tools by schema; D3 tokens seen mid-run.
run_case D shim "$FINAL" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 120s
if [ -f "$R/D/tools.json" ]; then
  D2=$(python3 "$SPIKE_DIR/mkscript.py" "$R/D/tools.json" "$R/D2/ws"); echo "D2 script: $D2"
  jq -c '.[] | {name, parameters: (.parameters // .parametersJsonSchema)}' "$R/D/tools.json" > "$R/D/tool-schemas.jsonl"
  run_case D2 shim "$D2" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 120s
  echo "D2 written.txt: $(cat "$R/D2/ws/written.txt" 2>/dev/null)" | tee -a "$R/D2/summary.txt"
fi
SPIKE_MAXT=4 run_case D3 shim '[{"op":"loop"}]' -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 120s
# P: the egress proxy of ADR 0046 (merge/harness-egress): which hosts agy
# asks for with nothing allowed, then a full run with only the stub allowed.
# Go never proxies loopback, so the stub is reached by the runner's address.
if [ -z "$ONLY" ] || printf ' %s ' "$ONLY" | grep -q ' P'; then
  ip=$(hostname -I | awk '{print $1}')
  mkdir -p "$R/egress"; : > "$R/egress/allow"
  EGRESS_DIR=$R/egress python3 "$SPIKE_DIR/egress.py" 3128 2> "$R/egress/log" &
  EG_PID=$!; sleep 1
  px="env HTTPS_PROXY=http://$ip:3128 HTTP_PROXY=http://$ip:3128 https_proxy=http://$ip:3128 http_proxy=http://$ip:3128 NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1"
  BASE_URL= WRAP=$px OUTER_TIMEOUT=90 \
  run_case P1 shim "$PUSH_ENV" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 60s
  printf '%s:8089\n' "$ip" > "$R/egress/allow"
  BASE_URL=http://$ip:8089 WRAP=$px \
  run_case P2 shim "$PUSH_ENV" -p "Do the task." --output-format stream-json --dangerously-skip-permissions --model "$MODEL" --print-timeout 180s
  kill "$EG_PID"; echo "egress decisions:"; sort "$R/egress/log" | uniq -c | tee "$R/egress/summary.txt"
fi
exit 0
