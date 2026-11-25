"""The tests start from the environment `./go ci` gives them.

Under `make gates` every make variable is exported (Makefile, bare
`export`), so a test run by the test or mutation gate inherited WORKLOAD,
ATTACHED, BASE, HEAD and the rest, all naming the checkout under judgment;
a test that ran `make init` then rewrote that checkout's contract directory while
the mutation gate was running from it (self, 2026-11-25, run 45705097435).
A test that needs one of these sets it itself.
"""

import os

for _name in ("WORKLOAD", "WORKLOAD_CONTRACT", "WORKLOAD_ATTACHED", "ATTACHED", "BASE", "HEAD", "STAGED",
              "TICKET", "GATES", "GATES_OUT", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"):
    os.environ.pop(_name, None)
