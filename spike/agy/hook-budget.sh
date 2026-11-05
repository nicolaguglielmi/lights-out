#!/bin/sh
# Spike: PreInvocation; terminates once invocationNum reaches SPIKE_MAXT.
input=$(cat)
printf '%s\n' "$input" >> "$SPIKE_OUT/PreInvocation-stdin.jsonl"
n=$(printf '%s' "$input" | jq -r '.invocationNum // 0')
if [ "$n" -ge "${SPIKE_MAXT:-1000}" ]; then
  printf 'over MAX_TURNS (%s)\n' "$SPIKE_MAXT" > "$SPIKE_OUT/stop"
  jq -nc '{injectSteps: [{errorMessage: {userMessage: "over MAX_TURNS"}}], terminationBehavior: "terminate"}'
fi
exit 0
