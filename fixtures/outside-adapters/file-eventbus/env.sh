#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# file-eventbus env.sh: file-backed EventBus adapter fixture for cold
# ADR 0058 / ADR 0129 outside/* conformance testing.
set -eu

FILE_EVENTBUS_DIR=${FILE_EVENTBUS_DIR:-.crew/file-eventbus}
FILE_EVENTBUS_ACK_DEADLINE=${FILE_EVENTBUS_ACK_DEADLINE:-${CONFORMANCE_ACK_DEADLINE:-600}}

case "$FILE_EVENTBUS_ACK_DEADLINE" in
  '' | 0* | *[!0-9]*)
    printf 'file-eventbus: invalid ack deadline [%s], want positive integer seconds\n' "$FILE_EVENTBUS_ACK_DEADLINE" >&2
    exit 1
    ;;
esac

eventbus_op() {
  command -v python3 >/dev/null 2>&1 || {
    printf 'file-eventbus: python3 is not on PATH\n' >&2
    exit 1
  }
  python3 -I - "$FILE_EVENTBUS_DIR" "$FILE_EVENTBUS_ACK_DEADLINE" "$@" <<'PY'
import fcntl
import json
import os
import re
import secrets
import sys
import time

bus_dir = sys.argv[1]
deadline = int(sys.argv[2])
op = sys.argv[3]
args = sys.argv[4:]


def fail(msg):
    sys.stderr.write(f"file-eventbus: {msg}\n")
    raise SystemExit(1)


def atomic_write_bytes(path, payload):
    tmp = f"{path}.tmp.{os.getpid()}.{secrets.token_hex(4)}"
    with open(tmp, "wb") as fh:
        fh.write(payload)
    os.replace(tmp, path)


if op == "publish":
    if len(args) != 1:
        fail("usage: publish.sh <data>")
    data = args[0]
    if not data:
        fail("message data must not be empty")
    if "\n" in data or "\r" in data:
        fail("message data must be a single line")
elif op == "ack":
    if len(args) != 1 or not args[0] or re.search(r"\s|/", args[0]) or ".." in args[0]:
        fail("usage: ack.sh <ackId>")
elif op not in ("ready", "pull"):
    fail(f"unknown operation {op!r}")

os.makedirs(bus_dir, exist_ok=True)
queue_dir = os.path.join(bus_dir, "queue")
lease_dir = os.path.join(bus_dir, "leases")
os.makedirs(queue_dir, exist_ok=True)
os.makedirs(lease_dir, exist_ok=True)
lock_path = os.path.join(bus_dir, ".lock")
seq_path = os.path.join(bus_dir, "seq")

with open(lock_path, "a+b") as lock_fh:
    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
    if op == "ready":
        raise SystemExit(0)

    if op == "publish":
        try:
            with open(seq_path, "r", encoding="ascii") as sf:
                seq = int(sf.read().strip() or "0")
        except FileNotFoundError:
            seq = 0
        seq += 1
        atomic_write_bytes(seq_path, f"{seq}\n".encode("ascii"))
        msg_path = os.path.join(queue_dir, f"{seq:012d}.msg")
        atomic_write_bytes(msg_path, args[0].encode("utf-8"))
        raise SystemExit(0)

    if op == "pull":
        now = time.time()
        for name in sorted(os.listdir(queue_dir)):
            if not name.endswith(".msg"):
                continue
            stem = name[:-4]
            msg_path = os.path.join(queue_dir, name)
            lease_path = os.path.join(lease_dir, f"{stem}.json")
            if os.path.isfile(lease_path):
                try:
                    with open(lease_path, "r", encoding="utf-8") as lf:
                        lease = json.load(lf)
                    if float(lease.get("expires_at", 0.0)) > now:
                        continue
                except (OSError, ValueError):
                    pass
            try:
                with open(msg_path, "rb") as mf:
                    raw = mf.read()
            except FileNotFoundError:
                continue
            ack_id = secrets.token_hex(16)
            lease_payload = json.dumps({"ack_id": ack_id, "expires_at": now + deadline}).encode("utf-8")
            atomic_write_bytes(lease_path, lease_payload)
            sys.stdout.buffer.write(ack_id.encode("ascii") + b" " + raw + b"\n")
            sys.stdout.buffer.flush()
            raise SystemExit(0)
        raise SystemExit(0)

    if op == "ack":
        want_ack = args[0]
        for name in sorted(os.listdir(lease_dir)):
            if not name.endswith(".json"):
                continue
            lease_path = os.path.join(lease_dir, name)
            try:
                with open(lease_path, "r", encoding="utf-8") as lf:
                    lease = json.load(lf)
            except (OSError, ValueError):
                continue
            if lease.get("ack_id") == want_ack:
                stem = name[:-5]
                msg_path = os.path.join(queue_dir, f"{stem}.msg")
                try:
                    os.unlink(msg_path)
                except FileNotFoundError:
                    pass
                try:
                    os.unlink(lease_path)
                except FileNotFoundError:
                    pass
                break
        raise SystemExit(0)
PY
}
