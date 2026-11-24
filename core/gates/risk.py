# SPDX-License-Identifier: Apache-2.0
"""gates/risk.sh's classification of a range (docs/adr/0016).

    python3 -m core.gates.risk <base> <head> <effective-policy>

`git diff --no-renames --numstat` of the workload's range, classified by
core/risk.py against the effective policy the wrapper wrote (docs/adr/0037
§5), written to $GATES_OUT/risk.json, and summarised on stderr as `risk:
level=<l> decision=<d> signals=<name>(<paths>),... adr_required=<bool>`
(docs/adr/0115 §4). Always GO when it
classified: risk is a routing decision `make gates` reads from risk.json,
not a verdict. NO-GO only when the range or the policy cannot be read or a
numstat line does not parse.
"""

import json
import os

from .. import risk
from . import PIPE, NoGo, main, proc, say


def judge(argv, env=None):
    env = os.environ if env is None else env
    base, head, policy_file = argv
    out_dir = env.get("GATES_OUT") or ".gates-out"
    os.makedirs(out_dir, exist_ok=True)
    got = proc(["git", "-C", env.get("WORKLOAD", "."), "diff", "--no-renames", "--numstat", base, head],
               stdout=PIPE, stderr=PIPE, text=True)
    try:
        if got.returncode != 0:
            raise ValueError(f"git diff failed: {got.stderr.strip()}")
        # The added files, for RISK_REQUIRE_ADR=structural (docs/adr/0115 §4).
        added = proc(["git", "-C", env.get("WORKLOAD", "."), "diff", "--no-renames", "--diff-filter=A",
                      "--name-only", base, head], stdout=PIPE, stderr=PIPE, text=True)
        if added.returncode != 0:
            raise ValueError(f"git diff failed: {added.stderr.strip()}")
        with open(policy_file) as fh:
            result = risk.classify(got.stdout.splitlines(), risk.parse_policy(fh.read()),
                                   added_paths=[p for p in added.stdout.splitlines() if p])
    except (OSError, ValueError) as exc:
        say(f"risk: {exc}")
        raise NoGo(f"core/risk.py could not classify the range {base}..{head}") from None
    with open(os.path.join(out_dir, "risk.json"), "w") as fh:
        fh.write(json.dumps(result) + "\n")
    signals = ",".join(f"{s['name']}({' '.join(s['paths'])})" for s in result["signals"])
    say(f"risk: level={result['level']} decision={result['decision']} signals={signals or 'none'} "
        f"adr_required={'true' if result['adr_required'] else 'false'}")
    return None


if __name__ == "__main__":
    raise SystemExit(main(judge))
