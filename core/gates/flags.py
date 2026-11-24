# SPDX-License-Identifier: Apache-2.0
"""gates/flags-conformance.sh's suite: the Flags port (ADR 0026, ADR 0107).

    python3 -m core.gates.flags

Drives whichever FLAGS adapter is set via adapters/$FLAGS/{ready,evaluate,set}.sh.
Six cases in order, restoring the initial boolean value of CONFORMANCE_FLAG
(default release-freeze) on exit:
  1 ready: ready.sh exits 0 with empty stdout
  2 absent-flag-refused: evaluate.sh on an absent flag exits nonzero with empty stdout
  3 evaluate-known: evaluate.sh <flag> and <flag> <version> return true or false
  4 set-and-evaluate: set.sh <flag> on -> true, off -> false (skipped if CONFORMANCE_READONLY=1)
  5 invalid-set-refused: set.sh on absent flag or unknown variant exits nonzero with empty stdout
    and leaves <flag> unchanged (skipped if CONFORMANCE_READONLY=1)
  6 missing-arg-refused: evaluate.sh "" (and set.sh "" "") exit nonzero with empty stdout
"""

import os

from . import NoGo, adapter, main, run_id, say


def _single_bool(text):
    lines = text.splitlines()
    if len(lines) != 1 or lines[0] not in ("true", "false"):
        return None
    return lines[0]


class Suite:
    def __init__(self, flags_name, verb, env):
        self.flags_name = flags_name
        self.verb = verb
        self.env = env
        self.rid = run_id()
        self.flag = env.get("CONFORMANCE_FLAG") or "release-freeze"
        self.readonly = env.get("CONFORMANCE_READONLY") == "1"
        self.initial_variant = None
        say(f"[flags-conformance] run {self.rid} on FLAGS={flags_name}, flag={self.flag}")

    def call(self, name, *args):
        rc, out, err = self.verb(name, *args)
        for line in err.splitlines():
            say(f"  [{name}] {line}")
        return rc, out

    def restore(self):
        if self.readonly or self.initial_variant is None:
            return
        rc, out, err = self.verb("set", self.flag, self.initial_variant)
        if rc != 0 or out != "":
            say(f"[flags-conformance] warning: restore {self.flag}={self.initial_variant} failed (rc={rc}): {err.strip()}")

    def run(self):
        rc, out = self.call("ready")
        if rc != 0 or out != "":
            raise NoGo(f"case1 ready: ready.sh exited {rc} with stdout {out!r}, want exit 0 and empty stdout")
        say("[flags-conformance] case 1 ready: GO")

        absent = f"conf-absent-{self.rid}"
        rc, out = self.call("evaluate", absent)
        if rc == 0 or out != "":
            raise NoGo(
                f"case2 absent-flag-refused: evaluate.sh {absent} exited {rc} with stdout {out!r}, "
                "want nonzero and empty stdout"
            )
        say("[flags-conformance] case 2 absent-flag-refused: GO")

        rc, out = self.call("evaluate", self.flag)
        val = _single_bool(out)
        if rc != 0 or val is None:
            raise NoGo(f"case3 evaluate-known: evaluate.sh {self.flag} exited {rc} with {out!r}, want true or false")
        self.initial_variant = "on" if val == "true" else "off"
        rc_v, out_v = self.call("evaluate", self.flag, "v-conformance")
        if rc_v != 0 or _single_bool(out_v) is None:
            raise NoGo(
                f"case3 evaluate-known: evaluate.sh {self.flag} v-conformance exited {rc_v} with {out_v!r}, "
                "want true or false"
            )
        say(f"[flags-conformance] case 3 evaluate-known (initial={val}): GO")

        if not self.readonly:
            for variant, want in (("on", "true"), ("off", "false")):
                rc, out = self.call("set", self.flag, variant)
                if rc != 0 or out != "":
                    raise NoGo(f"case4 set-and-evaluate: set.sh {self.flag} {variant} exited {rc} with {out!r}")
                for extra in ((), ("v-conformance",)):
                    rc_e, out_e = self.call("evaluate", self.flag, *extra)
                    if rc_e != 0 or _single_bool(out_e) != want:
                        raise NoGo(
                            f"case4 set-and-evaluate: after set {variant}, evaluate {self.flag} {extra} "
                            f"exited {rc_e} with {out_e!r}, want {want!r}"
                        )
            say("[flags-conformance] case 4 set-and-evaluate: GO")

            for bad_args, desc in (((absent, "on"), "absent flag"), ((self.flag, f"bad-var-{self.rid}"), "absent variant")):
                rc, out = self.call("set", *bad_args)
                if rc == 0 or out != "":
                    raise NoGo(
                        f"case5 invalid-set-refused: set.sh on {desc} exited {rc} with stdout {out!r}, "
                        "want nonzero and empty stdout"
                    )
                rc_chk, out_chk = self.call("evaluate", self.flag)
                if rc_chk != 0 or _single_bool(out_chk) != "false":
                    raise NoGo(
                        f"case5 invalid-set-refused: refused set mutated {self.flag} to {out_chk!r} (rc={rc_chk})"
                    )
            say("[flags-conformance] case 5 invalid-set-refused: GO")

        rc, out = self.call("evaluate", "")
        if rc == 0 or out != "":
            raise NoGo(f"case6 missing-arg-refused: evaluate.sh '' exited {rc} with stdout {out!r}")
        if not self.readonly:
            rc, out = self.call("set", "", "")
            if rc == 0 or out != "":
                raise NoGo(f"case6 missing-arg-refused: set.sh '' '' exited {rc} with stdout {out!r}")
        say("[flags-conformance] case 6 missing-arg-refused: GO")

        suffix = " (CONFORMANCE_READONLY=1)" if self.readonly else ""
        return f"{self.flags_name}, 6 cases{suffix}"


def judge(argv, env=None, verb=None):
    env = os.environ if env is None else env
    flags_name = env.get("FLAGS", "")
    if not flags_name:
        raise NoGo("FLAGS is not set; set FLAGS=<adapter> (CONFORMANCE=1)")
    if verb is None:
        for v in ("ready", "evaluate", "set"):
            if not os.path.isfile(f"adapters/{flags_name}/{v}.sh"):
                raise NoGo(f"adapters/{flags_name}/{v}.sh is missing: a Flags adapter ships ready, evaluate and set")
        verb = adapter(f"adapters/{flags_name}")
    suite = Suite(flags_name, verb, env)
    try:
        return suite.run()
    finally:
        suite.restore()


if __name__ == "__main__":
    raise SystemExit(main(judge))
