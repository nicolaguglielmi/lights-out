"""End-to-end subprocess tests for M80 (ADR 0120): NETWORK_AUTO_SPLIT, ReleaseTrainLock, and parallel fleet."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _init_git_repo(ws: Path) -> str:
    ws.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(ws),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@lights-out.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        check=True,
    )
    base = subprocess.run(["git", "-C", str(ws), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(ws), "checkout", "-q", "-b", "ticket/0099"], check=True)
    return base


class NetworkAutoSplitE2ETests(unittest.TestCase):
    def _setup_network_root(self, tmp: Path) -> Path:
        root = tmp / "root"
        (root / "adapters" / "network").mkdir(parents=True)
        (root / "adapters" / "stub").mkdir(parents=True)
        (root / "core").mkdir(parents=True)
        (root / "workload").mkdir(parents=True)

        shutil.copy2(ROOT / "adapters" / "network" / "run.sh", root / "adapters" / "network" / "run.sh")
        shutil.copy2(ROOT / "core" / "perimeter.sh", root / "core" / "perimeter.sh")
        shutil.copy2(ROOT / "core" / "harness-check.sh", root / "core" / "harness-check.sh")
        for mod in ("spec_split.py", "spec.py", "assign.py", "tower.py"):
            shutil.copy2(ROOT / "core" / mod, root / "core" / mod)

        (root / "adapters" / "stub" / "run.sh").write_text(
            """#!/bin/sh
set -eu
printf '%s\\n' "$TICKET" >> "$STUB_CALLS"
id=${TICKET#*/}
mkdir -p "$OUT"
cp "$TICKET_FILE" "$OUT/seen.md"
printf '%s\\n' "${STUB_PLAN:-}" | while read -r who path; do
  [ "$who" = "$id" ] || continue
  mkdir -p "$WORKSPACE/$(dirname "$path")"
  printf '%s by %s\\n' "$path" "$id" > "$WORKSPACE/$path"
done
git -C "$WORKSPACE" add -A
git -C "$WORKSPACE" -c user.name=stub -c user.email=stub@lights-out.invalid commit -q -m "stub $id" || true
git -C "$WORKSPACE" format-patch --stdout "$BASE..HEAD" > "$OUT/patch"
printf '{"title":"stub %s","body":"body of %s"}\\n' "$id" "$id" > "$OUT/pr.json"
printf '{"harness":"stub","model":"stub-model"}\\n' > "$OUT/run.json"
""",
            encoding="utf-8",
        )
        return root

    def test_network_auto_split_on_generates_split_spec_and_runs_disjoint_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            root = self._setup_network_root(tpath)
            ws = tpath / "case" / "workspace"
            out = tpath / "case" / "out"
            out.mkdir(parents=True)
            base = _init_git_repo(ws)

            ticket = tpath / "case" / "ticket.md"
            ticket.write_text(
                """# 0099 — Auto-split test

## Problem

Need validation and tests.

## Acceptance criteria

Given an invalid payload, when posted, then status is 422.

## Perimeter

- `src/service.py`
- `docs/guide.md`
- `tests/test_service.py`
""",
                encoding="utf-8",
            )
            calls = tpath / "case" / "calls"
            calls.write_text("", encoding="utf-8")

            env = dict(
                os.environ,
                NETWORK_AUTO_SPLIT="on",
                AGENT_HARNESS="stub",
                WORKLOAD="workload",
                ROLE="dev-worker",
                TICKET="0099",
                TICKET_FILE=str(ticket),
                WORKSPACE=str(ws),
                BASE=base,
                OUT=str(out),
                STUB_CALLS=str(calls),
                STUB_PLAN="impl src/service.py\nimpl docs/guide.md\ntests tests/test_service.py",
            )
            proc = subprocess.run(
                ["sh", "adapters/network/run.sh"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(sorted(calls.read_text(encoding="utf-8").splitlines()), ["0099/impl", "0099/tests"])
            self.assertTrue((out / "split-spec.md").is_file())
            self.assertIn("## Network", (out / "split-spec.md").read_text(encoding="utf-8"))
            run_doc = json.loads((out / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_doc["harness"], "network")
            self.assertEqual([m["id"] for m in run_doc["members"]], ["impl", "tests"])
            subjects = subprocess.run(
                ["git", "-C", str(ws), "log", "--reverse", "--format=%s", f"{base}..HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
            self.assertEqual(subjects, ["stub impl", "stub tests"])

    def test_network_auto_split_on_unsplittable_or_perimeter_violation_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            root = self._setup_network_root(tpath)
            ws = tpath / "unsplittable" / "workspace"
            out = tpath / "unsplittable" / "out"
            out.mkdir(parents=True)
            base = _init_git_repo(ws)

            ticket = tpath / "unsplittable" / "ticket.md"
            ticket.write_text(
                "# 0099 — Unsplittable\n\n## Perimeter\n\n- `src/only_impl.py`\n",
                encoding="utf-8",
            )
            calls = tpath / "unsplittable" / "calls"
            calls.write_text("", encoding="utf-8")

            env = dict(
                os.environ,
                NETWORK_AUTO_SPLIT="on",
                AGENT_HARNESS="stub",
                WORKLOAD="workload",
                ROLE="dev-worker",
                TICKET="0099",
                TICKET_FILE=str(ticket),
                WORKSPACE=str(ws),
                BASE=base,
                OUT=str(out),
                STUB_CALLS=str(calls),
                STUB_PLAN="",
            )
            proc = subprocess.run(
                ["sh", "adapters/network/run.sh"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(calls.read_text(encoding="utf-8"), "")
            self.assertFalse((out / "split-spec.md").exists())

            # Default NETWORK_AUTO_SPLIT=off falls back to single member harness.
            env_off = dict(env, STUB_PLAN="0099 src/only_impl.py")
            del env_off["NETWORK_AUTO_SPLIT"]
            proc_off = subprocess.run(
                ["sh", "adapters/network/run.sh"],
                cwd=root,
                env=env_off,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(proc_off.returncode, 0, proc_off.stderr)
            self.assertEqual(calls.read_text(encoding="utf-8").splitlines(), ["0099"])

    def test_network_auto_split_on_refuses_cross_member_edit_and_preserves_existing_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            root = self._setup_network_root(tpath)
            ws = tpath / "cross" / "workspace"
            out = tpath / "cross" / "out"
            out.mkdir(parents=True)
            base = _init_git_repo(ws)

            ticket = tpath / "cross" / "ticket.md"
            ticket.write_text(
                "# 0099 — Cross-member guard\n\n## Perimeter\n\n- `src/service.py`\n- `tests/test_service.py`\n",
                encoding="utf-8",
            )
            calls = tpath / "cross" / "calls"
            calls.write_text("", encoding="utf-8")

            env = dict(
                os.environ,
                NETWORK_AUTO_SPLIT="on",
                AGENT_HARNESS="stub",
                WORKLOAD="workload",
                ROLE="dev-worker",
                TICKET="0099",
                TICKET_FILE=str(ticket),
                WORKSPACE=str(ws),
                BASE=base,
                OUT=str(out),
                STUB_CALLS=str(calls),
                STUB_PLAN="impl src/service.py\ntests tests/test_service.py\ntests src/service.py",
            )
            proc = subprocess.run(
                ["sh", "adapters/network/run.sh"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("tests", proc.stderr)
            commits = subprocess.run(
                ["git", "-C", str(ws), "rev-list", "--count", f"{base}..HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(commits, "0")

            # Existing ## Network section is preserved even when NETWORK_AUTO_SPLIT=on.
            ws2 = tpath / "keep" / "workspace"
            out2 = tpath / "keep" / "out"
            out2.mkdir(parents=True)
            base2 = _init_git_repo(ws2)
            ticket2 = tpath / "keep" / "ticket.md"
            ticket2.write_text(
                "# 0099 — Keep network\n\n## Perimeter\n\n- `src/a.py`\n- `src/b.py`\n\n"
                "## Network\n\n### alpha\n\nPart alpha.\n\n- `src/a.py`\n\n"
                "### beta\n\nPart beta.\n\n- `src/b.py`\n",
                encoding="utf-8",
            )
            calls2 = tpath / "keep" / "calls"
            calls2.write_text("", encoding="utf-8")
            proc2 = subprocess.run(
                ["sh", "adapters/network/run.sh"],
                cwd=root,
                env=dict(
                    env,
                    WORKSPACE=str(ws2),
                    BASE=base2,
                    OUT=str(out2),
                    TICKET_FILE=str(ticket2),
                    STUB_CALLS=str(calls2),
                    STUB_PLAN="alpha src/a.py\nbeta src/b.py",
                ),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            self.assertEqual(sorted(calls2.read_text(encoding="utf-8").splitlines()), ["0099/alpha", "0099/beta"])
            self.assertFalse((out2 / "split-spec.md").exists())


class FleetAndWrappersE2ETests(unittest.TestCase):
    def test_releases_train_cross_process_lock_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            env = dict(
                os.environ,
                PYTHONPATH=str(ROOT),
                RELEASE_RECORD=str(work / "release.json"),
                RELEASES_LOG=str(work / "releases.jsonl"),
                CANARY_VERSION="v4",
                STABLE_VERSION="v3",
            )
            with subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "core" / "releases.py"),
                    "train",
                    sys.executable,
                    "-c",
                    "import os, time; assert os.environ.get('LIGHTS_OUT_TRAIN_HELD') == '1'; time.sleep(0.6)",
                ],
                cwd=work,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as holder:
                try:
                    time.sleep(0.15)
                    contender = subprocess.run(
                        [sys.executable, str(ROOT / "core" / "releases.py"), "record", "held", "25", "[]", "img@sha"],
                        cwd=work,
                        env={**env, "RELEASE_TRAIN_TIMEOUT_S": "0.15"},
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5,
                    )
                    self.assertNotEqual(contender.returncode, 0)
                    self.assertIn("timed out", contender.stderr.lower())
                finally:
                    self.assertEqual(holder.wait(timeout=10), 0)

            after = subprocess.run(
                [sys.executable, str(ROOT / "core" / "releases.py"), "record", "held", "25", "[]", "img@sha"],
                cwd=work,
                env={**env, "RELEASE_TRAIN_TIMEOUT_S": "2"},
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertTrue((work / "release.json").is_file())

    def test_runner_workers_subprocess_parallel_assign_and_serialized_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            bus_dir = work / "adapters" / "stub-bus"
            bus_dir.mkdir(parents=True)
            (work / "core").mkdir()
            queue = work / "queue.txt"
            queue.write_text(
                "\n".join([
                    'm1 {"ticket":"0001","step":"assign"}',
                    'm2 {"ticket":"0002","step":"assign"}',
                    'm3 {"ticket":"0003","step":"release"}',
                    'm4 {"ticket":"0004","step":"release"}',
                ]) + "\n",
                encoding="utf-8",
            )
            trace = work / "trace.jsonl"
            (bus_dir / "pull.sh").write_text(
                f'#!/bin/sh\nset -eu\n'
                f'python3 -c "import fcntl, sys\nwith open(sys.argv[1], \'a+\') as lf:\n'
                f'  fcntl.flock(lf.fileno(), fcntl.LOCK_EX)\n'
                f'  with open(sys.argv[2], \'r+\') as qf:\n'
                f'    lines = qf.readlines()\n'
                f'    if lines:\n'
                f'      sys.stdout.write(lines[0])\n'
                f'      qf.seek(0); qf.truncate(); qf.writelines(lines[1:])\n'
                f'" "{work / "q.lock"}" "{queue}"\n',
                encoding="utf-8",
            )
            (bus_dir / "ack.sh").write_text('#!/bin/sh\nset -eu\nexit 0\n', encoding="utf-8")
            (work / "core" / "tower.sh").write_text(
                f"""#!/bin/sh
set -eu
ticket=$2
step=$3
python3 - "$ticket" "$step" "{trace}" "{ROOT / "core" / "releases.py"}" <<'PY'
import json, os, subprocess, sys, time
ticket, step, trace_path, releases_py = sys.argv[1:]
t0 = time.monotonic()
if step == "release":
    env = dict(os.environ, CANARY_VERSION=f"v-{{ticket}}", STABLE_VERSION="v-0", RELEASE_TICKET=ticket)
    rc = subprocess.run([sys.executable, releases_py, "record", "held", "25", "[]", f"img:{{ticket}}"], env=env, check=False).returncode
    assert rc == 0
time.sleep(0.08)
t1 = time.monotonic()
with open(trace_path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"ticket": ticket, "step": step, "start": t0, "end": t1, "held": os.environ.get("LIGHTS_OUT_TRAIN_HELD")}}) + "\\n")
PY
""",
                encoding="utf-8",
            )
            env = dict(
                os.environ,
                PYTHONPATH=str(ROOT),
                EVENT_BUS="stub-bus",
                RELEASE_RECORD=str(work / ".crew" / "release.json"),
                RELEASES_LOG=str(work / ".crew" / "releases.jsonl"),
            )
            proc = subprocess.run(
                [sys.executable, str(ROOT / "core" / "runner.py"), "--once", "--workers", "4", "--lockfile", ".crew/runner.lock"],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            rel_entries = [json.loads(l) for l in (work / ".crew" / "releases.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual({e["ticket"] for e in rel_entries}, {"0003", "0004"})
            rec = json.loads((work / ".crew" / "release.json").read_text(encoding="utf-8"))
            self.assertIn(rec["version"], ("v-0003", "v-0004"))
            rels = sorted(
                (json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if json.loads(l)["step"] == "release"),
                key=lambda x: x["start"],
            )
            self.assertEqual(len(rels), 2)
            self.assertLessEqual(rels[0]["end"], rels[1]["start"] + 0.002)
            self.assertTrue(all(r["held"] == "1" for r in rels))

    def test_shell_wrappers_spec_split_and_m24_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            ticket = tpath / "0088.md"
            ticket.write_text(
                "# 0088 — Wrapper check\n\n## Perimeter\n\n- `src/handler.py`\n- `tests/test_handler.py`\n",
                encoding="utf-8",
            )
            sp_proc = subprocess.run(
                ["sh", "core/spec_split.sh", "split", str(ticket), "--json"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(sp_proc.returncode, 0, sp_proc.stderr)
            sp_json = json.loads(sp_proc.stdout)
            self.assertEqual([m["id"] for m in sp_json["members"]], ["impl", "tests"])

            m24_proc = subprocess.run(
                ["sh", "core/m24_runner.sh", "evaluate", "--json"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(m24_proc.returncode, 3, m24_proc.stderr)
            m24_json = json.loads(m24_proc.stdout)
            self.assertEqual(m24_json["verdict"], "incomplete")
            self.assertEqual([n["provider"] for n in m24_json["not_run"]], ["openai"])


if __name__ == "__main__":
    unittest.main()
