#!/bin/sh
# Spike: records the payload of event $1.
cat >> "$SPIKE_OUT/$1-stdin.jsonl"; printf '\n' >> "$SPIKE_OUT/$1-stdin.jsonl"
