#!/bin/sh
# Spike: PreInvocation; terminates once invocationNum reaches SPIKE_MAXT, and
# logs what the captured stream-json stdout says was spent so far.
input=$(cat)
printf '%s\n' "$input" >> "$SPIKE_OUT/PreInvocation-stdin.jsonl"
n=$(printf '%s' "$input" | jq -r '.invocationNum // 0')
t=$(jq -R 'fromjson? // empty | .step_update.usage.total_tokens // 0' "$SPIKE_OUT/stdout.txt" 2>/dev/null | awk '{s+=$1} END{print s+0}')
printf 'inv %s tokens %s lines %s\n' "$n" "$t" "$(wc -l < "$SPIKE_OUT/stdout.txt" 2>/dev/null)" >> "$SPIKE_OUT/tokens.log"
if [ "$n" -ge "${SPIKE_MAXT:-1000}" ]; then
  printf 'over MAX_TURNS (%s)\n' "$SPIKE_MAXT" > "$SPIKE_OUT/stop"
  jq -nc '{injectSteps: [{errorMessage: {userMessage: "over MAX_TURNS"}}], terminationBehavior: "terminate"}'
fi
exit 0
