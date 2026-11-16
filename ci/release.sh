#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ci-release: docs/adr/0075, engine-free and network-free; written from the
# ADR, not from the workflow or the scripts.
#   workflow  (PyYAML) package.yml: workflow_dispatch has the boolean input
#             dry_run, default true; the jobs dist, publish, pypi, actions,
#             release, with publish needing dist, pypi dist and publish,
#             actions publish, release publish and actions; dist's if is the
#             github.com guard and "not a branch push"; pypi runs on a v* tag
#             only, in the environment pypi, with exactly contents: read and
#             id-token: write, uploads with `uv publish --trusted-publishing
#             always`, and names no secret, password or token; actions holds
#             exactly contents: read and release exactly contents: write; the
#             one secret in the file is ACTION_REPOS_TOKEN, named only in the
#             actions job and only under a v* tag; in publish every step that
#             logs in, pushes, signs or verifies runs only when not
#             inputs.dry_run, and the job runs `cosign verify` with an exact
#             identity and GitHub's issuer, and outputs version, gates and
#             full; publish also signs the release files with `cosign
#             sign-blob --bundle <file>.sigstore.json`, verifies each with
#             `cosign verify-blob`, an exact identity and GitHub's issuer, and
#             release attaches them (assets/*) to the release it creates
#             (ADR 0096 §6); elsewhere every step that pushes, uploads or creates a
#             release runs only on a v* tag; dist runs uv build, the wheel's
#             version, uploads dist/ at the Action's pin of upload-artifact.
#   sync      package/sync-action.sh against local bare repositories, for
#             test-proof: without --push the remote is untouched and the
#             local clone holds the tag v<version> with action.yml, README.md
#             and LICENSE equal to the sources; with --push the remote's
#             branch and tag hold them, committed by the owner's noreply
#             identity with no trailer; again with --push, exit 0 and the
#             remote unmoved; a remote tag holding other files: exit 1, the
#             tag unmoved; a version the shim does not use: exit 1; --push to
#             an https remote with no ACTION_REPOS_TOKEN: exit 1 naming it.
#   notes     package/release-notes.sh lights-out V GATES FULL: both images by
#             tag and by digest, a cosign verify per image by digest with the
#             identity regexp of ADR 0057 §4 and GitHub's issuer, the PyPI
#             line, both Action repositories at vV; docs/releases/vV.md first
#             when it exists; a malformed digest: exit 1, nothing on stdout.
#             `action NAME V`: the uses line at vV and the link to lights-out
#             V's release.
# Each judge then runs on copies with one planted defect (the workflow as
# data; the scripts by an edit naming their real text: if the text moved,
# the edit is not applied and this says the check cannot fail).
# shellcheck disable=SC2016 # edits and expressions are text
set -eu
name=release
. ci/lib.sh

for t in git python3; do command -v "$t" >/dev/null 2>&1 || nogo "$t is not on PATH (./go ci runs in the workbench)"; done
python3 -c 'import yaml' 2>/dev/null || nogo "python3 cannot import yaml (PyYAML, workbench/uv.lock)"
for f in .github/workflows/package.yml package/sync-action.sh package/release-notes.sh actions/test-proof/action.yml LICENSE; do
  [ -e "$f" ] || nogo "$f not found"
done

root=$(pwd -P)
version=$(sed -n 's/^version=//p' lights-out)
w=$(mktemp -d)
trap 'rm -rf "$w"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=ci GIT_AUTHOR_EMAIL=ci@lights-out.invalid \
  GIT_COMMITTER_NAME=ci GIT_COMMITTER_EMAIL=ci@lights-out.invalid
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE ACTION_REPOS_TOKEN

# --- workflow -------------------------------------------------------------------
cat > "$w/wf.py" <<'PY'
import copy, json, re, sys, yaml

path, mode = sys.argv[1], sys.argv[2]
d0 = yaml.safe_load(open(path))
S = "github.server_url == 'https://github.com'"
TAG = "startsWith(github.ref, 'refs/tags/v')"
NOT_BRANCH = "!(github.event_name == 'push' && !startsWith(github.ref, 'refs/tags/v'))"
UP = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
ISSUER = "--certificate-oidc-issuer https://token.actions.githubusercontent.com"


def cond(x):
    return re.sub(r"\s+", " ", re.sub(r"^\$\{\{(.*)\}\}$", r"\1", str(x or "").strip()).strip())


def live(r):
    # what a run executes: the text an echo prints (a dry run shows commands) is not run
    return re.sub(r"""\becho\s+("(?:[^"\\]|\\.)*"|'[^']*')""", "echo", str(r or ""))


def needs(j):
    n = j.get("needs") or []
    return {n} if isinstance(n, str) else set(n)


def tag_only(c):
    # a v* tag, not negated: the expression holds TAG and no "!" right before it
    return TAG in c and ("!" + TAG) not in c and "workflow_dispatch" not in c


def flaws(d):
    on = d.get("on", d.get(True)) or {}
    inp = ((on.get("workflow_dispatch") or {}).get("inputs") or {}).get("dry_run")
    if not (isinstance(inp, dict) and inp.get("type") == "boolean" and inp.get("default") is True):
        yield "workflow_dispatch has no boolean input dry_run with default true (ADR 0075 §5)"
    jobs = d.get("jobs") or {}
    want = {"dist": set(), "publish": {"dist"}, "pypi": {"dist", "publish"}, "actions": {"publish"}, "release": {"publish", "actions"}}
    for n, nd in want.items():
        if n not in jobs:
            yield "package.yml has no job %s (ADR 0075 §1)" % n
        elif not nd <= needs(jobs[n]):
            yield "job %s needs %s, want at least %s (ADR 0075 §1)" % (n, sorted(needs(jobs[n])), sorted(nd))
    if any(n not in jobs for n in want):
        return
    if cond(jobs["dist"].get("if")) != S + " && " + NOT_BRANCH:
        yield "job dist: if: %r is not the github.com guard and not a branch push (ADR 0075 §1)" % cond(jobs["dist"].get("if"))
    p = jobs["pypi"]
    c = cond(p.get("if"))
    if not (c.startswith(S + " && ") and tag_only(c)):
        yield "job pypi: if: %r does not run on a v* tag only (ADR 0075 §2)" % c
    env = p.get("environment")
    if (env.get("name") if isinstance(env, dict) else env) != "pypi":
        yield "job pypi does not run in the environment pypi (ADR 0075 §2)"
    if p.get("permissions") != {"contents": "read", "id-token": "write"}:
        yield "job pypi holds %s, want exactly contents: read and id-token: write (ADR 0075 §2)" % p.get("permissions")
    pr = "\n".join(live(s.get("run")) for s in p.get("steps") or [])
    if not re.search(r"\buv publish\b[^\n]*--trusted-publishing always", pr):
        yield "job pypi does not upload with uv publish --trusted-publishing always (ADR 0075 §2)"
    if re.search(r"secrets\.|password|PYPI_TOKEN|UV_PUBLISH_TOKEN|--token", json.dumps(p), re.I):
        yield "job pypi names a secret, a password or a token: trusted publishing needs none (ADR 0075 §2)"
    for n, want_p in (("actions", {"contents": "read"}), ("release", {"contents": "write"})):
        if jobs[n].get("permissions") != want_p:
            yield "job %s holds %s, want exactly %s (ADR 0075 §1)" % (n, jobs[n].get("permissions"), want_p)
    for n, j in jobs.items():
        for s in j.get("steps") or []:
            for x in re.findall(r"\$\{\{([^}]*secrets\.[^}]*)\}\}", json.dumps(s)):
                names = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", x))
                if names != {"ACTION_REPOS_TOKEN"} or n != "actions":
                    yield "job %s names secrets %s: the one secret is ACTION_REPOS_TOKEN, in the actions job (ADR 0075 §3)" % (n, sorted(names))
                elif not tag_only(cond(x)):
                    yield "job actions reads ACTION_REPOS_TOKEN outside a v* tag: %r (ADR 0075 §3)" % cond(x)
    pub = jobs["publish"]
    for s in pub.get("steps") or []:
        r = live(s.get("run"))
        if re.search(r"--push\b|cosign (sign|verify)\b|docker login", r) and "!inputs.dry_run" not in cond(s.get("if")).replace(" ", ""):
            yield "publish: the step %r logs in, pushes, signs or verifies without if: !inputs.dry_run (ADR 0075 §5)" % s.get("name", r[:40])
    prun = "\n".join(live(s.get("run")) for s in pub.get("steps") or [])
    if not (re.search(r"cosign verify(?!-)\b[^\n]*(\\\n[^\n]*)*--certificate-identity\s", prun) and ISSUER in prun):
        yield "publish does not run cosign verify with an exact --certificate-identity and GitHub's issuer (ADR 0075 §1)"
    # ADR 0096 §6: the wheel and the sdist signed keyless into <file>.sigstore.json, each bundle verified, handed to release.
    if not re.search(r"cosign sign-blob\b[^\n]*--bundle\s+\S*\.sigstore\.json", prun):
        yield "publish does not sign the release files with cosign sign-blob --bundle <file>.sigstore.json (ADR 0096 §6)"
    if not re.search(r"cosign verify-blob\b[^\n]*(\\\n[^\n]*)*--certificate-identity\s[^\n]*(\\\n[^\n]*)*" + re.escape(ISSUER), prun):
        yield "publish does not verify each bundle with cosign verify-blob, an exact --certificate-identity and GitHub's issuer (ADR 0096 §6)"
    rrun = "\n".join(live(s.get("run")) for s in jobs["release"].get("steps") or [])
    if not re.search(r"gh release create\b[^\n]*assets/\*", rrun):
        yield "release does not attach the signed files (assets/*) to the release it creates (ADR 0096 §6)"
    if not {"version", "gates", "full"} <= set(pub.get("outputs") or {}):
        yield "publish does not output version, gates and full (ADR 0075 §1)"
    for n, j in jobs.items():
        if n == "publish":
            continue
        for s in j.get("steps") or []:
            r = live(s.get("run"))
            if re.search(r"--push\b|gh release create|\buv publish\b", r) and not (tag_only(cond(s.get("if"))) or (n == "pypi" and tag_only(c))):
                yield "job %s: the step %r pushes, uploads or creates a release outside a v* tag (ADR 0075 §5)" % (n, s.get("name", r[:40]))
    dr = "\n".join(live(s.get("run")) for s in jobs["dist"].get("steps") or [])
    if "uv build" not in dr or "lights-out-gates version" not in dr:
        yield "job dist does not run uv build and the wheel's lights-out-gates version (ADR 0075 §1)"
    if UP not in [str(s.get("uses", "")) for s in jobs["dist"].get("steps") or []]:
        yield "job dist does not upload dist/ with %s (ADR 0075 §1)" % UP


J = lambda d: d["jobs"]


def step(d, job, needle):
    return next(s for s in J(d)[job]["steps"] if needle in live(s.get("run")) + str(s.get("uses", "")))


def on(d):
    return d.get("on", d.get(True))


CASES = [
    ("dry_run defaults to false", lambda d: on(d)["workflow_dispatch"]["inputs"]["dry_run"].update(default=False), "input dry_run"),
    ("no dry_run input", lambda d: on(d)["workflow_dispatch"].pop("inputs"), "input dry_run"),
    ("no pypi job", lambda d: J(d).pop("pypi"), "no job pypi"),
    ("pypi does not need dist", lambda d: J(d)["pypi"].update(needs=["publish"]), "job pypi needs"),
    ("publish does not need dist", lambda d: J(d)["publish"].pop("needs"), "job publish needs"),
    ("release does not need actions", lambda d: J(d)["release"].update(needs=["publish"]), "job release needs"),
    ("dist runs on every push", lambda d: J(d)["dist"].update({"if": S}), "job dist: if"),
    ("pypi runs on a dispatch", lambda d: J(d)["pypi"].update({"if": S + " && (" + TAG + " || github.event_name == 'workflow_dispatch')"}), "job pypi: if"),
    ("pypi runs on any ref", lambda d: J(d)["pypi"].update({"if": S}), "job pypi: if"),
    ("pypi has no environment", lambda d: J(d)["pypi"].pop("environment"), "environment pypi"),
    ("pypi holds packages: write", lambda d: J(d)["pypi"]["permissions"].update(packages="write"), "want exactly contents: read and id-token: write"),
    ("pypi has no id-token", lambda d: J(d)["pypi"]["permissions"].pop("id-token"), "want exactly contents: read and id-token: write"),
    ("pypi uploads with a token", lambda d: step(d, "pypi", "uv publish").update(env={"UV_PUBLISH_TOKEN": "${{ secrets.PYPI_TOKEN }}"}), "names a secret"),
    ("pypi does not force trusted publishing", lambda d: step(d, "pypi", "uv publish").update(run=step(d, "pypi", "uv publish")["run"].replace("--trusted-publishing always", "")), "trusted-publishing always"),
    ("actions holds contents: write", lambda d: J(d)["actions"].update(permissions={"contents": "write"}), "job actions holds"),
    ("release holds packages: write", lambda d: J(d)["release"]["permissions"].update(packages="write"), "job release holds"),
    ("release reads the Action repositories' token", lambda d: J(d)["release"]["steps"].append({"run": "true", "env": {"T": "${{ secrets.ACTION_REPOS_TOKEN }}"}}), "job release names secrets"),
    ("actions reads another secret", lambda d: J(d)["actions"]["steps"].append({"run": "true", "env": {"T": "${{ secrets.OTHER }}"}}), "job actions names secrets ['OTHER']"),
    ("actions reads the token on any ref", lambda d: J(d)["actions"]["steps"].append({"run": "true", "env": {"T": "${{ secrets.ACTION_REPOS_TOKEN }}"}}), "outside a v* tag"),
    ("publish pushes in a dry run", lambda d: step(d, "publish", "--push").pop("if"), "without if: !inputs.dry_run"),
    ("publish signs in a dry run", lambda d: step(d, "publish", "cosign sign").pop("if"), "without if: !inputs.dry_run"),
    ("publish does not verify", lambda d: step(d, "publish", "cosign verify").update(run="true"), "does not run cosign verify"),
    ("publish verifies by a regexp only", lambda d: step(d, "publish", "cosign verify").update(run=step(d, "publish", "cosign verify")["run"].replace("--certificate-identity ", "--certificate-identity-regexp ")), "does not run cosign verify"),
    ("publish does not sign the release files", lambda d: step(d, "publish", "cosign sign-blob").update(run="true"), "cosign sign-blob --bundle"),
    ("publish signs them into another name", lambda d: step(d, "publish", "cosign sign-blob").update(run=step(d, "publish", "cosign sign-blob")["run"].replace(".sigstore.json", ".bundle")), "cosign sign-blob --bundle"),
    ("publish does not verify the bundles", lambda d: step(d, "publish", "cosign sign-blob").update(run=step(d, "publish", "cosign sign-blob")["run"].replace("cosign verify-blob", "true")), "cosign verify-blob"),
    ("publish signs the files in a dry run", lambda d: step(d, "publish", "cosign sign-blob").pop("if"), "without if: !inputs.dry_run"),
    ("release attaches nothing", lambda d: step(d, "release", "gh release create").update(run=step(d, "release", "gh release create")["run"].replace(' "$RUNNER_TEMP"/assets/*', "")), "does not attach the signed files"),
    ("publish has no digest outputs", lambda d: J(d)["publish"]["outputs"].pop("gates"), "output version, gates and full"),
    ("actions pushes on a dispatch", lambda d: step(d, "actions", "--push").pop("if"), "outside a v* tag"),
    ("actions pushes when not a tag", lambda d: step(d, "actions", "--push").update({"if": "!" + TAG}), "outside a v* tag"),
    ("release creates on a dispatch", lambda d: step(d, "release", "gh release create").update({"if": "always()"}), "outside a v* tag"),
    ("dist does not try the wheel", lambda d: J(d)["dist"]["steps"].__setitem__(slice(None), [s for s in J(d)["dist"]["steps"] if "lights-out-gates version" not in live(s.get("run"))]), "lights-out-gates version"),
    ("publish's dry run pushes for real", lambda d: next(s for s in J(d)["publish"]["steps"] if cond(s.get("if")) == "inputs.dry_run").update(run="docker push x --push"), "without if: !inputs.dry_run"),
    ("a dry run of actions pushes", lambda d: next(s for s in J(d)["actions"]["steps"] if cond(s.get("if")) == "!" + TAG).update(run='sh package/sync-action.sh a 1 d --push'), "outside a v* tag"),
    ("dist uploads at another pin", lambda d: step(d, "dist", "actions/upload-artifact@").update(uses="actions/upload-artifact@v4"), "does not upload dist/"),
]
if mode == "check":
    print("\n".join(flaws(d0)))
else:
    for desc, mutate, want in CASES:
        d = copy.deepcopy(d0)
        try:
            mutate(d)
        except (KeyError, StopIteration) as e:
            print("it cannot fail: %s (the edit found nothing to change: %r)" % (desc, e))
            break
        got = "\n".join(flaws(d))
        if want not in got:
            print("it cannot fail: %s -> [%s]" % (desc, got[:200]))
            break
PY

# --- sync -----------------------------------------------------------------------
# remote DIR: a bare repository at DIR with one commit on main.
remote() {
  git init -q --bare -b main "$1"
  git clone -q "$1" "$w/seed" 2>/dev/null
  echo old > "$w/seed/README.md"
  git -C "$w/seed" add -A && git -C "$w/seed" commit -q -m seed && git -C "$w/seed" push -q origin HEAD:main
  rm -rf "$w/seed"
}
# same TREEISH ROOT: the three files at TREEISH (in the current git dir) equal ROOT's sources.
same() {
  for f in action.yml README.md; do git show "$1:$f" 2>/dev/null | cmp -s - "$2/actions/test-proof/$f" || return 1; done
  git show "$1:LICENSE" 2>/dev/null | cmp -s - "$2/LICENSE"
}
# sync_flaws SCRIPT: package/sync-action.sh's contract, one line per flaw.
sync_flaws() {
  s=$1 r=$w/r1.git c=0
  rm -rf "$w"/r*.git "$w"/clone*
  remote "$r"
  before=$(git -C "$r" rev-parse main)
  ACTION_REMOTE=$r sh "$s" test-proof "$version" "$w/clone0" >"$w/o" 2>"$w/e" || { echo "a dry run exited non-zero: $(head -n 3 "$w/e")"; c=1; }
  [ "$(git -C "$r" rev-parse main)" = "$before" ] && ! git -C "$r" rev-parse -q --verify "refs/tags/v$version" >/dev/null \
    || echo "a dry run changed the remote"
  (cd "$w/clone0" 2>/dev/null && same "v$version" "$root") || echo "a dry run left no local tag v$version holding action.yml, README.md and LICENSE"
  [ "$c" = 0 ] || return 0
  ACTION_REMOTE=$r sh "$s" test-proof "$version" "$w/clone1" --push >"$w/o" 2>"$w/e" || echo "--push exited non-zero: $(head -n 3 "$w/e")"
  (cd "$r" && same "v$version" "$root" && same main "$root" && [ "$(git rev-parse "v$version^{commit}")" = "$(git rev-parse main)" ]) \
    || echo "after --push the remote's main and tag v$version do not hold the three files"
  au=$(git -C "$r" log -1 --format='%an <%ae>' main 2>/dev/null || true)
  [ "$au" = "Nicola <45848139+nicolaguglielmi@users.noreply.github.com>" ] || echo "the commit's author is '$au', not the owner's noreply identity"
  ! git -C "$r" log -1 --format=%B main 2>/dev/null | grep -qi 'co-authored-by' || echo "the commit carries a co-author trailer"
  after=$(git -C "$r" rev-parse main)
  ACTION_REMOTE=$r sh "$s" test-proof "$version" "$w/clone2" --push >"$w/o" 2>"$w/e" || echo "--push again, on the same files, exited non-zero: $(head -n 3 "$w/e")"
  [ "$(git -C "$r" rev-parse main)" = "$after" ] || echo "--push again moved the remote's main"
  # a remote whose tag holds other files
  r2=$w/r2.git
  remote "$r2"
  git -C "$r2" tag "v$version" main
  t2=$(git -C "$r2" rev-parse "v$version")
  if ACTION_REMOTE=$r2 sh "$s" test-proof "$version" "$w/clone3" --push >"$w/o" 2>"$w/e"; then echo "a remote tag holding other files was not refused"; fi
  [ "$(git -C "$r2" rev-parse "v$version")" = "$t2" ] || echo "a remote tag holding other files was moved"
  # a version the shim does not use
  r3=$w/r3.git
  remote "$r3"
  b3=$(git -C "$r3" rev-parse main)
  if ACTION_REMOTE=$r3 sh "$s" test-proof 9.9.9 "$w/clone4" --push >"$w/o" 2>"$w/e"; then echo "a version the shim does not use was not refused"; fi
  [ "$(git -C "$r3" rev-parse main)" = "$b3" ] && ! git -C "$r3" rev-parse -q --verify refs/tags/v9.9.9 >/dev/null || echo "a version the shim does not use changed the remote"
  # --push to https with no token: refused before any network
  if ACTION_REMOTE=https://example.invalid/x.git sh "$s" test-proof "$version" "$w/clone5" --push >"$w/o" 2>"$w/e"; then
    echo "--push to an https remote with no ACTION_REPOS_TOKEN was not refused"
  elif ! grep -q ACTION_REPOS_TOKEN "$w/e"; then echo "--push with no token: the refusal does not name ACTION_REPOS_TOKEN"; fi
  :
}

# --- notes ----------------------------------------------------------------------
DG=sha256:$(printf '%064d' 1 | tr 0 a)
DF=sha256:$(printf '%064d' 2 | tr 0 b)
RX="--certificate-identity-regexp '^https://github.com/nicolaguglielmi/lights-out/.github/workflows/package.yml@refs/tags/v'"
IS="--certificate-oidc-issuer https://token.actions.githubusercontent.com"
# notes_flaws SCRIPT: package/release-notes.sh's contract, one line per flaw.
notes_flaws() {
  s=$1
  rm -rf "$w/n" && mkdir -p "$w/n/docs/releases"
  (cd "$w/n" && sh "$s" lights-out 7.8.9 "$DG" "$DF") >"$w/o" 2>"$w/e" || { echo "lights-out notes exited non-zero: $(head -n 2 "$w/e")"; return 0; }
  for x in "ghcr.io/nicolaguglielmi/lights-out-gates:7.8.9" "ghcr.io/nicolaguglielmi/lights-out:7.8.9" \
    "ghcr.io/nicolaguglielmi/lights-out-gates@$DG" "ghcr.io/nicolaguglielmi/lights-out@$DF" \
    "cosign verify ghcr.io/nicolaguglielmi/lights-out-gates@$DG" "cosign verify ghcr.io/nicolaguglielmi/lights-out@$DF" \
    "lights-out-gates 7.8.9" "https://pypi.org/project/lights-out-gates/7.8.9/" \
    "nicolaguglielmi/test-proof@v7.8.9" "nicolaguglielmi/test-integrity@v7.8.9"; do
    grep -qF -- "$x" "$w/o" || echo "the notes lack '$x'"
  done
  [ "$(grep -cF -- "$RX" "$w/o")" -ge 2 ] && [ "$(grep -cF -- "$IS" "$w/o")" -ge 2 ] \
    || echo "the notes lack, for each image, the identity regexp of ADR 0057 §4 and GitHub's issuer"
  echo "PROSE-7.8.9 what changed" > "$w/n/docs/releases/v7.8.9.md"
  (cd "$w/n" && sh "$s" lights-out 7.8.9 "$DG" "$DF") >"$w/o" 2>"$w/e" || true
  [ "$(head -n 1 "$w/o")" = "PROSE-7.8.9 what changed" ] || echo "docs/releases/v7.8.9.md does not come first"
  for bad in "sha256:abc" "$DG
x"; do
    if (cd "$w/n" && sh "$s" lights-out 7.8.9 "$bad" "$DF") >"$w/o" 2>"$w/e" || [ -s "$w/o" ]; then echo "a malformed digest was not refused with nothing on stdout"; fi
  done
  (cd "$w/n" && sh "$s" action test-proof 7.8.9) >"$w/o" 2>"$w/e" || echo "action notes exited non-zero: $(head -n 2 "$w/e")"
  grep -qF "uses: nicolaguglielmi/test-proof@v7.8.9" "$w/o" && grep -qF "https://github.com/nicolaguglielmi/lights-out/releases/tag/v7.8.9" "$w/o" \
    || echo "the action notes lack the uses line at v7.8.9 or the link to lights-out 7.8.9's release"
  :
}

judge() { [ -z "$2" ] || nogo "$1: $(printf '%s\n' "$2" | head -n 1)" "$2"; }
judge workflow "$(python3 -I "$w/wf.py" .github/workflows/package.yml check)"
judge sync "$(sync_flaws "$root/package/sync-action.sh")"
judge notes "$(notes_flaws "$root/package/release-notes.sh")"

# --- the checks must be able to fail ---------------------------------------------
out=$(python3 -I "$w/wf.py" .github/workflows/package.yml prove)
[ -z "$out" ] || nogo "workflow: $out"
# planted FILE JUDGE WANT FROM TO: JUDGE on a copy of FILE with FROM replaced by TO must name WANT.
planted() {
  cp "$1" "$w/planted.sh"
  python3 -I - "$w/planted.sh" "$4" "$5" <<'PY' || nogo "$(basename "$1"): '$4' is not in the script, so the check cannot fail on it"
import sys
p, a, b = sys.argv[1:]
s = open(p).read()
sys.exit(1) if a not in s else open(p, "w").write(s.replace(a, b, 1))
PY
  got=$($2 "$w/planted.sh")
  case "$got" in *"$3"*) ;; *) nogo "$(basename "$1"): it cannot fail on '$4' -> '$5': [$(printf '%s' "$got" | head -n 1)]" ;; esac
}
planted package/sync-action.sh sync_flaws "a dry run changed the remote" 'if [ "$push" = 1 ]; then' 'if true; then'
planted package/sync-action.sh sync_flaws "holding other files was not refused" 'if same "$tag"; then' 'if true; then'
planted package/sync-action.sh sync_flaws "does not use was not refused" 'exit 1 # the shim is not at this version' ': # the shim is not at this version'
planted package/sync-action.sh sync_flaws "not the owner's noreply identity" '45848139+nicolaguglielmi@users.noreply.github.com' 'someone@example.com'
planted package/sync-action.sh sync_flaws "a dry run" 'cp "$here/LICENSE" "$dir/"' ':'
planted package/sync-action.sh sync_flaws "ACTION_REPOS_TOKEN" 'if [ "$push" = 1 ] && [ -z "${ACTION_REPOS_TOKEN:-}" ]; then' 'if false; then'
planted package/release-notes.sh notes_flaws "a malformed digest" 'exit 1 # a malformed digest' ': # a malformed digest'
planted package/release-notes.sh notes_flaws "identity regexp" '@refs/tags/v' '@refs/heads/'
planted package/release-notes.sh notes_flaws "does not come first" 'cat "$prose"' ':'
planted package/release-notes.sh notes_flaws "nicolaguglielmi/test-integrity@v7.8.9" 'test-proof test-integrity' 'test-proof'

go_with "package.yml's release jobs, sync-action.sh and release-notes.sh as ADR 0075 says, each check shown to fail"
