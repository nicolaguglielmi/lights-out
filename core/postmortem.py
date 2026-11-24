"""The checks and the rendering of ./go postmortem (docs/adr/0044 §3, §5):
stdlib only, vendor-free. core/postmortem.sh calls three subcommands and
turns a failure into its one NO-GO line.

    python3 core/postmortem.py validate <incident.json>
    python3 core/postmortem.py check    <incident.json> <calls.json> <checked.json>
    python3 core/postmortem.py render   <checked.json> <tickets.json> <run.json> <dir>

A failure is exit 1 with the reason alone on stdout. `check` is where the
anchor rule lives: an anchor is real when the incident document holds
it, and core prints that evidence under the model's sentence.
"""

import json
import re
import sys

NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA = re.compile(r"^[0-9a-f]{40}$")
TRACE_HEX = re.compile(r"^[0-9a-f]{32}$")


class Fail(Exception):
    pass


def load(path: str, what: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise Fail(f"{what} is not JSON ({e})")


def validate(path: str) -> dict:
    try:
        doc = load(path, path)
    except Fail as e:
        raise Fail(f"{path} is not an incident document: {str(e).split(' is not JSON ', 1)[-1]}")

    def bad(what: str):
        raise Fail(f"{path} is not an incident document: {what}")

    if not isinstance(doc, dict):
        bad("it is not a JSON object")
    if doc.get("kind") != "incident":
        bad('"kind" is not "incident"')
    if not isinstance(doc.get("name"), str) or not NAME.match(doc["name"]):
        bad('"name" is not a slug (lowercase letters, digits, dashes)')
    window = doc.get("window")
    if not isinstance(window, dict) or not all(
        isinstance(window.get(k), str) and STAMP.match(window[k]) for k in ("from", "to")
    ):
        bad('"window" needs "from" and "to" as UTC times like 2026-11-04T09:30:00Z')
    if window["from"] > window["to"]:
        bad('"window" ends before it starts')
    release = doc.get("release")
    if not isinstance(release, dict) or not isinstance(release.get("version"), str) or not release["version"]:
        bad('"release" is not a release record with a version')
    if release.get("verdict") == "in-progress":
        raise Fail(f"release {release['version']} is in-progress")
    if release.get("verdict") not in ("held", "rolled-back"):
        bad('"release.verdict" is neither held nor rolled-back')
    if not isinstance(release.get("stages"), list):
        bad('"release.stages" is not a list')
    if not isinstance(doc.get("alerts"), list):
        bad('"alerts" is not a list')
    logs = doc.get("logs")
    if not isinstance(logs, list) or not all(isinstance(line, str) for line in logs):
        bad('"logs" is not a list of strings')
    chain = doc.get("chain")
    if chain is not None and not (
        isinstance(chain, dict) and all(chain.get(k) not in (None, "") for k in ("ticket", "pr", "merge"))
    ):
        bad('"chain" is neither null nor {ticket, pr, merge}')
    if "traces" in doc and doc["traces"] is not None and not isinstance(doc["traces"], list):
        bad('"traces" is not a list')
    return doc


def anchor_evidence(anchor: str, doc: dict) -> list[str]:
    """The evidence lines for an anchor the document holds; Fail otherwise."""
    release = doc["release"]
    chain = doc["chain"]
    version = release["version"]
    kind, sep, rest = anchor.partition(":")
    if not sep or not rest:
        raise Fail("is not a known anchor")
    if kind == "release":
        v, at, pct = rest.partition("@")
        if v != version:
            raise Fail("names no release in the incident")
        if not at:
            return [f"release {version}: {release['verdict']}, image {release.get('image') or 'none'}"]
        stage = next((s for s in release["stages"] if str(s.get("pct")) == pct), None)
        if stage is None:
            raise Fail(f"names no stage of {version} in the incident")
        return [f"stage {pct}%: {stage.get('verdict', '')}"] + [f"  {n}" for n in stage.get("numbers", [])]
    if kind in ("pr", "merge", "ticket"):
        key, label = {"pr": ("pr", "PR"), "merge": ("merge", "merge"), "ticket": ("ticket", "ticket")}[kind]
        if kind == "merge" and not SHA.match(rest):
            raise Fail("is not a known anchor")
        if chain is None or str(chain[key]) != rest:
            raise Fail(f"names no {label} in the incident")
        if kind == "pr":
            return [f"PR #{rest} of ticket {chain['ticket']}, merged as {chain['merge']}"]
        if kind == "merge":
            return [f"merge {rest}, PR #{chain['pr']} of ticket {chain['ticket']}"]
        return [f"ticket {rest}: PR #{chain['pr']}, merge {chain['merge']}"]
    if kind in ("log", "alert"):
        items = doc["logs"] if kind == "log" else doc["alerts"]
        label = "log line" if kind == "log" else "alert"
        if not rest.isdigit() or not 1 <= int(rest) <= len(items):
            raise Fail(f"names no {label} in the incident")
        item = items[int(rest) - 1]
        return [item if kind == "log" else str(item.get("headline", ""))]
    if kind == "trace":
        traces = doc.get("traces")
        if not isinstance(traces, list):
            traces = []
        if rest.isdigit():
            idx = int(rest)
            if not 1 <= idx <= len(traces):
                raise Fail("names no trace in the incident")
            entry = traces[idx - 1]
        elif TRACE_HEX.match(rest.lower()):
            wanted = rest.lower()
            entry = next(
                (
                    t
                    for t in traces
                    if (isinstance(t, dict) and str(t.get("trace_id", "")).lower() == wanted)
                    or (isinstance(t, str) and t.lower() == wanted)
                ),
                None,
            )
            if entry is None:
                raise Fail("names no trace in the incident")
        else:
            raise Fail("is not a known anchor")
        if isinstance(entry, dict):
            tid = str(entry.get("trace_id", ""))
            sid = entry.get("span_id")
            return [f"trace {tid} (span {sid})" if sid else f"trace {tid}"]
        return [f"trace {entry}"]
    raise Fail("is not a known anchor")


def check(path: str, calls_path: str) -> dict:
    doc = validate(path)
    calls = load(calls_path, calls_path)
    args = calls[0].get("args", {})
    version = doc["release"]["version"]
    chain = doc["chain"]

    timeline = args.get("timeline") or []
    out = []
    for n, entry in enumerate(timeline, 1):
        anchor = entry.get("anchor") if isinstance(entry, dict) else None
        if not isinstance(anchor, str) or not anchor:
            raise Fail(f'timeline entry {n}: anchor "{anchor or ""}" is not a known anchor')
        try:
            evidence = anchor_evidence(anchor, doc)
        except Fail as e:
            raise Fail(f'timeline entry {n}: anchor "{anchor}" {e}')
        out.append({"event": str(entry.get("event", "")), "anchor": anchor, "evidence": evidence})
    anchors = [e["anchor"] for e in out]
    if not any(a == f"release:{version}" or a.startswith(f"release:{version}@") for a in anchors):
        raise Fail(f"no timeline entry is anchored to release {version}")
    if chain is not None and f"pr:{chain['pr']}" not in anchors:
        raise Fail(f"the chain has PR #{chain['pr']} and no timeline entry is anchored to it")

    causes = args.get("causes") or []
    if not causes:
        raise Fail("a post mortem needs at least one contributing cause")
    for n, c in enumerate(causes, 1):
        for field in ("cause", "evidence"):
            if not isinstance(c, dict) or not str(c.get(field, "")).strip():
                raise Fail(f"cause {n} has no {field}")
    actions = args.get("actions") or []
    if not actions:
        raise Fail("a post mortem needs at least one action item")
    for n, a in enumerate(actions, 1):
        for field in ("title", "problem", "acceptance"):
            if not isinstance(a, dict) or not str(a.get(field, "")).strip():
                raise Fail(f"action {n} has no {field}")

    return {
        "incident": doc["name"],
        "title": str(args.get("title", "")).strip() or doc["name"],
        "window": doc["window"],
        "release": {k: doc["release"].get(k) for k in ("version", "stable", "verdict", "image")},
        "chain": chain,
        "timeline": out,
        "causes": [{"cause": c["cause"], "evidence": c["evidence"]} for c in causes],
        "actions": [
            {"n": n, "title": a["title"], "problem": a["problem"], "acceptance": a["acceptance"]}
            for n, a in enumerate(actions, 1)
        ],
    }


def model_line(run: dict) -> str:
    recorded = run.get("recorded")
    if run.get("source") and recorded:
        who = " ".join(x for x in (recorded.get("provider"), recorded.get("model")) if x) or "an unknown model"
        return f"replayed, recorded by {who}"
    who = " ".join(x for x in (run.get("provider"), run.get("model")) if x) or "an unknown model"
    return f"written by {who}"


def render(checked_path: str, tickets_path: str, run_path: str, out_dir: str) -> None:
    pm = load(checked_path, checked_path)
    tickets = load(tickets_path, tickets_path)
    run = load(run_path, run_path)
    for action in pm["actions"]:
        action["ticket"] = tickets[str(action["n"])]
        del action["problem"], action["acceptance"]  # they live in the ticket
    pm["run"] = run
    chain = pm["chain"]
    lines = [
        f"# Post mortem {pm['incident']}: {pm['title']}",
        "",
        f"- Window: {pm['window']['from']} to {pm['window']['to']}",
        f"- Release: {pm['release']['version']} ({pm['release']['verdict']}), stable {pm['release']['stable']}",
        "- Chain: "
        + (
            f"ticket {chain['ticket']}, PR #{chain['pr']}, merge {chain['merge']}; check it against the code host with ./go audit TICKET={chain['ticket']}"
            if chain
            else "none: not released by the tower"
        ),
        f"- Model: {model_line(run)}",
        "",
        "## Timeline",
        "",
    ]
    for e in pm["timeline"]:
        lines.append(f"- {e['event']} (`{e['anchor']}`)")
        lines += [f"  > {line}" for line in e["evidence"]]
    lines += ["", "## Contributing causes", ""]
    lines += [f"- {c['cause']} — {c['evidence']}" for c in pm["causes"]]
    lines += ["", "## Action items", ""]
    lines += [f"- {a['title']} — ticket {a['ticket']}" for a in pm["actions"]]
    with open(f"{out_dir}/postmortem.json", "w") as f:
        json.dump(pm, f, indent=2)
        f.write("\n")
    with open(f"{out_dir}/postmortem.md", "w") as f:
        f.write("\n".join(lines) + "\n")


def main(argv: list[str]) -> int:
    try:
        if argv[1] == "validate":
            validate(argv[2])
        elif argv[1] == "check":
            with open(argv[4], "w") as f:
                json.dump(check(argv[2], argv[3]), f)
        elif argv[1] == "render":
            render(*argv[2:6])
        else:
            raise Fail(f"unknown subcommand {argv[1]}")
    except Fail as e:
        print(e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
