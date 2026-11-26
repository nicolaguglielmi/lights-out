"""Tests for network partial sub-worker & release-brief spend ledger (docs/adr/0135).

Strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import cmd_util, promote_cmd, spend, tower

REPO = Path(__file__).resolve().parents[2]

PRICES = {
    "prices": [
        {
            "provider": "prov",
            "model": "model-a",
            "input": 1.00,
            "output": 5.00,
            "source": "https://example.invalid/model-a",
            "checked": "2026-11-25",
        },
        {
            "provider": "prov",
            "model": "model-rel",
            "input": 2.00,
            "output": 8.00,
            "source": "https://example.invalid/model-rel",
            "checked": "2026-11-25",
        },
    ]
}


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Crew",
        "GIT_AUTHOR_EMAIL": "crew@example.invalid",
        "GIT_COMMITTER_NAME": "Crew",
        "GIT_COMMITTER_EMAIL": "crew@example.invalid",
    }
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_member_sandbox(tmp: Path, member_behavior: str) -> tuple[Path, Path, Path, Path, str]:
    """Create a synthetic repo + adapters tree with `adapters/network/run.sh` copied in."""
    ws_root = tmp / "platform"
    (ws_root / "adapters" / "network").mkdir(parents=True)
    (ws_root / "adapters" / "mock-harness").mkdir(parents=True)
    (ws_root / "core").mkdir(parents=True)

    (ws_root / "adapters" / "network" / "run.sh").write_text(
        (REPO / "adapters" / "network" / "run.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # Minimal harness-check + perimeter helpers so passing member succeeds
    (ws_root / "core" / "harness-check.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (ws_root / "core" / "perimeter.sh").write_text("#!/bin/sh\ncat >/dev/null\nexit 0\n", encoding="utf-8")

    # Mock member runner: `impl` succeeds cleanly; `tests` follows `member_behavior`
    (ws_root / "adapters" / "mock-harness" / "run.sh").write_text(
        f"""#!/bin/sh
set -eu
mkdir -p "$OUT"
id=${{TICKET##*/}}
if [ "$id" = "impl" ]; then
  printf 'x\n' > "$WORKSPACE/src_file.py"
  git -C "$WORKSPACE" add src_file.py
  git -C "$WORKSPACE" -c user.name=Crew -c user.email=crew@example.invalid commit -q -m "impl work"
  cat > "$OUT/pr.json" <<'JSON'
{{"title":"impl","body":"done"}}
JSON
  cat > "$OUT/run.json" <<'JSON'
{{"harness":"mock-harness","provider":"prov","model":"model-a","replayed":false,"duration_s":12,"tokens":{{"input":1000000,"output":100000,"total":1100000}}}}
JSON
  exit 0
fi
if [ "{member_behavior}" = "token_budget" ]; then
  printf 'TOKEN_BUDGET\n' > "$OUT/stop"
  cat > "$OUT/run.json" <<'JSON'
{{"harness":"mock-harness","provider":"prov","model":"model-a","replayed":false,"duration_s":8,"tokens":{{"input":500000,"output":0,"total":500000}}}}
JSON
  echo "stopped on token budget" >&2
  exit 1
fi
echo "crashed early" >&2
exit 1
""",
        encoding="utf-8",
    )
    for sh_file in (
        ws_root / "adapters" / "network" / "run.sh",
        ws_root / "adapters" / "mock-harness" / "run.sh",
        ws_root / "core" / "harness-check.sh",
        ws_root / "core" / "perimeter.sh",
    ):
        os.chmod(sh_file, 0o755)

    repo = tmp / "work"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    ticket_md = tmp / "ticket.md"
    ticket_md.write_text(
        "# Split ticket\n\n"
        "## Network\n"
        "### impl\n"
        "- src_file.py\n"
        "Build src\n\n"
        "### tests\n"
        "- test_file.py\n"
        "Build tests\n",
        encoding="utf-8",
    )
    out_dir = tmp / "crew" / "0099" / "out"
    return ws_root, repo, ticket_md, out_dir, base_sha


class NetworkPartialSpendTests(unittest.TestCase):
    def test_budget_stopped_member_still_writes_out_run_json_and_prices_all_members(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws_root, repo, ticket_md, out_dir, base_sha = _init_member_sandbox(tmp, "token_budget")
            env = {
                **os.environ,
                "ROLE": "dev-worker",
                "TICKET": "0099",
                "TICKET_FILE": str(ticket_md),
                "WORKSPACE": str(repo),
                "BASE": base_sha,
                "OUT": str(out_dir),
                "AGENT_HARNESS": "mock-harness",
                "WORKLOAD": str(repo),
            }
            proc = subprocess.run(
                ["sh", str(ws_root / "adapters" / "network" / "run.sh")],
                cwd=ws_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue((out_dir / "run.json").is_file(), proc.stderr)
            self.assertEqual((out_dir / "stop").read_text(encoding="utf-8").strip(), "TOKEN_BUDGET")

            run_json = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["harness"], "network")
            by_id = {m["id"]: m for m in run_json["members"]}
            self.assertFalse(by_id["impl"].get("failed", False))
            self.assertIs(by_id["tests"]["failed"], True)
            self.assertEqual(by_id["tests"]["stop"], "TOKEN_BUDGET")
            self.assertEqual(by_id["tests"]["run"]["tokens"]["input"], 500_000)

            prices = spend.parse_prices(PRICES)
            usd, why = spend.cost(run_json, prices, "2026-11-25")
            self.assertIsNone(why)
            self.assertEqual(usd, 2.0)

            # Flattened via tower.task() preserves member token pricing too
            prices_path = tmp / "prices.json"
            prices_path.write_text(json.dumps(PRICES), encoding="utf-8")
            entries = spend.entries("assign", {"task": dict(tower.task(run_json), role="dev-worker")}, 1, "2026-11-25T10:00:00Z", path=prices_path)
            self.assertEqual(entries[0]["cost_usd"], 2.0)

    def test_early_crash_member_without_run_json_preserves_sibling_spend(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws_root, repo, ticket_md, out_dir, base_sha = _init_member_sandbox(tmp, "crash_early")
            env = {
                **os.environ,
                "ROLE": "dev-worker",
                "TICKET": "0099",
                "TICKET_FILE": str(ticket_md),
                "WORKSPACE": str(repo),
                "BASE": base_sha,
                "OUT": str(out_dir),
                "AGENT_HARNESS": "mock-harness",
                "WORKLOAD": str(repo),
            }
            proc = subprocess.run(
                ["sh", str(ws_root / "adapters" / "network" / "run.sh")],
                cwd=ws_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            run_json = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
            by_id = {m["id"]: m for m in run_json["members"]}
            self.assertIs(by_id["tests"]["failed"], True)

            prices = spend.parse_prices(PRICES)
            # Both raw OUT/run.json and tower.task(run_json) retain member `impl`'s $1.50 cost
            self.assertEqual(spend.cost(run_json, prices, "2026-11-25"), (1.5, None))
            self.assertEqual(spend.cost(tower.task(run_json), prices, "2026-11-25"), (1.5, None))

    def test_preflight_overlap_refuses_before_writing_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws_root, repo, ticket_md, out_dir, base_sha = _init_member_sandbox(tmp, "token_budget")
            ticket_md.write_text(
                "# Split\n\n## Network\n### a\n- src/*\nwork\n\n### b\n- src/*\nwork\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "ROLE": "dev-worker",
                "TICKET": "0099",
                "TICKET_FILE": str(ticket_md),
                "WORKSPACE": str(repo),
                "BASE": base_sha,
                "OUT": str(out_dir),
                "AGENT_HARNESS": "mock-harness",
                "WORKLOAD": str(repo),
            }
            proc = subprocess.run(
                ["sh", str(ws_root / "adapters" / "network" / "run.sh")],
                cwd=ws_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse((out_dir / "run.json").exists())


class ReleaseBriefLedgerTests(unittest.TestCase):
    def test_promote_records_sanitized_brief_run_and_spend_counts_release_role(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old_cwd = Path.cwd()
            try:
                tmp = Path(td)
                os.chdir(REPO)
                crew = tmp / ".crew"
                crew.mkdir(parents=True)
                rec = {"version": "0.9.1-rc1", "stable": "0.9.0", "verdict": "held", "held_at": 25, "stages": [{"pct": 25}]}
                (crew / "release.json").write_text(json.dumps(rec), encoding="utf-8")
                (crew / "releases.jsonl").write_text(
                    json.dumps({"kind": "release", "at": "2026-11-25T10:00:00Z", "ticket": "0010", "record": rec}) + "\n",
                    encoding="utf-8",
                )

                env = {
                    "DELIVERY": "mock-delivery",
                    "AGENT_HARNESS": "fake-adk",
                    "GO_FOR_LAUNCH": "GO",
                    "RELEASE_RECORD": str(crew / "release.json"),
                    "RELEASES_LOG": str(crew / "releases.jsonl"),
                    "PROMOTE_OUT": str(crew / "promote"),
                    "GO_FOR_LAUNCH_BY": "alice",
                    "GO_FOR_LAUNCH_VIA": "cli",
                }

                def fake_sh(script: object, *_args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
                    s = str(script)
                    if s.endswith("status.sh"):
                        return subprocess.CompletedProcess([], 0, stdout='{"canary": "0.9.1-rc1", "weight": 25}', stderr="")
                    if s.endswith("run.sh"):
                        out_d = Path(kwargs["env"]["OUT"])  # type: ignore[index]
                        (out_d / "calls.json").write_text(
                            json.dumps([{"name": "submit_brief", "args": {"version": "0.9.1-rc1", "brief": "Release 0.9.1-rc1 held at 25%."}}]),
                            encoding="utf-8",
                        )
                        (out_d / "run.json").write_text(
                            json.dumps({
                                "harness": "fake-adk",
                                "provider": "prov",
                                "model": "model-rel",
                                "replayed": False,
                                "turns": 2,
                                "duration_s": 15.0,
                                "tokens": {"input": 500_000, "output": 25_000, "total": 525_000},
                                "environment_leak": "DO_NOT_COPY_SECRET",
                            }),
                            encoding="utf-8",
                        )
                        return subprocess.CompletedProcess([], 0, stdout="", stderr="")
                    return subprocess.CompletedProcess([], 0, stdout="", stderr="")

                with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    rc = promote_cmd.main([], env={**os.environ, **env})
                self.assertEqual(rc, 0)

                lines = [json.loads(ln) for ln in (crew / "releases.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(lines), 2)
                self.assertEqual(lines[0]["kind"], "release")
                self.assertEqual(lines[1]["kind"], "answer")
                br = lines[0]["brief_run"]
                self.assertEqual(br["role"], "release")
                self.assertEqual(br["model"], "model-rel")
                self.assertEqual(br["tokens"]["output"], 25_000)
                self.assertNotIn("environment_leak", br)
                self.assertNotIn("DO_NOT_COPY_SECRET", (crew / "releases.jsonl").read_text(encoding="utf-8"))

                prices_file = tmp / "prices.json"
                prices_file.write_text(json.dumps(PRICES), encoding="utf-8")
                rel_entries = spend.release_ledger(str(crew / "releases.jsonl"), prices_path=prices_file)
                self.assertEqual(len(rel_entries), 1)
                self.assertEqual(rel_entries[0]["role"], "release")
                self.assertAlmostEqual(rel_entries[0]["cost_usd"], 1.2, places=6)

                # The brief run is stamped with promote's own clock: the
                # ticket's spend falls on that same day, or the day's burn
                # would leave it out once the test runs past midnight UTC.
                day = rel_entries[0]["at"][:10]
                docs = {
                    "0010": {
                        "id": "0010",
                        "history": [{"step": "open", "state": "opened", "at": f"{day}T00:00:00Z"}],
                        "spend": [{
                            "role": "dev-worker",
                            "step": "assign",
                            "attempt": 1,
                            "cost_usd": 1.0,
                            "duration_s": 20,
                            "at": f"{day}T00:00:01Z",
                        }],
                    }
                }
                rep = spend.report(docs, release_runs=rel_entries)
                self.assertAlmostEqual(rep["roles"]["release"]["cost_usd"], 1.2, places=6)
                self.assertEqual(rep["roles"]["release"]["runs"], 1)
                self.assertAlmostEqual(rep["tickets"]["0010"]["cost_usd"], 2.2, places=6)
                self.assertAlmostEqual(rep["total"]["cost_usd"], 2.2, places=6)
                burned = spend.burn(docs, {"SPEND_BUDGET_DAY_USD": None}, release_runs=rel_entries)
                self.assertAlmostEqual(burned["spent_usd"], 2.2, places=6)
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
