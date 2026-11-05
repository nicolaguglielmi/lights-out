#!/bin/sh
# Spike: agy's {toolCall:{name,args}} -> the guard's {tool_name,tool_input};
# exit 2 -> {"decision":"deny"} on stdout.
input=$(cat)
printf '%s\n' "$input" >> "$SPIKE_OUT/pretool-stdin.jsonl"
err=$(printf '%s' "$input" | jq -c '{tool_name: .toolCall.name, tool_input: .toolCall.args}' | sh "$SPIKE_DIR/guard.sh" 2>&1 >/dev/null)
rc=$?
printf 'shim rc=%s %s\n' "$rc" "$err" >> "$SPIKE_OUT/hook-rc.log"
if [ "$rc" = 2 ]; then
  jq -nc --arg r "$err" '{decision: "deny", reason: $r}'
fi
exit 0
