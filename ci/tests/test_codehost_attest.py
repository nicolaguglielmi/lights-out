"""Integration tests for M36 / M92 CodeHost attest.sh adapters (ADR 0134).

Covers adapters/github/attest.sh, adapters/gitlab/attest.sh, and
adapters/gitea/attest.sh using local mock HTTP servers and curl recorders,
plus end-to-end attachment through core/provenance.py and core/provenance.sh.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


def _make_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _sample_statement(sha: str = COMMIT_SHA) -> dict:
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": sha, "digest": {"gitCommit": sha}}],
        "predicateType": "https://lights-out.dev/provenance/v1",
        "predicate": {
            "builder": {"id": "https://lights-out.dev/builder/tower/v1"},
            "built_at": "2026-11-25T10:00:00Z",
            "ticket": {"id": "0036", "state": "merged", "spec_sha256": "a" * 64, "spec_title": "Attest"},
            "intake": None,
            "adrs": [],
            "agents": [],
            "gates": {"verdict": "GO", "line": "GO: gates", "head_sha": sha, "decision": "auto-merge",
                      "failed": None, "lines": ["GO: risk"], "durations_s": {"risk": 0}, "checks": []},
            "merge": {"sha": sha, "head_sha": sha, "pr": {"number": 7, "url": "https://example/pr/7",
                                                          "head_sha": sha, "base_sha": "b" * 40},
                      "risk_level": "low"},
            "human_gate": None,
            "missing": [],
        },
    }


def _sample_bundle() -> dict:
    return {
        "mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.2",
        "verificationMaterial": {"publicKey": {"hint": "synthetic"}},
        "messageSignature": {"messageDigest": {"algorithm": "SHA2_256", "digest": "c" * 44}, "signature": "dGVzdA=="},
    }


def _install_curl_proxy(bin_dir: Path, rec_dir: Path, target_base_url: str, expected_prefix: str) -> None:
    """Install a curl shim on PATH that records argv/stdin and forwards HTTP requests to a local server."""
    shim = f'''#!{sys.executable}
import json, os, sys, urllib.error, urllib.request

rec_dir = {str(rec_dir)!r}
target_base = {target_base_url!r}
expected_prefix = {expected_prefix!r}

argv = sys.argv[1:]
with open(os.path.join(rec_dir, "curl.argv"), "a", encoding="utf-8") as fh:
    fh.write("curl" + "".join(f" [{{a}}]" for a in argv) + "\\n")

stdin_data = sys.stdin.read()
with open(os.path.join(rec_dir, "curl.stdin"), "a", encoding="utf-8") as fh:
    fh.write(stdin_data)

out_path = None
write_fmt = None
method = "GET"
headers = {{}}
body = None
url = None
i = 0
while i < len(argv):
    a = argv[i]
    if a == "-o" and i + 1 < len(argv):
        out_path = argv[i + 1]
        i += 2
    elif a == "-w" and i + 1 < len(argv):
        write_fmt = argv[i + 1]
        i += 2
    elif a == "-X" and i + 1 < len(argv):
        method = argv[i + 1]
        i += 2
    elif a == "-H" and i + 1 < len(argv):
        hval = argv[i + 1]
        i += 2
        if hval == "@-":
            for line in stdin_data.splitlines():
                if ":" in line:
                    hk, hv = line.split(":", 1)
                    headers[hk.strip()] = hv.strip()
        elif ":" in hval:
            hk, hv = hval.split(":", 1)
            headers[hk.strip()] = hv.strip()
    elif a in ("--data-binary", "-d") and i + 1 < len(argv):
        body = argv[i + 1].encode("utf-8")
        i += 2
    elif a in ("--connect-timeout", "--max-time") and i + 1 < len(argv):
        i += 2
    elif a in ("-sS", "-s", "-f", "-fsS"):
        i += 1
    elif a.startswith("http://") or a.startswith("https://"):
        url = a
        i += 1
    else:
        i += 1

if not url or not url.startswith(expected_prefix):
    sys.stderr.write(f"unexpected curl url: {{url}}\\n")
    if write_fmt == "%{{http_code}}":
        sys.stdout.write("000")
    sys.exit(1)

local_url = target_base + url[len(expected_prefix):]
req = urllib.request.Request(local_url, data=body, headers=headers, method=method)
code = 0
resp_bytes = b""
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        code = resp.status
        resp_bytes = resp.read()
except urllib.error.HTTPError as err:
    code = err.code
    resp_bytes = err.read()
except Exception as exc:
    sys.stderr.write(f"proxy error: {{exc}}\\n")
    if write_fmt == "%{{http_code}}":
        sys.stdout.write("000")
    sys.exit(1)

if out_path:
    with open(out_path, "wb") as fh:
        fh.write(resp_bytes)
elif resp_bytes:
    sys.stdout.buffer.write(resp_bytes)

if write_fmt == "%{{http_code}}":
    sys.stdout.write(str(code))
'''
    _make_executable(bin_dir / "curl", shim)


def _install_curl_recorder(bin_dir: Path, rec_dir: Path) -> None:
    _make_executable(
        bin_dir / "curl",
        f"""#!/bin/sh
{{ printf 'curl'; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\\n'; }} >> {str(rec_dir)!r}/curl.argv
cat >> {str(rec_dir)!r}/curl.stdin
exit 1
""",
    )


class GitHubAttestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-attest-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stmt_file = self.tmp / "0.9.0.intoto.json"
        self.stmt_file.write_text(json.dumps(_sample_statement()), encoding="utf-8")
        self.bundle_file = self.tmp / "0.9.0.intoto.json.bundle"
        self.bundle_file.write_text(json.dumps(_sample_bundle()), encoding="utf-8")

    def _start_server(self, tok: str, status_code: int = 201, resp_body: dict | None = None):
        seen: list[dict] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                seen.append({
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "accept": self.headers.get("Accept"),
                    "api_version": self.headers.get("X-GitHub-Api-Version"),
                    "content_type": self.headers.get("Content-Type"),
                    "body": json.loads(raw) if raw else None,
                })
                if self.headers.get("Authorization") != f"Bearer {tok}":
                    payload = json.dumps({"message": "Bad credentials"}).encode()
                    self.send_response(401)
                else:
                    payload = json.dumps(resp_body if resp_body is not None else {"id": 987654}).encode()
                    self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv, seen

    def test_github_attest_posts_bundle_with_stdin_bearer_and_emits_receipt(self) -> None:
        tok = "tok-" + "gh-synth" + "-991"
        srv, seen = self._start_server(tok, status_code=201, resp_body={"id": 987654})
        bin_dir = self.tmp / "bin"
        rec_dir = self.tmp / "rec"
        bin_dir.mkdir()
        rec_dir.mkdir()
        _install_curl_proxy(bin_dir, rec_dir, f"http://127.0.0.1:{srv.server_port}", "https://api.github.com")

        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_TOKEN": tok,
            "GITHUB_REPO": "octo/app",
            "WORKLOAD_ATTACHED": "1",
        }
        proc = subprocess.run(
            ["sh", str(ROOT / "adapters" / "github" / "attest.sh"), COMMIT_SHA, str(self.bundle_file)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1, proc.stdout)
        receipt = json.loads(lines[0])
        self.assertEqual(receipt["stored"], "remote")
        self.assertEqual(receipt["id"], 987654)
        self.assertEqual(receipt["url"], "https://github.com/octo/app/attestations/987654")
        self.assertEqual(receipt["sha"], COMMIT_SHA)
        self.assertEqual(receipt["verify_cmd"], f"gh attestation verify --repo octo/app --bundle {self.bundle_file}")

        self.assertEqual(len(seen), 1)
        req = seen[0]
        self.assertEqual(req["path"], "/repos/octo/app/attestations")
        self.assertEqual(req["auth"], f"Bearer {tok}")
        self.assertEqual(req["accept"], "application/vnd.github+json")
        self.assertEqual(req["api_version"], "2022-11-28")
        self.assertEqual(req["content_type"], "application/json")
        self.assertEqual(req["body"], {"bundle": _sample_bundle()})

        argv_text = (rec_dir / "curl.argv").read_text(encoding="utf-8")
        stdin_text = (rec_dir / "curl.stdin").read_text(encoding="utf-8")
        self.assertIn("[-H] [@-]", argv_text)
        self.assertNotIn(tok, argv_text)
        self.assertIn(f"Authorization: Bearer {tok}", stdin_text)
        self.assertNotIn(tok, proc.stdout + proc.stderr)

    def test_github_attest_wraps_statement_when_given_statement_json(self) -> None:
        tok = "tok-" + "gh-stmt" + "-202"
        srv, seen = self._start_server(
            tok, status_code=201, resp_body={"id": 42, "url": "https://api.github.com/repos/octo/app/attestations/42"}
        )
        bin_dir = self.tmp / "bin-stmt"
        rec_dir = self.tmp / "rec-stmt"
        bin_dir.mkdir()
        rec_dir.mkdir()
        _install_curl_proxy(bin_dir, rec_dir, f"http://127.0.0.1:{srv.server_port}", "https://api.github.com")

        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_TOKEN": tok,
            "GITHUB_REPO": "octo/app",
            "WORKLOAD_ATTACHED": "1",
        }
        proc = subprocess.run(
            ["sh", str(ROOT / "adapters" / "github" / "attest.sh"), COMMIT_SHA, str(self.stmt_file)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        receipt = json.loads(proc.stdout.strip())
        self.assertEqual(receipt["url"], "https://api.github.com/repos/octo/app/attestations/42")
        self.assertEqual(receipt["verify_cmd"], f"gh attestation verify --repo octo/app {self.stmt_file}")
        self.assertEqual(seen[0]["body"], {"bundle": _sample_statement()})

    def test_github_attest_refuses_missing_token_repo_unattached_and_bad_inputs_before_curl(self) -> None:
        tok = "tok-" + "gh-refuse" + "-303"
        bin_dir = self.tmp / "bin-rec"
        rec_dir = self.tmp / "rec-rec"
        bin_dir.mkdir()
        rec_dir.mkdir()
        _install_curl_recorder(bin_dir, rec_dir)
        base_env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

        # Missing / empty / placeholder token -> names .env, 1 stderr line, 0 curl calls
        for bad_env in (
            {"GITHUB_REPO": "octo/app", "WORKLOAD_ATTACHED": "1"},
            {"GITHUB_TOKEN": "", "GITHUB_REPO": "octo/app", "WORKLOAD_ATTACHED": "1"},
            {"GITHUB_TOKEN": "replace-me", "GITHUB_REPO": "octo/app", "WORKLOAD_ATTACHED": "1"},
            {"GITHUB_TOKEN": tok, "GITHUB_REPO": "", "WORKLOAD_ATTACHED": "1"},
        ):
            with self.subTest(bad_env=bad_env):
                res = subprocess.run(
                    ["sh", str(ROOT / "adapters" / "github" / "attest.sh"), COMMIT_SHA, str(self.bundle_file)],
                    env={**base_env, **bad_env},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(res.returncode, 1)
                self.assertEqual(res.stdout, "")
                self.assertEqual(len(res.stderr.splitlines()), 1, res.stderr)
                self.assertIn(".env", res.stderr)
                self.assertFalse((rec_dir / "curl.argv").exists())

        # Unattached -> 1 stderr line, empty stdout, 0 curl calls
        unattached = subprocess.run(
            ["sh", str(ROOT / "adapters" / "github" / "attest.sh"), COMMIT_SHA, str(self.bundle_file)],
            env={**base_env, "GITHUB_TOKEN": tok, "GITHUB_REPO": "octo/app"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(unattached.returncode, 1)
        self.assertEqual(unattached.stdout, "")
        self.assertEqual(len(unattached.stderr.splitlines()), 1)
        self.assertFalse((rec_dir / "curl.argv").exists())

        # Invalid arguments and non-JSON file -> 1 stderr line, empty stdout, 0 curl calls
        bad_json = self.tmp / "bad.json"
        bad_json.write_text("{not-json", encoding="utf-8")
        arr_json = self.tmp / "arr.json"
        arr_json.write_text("[]", encoding="utf-8")
        for args in (
            ["", str(self.bundle_file)],
            ["-bad", str(self.bundle_file)],
            [COMMIT_SHA, str(self.tmp / "missing.json")],
            [COMMIT_SHA, str(bad_json)],
            [COMMIT_SHA, str(arr_json)],
        ):
            with self.subTest(args=args):
                res = subprocess.run(
                    ["sh", str(ROOT / "adapters" / "github" / "attest.sh"), *args],
                    env={**base_env, "GITHUB_TOKEN": tok, "GITHUB_REPO": "octo/app", "WORKLOAD_ATTACHED": "1"},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(res.returncode, 1)
                self.assertEqual(res.stdout, "")
                self.assertEqual(len(res.stderr.splitlines()), 1)
                self.assertFalse((rec_dir / "curl.argv").exists())

    def test_github_attest_fails_cleanly_on_http_error(self) -> None:
        tok = "tok-" + "gh-err" + "-404"
        srv, _ = self._start_server(tok, status_code=422, resp_body={"message": "invalid bundle"})
        bin_dir = self.tmp / "bin-err"
        rec_dir = self.tmp / "rec-err"
        bin_dir.mkdir()
        rec_dir.mkdir()
        _install_curl_proxy(bin_dir, rec_dir, f"http://127.0.0.1:{srv.server_port}", "https://api.github.com")
        proc = subprocess.run(
            ["sh", str(ROOT / "adapters" / "github" / "attest.sh"), COMMIT_SHA, str(self.bundle_file)],
            env={
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "GITHUB_TOKEN": tok,
                "GITHUB_REPO": "octo/app",
                "WORKLOAD_ATTACHED": "1",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(len(proc.stderr.splitlines()), 1)
        self.assertIn("422", proc.stderr)
        self.assertIn("invalid bundle", proc.stderr)
        self.assertNotIn(tok, proc.stderr)


class GitLabAttestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gl-attest-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stmt_file = self.tmp / "0.9.0.intoto.json"
        self.stmt_file.write_text(json.dumps(_sample_statement()), encoding="utf-8")

    def _start_server(self, tok: str, status_code: int = 201, resp_body: dict | None = None):
        seen: list[dict] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                seen.append({
                    "path": self.path,
                    "token": self.headers.get("PRIVATE-TOKEN"),
                    "body": json.loads(raw) if raw else None,
                })
                if self.headers.get("PRIVATE-TOKEN") != tok:
                    payload = json.dumps({"message": "401 Unauthorized"}).encode()
                    self.send_response(401)
                else:
                    payload = json.dumps(resp_body if resp_body is not None else {"id": 55, "note": "ok"}).encode()
                    self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv, seen

    def test_gitlab_attest_posts_commit_comment_and_returns_remote_receipt(self) -> None:
        tok = "tok-" + "gl-synth" + "-505"
        srv, seen = self._start_server(tok, status_code=201, resp_body={"id": 55})
        base_url = f"http://127.0.0.1:{srv.server_port}"
        env = {
            "PATH": os.environ["PATH"],
            "GITLAB_URL": base_url,
            "GITLAB_PROJECT_PATH": "group/sub/svc",
            "GITLAB_TOKEN": tok,
        }
        proc = subprocess.run(
            ["sh", str(ROOT / "adapters" / "gitlab" / "attest.sh"), COMMIT_SHA, str(self.stmt_file)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1, proc.stdout)
        receipt = json.loads(lines[0])
        expected_sha256 = hashlib.sha256(self.stmt_file.read_bytes()).hexdigest()
        self.assertEqual(receipt["stored"], "remote")
        self.assertEqual(receipt["id"], 55)
        self.assertEqual(receipt["sha"], COMMIT_SHA)
        self.assertEqual(receipt["digest"], f"sha256:{expected_sha256}")
        self.assertEqual(receipt["url"], f"{base_url}/group/sub/svc/-/commit/{COMMIT_SHA}")
        self.assertNotIn(tok, proc.stdout + proc.stderr)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["path"], f"/api/v4/projects/group%2Fsub%2Fsvc/repository/commits/{COMMIT_SHA}/comments")
        self.assertEqual(seen[0]["token"], tok)
        note = seen[0]["body"]["note"]
        self.assertIn(COMMIT_SHA, note)
        self.assertIn(expected_sha256, note)
        self.assertIn("0.9.0.intoto.json", note)
        self.assertIn("https://lights-out.dev/provenance/v1", note)

    def test_gitlab_attest_uses_stdin_header_and_refuses_missing_token_before_curl(self) -> None:
        tok = "tok-" + "gl-rec" + "-606"
        bin_dir = self.tmp / "bin-gl"
        rec_dir = self.tmp / "rec-gl"
        bin_dir.mkdir()
        rec_dir.mkdir()
        _install_curl_recorder(bin_dir, rec_dir)
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITLAB_URL": "https://gitlab.com",
            "GITLAB_PROJECT_PATH": "group/svc",
            "GITLAB_TOKEN": tok,
        }
        res = subprocess.run(
            ["sh", str(ROOT / "adapters" / "gitlab" / "attest.sh"), COMMIT_SHA, str(self.stmt_file)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 1)
        argv_text = (rec_dir / "curl.argv").read_text(encoding="utf-8")
        stdin_text = (rec_dir / "curl.stdin").read_text(encoding="utf-8")
        self.assertIn("[-H] [@-]", argv_text)
        self.assertNotIn(tok, argv_text)
        self.assertIn(f"PRIVATE-TOKEN: {tok}", stdin_text)
        self.assertNotIn(tok, res.stdout + res.stderr)

        rec_argv = rec_dir / "curl.argv"
        rec_argv.unlink()
        no_tok = subprocess.run(
            ["sh", str(ROOT / "adapters" / "gitlab" / "attest.sh"), COMMIT_SHA, str(self.stmt_file)],
            env={
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "GITLAB_URL": "https://gitlab.com",
                "GITLAB_PROJECT_PATH": "group/svc",
                "GITLAB_TOKEN": "",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(no_tok.returncode, 1)
        self.assertEqual(no_tok.stdout, "")
        self.assertIn(".env", no_tok.stderr)
        self.assertFalse(rec_argv.exists())


class GiteaAttestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gitea-attest-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stmt_file = self.tmp / "0.9.0.intoto.json"
        self.stmt_file.write_text(json.dumps(_sample_statement()), encoding="utf-8")

    def test_gitea_attest_returns_honest_local_receipt_and_writes_sidecar_without_curl(self) -> None:
        bin_dir = self.tmp / "bin-gitea"
        rec_dir = self.tmp / "rec-gitea"
        bin_dir.mkdir()
        rec_dir.mkdir()
        _install_curl_recorder(bin_dir, rec_dir)

        proc = subprocess.run(
            ["sh", str(ROOT / "adapters" / "gitea" / "attest.sh"), COMMIT_SHA, str(self.stmt_file)],
            env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((rec_dir / "curl.argv").exists())
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1, proc.stdout)
        receipt = json.loads(lines[0])
        self.assertEqual(
            receipt,
            {
                "stored": "local",
                "id": None,
                "sha": COMMIT_SHA,
                "note": "local codehost has no remote attestations API; verify with cosign",
            },
        )
        sidecar = self.tmp / "0.9.0.intoto.json.attestation-receipt.json"
        self.assertTrue(sidecar.is_file())
        self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), receipt)

    def test_gitea_attest_refuses_bad_args_and_invalid_json(self) -> None:
        bad = self.tmp / "bad.json"
        bad.write_text("not-json", encoding="utf-8")
        for args in (
            ["", str(self.stmt_file)],
            [COMMIT_SHA, str(self.tmp / "missing.json")],
            [COMMIT_SHA, str(bad)],
        ):
            with self.subTest(args=args):
                res = subprocess.run(
                    ["sh", str(ROOT / "adapters" / "gitea" / "attest.sh"), *args],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(res.returncode, 1)
                self.assertEqual(res.stdout, "")
                self.assertEqual(len(res.stderr.splitlines()), 1)


class ProvenanceAttachRealCodeHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="prov-real-ch-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stmt_file = self.tmp / "0.9.0.intoto.json"
        self.stmt_file.write_text(json.dumps(_sample_statement()), encoding="utf-8")

    def test_provenance_py_attach_works_with_gitea_adapter(self) -> None:
        receipt_out = self.tmp / "receipt.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "core" / "provenance.py"),
                "attach",
                "--statement",
                str(self.stmt_file),
                "--out-receipt",
                str(receipt_out),
            ],
            cwd=ROOT,
            env={"PATH": os.environ["PATH"], "CODE_HOST": "gitea"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("OK: "), proc.stdout)
        self.assertIn("stored=local", proc.stdout)
        self.assertIn("local codehost has no remote attestations API", proc.stderr)
        data = json.loads(receipt_out.read_text(encoding="utf-8"))
        self.assertEqual(data["code_host"], "gitea")
        self.assertEqual(data["status"], "attached")
        self.assertEqual(data["receipt"]["stored"], "local")

    def test_provenance_sh_build_attach_signs_then_attaches_bundle_to_gitlab(self) -> None:
        self.assertIsNotNone(shutil.which("cosign"), "cosign is not on PATH: this test runs in the workbench, ./go ci")
        tok = "tok-" + "e2e-gl" + "-707"
        seen: list[dict] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                seen.append({
                    "path": self.path,
                    "token": self.headers.get("PRIVATE-TOKEN"),
                    "body": json.loads(raw) if raw else None,
                })
                payload = json.dumps({"id": 88}).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        for rel in ("core", "ci", "adapters"):
            (self.tmp / rel).symlink_to(ROOT / rel)
        (self.tmp / "keys").mkdir()
        gen = subprocess.run(
            ["cosign", "generate-key-pair"],
            cwd=self.tmp / "keys",
            env=dict(os.environ, COSIGN_PASSWORD=""),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(gen.returncode, 0, gen.stderr)

        work = self.tmp / "work"
        work.mkdir()
        git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        subprocess.run(["git", "init", "-q"], cwd=work, env=git_env, check=True)
        (work / "README.md").write_text("hi\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=work, env=git_env, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=work, env=git_env, check=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=work, env=git_env, capture_output=True, text=True, check=True
        ).stdout.strip()

        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "WORKLOAD": str(work),
            "TICKET": "",
            "VERSION": "",
            "STATE_STORE": "",
            "RELEASES_LOG": "",
            "WORKLOAD_ATTACHED": "",
            "PROVENANCE_DIR": str(self.tmp / "prov"),
            "COSIGN_KEY": str(self.tmp / "keys" / "cosign.key"),
            "CODE_HOST": "gitlab",
            "GITLAB_URL": f"http://127.0.0.1:{srv.server_port}",
            "GITLAB_PROJECT_PATH": "group/svc",
            "GITLAB_TOKEN": tok,
        }
        res = subprocess.run(
            ["sh", "core/provenance.sh", "build", "--attach"],
            cwd=self.tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(res.stdout.startswith("GO: provenance — "), res.stdout)

        att = self.tmp / "prov" / f"{head}.intoto.json"
        bundle = self.tmp / "prov" / f"{head}.intoto.json.bundle"
        receipt_file = self.tmp / "prov" / f"{head}.intoto.json.attestation-receipt.json"
        self.assertTrue(att.is_file())
        self.assertTrue(bundle.is_file())
        self.assertTrue(receipt_file.is_file())

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["path"], f"/api/v4/projects/group%2Fsvc/repository/commits/{head}/comments")
        bundle_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
        self.assertIn(bundle_sha, seen[0]["body"]["note"])
        self.assertIn(f"{head}.intoto.json.bundle", seen[0]["body"]["note"])

        ver = subprocess.run(
            ["sh", "core/provenance.sh", "verify", str(att)],
            cwd=self.tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ver.returncode, 0, ver.stderr)
        self.assertTrue(ver.stdout.startswith("GO: provenance — "), ver.stdout)


if __name__ == "__main__":
    unittest.main()
