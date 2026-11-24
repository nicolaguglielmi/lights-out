# SPDX-License-Identifier: Apache-2.0
"""gates/delivery-conformance.sh's suite: the Delivery/Flags port
(docs/adr/0026). Written from the ADR, never from an adapter.

    python3 -m core.gates.delivery

Six cases in the fixed order §6 requires: cases 1-4 stage a real release
each through `make release` / `make promote`, never an adapter script by
hand (ADR 0024 §2); cases 5-6 stage nothing and sit last. Every assertion
goes through adapters/$DELIVERY/status.sh (§2's three keys: stable a
string, canary a string or null, weight an integer 0-100) and
$APP_URL/healthz (10 GETs, ADR 0013 §4's count), never a vendor's admin API.
`make release` reports a failed recipe as 2, whatever the launchpad's own
exit, so a refused release is 2.

  preflight  release-freeze evaluates false, the kill switch
             (WORKLOAD_KILL_SWITCH) evaluates true for CANARY_VERSION, the
             weight is 0: each unmet one its own NO-GO naming the fix
  1 rejects and rolls back: `NO-GO: launchpad — 5%: ...`, the record's
    verdict rolled-back, weight 0, stable the record's, 10/10 healthz on it
  2 promotes: the kill switch off, held at 25 with the record's stable and
    canary, then GO_FOR_LAUNCH=GO make promote to 100 and 10/10 healthz on
    the new version
  3 respects the freeze: `NO-GO: launchpad — release-freeze is on`, weight
    0 and stable unchanged from a snapshot
  4 the same frozen release again: the same, against the same snapshot
  5 an unsigned image (a tag of zeros, CANARY_VERSION the stable):
    `NO-GO: launchpad — unsigned image`, status unchanged
  6 every status.sh read above parsed and agreed: reaching the end is GO

Every flag it changes is restored through set.sh on any exit: the freeze
off, the kill switch off (set.sh cannot write the committed targeting
rule; "off" fails safe). On GO it records the adapter's hash
(core/gates/readiness.py's adapter_hash) in $CONFORMANCE_DIR/delivery-
<adapter>.env, which production-readiness reads (docs/adr/0057 §9).
"""

import json
import os
import time
import urllib.request

from . import PIPE, NoGo, main, proc, say
from .readiness import adapter_hash


class Plane:
    """The processes and the URL the suite drives: stdout captured, stderr live."""

    def run(self, argv, env=None, quiet=False):
        """(exit code, stdout without its trailing newlines); QUIET sends stdout to stderr."""
        full = dict(os.environ, **(env or {}))
        got = proc(argv, env=full, stdout=2 if quiet else PIPE, text=True)
        return got.returncode, (got.stdout or "").rstrip("\n")

    def healthz(self, url):
        """The version GET URL reports, or None."""
        try:
            with urllib.request.urlopen(url, timeout=3) as r:  # nosec B310 - the plane's own APP_URL
                return jq_r(json.loads(r.read()).get("version"))
        except (OSError, ValueError, AttributeError):
            return None


def jq_r(value):
    """A value as `jq -r` prints it."""
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    return json.dumps(value)


class Suite:
    def __init__(self, env, plane):
        self.env, self.plane = env, plane
        self.record_file = env.get("RELEASE_RECORD") or ".crew/release.json"
        host = env.get("PLANE_HOST") or "host.containers.internal"
        self.app_url = env.get("APP_URL") or f"http://{host}:8080"
        for key, why in (("DELIVERY", "DELIVERY is not set; a profile sets it — run via make delivery-conformance"),
                         ("FLAGS", "FLAGS is not set; a profile sets it — run via make delivery-conformance"),
                         ("WORKLOAD_KILL_SWITCH", "WORKLOAD_KILL_SWITCH is empty; the workload's workload.env should "
                                                  "set it (docs/adr/0030 §2)"),
                         ("CANARY_VERSION", "CANARY_VERSION is empty; the workload's workload.env should set it "
                                            "(docs/adr/0030 §2)")):
            if not env.get(key):
                raise NoGo(why)
        self.delivery, self.flags = env["DELIVERY"], env["FLAGS"]
        self.ks, self.cv = env["WORKLOAD_KILL_SWITCH"], env["CANARY_VERSION"]
        self.tag = env.get("CANARY_TAG") or ""
        if not self.tag:
            rc, sha = plane.run(["git", "rev-parse", "HEAD"])
            if rc != 0:
                raise NoGo("could not compute CANARY_TAG: git rev-parse HEAD failed")
            self.tag = f"sha-{sha}"
        self.flags_dir, self.delivery_dir = f"adapters/{self.flags}", f"adapters/{self.delivery}"
        self.touched = {"release-freeze": False, self.ks: False}

    def set_flag(self, key, value, label):
        if self.plane.run(["sh", f"{self.flags_dir}/set.sh", key, value], quiet=True)[0] != 0:
            raise NoGo(f"{label}: adapters/{self.flags}/set.sh {key} {value} failed")
        # The kill switch is restored once it was set at all; the freeze only
        # while it is on.
        self.touched[key] = key == self.ks or value == "on"

    def restore(self):
        for key in ("release-freeze", self.ks):
            if self.touched.get(key):
                if self.plane.run(["sh", f"{self.flags_dir}/set.sh", key, "off"], quiet=True)[0] != 0:
                    say(f"delivery-conformance: cleanup could not restore {key}; run: sh {self.flags_dir}/set.sh {key} off")

    def status(self, label):
        rc, text = self.plane.run(["sh", f"{self.delivery_dir}/status.sh"])
        if rc != 0:
            raise NoGo(f"{label}: adapters/{self.delivery}/status.sh exited nonzero")
        try:
            data = json.loads(text)
            keys = ",".join(sorted(data))
        except (ValueError, TypeError):
            raise NoGo(f"{label}: adapters/{self.delivery}/status.sh did not print valid JSON: {text}") from None
        if not isinstance(data, dict):
            raise NoGo(f"{label}: adapters/{self.delivery}/status.sh did not print valid JSON: {text}")
        if keys != "canary,stable,weight":
            raise NoGo(f"{label}: adapters/{self.delivery}/status.sh printed keys [{keys}], want exactly stable, "
                       "canary, weight")
        w = data["weight"]
        if not (isinstance(data["stable"], str) and (data["canary"] is None or isinstance(data["canary"], str))
                and isinstance(w, (int, float)) and not isinstance(w, bool) and w == int(w) and 0 <= w <= 100):
            raise NoGo(f"{label}: adapters/{self.delivery}/status.sh printed the wrong shape: {text}")
        return data

    def record(self, label):
        if not os.path.isfile(self.record_file):
            raise NoGo(f"{label}: {self.record_file} does not exist")
        try:
            with open(self.record_file) as fh:
                return json.load(fh)
        except ValueError:
            raise NoGo(f"{label}: {self.record_file} is not valid JSON") from None

    def release(self, label, version, tag):
        say(f"[delivery-conformance] {label}: make release CANARY_VERSION={version} CANARY_TAG={tag}")
        return self.plane.run(["make", "--no-print-directory", "release", f"CANARY_VERSION={version}",
                               f"CANARY_TAG={tag}"])

    def healthz(self, want):
        return sum(1 for _ in range(10) if self.plane.healthz(f"{self.app_url}/healthz") == want)

    def expect(self, label, what, got, want, why=""):
        if got != want:
            raise NoGo(f"{label}: {what} is {jq_r(got)}, want {jq_r(want)}{why}")

    def run(self):
        rc, freeze = self.plane.run(["sh", f"{self.flags_dir}/evaluate.sh", "release-freeze"])
        if rc != 0:
            raise NoGo(f"adapters/{self.flags}/evaluate.sh release-freeze failed; is the plane up? (./go up)")
        if freeze != "false":
            raise NoGo(f"release-freeze already evaluates {freeze}, want false; run: sh {self.flags_dir}/set.sh "
                       "release-freeze off")
        rc, ks = self.plane.run(["sh", f"{self.flags_dir}/evaluate.sh", self.ks, self.cv])
        if rc != 0:
            raise NoGo(f"adapters/{self.flags}/evaluate.sh {self.ks} {self.cv} failed; is the plane up? (./go up)")
        if ks != "true":
            raise NoGo(f"{self.ks} {self.cv} already evaluates {ks}, want true; the workload's committed "
                       "flags/flags.json targeting rule may be missing")
        status = self.status("preflight")
        if status["weight"] != 0:
            raise NoGo(f"preflight: adapters/{self.delivery}/status.sh reports weight {jq_r(status['weight'])}, "
                       f"want 0; run: sh {self.delivery_dir}/rollback.sh")
        say(f"[delivery-conformance] preflight GO: DELIVERY={self.delivery} FLAGS={self.flags} "
            f"stable={status['stable']} CANARY_VERSION={self.cv} CANARY_TAG={self.tag}")
        self.case1()
        self.case2()
        snap = self.case3()
        self.case4(snap)
        self.case5()
        say("[delivery-conformance] case6 reports-state: every status.sh read above parsed and agreed: GO")
        self.write_record()

    def case1(self):
        label = "case1 rejects-and-rolls-back"
        rc, out = self.release(label, self.cv, self.tag)
        if rc != 2:
            raise NoGo(f"{label}: make release exited {rc}, want 2")
        if not out.startswith("NO-GO: launchpad — 5%: "):
            raise NoGo(f"{label}: stdout was [{out}], want a line starting with NO-GO: launchpad — 5%: ")
        record = self.record(label)
        self.expect(label, "RELEASE_RECORD.verdict", record.get("verdict"), "rolled-back")
        status = self.status(label)
        self.expect(label, "status.sh weight", status["weight"], 0)
        self.expect(label, "status.sh stable", status["stable"], record.get("stable"), " (the release record's)")
        ok = self.healthz(jq_r(record.get("stable")))
        if ok != 10:
            raise NoGo(f"{label}: only {ok}/10 healthz report {jq_r(record.get('stable'))}")
        say(f"[delivery-conformance] {label}: GO")

    def case2(self):
        label = "case2 promotes"
        self.set_flag(self.ks, "off", label)
        rc, out = self.release(label, self.cv, self.tag)
        if rc != 0:
            raise NoGo(f"{label}: make release exited {rc}, want 0 (stdout: {out})")
        record = self.record(label)
        self.expect(label, "RELEASE_RECORD.verdict", record.get("verdict"), "held")
        if jq_r(record.get("held_at")) != "25":
            raise NoGo(f"{label}: RELEASE_RECORD.held_at is {jq_r(record.get('held_at'))}, want 25")
        status = self.status(f"{label} (held at 25%)")
        self.expect(label, "status.sh stable", status["stable"], record.get("stable"), " (the release record's)")
        self.expect(label, "status.sh canary", status["canary"], record.get("version"), " (the release record's)")
        self.expect(label, "status.sh weight", status["weight"], 25)
        say(f"[delivery-conformance] {label}: GO_FOR_LAUNCH=GO make promote")
        rc, out = self.plane.run(["make", "--no-print-directory", "promote"], env={"GO_FOR_LAUNCH": "GO"})
        if rc != 0:
            raise NoGo(f"{label}: GO_FOR_LAUNCH=GO make promote exited {rc} (stdout: {out})")
        status = self.status(f"{label} (after promote)")
        self.expect(label, "status.sh weight after promote", status["weight"], 100)
        ok = self.healthz(jq_r(record.get("version")))
        if ok != 10:
            raise NoGo(f"{label}: only {ok}/10 healthz report {jq_r(record.get('version'))} at 100%")
        say(f"[delivery-conformance] {label}: GO")

    def frozen(self, label):
        rc, out = self.release(label, self.cv, self.tag)
        if rc != 2:
            raise NoGo(f"{label}: make release exited {rc}, want 2")
        if out != "NO-GO: launchpad — release-freeze is on":
            raise NoGo(f"{label}: stdout was [{out}], want exactly NO-GO: launchpad — release-freeze is on")
        status = self.status(label)
        self.expect(label, "status.sh weight", status["weight"], 0)
        return status

    def case3(self):
        label = "case3 respects-the-freeze"
        self.set_flag("release-freeze", "on", label)
        snap = self.status(f"{label} (snapshot)")["stable"]
        status = self.frozen(label)
        self.expect(label, "status.sh stable", status["stable"], snap, " (the snapshot's)")
        say(f"[delivery-conformance] {label}: GO")
        return snap

    def case4(self, snap):
        label = "case4 rollback-is-idempotent"
        status = self.frozen(label)
        self.expect(label, "status.sh stable", status["stable"], snap, " (case 3's snapshot)")
        self.set_flag("release-freeze", "off", label)
        say(f"[delivery-conformance] {label}: GO")

    def case5(self):
        label = "case5 unsigned-image"
        snap = self.status(f"{label} (snapshot)")
        rc, out = self.release(label, jq_r(snap["stable"]), "sha-" + "0" * 40)
        if rc != 2:
            raise NoGo(f"{label}: make release exited {rc}, want 2")
        if out != "NO-GO: launchpad — unsigned image":
            raise NoGo(f"{label}: stdout was [{out}], want exactly NO-GO: launchpad — unsigned image")
        status = self.status(label)
        for key in ("stable", "canary", "weight"):
            self.expect(label, f"status.sh {key}", status[key], snap[key], " (the snapshot's)")
        say(f"[delivery-conformance] {label}: GO")

    def write_record(self):
        record_dir = self.env.get("CONFORMANCE_DIR") or ".crew/conformance"
        path = f"{record_dir}/delivery-{self.delivery}.env"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(f"ADAPTER={self.delivery}\nHASH={adapter_hash(self.delivery_dir)}\n"
                     f"AT={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        say(f"[delivery-conformance] recorded in {record_dir}/delivery-{self.delivery}.env")


def judge(argv, env=None, plane=None):
    env = os.environ if env is None else env
    suite = Suite(env, plane or Plane())
    try:
        suite.run()
    finally:
        suite.restore()
    return None


if __name__ == "__main__":
    raise SystemExit(main(judge))
