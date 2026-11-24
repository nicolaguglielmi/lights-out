# SPDX-License-Identifier: Apache-2.0
"""gates/adr-check.sh's judgement of a range (docs/adr/0115 §5).

    python3 -m core.gates.adr_check <base> <head> <effective-policy>

The workload's range base..head, judged by core/adr_gov.py against the
effective policy's RISK_REQUIRE_ADR (docs/adr/0115 §3). GO when it is off,
when no decision record is required, or when the requirement is met: a new
or updated valid record in the range, or, at `high`, a commit message
naming one that exists at head and is valid. When a record is required,
NO-GO when it is missing, or when a record in the range or one a commit
message names is missing or incomplete (each defect a detail); NO-GO also
when the range or the policy cannot be read.
"""

import os

from .. import adr_gov, risk
from . import NoGo, main, say


def judge(argv, env=None):
    env = os.environ if env is None else env
    base, head, policy_file = argv
    try:
        with open(policy_file) as fh:
            policy = risk.parse_policy(fh.read())
        got = adr_gov.check_range_adr(env.get("WORKLOAD", "."), base, head, policy)
    except (OSError, ValueError) as exc:
        say(f"adr-check: {exc}")
        raise NoGo(f"core/adr_gov.py could not judge the range {base}..{head}") from None
    say(f"adr-check: {got.reason}")
    if not got.go:
        raise NoGo(got.reason, *got.details)
    return None


if __name__ == "__main__":
    raise SystemExit(main(judge))
