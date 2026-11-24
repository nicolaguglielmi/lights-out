# SPDX-License-Identifier: Apache-2.0
"""gates/registry-conformance.sh's suite: the Registry port (ADR 0025, ADR 0072, ADR 0107).

    python3 -m core.gates.registry

Drives whichever REGISTRY adapter is set via its scripts under
adapters/$REGISTRY/{ready,publish,verify,sbom}.sh. Six cases in order, the
first disagreement a NO-GO naming the case:
  1 ready: optional ready.sh exits 0 with empty stdout
  2 unsigned-refused: verify.sh on an absent/unsigned tag exits nonzero with
    empty stdout
  3 publish-digest: publish.sh <tag> exits 0 and prints one immutable
    <repo>@sha256:<64 hex> reference on stdout
  4 verify-roundtrip: verify.sh <tag> exits 0 and prints the exact same
    <repo>@sha256:<64 hex> reference
  5 missing-arg-refused: publish.sh "" and verify.sh "" exit nonzero with
    empty stdout
  6 sbom: optional sbom.sh refuses an absent digest with empty stdout, and on
    the published ref either returns sha256:<64 hex> (exit 0) or exits
    nonzero with empty stdout (NO-GO if CONFORMANCE_REQUIRE_SBOM=1)
"""

import os
import re

from . import NoGo, adapter, main, run_id, say

IMMUTABLE_REF = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
DIGEST_ONLY = re.compile(r"^sha256:[0-9a-f]{64}$")


def _single_line(text):
    lines = text.splitlines()
    return lines[0] if len(lines) == 1 else None


class Suite:
    def __init__(self, reg, verb, env, *, has_ready=True, has_sbom=True):
        self.reg = reg
        self.verb = verb
        self.env = env
        self.has_ready = has_ready
        self.has_sbom = has_sbom
        self.rid = run_id()
        self.tag = env.get("CONFORMANCE_REGISTRY_TAG") or f"conf-{self.rid}"
        self.require_sbom = env.get("CONFORMANCE_REQUIRE_SBOM") == "1"
        say(f"[registry-conformance] run {self.rid} on REGISTRY={reg}, tag={self.tag}")

    def call(self, name, *args):
        rc, out, err = self.verb(name, *args)
        for line in err.splitlines():
            say(f"  [{name}] {line}")
        return rc, out

    def run(self):
        if self.has_ready:
            rc, out = self.call("ready")
            if rc != 0 or out != "":
                raise NoGo(f"case1 ready: ready.sh exited {rc} with stdout {out!r}, want exit 0 and empty stdout")
        say("[registry-conformance] case 1 ready: GO")

        absent_tag = f"conf-unsigned-{self.rid}"
        rc, out = self.call("verify", absent_tag)
        if rc == 0:
            raise NoGo(f"case2 unsigned-refused: verify.sh {absent_tag} exited 0 with {out!r}, want nonzero")
        if out != "":
            raise NoGo(f"case2 unsigned-refused: verify.sh {absent_tag} wrote {out!r} to stdout on failure")
        say("[registry-conformance] case 2 unsigned-refused: GO")

        rc, out = self.call("publish", self.tag)
        if rc != 0:
            raise NoGo(f"case3 publish-digest: publish.sh {self.tag} exited {rc}")
        published = _single_line(out)
        if not published or not IMMUTABLE_REF.fullmatch(published):
            raise NoGo(f"case3 publish-digest: publish.sh printed {out!r}, want one <repo>@sha256:<64hex> line")
        say(f"[registry-conformance] case 3 publish-digest ({published}): GO")

        rc, out = self.call("verify", self.tag)
        if rc != 0:
            raise NoGo(f"case4 verify-roundtrip: verify.sh {self.tag} exited {rc}")
        verified = _single_line(out)
        if verified != published:
            raise NoGo(f"case4 verify-roundtrip: verify.sh printed {out!r}, want exact {published!r}")
        say("[registry-conformance] case 4 verify-roundtrip: GO")

        for verb_name in ("publish", "verify"):
            rc, out = self.call(verb_name, "")
            if rc == 0 or out != "":
                raise NoGo(
                    f"case5 missing-arg-refused: {verb_name}.sh '' exited {rc} with stdout {out!r}, "
                    "want nonzero and empty stdout"
                )
        say("[registry-conformance] case 5 missing-arg-refused: GO")

        if self.has_sbom:
            repo = published.rsplit("@", 1)[0]
            absent_ref = f"{repo}@sha256:{'0' * 64}"
            rc, out = self.call("sbom", absent_ref)
            if rc == 0 or out != "":
                raise NoGo(
                    f"case6 sbom: sbom.sh on absent digest exited {rc} with stdout {out!r}, "
                    "want nonzero and empty stdout"
                )
            rc, out = self.call("sbom", published)
            if rc == 0:
                sbom_digest = _single_line(out)
                if not sbom_digest or not DIGEST_ONLY.fullmatch(sbom_digest):
                    raise NoGo(f"case6 sbom: sbom.sh printed {out!r}, want one sha256:<64hex> line")
            else:
                if out != "":
                    raise NoGo(f"case6 sbom: sbom.sh exited {rc} with non-empty stdout {out!r}")
                if self.require_sbom:
                    raise NoGo(f"case6 sbom: CONFORMANCE_REQUIRE_SBOM=1 but {published} has no SBOM attestation")
        say("[registry-conformance] case 6 sbom: GO")
        return f"{self.reg}, 6 cases"


def judge(argv, env=None, verb=None):
    env = os.environ if env is None else env
    reg = env.get("REGISTRY", "")
    if not reg:
        raise NoGo("REGISTRY is not set; set REGISTRY=<adapter> (CONFORMANCE=1)")
    has_ready = True
    has_sbom = True
    if verb is None:
        for v in ("publish", "verify"):
            if not os.path.isfile(f"adapters/{reg}/{v}.sh"):
                raise NoGo(f"adapters/{reg}/{v}.sh is missing: a Registry adapter ships publish and verify")
        has_ready = os.path.isfile(f"adapters/{reg}/ready.sh")
        has_sbom = os.path.isfile(f"adapters/{reg}/sbom.sh")
        verb = adapter(f"adapters/{reg}")
    suite = Suite(reg, verb, env, has_ready=has_ready, has_sbom=has_sbom)
    return suite.run()


if __name__ == "__main__":
    raise SystemExit(main(judge))
