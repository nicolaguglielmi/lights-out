"""Integration tests for M68: real CodeHost remote probes, remote GitLab, and Makefile .env precedence."""

from __future__ import annotations

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

from core.tests.gitrepo import ENV, Repo

ROOT = Path(__file__).resolve().parents[2]


def _make_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class RealAdapterDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="remote-codehost-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _detect(self, url: str, extra_env: dict[str, str] | None = None) -> tuple[int, str, Path]:
        repo = Repo(self)
        repo.commit({"README.md": "hello\n"})
        repo.git("remote", "add", "origin", url)
        out_file = self.tmp / f"remote-{len(list(self.tmp.iterdir()))}.env"
        env = dict(ENV)
        for key in ("GITHUB_HOST", "GITLAB_HOST", "PLANE_HOST", "CODE_HOST_PORT"):
            env.pop(key, None)
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "core" / "remote.py"),
                "detect",
                repo.dir,
                "--adapters",
                str(ROOT / "adapters"),
                "--out",
                str(out_file),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode, proc.stdout, out_file

    def test_github_probe_variants_and_strict_host_match(self) -> None:
        for url in (
            "https://github.com/octocat/hello-world.git",
            "git@github.com:octocat/hello-world.git",
            "ssh://git@github.com/octocat/hello-world",
            "https://x-access-token:dummy-secret-42@github.com/octocat/hello-world.git",
        ):
            with self.subTest(url=url):
                rc, stdout, out_file = self._detect(url)
                self.assertEqual(rc, 0)
                self.assertEqual(stdout.strip(), "CODE_HOST=github\nGITHUB_REPO=octocat/hello-world")
                content = out_file.read_text(encoding="utf-8")
                self.assertIn("CODE_HOST=github\n", content)
                self.assertIn("GITHUB_REPO=octocat/hello-world\n", content)
                self.assertNotIn("dummy-secret-42", content)

        rc, stdout, out_file = self._detect(
            "https://gh.corp.example/team/service.git",
            {"GITHUB_HOST": "gh.corp.example"},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.strip(), "CODE_HOST=github\nGITHUB_REPO=team/service")
        self.assertTrue(out_file.is_file())

        for bad_url in (
            "https://github.com.evil.test/octocat/hello-world.git",
            "https://github.com/org/sub/repo.git",
        ):
            with self.subTest(bad_url=bad_url):
                rc, stdout, out_file = self._detect(bad_url)
                self.assertEqual(rc, 0)
                self.assertEqual(stdout, "")
                self.assertFalse(out_file.exists())

    def test_gitlab_probe_variants_nested_paths_and_custom_host(self) -> None:
        cases = [
            ("https://gitlab.com/group/sub/service.git", None, "https://gitlab.com", "group/sub/service"),
            ("git@gitlab.com:group/sub/service.git", None, "https://gitlab.com", "group/sub/service"),
            ("ssh://git@gitlab.com:2222/group/service.git", None, "https://gitlab.com", "group/service"),
            ("https://oauth2:dummy-secret-77@gitlab.com/group/service.git", None, "https://gitlab.com", "group/service"),
            ("https://gl.corp.example/team/svc.git", {"GITLAB_HOST": "gl.corp.example"}, "https://gl.corp.example", "team/svc"),
            ("https://gl.corp.example:8443/team/svc.git", {"GITLAB_HOST": "gl.corp.example"}, "https://gl.corp.example:8443", "team/svc"),
        ]
        for url, extra, want_url, want_path in cases:
            with self.subTest(url=url):
                rc, stdout, out_file = self._detect(url, extra)
                self.assertEqual(rc, 0)
                self.assertEqual(
                    stdout.strip(),
                    f"CODE_HOST=gitlab\nGITLAB_URL={want_url}\nGITLAB_PROJECT_PATH={want_path}",
                )
                content = out_file.read_text(encoding="utf-8")
                self.assertNotIn("dummy-secret-77", content)

        rc, stdout, out_file = self._detect("https://gitlab.com.evil.test/a/b.git")
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertFalse(out_file.exists())

    def test_gitea_probe_matches_local_plane_only(self) -> None:
        for url, extra in (
            ("http://localhost:3000/lights-out/workload.git", None),
            ("http://host.containers.internal:3000/lights-out/workload.git", None),
            ("http://127.0.0.1:3005/lights-out/workload.git", {"PLANE_HOST": "127.0.0.1", "CODE_HOST_PORT": "3005"}),
        ):
            with self.subTest(url=url):
                rc, stdout, _ = self._detect(url, extra)
                self.assertEqual(rc, 0)
                self.assertEqual(stdout.strip(), "CODE_HOST=gitea")

        rc, stdout, out_file = self._detect("http://localhost:8929/root/workload.git")
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, "")
        self.assertFalse(out_file.exists())


class RemoteGitLabAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="remote-gitlab-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _source_env(self, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
        env = {"PATH": os.environ["PATH"], **extra}
        script = (
            f'. "{ROOT}/adapters/gitlab/env.sh"\n'
            'printf "GITLAB_URL=%s\\nGITLAB_API=%s\\nGITLAB_PROJECT=%s\\n'
            'GITLAB_CLONE_URL=%s\\nGITLAB_PUSH_URL=%s\\nGITLAB_OWNER=%s\\nGITLAB_REPO=%s\\n'
            'CODEHOST_SECRET=%s\\n" '
            '"$GITLAB_URL" "$GITLAB_API" "$GITLAB_PROJECT" "$GITLAB_CLONE_URL" '
            '"$GITLAB_PUSH_URL" "$GITLAB_OWNER" "$GITLAB_REPO" "$CODEHOST_SECRET"\n'
        )
        return subprocess.run(["sh", "-eu", "-c", script], env=env, capture_output=True, text=True, check=False)

    def test_local_gitlab_conformance_defaults_preserved_when_project_path_unset(self) -> None:
        proc = self._source_env({})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        vals = dict(line.split("=", 1) for line in proc.stdout.strip().splitlines())
        self.assertEqual(vals["GITLAB_URL"], "http://host.containers.internal:8929")
        self.assertEqual(vals["GITLAB_API"], "http://host.containers.internal:8929/api/v4")
        self.assertEqual(vals["GITLAB_PROJECT"], "http://host.containers.internal:8929/api/v4/projects/root%2Fworkload")
        self.assertEqual(vals["GITLAB_CLONE_URL"], "http://host.containers.internal:8929/root/workload.git")
        self.assertEqual(
            vals["GITLAB_PUSH_URL"],
            "http://oauth2:lights-out-local-gitlab-not-a-secret@host.containers.internal:8929/root/workload.git",
        )
        self.assertEqual(vals["GITLAB_OWNER"], "root")
        self.assertEqual(vals["GITLAB_REPO"], "workload")

    def test_remote_gitlab_mode_derives_urls_and_refuses_missing_or_placeholder_token(self) -> None:
        tok = "dummy-gl-token-abc123"
        proc = self._source_env(
            {
                "GITLAB_URL": "https://gitlab.com",
                "GITLAB_PROJECT_PATH": "group/sub/workload",
                "GITLAB_TOKEN": tok,
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        vals = dict(line.split("=", 1) for line in proc.stdout.strip().splitlines())
        self.assertEqual(vals["GITLAB_API"], "https://gitlab.com/api/v4")
        self.assertEqual(vals["GITLAB_PROJECT"], "https://gitlab.com/api/v4/projects/group%2Fsub%2Fworkload")
        self.assertEqual(vals["GITLAB_CLONE_URL"], "https://gitlab.com/group/sub/workload.git")
        self.assertEqual(vals["GITLAB_PUSH_URL"], "https://gitlab.com/group/sub/workload.git")
        self.assertNotIn(tok, vals["GITLAB_PUSH_URL"])
        self.assertEqual(vals["CODEHOST_SECRET"], tok)

        for bad_env in (
            {"GITLAB_URL": "https://gitlab.com", "GITLAB_PROJECT_PATH": "group/repo"},
            {"GITLAB_URL": "https://gitlab.com", "GITLAB_PROJECT_PATH": "group/repo", "GITLAB_TOKEN": ""},
            {"GITLAB_URL": "https://gitlab.com", "GITLAB_PROJECT_PATH": "group/repo", "GITLAB_TOKEN": "replace-me"},
        ):
            with self.subTest(bad_env=bad_env):
                bad = self._source_env(bad_env)
                self.assertEqual(bad.returncode, 1)
                self.assertEqual(bad.stdout, "")
                self.assertIn(".env", bad.stderr)

    def test_gitlab_credential_helper_get_store_erase_and_host_matching(self) -> None:
        helper = ROOT / "adapters" / "gitlab" / "credential.sh"
        tok = "dummy-gl-token-xyz789"

        def run_helper(verb: str, stdin_text: str, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["sh", str(helper), verb],
                input=stdin_text,
                env={"PATH": os.environ["PATH"], **extra},
                capture_output=True,
                text=True,
                check=False,
            )

        ok = run_helper("get", "protocol=https\nhost=gitlab.com\n\n", {"GITLAB_TOKEN": tok})
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(ok.stdout, f"username=oauth2\npassword={tok}\n")
        self.assertEqual(ok.stderr, "")

        custom = run_helper(
            "get",
            "protocol=https\nhost=gl.corp.example:8443\n\n",
            {"GITLAB_URL": "https://gl.corp.example:8443", "GITLAB_TOKEN": tok},
        )
        self.assertEqual(custom.returncode, 0)
        self.assertEqual(custom.stdout, f"username=oauth2\npassword={tok}\n")

        for wrong in (
            ("get", "protocol=https\nhost=example.com\n\n", {"GITLAB_TOKEN": tok}),
            ("get", "protocol=https\nhost=gitlab.com.evil.test\n\n", {"GITLAB_TOKEN": tok}),
            ("get", "protocol=http\nhost=gitlab.com\n\n", {"GITLAB_TOKEN": tok}),
            ("store", "protocol=https\nhost=gitlab.com\nusername=oauth2\npassword=x\n\n", {"GITLAB_TOKEN": tok}),
            ("erase", "protocol=https\nhost=gitlab.com\n\n", {"GITLAB_TOKEN": tok}),
            ("get", "protocol=https\nhost=gitlab.com\n\n", {"GITLAB_TOKEN": ""}),
            ("get", "protocol=https\nhost=gitlab.com\n\n", {"GITLAB_TOKEN": "replace-me"}),
            ("get", "protocol=https\nhost=gitlab.com\n\n", {}),
        ):
            with self.subTest(wrong=wrong):
                res = run_helper(*wrong)
                self.assertEqual(res.returncode, 0)
                self.assertEqual(res.stdout, "")
                self.assertNotIn(tok, res.stderr)

    def test_remote_gitlab_ready_and_push_never_leak_token_in_argv(self) -> None:
        rec_dir = self.tmp / "rec"
        rec_bin = self.tmp / "bin"
        rec_dir.mkdir()
        rec_bin.mkdir()
        for tool in ("curl", "git"):
            stdin_cmd = f"cat >> {str(rec_dir)!r}/$tool.stdin" if tool == "curl" else ":"
            _make_executable(
                rec_bin / tool,
                f"""#!/bin/sh
tool=$(basename "$0")
{{ printf '%s' "$tool"; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\\n'; }} >> {str(rec_dir)!r}/$tool.argv
{stdin_cmd}
exit 1
""",
            )
        tok = "dummy-gl-secret-never-argv"
        env = {
            "PATH": f"{rec_bin}{os.pathsep}{os.environ['PATH']}",
            "GITLAB_URL": "https://gitlab.com",
            "GITLAB_PROJECT_PATH": "group/repo",
            "GITLAB_TOKEN": tok,
            "READY_TIMEOUT": "1",
        }
        ready = subprocess.run(
            ["sh", str(ROOT / "adapters" / "gitlab" / "ready.sh")],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ready.returncode, 1)
        self.assertIn(f"PRIVATE-TOKEN: {tok}", (rec_dir / "curl.stdin").read_text(encoding="utf-8"))
        self.assertNotIn(tok, (rec_dir / "curl.argv").read_text(encoding="utf-8"))
        self.assertNotIn(tok, ready.stdout + ready.stderr)

        # Remote push refuses non-ticket branch without running git.
        refuse_main = subprocess.run(
            ["sh", str(ROOT / "adapters" / "gitlab" / "push.sh"), str(self.tmp), "main"],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(refuse_main.returncode, 1)
        self.assertFalse((rec_dir / "git.argv").exists())

        # Remote push for ticket/0001 uses clean URL without token in argv.
        push_ticket = subprocess.run(
            ["sh", str(ROOT / "adapters" / "gitlab" / "push.sh"), str(self.tmp), "ticket/0001"],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(push_ticket.returncode, 1)
        git_argv = (rec_dir / "git.argv").read_text(encoding="utf-8")
        self.assertIn("[https://gitlab.com/group/repo.git]", git_argv)
        self.assertNotIn(tok, git_argv)

    def test_remote_gitlab_bootstrap_refuses_unattached_and_never_creates_or_pushes_main(self) -> None:
        rec_dir = self.tmp / "rec_boot"
        rec_bin = self.tmp / "bin_boot"
        rec_dir.mkdir()
        rec_bin.mkdir()
        for tool in ("curl", "git"):
            stdin_cmd = f"cat >> {str(rec_dir)!r}/$tool.stdin" if tool == "curl" else ":"
            _make_executable(
                rec_bin / tool,
                f"""#!/bin/sh
tool=$(basename "$0")
{{ printf '%s' "$tool"; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\\n'; }} >> {str(rec_dir)!r}/$tool.argv
{stdin_cmd}
exit 1
""",
            )
        tok = "dummy-gl-boot-token"
        env = {
            "PATH": f"{rec_bin}{os.pathsep}{os.environ['PATH']}",
            "GITLAB_URL": "https://gitlab.com",
            "GITLAB_PROJECT_PATH": "group/repo",
            "GITLAB_TOKEN": tok,
            "WORKLOAD": str(self.tmp),
            "WORKLOAD_ATTACHED": "",
        }
        unattached = subprocess.run(
            ["sh", str(ROOT / "adapters" / "gitlab" / "bootstrap.sh")],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(unattached.returncode, 1)
        self.assertIn("attach", unattached.stderr)
        self.assertFalse((rec_dir / "curl.argv").exists())
        self.assertFalse((rec_dir / "git.argv").exists())

        attached = subprocess.run(
            ["sh", str(ROOT / "adapters" / "gitlab" / "bootstrap.sh")],
            env={**env, "WORKLOAD_ATTACHED": "1"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(attached.returncode, 1)
        curl_argv = (rec_dir / "curl.argv").read_text(encoding="utf-8")
        self.assertNotIn("POST", curl_argv)
        self.assertIn("[https://gitlab.com/api/v4/projects/group%2Frepo]", curl_argv)
        self.assertFalse((rec_dir / "git.argv").exists())
        self.assertNotIn(tok, attached.stdout + attached.stderr)

    def test_go_detach_mentions_cleaning_remote_env(self) -> None:
        go_text = (ROOT / "go").read_text(encoding="utf-8")
        self.assertIn("remote.env", go_text)

    def test_remote_gitlab_open_pr_targets_project_default_branch(self) -> None:
        seen_bodies: list[dict] = []
        tok = "dummy-gl-openpr-token"

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                if self.headers.get("PRIVATE-TOKEN") != tok:
                    self.send_error(401)
                    return
                if self.path.startswith("/api/v4/projects/group%2Fsvc/merge_requests"):
                    payload = b"[]"
                elif self.path == "/api/v4/projects/group%2Fsvc":
                    payload = json.dumps({"default_branch": "trunk"}).encode()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):  # noqa: N802
                if self.headers.get("PRIVATE-TOKEN") != tok:
                    self.send_error(401)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode())
                seen_bodies.append(body)
                payload = json.dumps({"iid": 19, "web_url": "https://gitlab.example/group/svc/-/merge_requests/19"}).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            pr_file = self.tmp / "pr.json"
            pr_file.write_text('{"title":"t","body":"b"}\n', encoding="utf-8")
            env = {
                "PATH": os.environ["PATH"],
                "GITLAB_URL": f"http://127.0.0.1:{srv.server_port}",
                "GITLAB_PROJECT_PATH": "group/svc",
                "GITLAB_TOKEN": tok,
            }
            proc = subprocess.run(
                ["sh", str(ROOT / "adapters" / "gitlab" / "open-pr.sh"), "ticket/0001", str(pr_file)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "19 https://gitlab.example/group/svc/-/merge_requests/19")
            self.assertEqual(len(seen_bodies), 1)
            self.assertEqual(seen_bodies[0]["target_branch"], "trunk")
            self.assertEqual(seen_bodies[0]["source_branch"], "ticket/0001")
        finally:
            srv.shutdown()
            srv.server_close()


class MakefilePrecedenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="remote-make-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _scratch_root(self) -> Path:
        root = self.tmp / "root"
        root.mkdir()
        for item in ROOT.iterdir():
            if item.name in (".crew", ".attached", ".env", ".git"):
                continue
            (root / item.name).symlink_to(item)
        (root / ".crew").mkdir()
        return root

    def _probe_make(self, root: Path) -> dict[str, str]:
        probe = 'lo-remote-probe: ; $(foreach v,CODE_HOST GITHUB_REPO GITLAB_URL GITLAB_PROJECT_PATH GIT_CONFIG_COUNT GIT_CONFIG_KEY_1 GIT_CONFIG_VALUE_1,$(info $(v)=$($(v))))'
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.tmp),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
        }
        proc = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-s",
                "WORKLOAD=app",
                "WORKLOAD_CONTRACT=app",
                "WORKLOAD_ATTACHED=",
                f"ATTACHED={self.tmp / 'none'}",
                "--eval",
                probe,
                "lo-remote-probe",
            ],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
        return out

    def test_remote_env_overrides_profile_and_dotenv_overrides_remote_env(self) -> None:
        root = self._scratch_root()
        (root / ".crew" / "remote.env").write_text(
            "CODE_HOST=gitlab\nGITLAB_URL=https://gitlab.com\nGITLAB_PROJECT_PATH=org/sub/repo\n",
            encoding="utf-8",
        )
        vals = self._probe_make(root)
        self.assertEqual(vals["CODE_HOST"], "gitlab")
        self.assertEqual(vals["GITLAB_URL"], "https://gitlab.com")
        self.assertEqual(vals["GITLAB_PROJECT_PATH"], "org/sub/repo")
        self.assertEqual(vals["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(vals["GIT_CONFIG_KEY_1"], "credential.helper")
        self.assertIn("adapters/gitlab/credential.sh", vals["GIT_CONFIG_VALUE_1"])

        # Explicit .env MUST win over .crew/remote.env.
        (root / ".env").write_text("CODE_HOST=github\nGITHUB_REPO=explicit/override\n", encoding="utf-8")
        vals2 = self._probe_make(root)
        self.assertEqual(vals2["CODE_HOST"], "github")
        self.assertEqual(vals2["GITHUB_REPO"], "explicit/override")
        self.assertIn("adapters/github/credential.sh", vals2["GIT_CONFIG_VALUE_1"])

        # Explicit CODE_HOST=gitea in .env reverts credential helper to index-0 only.
        (root / ".env").write_text("CODE_HOST=gitea\n", encoding="utf-8")
        vals3 = self._probe_make(root)
        self.assertEqual(vals3["CODE_HOST"], "gitea")
        self.assertEqual(vals3["GIT_CONFIG_COUNT"], "1")
        self.assertEqual(vals3.get("GIT_CONFIG_KEY_1", ""), "")

    def test_attach_sh_detects_real_remotes_and_clears_stale_on_local(self) -> None:
        gh_src = Repo(self)
        gh_src.commit({"README.md": "gh\n"})
        gh_src.git("remote", "add", "origin", "git@github.com:octo/app.git")
        out_file = self.tmp / "remote.env"

        proc = subprocess.run(
            ["sh", str(ROOT / "core" / "attach.sh"), gh_src.dir, str(self.tmp / "dest1")],
            cwd=ROOT,
            env={**ENV, "REMOTE_ENV_OUT": str(out_file)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(out_file.is_file())
        self.assertIn("CODE_HOST=github\n", out_file.read_text(encoding="utf-8"))
        self.assertIn("CODE_HOST=github", proc.stderr)

        local_src = Repo(self)
        local_src.commit({"README.md": "local\n"})
        proc2 = subprocess.run(
            ["sh", str(ROOT / "core" / "attach.sh"), local_src.dir, str(self.tmp / "dest2")],
            cwd=ROOT,
            env={**ENV, "REMOTE_ENV_OUT": str(out_file)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertFalse(out_file.exists(), "attaching local repo should remove stale remote.env")


if __name__ == "__main__":
    unittest.main()
