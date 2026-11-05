#!/bin/sh
# Spike: the guard exactly as the other harnesses run it; agy's payload unchanged.
input=$(cat)
printf '%s\n' "$input" >> "$SPIKE_OUT/pretool-stdin.jsonl"
printf '%s' "$input" | sh "$SPIKE_DIR/guard.sh"
rc=$?
printf 'raw rc=%s\n' "$rc" >> "$SPIKE_OUT/hook-rc.log"
exit $rc
