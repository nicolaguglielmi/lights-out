"""End-to-end tests for ADR 0116: gates/tracker-conformance.sh,
adapters/file/sync.sh, fixtures/outside-adapters/file-tracker/,
adapters/github-issues/, and adapters/linear/.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]


def _write_exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR)


def _curl_wrapper(bin_dir: Path, argv_log: Path) -> None:
    real_curl = shutil.which("curl")
    assert real_curl is not None, "curl must exist on PATH"
    _write_exe(
        bin_dir / "curl",
        f"""#!/usr/bin/env python3
import os, sys
from pathlib import Path
Path({str(argv_log)!r}).open("a", encoding="utf-8").write(repr(sys.argv[1:]) + "\\n")
os.execv({real_curl!r}, [{real_curl!r}, *sys.argv[1:]])
""",
    )


class _GitHubIssuesState:
    def __init__(self, expected_token: str, repo: str = "acme/service"):
        self.expected_token = expected_token
        self.repo = repo
        self.next_num = 1
        self.issues: dict[int, dict] = {}
        self.comments: dict[int, list[dict]] = {}
        self.seen_auth: list[str] = []


def _start_github_server(state: _GitHubIssuesState):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _send(self, status: int, obj: object) -> None:
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            state.seen_auth.append(auth)
            if auth != f"Bearer {state.expected_token}":
                self._send(401, {"message": "Bad credentials"})
                return False
            return True

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n).decode("utf-8") if n else "{}"
            return json.loads(raw)

        def do_GET(self):  # noqa: N802
            if not self._auth():
                return
            parsed = urlparse(self.path)
            base = f"/repos/{state.repo}"
            if parsed.path == base:
                self._send(200, {"full_name": state.repo})
                return
            if parsed.path == f"{base}/issues":
                qs = parse_qs(parsed.query)
                want_state = qs.get("state", ["open"])[0]
                per_page = int(qs.get("per_page", ["30"])[0])
                page = int(qs.get("page", ["1"])[0])
                items = [
                    iss for _, iss in sorted(state.issues.items())
                    if want_state == "all" or iss["state"] == want_state
                ]
                start = (page - 1) * per_page
                self._send(200, items[start:start + per_page])
                return
            if parsed.path.startswith(f"{base}/issues/"):
                rest = parsed.path[len(f"{base}/issues/"):]
                if rest.endswith("/comments"):
                    num_s = rest[:-len("/comments")]
                    if num_s.isdigit() and int(num_s) in state.issues:
                        self._send(200, state.comments.get(int(num_s), []))
                        return
                elif rest.isdigit() and int(rest) in state.issues:
                    self._send(200, state.issues[int(rest)])
                    return
            self._send(404, {"message": "Not Found"})

        def do_POST(self):  # noqa: N802
            if not self._auth():
                return
            parsed = urlparse(self.path)
            base = f"/repos/{state.repo}"
            body = self._read_json()
            if parsed.path == f"{base}/issues":
                num = state.next_num
                state.next_num += 1
                iss = {
                    "number": num,
                    "title": body.get("title", ""),
                    "body": body.get("body", ""),
                    "state": "open",
                    "labels": [{"name": lbl} for lbl in body.get("labels", [])],
                    "html_url": f"https://github.com/{state.repo}/issues/{num}",
                }
                state.issues[num] = iss
                state.comments[num] = []
                self._send(201, iss)
                return
            if parsed.path.startswith(f"{base}/issues/") and parsed.path.endswith("/comments"):
                num_s = parsed.path[len(f"{base}/issues/"):-len("/comments")]
                if num_s.isdigit() and int(num_s) in state.issues:
                    num = int(num_s)
                    comment = {"id": len(state.comments[num]) + 1, "body": body.get("body", "")}
                    state.comments[num].append(comment)
                    self._send(201, comment)
                    return
            self._send(404, {"message": "Not Found"})

        def do_PATCH(self):  # noqa: N802
            if not self._auth():
                return
            parsed = urlparse(self.path)
            base = f"/repos/{state.repo}"
            if parsed.path.startswith(f"{base}/issues/"):
                num_s = parsed.path[len(f"{base}/issues/"):]
                if num_s.isdigit() and int(num_s) in state.issues:
                    iss = state.issues[int(num_s)]
                    patch = self._read_json()
                    if "state" in patch:
                        iss["state"] = patch["state"]
                    if "body" in patch:
                        iss["body"] = patch["body"]
                    if "labels" in patch:
                        iss["labels"] = [{"name": lbl} for lbl in patch["labels"]]
                    self._send(200, iss)
                    return
            self._send(404, {"message": "Not Found"})

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


class _LinearState:
    def __init__(self, expected_token: str, team_id: str = "team-eng-1"):
        self.expected_token = expected_token
        self.team_id = team_id
        self.next_num = 1
        self.issues: dict[str, dict] = {}
        self.comments: dict[str, list[dict]] = {}
        self.states = [
            {"id": "st-todo", "name": "Todo", "type": "unstarted"},
            {"id": "st-prog", "name": "In Progress", "type": "started"},
            {"id": "st-done", "name": "Done", "type": "completed"},
        ]
        self.seen_auth: list[str] = []
        self.force_gql_error: str | None = None


def _start_linear_server(state: _LinearState):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _send(self, status: int, obj: object) -> None:
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):  # noqa: N802
            if urlparse(self.path).path != "/graphql":
                self._send(404, {"errors": [{"message": "not found"}]})
                return
            auth = self.headers.get("Authorization", "")
            state.seen_auth.append(auth)
            valid_auths = {f"Bearer {state.expected_token}", state.expected_token}
            if auth not in valid_auths:
                self._send(401, {"errors": [{"message": "unauthorized"}]})
                return
            if state.force_gql_error:
                self._send(200, {"errors": [{"message": state.force_gql_error}]})
                return
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            query = req.get("query", "")
            vars_ = req.get("variables") or {}

            if "viewer" in query and "team(" in query:
                team_ok = vars_.get("teamId") == state.team_id
                self._send(
                    200,
                    {
                        "data": {
                            "viewer": {"id": "usr-1"},
                            "team": {"id": state.team_id, "name": "Engineering"} if team_ok else None,
                        }
                    },
                )
                return

            if "issues(" in query:
                needle = vars_.get("originLine", "")
                tid = vars_.get("teamId")
                nodes = [
                    iss for iss in state.issues.values()
                    if iss["teamId"] == tid and (not needle or needle in iss["description"])
                ]
                self._send(200, {"data": {"issues": {"nodes": nodes}}})
                return

            if "issueCreate" in query:
                inp = vars_.get("input") or vars_
                team_id = inp.get("teamId") or vars_.get("teamId")
                if team_id != state.team_id:
                    self._send(200, {"errors": [{"message": "unknown team"}]})
                    return
                num = state.next_num
                state.next_num += 1
                uid = f"uuid-{num}"
                ident = f"ENG-{num}"
                iss = {
                    "id": uid,
                    "identifier": ident,
                    "teamId": team_id,
                    "title": inp.get("title") or vars_.get("title", ""),
                    "description": inp.get("description") or vars_.get("description", ""),
                    "state": state.states[0],
                }
                state.issues[uid] = iss
                state.comments[uid] = []
                self._send(200, {"data": {"issueCreate": {"success": True, "issue": iss}}})
                return

            if "issueUpdate" in query:
                iid = vars_.get("id")
                target = next(
                    (iss for iss in state.issues.values() if iid in (iss["id"], iss["identifier"])),
                    None,
                )
                if not target:
                    self._send(200, {"errors": [{"message": "issue not found"}]})
                    return
                inp = vars_.get("input") or vars_
                desc = inp.get("description") if "description" in inp else vars_.get("description")
                if desc is not None:
                    target["description"] = desc
                sid = inp.get("stateId") if "stateId" in inp else vars_.get("stateId")
                if sid:
                    matched = next((s for s in state.states if s["id"] == sid), None)
                    if matched:
                        target["state"] = matched
                self._send(200, {"data": {"issueUpdate": {"success": True, "issue": target}}})
                return

            if "commentCreate" in query:
                inp = vars_.get("input") or vars_
                iid = inp.get("issueId") or vars_.get("issueId")
                target = next(
                    (iss for iss in state.issues.values() if iid in (iss["id"], iss["identifier"])),
                    None,
                )
                if not target:
                    self._send(200, {"errors": [{"message": "issue not found"}]})
                    return
                c = {"id": f"c-{len(state.comments[target['id']]) + 1}", "body": inp.get("body") or vars_.get("body", "")}
                state.comments[target["id"]].append(c)
                self._send(200, {"data": {"commentCreate": {"success": True}}})
                return

            if "issue(" in query:
                iid = vars_.get("id")
                target = next(
                    (iss for iss in state.issues.values() if iid in (iss["id"], iss["identifier"])),
                    None,
                )
                payload: dict = {
                    "issue": (
                        {
                            **target,
                            "comments": {"nodes": state.comments.get(target["id"], [])},
                        }
                        if target
                        else None
                    )
                }
                if "team(" in query:
                    payload["team"] = (
                        {"states": {"nodes": state.states}} if vars_.get("teamId") == state.team_id else None
                    )
                self._send(200, {"data": payload})
                return

            self._send(400, {"errors": [{"message": f"unsupported query: {query}"}]})

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/graphql"


class TrackerAdaptersE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_gate_shell_boundary_and_cold_nogo(self) -> None:
        sh_path = ROOT / "gates" / "tracker-conformance.sh"
        self.assertTrue(sh_path.is_file())
        self.assertTrue(os.access(sh_path, os.X_OK), "gates/tracker-conformance.sh must be +x")
        code_lines = [
            ln for ln in sh_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 15)
        cold = subprocess.run(
            ["sh", str(sh_path)],
            cwd=ROOT,
            env={k: v for k, v in os.environ.items() if k != "CONFORMANCE"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(cold.returncode, 1)
        self.assertEqual(len(cold.stdout.splitlines()), 1, cold.stdout)
        self.assertTrue(cold.stdout.startswith("NO-GO: tracker-conformance — "), cold.stdout)
        self.assertIn("CONFORMANCE=1", cold.stdout)
        self.assertNotIn("\x1b", cold.stdout)

    def test_file_adapter_and_sync_conformance(self) -> None:
        tickets_dir = self.root / "tickets"
        env = {**os.environ, "CONFORMANCE": "1", "TRACKER": "file", "TICKETS_DIR": str(tickets_dir)}
        conf = subprocess.run(
            ["sh", "gates/tracker-conformance.sh"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + conf.stderr)
        self.assertTrue(conf.stdout.startswith("GO: tracker-conformance — file, 6 cases"), conf.stdout)

        # Verify atomic sync preserves existing lines, does not duplicate, and leaves prior PR/ADR when omitted
        ticket_file = tickets_dir / "0001.md"
        before = ticket_file.read_text(encoding="utf-8")
        res = subprocess.run(
            ["sh", "adapters/file/sync.sh", "0001", "closed"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)
        after = ticket_file.read_text(encoding="utf-8")
        self.assertIn("- Status: closed\n", after)
        self.assertIn("- PR: ", after)
        self.assertIn("- ADR: ", after)
        self.assertEqual(after.count("- Status:"), 1)
        self.assertEqual(after.count("- PR:"), 1)
        self.assertEqual(after.count("- ADR:"), 1)
        self.assertNotEqual(before, after)

        bad = subprocess.run(
            ["sh", "adapters/file/sync.sh", "9999", "closed"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(bad.stdout, "")

    def test_outside_file_tracker_conformance_and_self_contained(self) -> None:
        fixture = ROOT / "fixtures" / "outside-adapters" / "file-tracker"
        for verb in ("get.sh", "put.sh", "sync.sh"):
            p = fixture / verb
            self.assertTrue(p.is_file(), f"missing {p}")
            self.assertTrue(os.access(p, os.X_OK), f"{p} must be +x")
            self.assertNotIn("../..", p.read_text(encoding="utf-8"))

        ext_dir = self.root / "ext"
        shutil.copytree(fixture, ext_dir / "file-tracker")
        state_dir = self.root / "file-tracker-state"
        conf = subprocess.run(
            ["sh", "gates/tracker-conformance.sh"],
            cwd=ROOT,
            env={
                **os.environ,
                "CONFORMANCE": "1",
                "TRACKER": "outside/file-tracker",
                "ADAPTERS_PATH": str(ext_dir),
                "FILE_TRACKER_DIR": str(state_dir),
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + conf.stderr)
        self.assertTrue(conf.stdout.startswith("GO: tracker-conformance — outside/file-tracker, 6 cases"), conf.stdout)

    def test_github_issues_conformance_and_stdin_auth_only(self) -> None:
        token = "tok-test-tracker-999"
        gh_state = _GitHubIssuesState(token, repo="acme/service")
        srv, base_url = _start_github_server(gh_state)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)

        bin_dir = self.root / "bin-gh"
        argv_log = self.root / "gh_curl_argv.log"
        _curl_wrapper(bin_dir, argv_log)

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GITHUB_TOKEN": token,
            "GITHUB_REPO": "acme/service",
            "GITHUB_API_URL": base_url,
            "READY_TIMEOUT": "2",
        }
        conf = subprocess.run(
            ["sh", "gates/tracker-conformance.sh"],
            cwd=ROOT,
            env={**env, "CONFORMANCE": "1", "TRACKER": "github-issues"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + conf.stderr)
        self.assertTrue(conf.stdout.startswith("GO: tracker-conformance — github-issues, 6 cases"), conf.stdout)
        self.assertNotIn(token, argv_log.read_text(encoding="utf-8"))
        self.assertTrue(any(h == f"Bearer {token}" for h in gh_state.seen_auth))
        self.assertEqual(len(gh_state.issues), 1, "idempotent put must create only 1 issue")
        self.assertEqual(len(gh_state.comments[1]), 1, "idempotent sync must create only 1 comment")

        # Env validation & ready failure on bad token
        bad_ready = subprocess.run(
            ["sh", "adapters/github-issues/ready.sh"],
            cwd=ROOT,
            env={**env, "GITHUB_TOKEN": "wrong-token"},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(bad_ready.returncode, 0)
        self.assertEqual(bad_ready.stdout, "")

        bad_url = subprocess.run(
            ["sh", "adapters/github-issues/ready.sh"],
            cwd=ROOT,
            env={**env, "GITHUB_API_URL": "https://user:pass@api.example.invalid"},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(bad_url.returncode, 0)

    def test_linear_conformance_and_stdin_auth_only(self) -> None:
        token = "lin_" + "api_test_placeholder_777"
        lin_state = _LinearState(token, team_id="team-eng-1")
        srv, api_url = _start_linear_server(lin_state)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)

        bin_dir = self.root / "bin-lin"
        argv_log = self.root / "lin_curl_argv.log"
        _curl_wrapper(bin_dir, argv_log)

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LINEAR_TOKEN": token,
            "LINEAR_TEAM_ID": "team-eng-1",
            "LINEAR_API_URL": api_url,
            "READY_TIMEOUT": "2",
        }
        conf = subprocess.run(
            ["sh", "gates/tracker-conformance.sh"],
            cwd=ROOT,
            env={**env, "CONFORMANCE": "1", "TRACKER": "linear"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(conf.returncode, 0, conf.stdout + conf.stderr)
        self.assertTrue(conf.stdout.startswith("GO: tracker-conformance — linear, 6 cases"), conf.stdout)
        self.assertNotIn(token, argv_log.read_text(encoding="utf-8"))
        self.assertEqual(len(lin_state.issues), 1, "idempotent put must create only 1 issue")
        only_issue = next(iter(lin_state.issues.values()))
        self.assertEqual(only_issue["state"]["name"], "In Progress")
        self.assertEqual(len(lin_state.comments[only_issue["id"]]), 1, "idempotent sync must create only 1 comment")

        # GraphQL error payload must fail closed
        lin_state.force_gql_error = "simulated graphql error"
        gql_fail = subprocess.run(
            ["sh", "adapters/linear/get.sh", only_issue["identifier"]],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(gql_fail.returncode, 0)
        self.assertEqual(gql_fail.stdout, "")


if __name__ == "__main__":
    unittest.main()
