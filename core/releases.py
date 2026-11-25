"""The release record and the release log (docs/adr/0020 §4, docs/adr/0047
§2 P2 and §3): stdlib only. core/launchpad.sh and core/promote.sh call it
(docs/adr/0052 §3); the shell keeps the plane and the human, this file the
JSON.

    python3 core/releases.py record VERDICT HELD_AT STAGES IMAGE [SBOM [DAST]]
        write RELEASE_RECORD (CANARY_VERSION, STABLE_VERSION; SBOM, the
        digest of the image's SPDX attestation, null when the Registry
        port gives none, docs/adr/0072 §2; DAST, the staging stage's
        verify/dast.sh line, null when DAST is off, §3); a final
        verdict, `held` or `rolled-back`, is also appended to RELEASES_LOG
        with the time and RELEASE_TICKET (null for a release run directly);
        a rolled-back record carries RELEASE_REASON as `reason`, null
        otherwise (docs/adr/0080 §3)
    python3 core/releases.py stage STAGES PCT VERDICT [STARTED_AT] < SLO_STDERR
        print STAGES with one more entry, {pct, verdict, numbers,
        started_at, judged_at, slo}: the numbers are the `slo:` lines of
        verify/slo.sh's stderr, `slo` the same lines as {query, canary,
        stable, delta, max_delta}, max_delta from ERROR_RATE_MAX_DELTA and
        LATENCY_P95_MAX_DELTA (docs/adr/0080 §1)
    python3 core/releases.py running STAGES PCT STARTED_AT JUDGE_AFTER_S IMAGE [DAST]
        write RELEASE_RECORD in-progress with the running stage
        (docs/adr/0080 §2); the log is not touched
    python3 core/releases.py brief
        print the brief rendered from RELEASE_RECORD, the L0 brief: no
        model at L0, the release role writes it from L1
    python3 core/releases.py plane < STATUS
        exit 0 when the plane (the DELIVERY port's status.sh output on
        stdin, docs/adr/0026 §2) runs RELEASE_RECORD's held canary at the
        weight it was held at (ticket 0008); else exit 1 and print why
    python3 core/releases.py answer GO|NO-GO
        record who answered (GO_FOR_LAUNCH_BY, default `unauthenticated`;
        GO_FOR_LAUNCH_VIA, default `cli`) into RELEASE_RECORD, `promoted`
        on GO and `rejected` on NO-GO, and one line of RELEASES_LOG; with
        the reason (GO_FOR_LAUNCH_REASON, null when empty) and the sha256
        and author of PROMOTE_OUT's brief (docs/adr/0079 §1)
    python3 core/releases.py train <cmd> [args...]
        hold ReleaseTrainLock (RELEASE_TRAIN_LOCK, bounded by
        RELEASE_TRAIN_TIMEOUT_S), set LIGHTS_OUT_TRAIN_HELD=1, execute
        <cmd> [args...], and preserve its exit code (docs/adr/0120)

RELEASE_RECORD keeps only the latest record; the log keeps them all and is
never rewritten, only appended.
"""

import errno
import fcntl
import hashlib
import json
import os
import re
import subprocess  # nosec B404 - train runs caller-supplied command with explicit argv (ADR 0120)
import sys
import threading
import time
from datetime import datetime, timezone

# Provenance, external/decision (docs/adr/0120): 15 * STAGE_SECONDS (60s default)
# matching docs/PLAN.md §2 900s release ceiling and TIMEOUT_S=900.
RELEASE_TRAIN_TIMEOUT_S = 900
# Provenance, decision (docs/adr/0120): 50 ms non-blocking flock poll interval
# avoids busy-spinning while waking promptly once a release finishes.
RELEASE_TRAIN_POLL_S = 0.05
TRAIN_HELD_ENV = "LIGHTS_OUT_TRAIN_HELD"

_TRAIN_LOCAL = threading.local()


class ReleaseTrainTimeout(TimeoutError):
    """Raised when ReleaseTrainLock cannot be acquired within RELEASE_TRAIN_TIMEOUT_S."""


def _local_depths():
    depths = getattr(_TRAIN_LOCAL, "depths", None)
    if depths is None:
        depths = {}
        _TRAIN_LOCAL.depths = depths
    return depths


def train_lock_path(env):
    """Path to the release-train flock file (RELEASE_TRAIN_LOCK or beside RELEASE_RECORD)."""
    explicit = (env.get("RELEASE_TRAIN_LOCK") or "").strip()
    if explicit:
        return explicit
    rec_path = paths(env)[0]
    return os.path.join(os.path.dirname(rec_path) or ".", "release-train.lock")


def train_timeout_s(env):
    """Bounded ReleaseTrainLock timeout in seconds from RELEASE_TRAIN_TIMEOUT_S."""
    raw = (env.get("RELEASE_TRAIN_TIMEOUT_S") or "").strip()
    if not raw:
        return float(RELEASE_TRAIN_TIMEOUT_S)
    try:
        val = float(raw)
    except ValueError:
        raise ValueError(f"RELEASE_TRAIN_TIMEOUT_S must be a non-negative number, got {raw!r}") from None
    if val < 0:
        raise ValueError(f"RELEASE_TRAIN_TIMEOUT_S must be >= 0, got {val}")
    return val


class ReleaseTrainLock:
    """Exclusive fcntl.flock lock serializing canary releases across the fleet (docs/adr/0120)."""

    def __init__(self, lock_path=None, timeout_s=None, env=None, poll_s=RELEASE_TRAIN_POLL_S):
        self.env = os.environ if env is None else env
        self.lock_path = str(lock_path) if lock_path else train_lock_path(self.env)
        self.timeout_s = float(timeout_s) if timeout_s is not None else train_timeout_s(self.env)
        if self.timeout_s < 0:
            raise ValueError(f"timeout_s must be >= 0, got {self.timeout_s}")
        self.poll_s = max(0.005, float(poll_s))
        self._fh = None
        self._key = os.path.abspath(self.lock_path)
        self._reentered = False

    def acquire(self):
        if self.env.get(TRAIN_HELD_ENV) == "1":
            return self
        depths = _local_depths()
        if depths.get(self._key, 0) > 0:
            depths[self._key] += 1
            self._reentered = True
            return self
        parent = os.path.dirname(self.lock_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fh = open(self.lock_path, "a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout_s
        try:
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ReleaseTrainTimeout(
                            f"release train lock timed out after {self.timeout_s:.2f}s ({self.lock_path})"
                        ) from None
                    time.sleep(min(self.poll_s, remaining))
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()}\n")
            fh.flush()
            self._fh = fh
            depths[self._key] = depths.get(self._key, 0) + 1
            return self
        except BaseException:
            fh.close()
            raise

    def release(self):
        depths = _local_depths()
        if self._reentered:
            depths[self._key] = max(0, depths.get(self._key, 1) - 1)
            self._reentered = False
            return
        if self._fh is not None:
            try:
                depths[self._key] = max(0, depths.get(self._key, 1) - 1)
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def paths(env):
    return env.get("RELEASE_RECORD") or ".crew/release.json", env.get("RELEASES_LOG") or ".crew/releases.jsonl"


def append(log, line):
    os.makedirs(os.path.dirname(log) or ".", exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_record(path, record):
    """As `jq .` writes it: two-space indent, the shape incident copies verbatim."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def record(env, verdict, held_at, stages, image, sbom="", dast="", running=None):
    with ReleaseTrainLock(env=env):
        path, log = paths(env)
        rec = {"version": env.get("CANARY_VERSION", ""), "stable": env.get("STABLE_VERSION", ""),
               "stages": json.loads(stages), "verdict": verdict,
               "held_at": json.loads(held_at) if held_at else None, "image": image, "sbom": sbom or None,
               "dast": dast or None,
               "reason": (env.get("RELEASE_REASON") or None) if verdict == "rolled-back" else None}
        if running is not None:
            rec["running"] = running
        write_record(path, rec)
        if verdict != "in-progress":
            append(log, {"kind": "release", "at": now(), "ticket": env.get("RELEASE_TICKET") or None, "record": rec})
        if verdict == "rolled-back" and env.get("AUTO_INCIDENT_ON_ROLLBACK") == "on":
            try:
                from . import ops_loop
            except ImportError:  # run as a script: core/ is sys.path[0]
                import ops_loop
            try:
                ops_loop.on_rollback(path, env, strict=False, release_hook=True)
            except (ops_loop.Refused, OSError) as exc:
                print(f"[releases] auto-incident skipped: {exc}", file=sys.stderr)


# One `slo:` line of verify/slo.sh's stderr (docs/adr/0080 §1).
_SLO = re.compile(r"^slo: (\S+) canary\(([^)]*)\)=(no data|-?[0-9.]+) stable\(([^)]*)\)=(no data|-?[0-9.]+)$")
MAX_DELTA = {"error_rate": "ERROR_RATE_MAX_DELTA", "latency_p95": "LATENCY_P95_MAX_DELTA"}


def number(text):
    return None if text == "no data" else float(text)


def thresholds(env):
    """ERROR_RATE_MAX_DELTA and LATENCY_P95_MAX_DELTA from the environment,
    else from the effective policy core/policy.py reads for verify/slo.sh
    (policy/merge.env under WORKLOAD_CONTRACT's own); {} when neither."""
    if any(env.get(k) for k in MAX_DELTA.values()) or not env.get("WORKLOAD_CONTRACT"):
        return env
    try:
        from policy import PolicyError, effective  # core/ is sys.path[0] when run as a script
    except ImportError:
        from .policy import PolicyError, effective
    contract = env["WORKLOAD_CONTRACT"]

    def text(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""
    try:
        policy, _ = effective(text("policy/merge.env"), text(f"{contract}/workload.env"), text(f"{contract}/policy.env"))
    except PolicyError:  # a policy verify/slo.sh would refuse: no threshold to show, never a guess
        return {}
    return policy


def slo_of(lines, env):
    """[{query, canary, stable, delta, max_delta}] from the `slo:` lines; a
    line off the shape adds nothing."""
    env = thresholds(env)
    parsed = [m for m in (_SLO.match(line) for line in lines) if m]
    # verify/slo.sh's own rule (docs/adr/0011, 0013): with a side's traffic
    # confirmed, an absent error series is zero errors, not missing data.
    traffic = next(((number(m.group(3)), number(m.group(5))) for m in parsed if m.group(1) == "traffic"), (None, None))
    out = []
    for m in parsed:
        canary, stable = number(m.group(3)), number(m.group(5))
        if m.group(1) == "error_rate":
            canary = 0.0 if canary is None and traffic[0] else canary
            stable = 0.0 if stable is None and traffic[1] else stable
        key = MAX_DELTA.get(m.group(1))
        limit = env.get(key) if key else None
        try:
            limit = float(limit) if limit not in (None, "") else None
        except ValueError:
            limit = None
        out.append({"query": m.group(1), "canary": canary, "stable": stable,
                    "delta": None if canary is None or stable is None else round(canary - stable, 3),
                    "max_delta": limit})
    return out


def brief(rec):
    lines = [f"# Promotion brief: {rec['version']}", "",
             f"Promote `{rec['version']}` (`{rec['image']}`), replacing stable `{rec['stable']}`.", "", "## Stages"]
    for stage in rec.get("stages") or []:
        lines.append(f"- {stage['pct']}%: {stage['verdict']}")
        lines.extend(f"  - {n}" for n in stage.get("numbers") or [])
    lines += ["", "## Not verified", "- 100% of traffic: not observed yet; that is what this decision starts.",
              "", "Recommendation: GO — every stage passed verify; the numbers above are the evidence."]
    return "\n".join(lines)


def raw(value):
    """A value as `jq -r` prints it."""
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return json.dumps(value)


def plane(env, status):
    """Why the plane is not the held record, or None when it is."""
    path = paths(env)[0]
    with open(path, encoding="utf-8") as fh:
        rec = json.load(fh)
    held_at = rec.get("held_at")
    if held_at is None or held_at is False:
        return f"{path} is held but names no held_at"
    try:
        status = json.loads(status)
    except ValueError:
        status = None
    if not isinstance(status, dict):
        return f"adapters/{env.get('DELIVERY', '')}/status.sh did not print JSON"
    canary = status.get("canary")
    canary = "none" if canary is None or canary is False else raw(canary)
    if canary != raw(rec.get("version")) or raw(status.get("weight")) != raw(held_at):
        return (f"the record holds {raw(rec.get('version'))} at {raw(held_at)}%, the plane has canary "
                f"{canary} at {raw(status.get('weight'))}%; nothing moved")
    return None


def answered_brief(env):
    """(sha256, author) of the brief on disk in PROMOTE_OUT, the one the
    answer is to (docs/adr/0079 §1); None for each that is not there."""
    out = env.get("PROMOTE_OUT") or ".crew/promote"
    sha = author = None
    try:
        with open(os.path.join(out, "brief.md"), "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        pass
    try:
        with open(os.path.join(out, "brief.author"), encoding="utf-8") as fh:
            author = fh.readline().rstrip("\n") or None
    except OSError:
        pass
    return sha, author


def answer(env, said):
    with ReleaseTrainLock(env=env):
        path, log = paths(env)
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        at, by, via = now(), env.get("GO_FOR_LAUNCH_BY") or "unauthenticated", env.get("GO_FOR_LAUNCH_VIA") or "cli"
        sha, author = answered_brief(env)
        why = {"reason": (env.get("GO_FOR_LAUNCH_REASON") or "").strip() or None, "brief_sha256": sha, "brief_author": author}
        if said == "GO":
            rec["promoted"] = dict({"weight": 100, "at": at, "by": by, "via": via}, **why)
        else:
            rec["rejected"] = dict({"at": at, "by": by, "via": via}, **why)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        append(log, dict({"kind": "answer", "at": at, "version": rec.get("version"), "answer": said, "by": by, "via": via}, **why))


def train(cmd_argv, env):
    """Hold ReleaseTrainLock, set LIGHTS_OUT_TRAIN_HELD=1, run CMD_ARGV, preserve exit code."""
    if not cmd_argv:
        raise ValueError("train requires a command")
    with ReleaseTrainLock(env=env):
        child_env = dict(env)
        child_env[TRAIN_HELD_ENV] = "1"
        try:
            proc = subprocess.run(cmd_argv, env=child_env, check=False)  # nosec B603 - explicit argv list
            return proc.returncode
        except OSError as exc:
            print(f"releases: cannot execute {cmd_argv[0]}: {exc}", file=sys.stderr)
            return 127


def main(argv, env=None):
    env = os.environ if env is None else env
    cmd = argv[0] if argv else ""
    try:
        if cmd == "record" and len(argv) in (5, 6, 7):
            record(env, *argv[1:])
        elif cmd == "stage" and len(argv) in (4, 5):
            numbers = [ln.rstrip("\n") for ln in sys.stdin if ln.startswith("slo: ")]
            entry = {"pct": int(argv[2]), "verdict": argv[3], "numbers": numbers,
                     "started_at": argv[4] if len(argv) == 5 and argv[4] else None, "judged_at": now(),
                     "slo": slo_of(numbers, env)}
            print(json.dumps(json.loads(argv[1]) + [entry], ensure_ascii=False, separators=(",", ":")))
        elif cmd == "running" and len(argv) in (6, 7):
            record(env, "in-progress", "", argv[1], argv[5], "", argv[6] if len(argv) == 7 else "",
                   running={"pct": int(argv[2]), "started_at": argv[3], "judge_after_s": int(argv[4])})
        elif cmd == "brief" and len(argv) == 1:
            with open(paths(env)[0], encoding="utf-8") as fh:
                print(brief(json.load(fh)))
        elif cmd == "plane" and len(argv) == 1:
            why = plane(env, sys.stdin.read())
            if why:
                print(why)
                return 1
        elif cmd == "answer" and len(argv) == 2 and argv[1] in ("GO", "NO-GO"):
            answer(env, argv[1])
        elif cmd == "train":
            sub_argv = argv[2:] if len(argv) > 1 and argv[1] == "--" else argv[1:]
            if not sub_argv:
                print("usage: releases.py train <cmd> [args...]", file=sys.stderr)
                return 2
            return train(sub_argv, env)
        else:
            print("usage: releases.py record VERDICT HELD_AT STAGES IMAGE [SBOM [DAST]] | stage STAGES PCT VERDICT [STARTED_AT]"
                  " | running STAGES PCT STARTED_AT JUDGE_AFTER_S IMAGE [DAST] | brief | plane | answer GO|NO-GO"
                  " | train <cmd> [args...]", file=sys.stderr)
            return 2
    except (ReleaseTrainTimeout, ValueError) as exc:
        print(f"releases: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
