"""Tests for core/remote.py, core/remote-env.sh, and core/attach.sh remote probing.

Strictly vendor-free and app-free: uses synthetic hostnames and fake probe
adapters under temporary directories.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.tests.gitrepo import ENV, Repo

ROOT = Path(__file__).resolve().parents[2]


def _make_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class ParseRemoteUrlTests(unittest.TestCase):
    def test_parses_scp_ssh_urls_and_preserves_kit_suffix(self) -> None:
        from core import remote

        info = remote.parse_remote_url("git@forge.example:owner/my-kit.git")
        self.assertEqual(
            info,
            {
                "url": "git@forge.example:owner/my-kit.git",
                "scheme": "ssh",
                "host": "forge.example",
                "port": "",
                "path": "owner/my-kit",
                "owner": "owner",
                "repo": "my-kit",
            },
        )
        no_ext = remote.parse_remote_url("git@forge.example:group/sub/service")
        self.assertEqual(no_ext["path"], "group/sub/service")
        self.assertEqual(no_ext["owner"], "group/sub")
        self.assertEqual(no_ext["repo"], "service")

    def test_parses_ssh_https_and_http_urls_with_ports(self) -> None:
        from core import remote

        ssh_info = remote.parse_remote_url("ssh://git@Code.Corp.Internal:2222/team/sub/api.git")
        self.assertEqual(ssh_info["scheme"], "ssh")
        self.assertEqual(ssh_info["host"], "code.corp.internal")
        self.assertEqual(ssh_info["port"], "2222")
        self.assertEqual(ssh_info["path"], "team/sub/api")
        self.assertEqual(ssh_info["owner"], "team/sub")
        self.assertEqual(ssh_info["repo"], "api")

        https_info = remote.parse_remote_url("https://Forge.Example:8443/Owner/Repo/")
        self.assertEqual(https_info["scheme"], "https")
        self.assertEqual(https_info["host"], "forge.example")
        self.assertEqual(https_info["port"], "8443")
        self.assertEqual(https_info["path"], "Owner/Repo")
        self.assertEqual(https_info["owner"], "Owner")
        self.assertEqual(https_info["repo"], "Repo")

        http_info = remote.parse_remote_url("http://localhost:3000/lights-out/service.git")
        self.assertEqual(http_info["scheme"], "http")
        self.assertEqual(http_info["host"], "localhost")
        self.assertEqual(http_info["port"], "3000")
        self.assertEqual(http_info["path"], "lights-out/service")

    def test_strips_embedded_userinfo_tokens_and_passwords(self) -> None:
        from core import remote

        cases = [
            ("https://oauth2:secret-tok-123@forge.example/org/svc.git", "https://forge.example/org/svc.git"),
            ("https://secret-tok-123@forge.example/org/svc.git", "https://forge.example/org/svc.git"),
            ("http://user:secret-tok-123@localhost:3000/org/svc.git", "http://localhost:3000/org/svc.git"),
            ("ssh://git:secret-tok-123@forge.example:2222/org/svc.git", "ssh://git@forge.example:2222/org/svc.git"),
            ("git:secret-tok-123@forge.example:org/svc.git", "git@forge.example:org/svc.git"),
        ]
        for raw, expected_url in cases:
            with self.subTest(raw=raw):
                info = remote.parse_remote_url(raw)
                self.assertIsNotNone(info)
                self.assertEqual(info["url"], expected_url)
                for value in info.values():
                    self.assertNotIn("secret-tok-123", value)

    def test_rejects_local_paths_file_urls_and_malformed_urls(self) -> None:
        from core import remote

        bad = [
            "",
            "/tmp/local/repo",
            "../relative/repo",
            "file:///tmp/local/repo",
            "ftp://forge.example/owner/repo.git",
            "https://forge.example",
            "https://forge.example/only-one-segment",
            "https://forge.example/owner/../secret.git",
            "https://forge.example:notaport/owner/repo.git",
            "git@forge.example:onlyone",
        ]
        for raw in bad:
            with self.subTest(raw=raw):
                self.assertIsNone(remote.parse_remote_url(raw))


class ProbeDiscoveryAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="remote-core-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = Repo(self)
        self.repo.commit({"README.md": "hello\n"})
        self.adapters = self.tmp / "adapters"
        self.adapters.mkdir()

    def test_discovers_sorted_adapters_passes_env_and_takes_first_exit_zero(self) -> None:
        seen_env = self.tmp / "seen.env"
        _make_executable(
            self.adapters / "01-miss" / "detect-remote.sh",
            "#!/bin/sh\nexit 1\n",
        )
        _make_executable(
            self.adapters / "02-match" / "detect-remote.sh",
            f"""#!/bin/sh
set -eu
cat > {str(seen_env)!r} <<EOF
REMOTE_URL=$REMOTE_URL
REMOTE_SCHEME=$REMOTE_SCHEME
REMOTE_HOST=$REMOTE_HOST
REMOTE_PORT=$REMOTE_PORT
REMOTE_PATH=$REMOTE_PATH
REMOTE_OWNER=$REMOTE_OWNER
REMOTE_REPO=$REMOTE_REPO
EOF
[ "$REMOTE_HOST" = "forge.example" ] || exit 1
printf 'CODE_HOST=customforge\\n'
printf 'FORGE_REPO=%s\\n' "$REMOTE_PATH"
""",
        )
        _make_executable(
            self.adapters / "03-second" / "detect-remote.sh",
            "#!/bin/sh\nprintf 'CODE_HOST=shadow\\n'\n",
        )
        self.repo.git("remote", "add", "origin", "https://oauth2:hidden-secret-99@forge.example:8443/team/sub/pkg.git")
        out_file = self.tmp / "out" / "remote.env"

        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "core" / "remote.py"),
                "detect",
                self.repo.dir,
                "--remote",
                "origin",
                "--adapters",
                str(self.adapters),
                "--out",
                str(out_file),
            ],
            env=ENV,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "CODE_HOST=customforge\nFORGE_REPO=team/sub/pkg")
        env_dump = seen_env.read_text(encoding="utf-8")
        self.assertIn("REMOTE_URL=https://forge.example:8443/team/sub/pkg.git\n", env_dump)
        self.assertIn("REMOTE_SCHEME=https\n", env_dump)
        self.assertIn("REMOTE_HOST=forge.example\n", env_dump)
        self.assertIn("REMOTE_PORT=8443\n", env_dump)
        self.assertIn("REMOTE_PATH=team/sub/pkg\n", env_dump)
        self.assertIn("REMOTE_OWNER=team/sub\n", env_dump)
        self.assertIn("REMOTE_REPO=pkg\n", env_dump)
        self.assertNotIn("hidden-secret-99", env_dump)
        written = out_file.read_text(encoding="utf-8")
        self.assertIn("CODE_HOST=customforge\n", written)
        self.assertIn("FORGE_REPO=team/sub/pkg\n", written)
        self.assertNotIn("hidden-secret-99", written)

    def test_no_match_or_local_remote_clears_stale_output_file(self) -> None:
        _make_executable(self.adapters / "01-miss" / "detect-remote.sh", "#!/bin/sh\nexit 1\n")
        out_file = self.tmp / "remote.env"
        out_file.write_text("CODE_HOST=stale\n", encoding="utf-8")
        self.repo.git("remote", "add", "origin", "/tmp/some/local/path")

        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "core" / "remote.py"),
                "detect",
                self.repo.dir,
                "--adapters",
                str(self.adapters),
                "--out",
                str(out_file),
            ],
            env=ENV,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertFalse(out_file.exists(), "stale remote.env should be removed when nothing matches")

    def test_refuses_secret_keys_from_adapter_output(self) -> None:
        _make_executable(
            self.adapters / "01-leaky" / "detect-remote.sh",
            "#!/bin/sh\nprintf 'CODE_HOST=bad\\nFORGE_TOKEN=leaked\\n'\n",
        )
        self.repo.git("remote", "add", "origin", "https://forge.example/a/b.git")
        out_file = self.tmp / "remote.env"
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "core" / "remote.py"),
                "detect",
                self.repo.dir,
                "--adapters",
                str(self.adapters),
                "--out",
                str(out_file),
            ],
            env=ENV,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("FORGE_TOKEN", proc.stderr)
        self.assertFalse(out_file.exists())

    def test_remote_env_sh_and_attach_sh_detect_src_origin(self) -> None:
        _make_executable(
            self.adapters / "01-forge" / "detect-remote.sh",
            "#!/bin/sh\n[ \"$REMOTE_HOST\" = 'forge.example' ] || exit 1\nprintf 'CODE_HOST=forge\\nFORGE_REPO=%s\\n' \"$REMOTE_PATH\"\n",
        )
        self.repo.git("remote", "add", "origin", "git@forge.example:owner/project.git")
        out_file = self.tmp / "crew-remote.env"
        dest = self.tmp / "clone"

        env = {
            **ENV,
            "REMOTE_ADAPTERS": str(self.adapters),
            "REMOTE_ENV_OUT": str(out_file),
        }
        proc = subprocess.run(
            ["sh", str(ROOT / "core" / "attach.sh"), self.repo.dir, str(dest)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(out_file.is_file())
        text = out_file.read_text(encoding="utf-8")
        self.assertIn("CODE_HOST=forge\n", text)
        self.assertIn("FORGE_REPO=owner/project\n", text)
        self.assertIn("CODE_HOST=forge", proc.stderr)
        self.assertIn("FORGE_REPO=owner/project", proc.stderr)


if __name__ == "__main__":
    unittest.main()
