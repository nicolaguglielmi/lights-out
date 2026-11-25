"""Cost and time per ticket, and the budget (docs/adr/0083): stdlib only.

    python3 core/spend.py report     cost and time per ticket, role and day,
                                     from every ticket's `spend` ledger, and
                                     the burn and the day's projection
                                     (docs/adr/0087 §3)
    python3 core/spend.py token-budget ROLE_ENV
                                     the role's TOKEN_BUDGET for MODEL_PROVIDER
                                     (docs/adr/0092 §7)

The tower imports the rest: the price of a run (`cost`), the ledger
entries a step's result gives (`entries`), the limits of policy/rework.env
(`limits`) and the stop line (`stop`). core/spend.sh is the command's
entry point. A model with no price is "cost unknown", never a guess.
"""

import glob
import json
import os
import re
import subprocess  # nosec B404 - the state store's adapter verb is a script
import sys
from datetime import datetime, timedelta

PRICES = "profiles/prices.json"
POLICY = "policy/rework.env"
LIMIT_KEYS = ("SPEND_BUDGET_TICKET_USD", "SPEND_BUDGET_DAY_USD")
# The step result keys that carry a role's run (§3), and the role of one
# whose object does not name it.
# The architect's run of an intake's perimeter step counts as product's does (docs/adr/0090 §7).
# The screen before a role reads a text is its own line, role `screen` (docs/adr/0091 §9).
RUN_FIELDS = (("task", None), ("review", None), ("product", "product"), ("architect", "architect"),
              ("screen", "screen"))
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DECIMAL = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


# --- prices ---------------------------------------------------------------


def parse_prices(table):
    """The price entries of a table object; ValueError naming what is wrong."""
    entries = table.get("prices") if isinstance(table, dict) else None
    if not isinstance(entries, list):
        raise ValueError("no `prices` list")
    out = []
    for n, e in enumerate(entries, 1):
        if not isinstance(e, dict):
            raise ValueError(f"price {n} is not an object")
        for key in ("provider", "model"):
            if not isinstance(e.get(key), str) or not e[key]:
                raise ValueError(f"price {n} has no {key}")
        for key in ("input", "output"):
            if not isinstance(e.get(key), (int, float)) or isinstance(e.get(key), bool) or e[key] < 0:
                raise ValueError(f"price {n} ({e['model']}) has no {key} price of 0 or more")
        if "cached_input" in e and (not isinstance(e["cached_input"], (int, float))
                                    or isinstance(e["cached_input"], bool) or e["cached_input"] < 0):
            raise ValueError(f"price {n} ({e['model']}) has a cached_input that is not a price of 0 or more")
        for key in ("from", "until"):
            if e.get(key) is not None and not (isinstance(e[key], str) and _DAY.match(e[key])):
                raise ValueError(f"price {n} ({e['model']}) has a {key} that is not YYYY-MM-DD")
        out.append(e)
    return out


def load_prices(path=PRICES):
    """(entries, None), or (None, why) when the table cannot be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            return parse_prices(json.load(fh)), None
    except OSError as exc:
        return None, f"no price table {path} ({exc.strerror})"
    except ValueError as exc:
        return None, f"the price table {path} is malformed: {exc}"


def price(prices, provider, model, day):
    for e in prices:
        if e["provider"] == provider and e["model"] == model \
                and (e.get("from") or "") <= day <= (e.get("until") or "9999-12-31"):
            return e
    return None


def _member_run(m):
    """Normalize either a flattened tower member or a raw `{id, run}` network member."""
    if not isinstance(m, dict):
        return {}
    inner = m.get("run")
    if isinstance(inner, dict):
        merged = dict(inner)
        for key, value in m.items():
            if key != "run" and (key not in merged or merged[key] is None):
                merged[key] = value
        if "replayed" not in merged:
            merged["replayed"] = merged.get("harness") == "replay"
        return merged
    return m


def _empty_failed_member(m):
    """True when a failed or uninitialized network sub-worker crashed before recording model usage."""
    tokens = m.get("tokens")
    has_tokens = isinstance(tokens, dict) and any(
        isinstance(tokens.get(k), int) and not isinstance(tokens.get(k), bool) for k in ("input", "output")
    )
    empty_usage = not has_tokens and not m.get("model") and not m.get("provider")
    return empty_usage and (m.get("failed") is True or m.get("harness") == "unknown")


def cost(run, prices, day):
    """(usd, None) for a run on DAY (YYYY-MM-DD), or (None, why)."""
    if run.get("replayed") is True:
        return 0, None
    if isinstance(run.get("members"), list):
        total = 0
        for raw_member in run["members"]:
            member = _member_run(raw_member)
            if _empty_failed_member(member):
                continue
            usd, why = cost(member, prices, day)
            if usd is None:
                return None, f"member {member.get('id') or '?'}: {why}"
            total += usd
        return round(total, 6), None
    provider, model = run.get("provider"), run.get("model")
    if not provider or not model:
        return None, "the run names no provider or model"
    tokens = run.get("tokens") if isinstance(run.get("tokens"), dict) else {}
    tin, tout = tokens.get("input"), tokens.get("output")
    if not all(isinstance(t, int) and not isinstance(t, bool) for t in (tin, tout)):
        return None, "the run reports no input and output tokens"
    if prices is None:
        return None, "no price table"
    p = price(prices, provider, model, day)
    if p is None:
        return None, f"no price for {provider}/{model} on {day} in {PRICES}"
    cached = _cached(tokens, p)
    if cached is None:
        return round(tin * p["input"] / 1e6 + tout * p["output"] / 1e6, 6), None
    return round((tin - cached) * p["input"] / 1e6 + cached * p["cached_input"] / 1e6
                 + tout * p["output"] / 1e6, 6), None


def _cached(tokens, p):
    """The cached input tokens to price at p's cached rate (docs/adr/0092 §6):
    a whole number from 0 to input, and a price that has one; else None."""
    tin, cached = tokens.get("input"), tokens.get("cached")
    if "cached_input" not in p or isinstance(cached, bool) or not isinstance(cached, int):
        return None
    return cached if 0 <= cached <= tin else None


def cost_basis(run, prices, day):
    """`cached` when the run's cost priced its cached input at the cached
    rate, `uncached` when it did not, None when the cost is unknown
    (docs/adr/0092 §6). A network is `cached` only when every member is."""
    usd, _ = cost(run, prices, day)
    if usd is None:
        return None
    if run.get("replayed") is True:
        return "uncached"
    if isinstance(run.get("members"), list):
        got = {
            cost_basis(_member_run(m), prices, day)
            for m in run["members"]
            if not _empty_failed_member(_member_run(m))
        }
        return "cached" if got == {"cached"} else "uncached"
    p = price(prices, run.get("provider"), run.get("model"), day)
    return "cached" if _cached(run["tokens"], p) is not None else "uncached"


# --- the ledger -----------------------------------------------------------


def entries(step, result, attempt, at, path=PRICES):
    """The ledger entries for the runs a step's RESULT carries (§3)."""
    out, prices, unread = [], None, None
    for key, role in RUN_FIELDS:
        run = result.get(key) if isinstance(result, dict) else None
        if not isinstance(run, dict) or "harness" not in run:
            continue
        if key in ("product", "architect") and run.get("consulted") is False:
            continue
        if prices is None and unread is None:
            prices, unread = load_prices(path)
        usd, why = cost(run, prices, at[:10])
        basis = cost_basis(run, prices, at[:10]) if usd is not None else None
        if usd is None and unread and why == "no price table":
            why = unread
        out.append({"step": step, "attempt": attempt, "role": run.get("role") or role or key,
                    "harness": run.get("harness"), "provider": run.get("provider"), "model": run.get("model"),
                    "replayed": run.get("replayed") is True, "tokens": run.get("tokens"),
                    "duration_s": run.get("duration_s"), "cost_usd": usd, "cost_unknown": why, "cost_basis": basis, "at": at})
    return out


# --- the token budget of a run (docs/adr/0092 §7) ------------------------


def token_budget(keys, provider):
    """A role's TOKEN_BUDGET for PROVIDER: its TOKEN_BUDGET_<PROVIDER> when the
    role sets one, else its TOKEN_BUDGET; None when neither is a whole number."""
    def whole(value):
        return int(value) if isinstance(value, str) and value.isdigit() else None
    if provider and provider.isalnum():
        mine = whole(keys.get(f"TOKEN_BUDGET_{provider.upper()}"))
        if mine is not None:
            return mine
    return whole(keys.get("TOKEN_BUDGET"))


def role_keys(path):
    """The KEY=value lines of a role.env, comments and blanks skipped."""
    with open(path, encoding="utf-8") as fh:
        return {m.group(1): m.group(2).strip() for m in (_KEY_LINE.match(line.strip()) for line in fh) if m}


# --- the budget -----------------------------------------------------------


def parse_limits(text):
    """{key: float or None}; ValueError naming the key for a malformed value."""
    got = {}
    for raw in text.splitlines():
        m = _KEY_LINE.match(raw.strip())
        if m and m.group(1) in LIMIT_KEYS:
            got[m.group(1)] = m.group(2).strip()
    out = {}
    for key in LIMIT_KEYS:
        value = got.get(key, "")
        if value == "":
            out[key] = None
        elif not _DECIMAL.match(value) or float(value) <= 0:
            raise ValueError(f"{POLICY}: {key}={value!r} is neither empty nor a positive decimal number of USD")
        else:
            out[key] = float(value)
    return out


def limits(path=POLICY):
    """The limits of PATH; a missing file has none."""
    try:
        with open(path, encoding="utf-8") as fh:
            return parse_limits(fh.read())
    except OSError:
        return {key: None for key in LIMIT_KEYS}


def ledger(doc):
    return [e for e in (doc.get("spend") or []) if isinstance(e, dict)] if isinstance(doc, dict) else []


def release_ledger(releases_log=None, prices_path=PRICES):
    """Ledger entries extracted from `brief_run` objects in `.crew/releases.jsonl` (ADR 0135)."""
    path = releases_log or os.environ.get("RELEASES_LOG") or ".crew/releases.jsonl"
    if not path or not os.path.isfile(path):
        return []
    out = []
    prices, unread = None, None
    try:
        with open(path, encoding="utf-8") as fh:
            raw_lines = fh.read().splitlines()
    except OSError:
        return []
    for raw in raw_lines:
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(line, dict):
            continue
        brief_run = line.get("brief_run")
        if not isinstance(brief_run, dict):
            continue
        at = str(brief_run.get("at") or line.get("at") or "")
        if prices is None and unread is None:
            prices, unread = load_prices(prices_path)
        run_norm = dict(brief_run)
        if "replayed" not in run_norm:
            run_norm["replayed"] = run_norm.get("harness") == "replay"
        day = at[:10] if len(at) >= 10 else ""
        usd, why = cost(run_norm, prices, day)
        basis = cost_basis(run_norm, prices, day) if usd is not None else None
        if usd is None and unread and why == "no price table":
            why = unread
        record = line.get("record") if isinstance(line.get("record"), dict) else {}
        entry = {
            "step": "promote",
            "attempt": None,
            "role": run_norm.get("role") or "release",
            "harness": run_norm.get("harness"),
            "provider": run_norm.get("provider"),
            "model": run_norm.get("model"),
            "replayed": run_norm.get("replayed") is True,
            "tokens": run_norm.get("tokens"),
            "duration_s": run_norm.get("duration_s"),
            "cost_usd": usd,
            "cost_unknown": why,
            "cost_basis": basis,
            "at": at,
            "ticket": line.get("ticket"),
            "version": record.get("version") or line.get("version"),
        }
        if run_norm.get("failed"):
            entry["failed"] = run_norm.get("stop") or "the run failed"
        out.append(entry)
    return out


def on_day(docs, day, release_runs=None):
    """Every ledger entry of DOCS ({id: doc}) and optional release_runs dated DAY (UTC)."""
    items = [e for doc in docs.values() for e in ledger(doc) if str(e.get("at") or "")[:10] == day]
    if release_runs:
        items.extend(e for e in release_runs if str(e.get("at") or "")[:10] == day)
    return items


def stop(mine, today, lim):
    """The one-line reason the next attempt is not launched, or ""."""
    for key, counted, what in (("SPEND_BUDGET_TICKET_USD", mine, "this ticket"),
                               ("SPEND_BUDGET_DAY_USD", today, "today")):
        limit = lim.get(key)
        if limit is None:
            continue
        unknown = [e for e in counted if e.get("cost_usd") is None]
        if unknown:
            e = unknown[0]
            return (f"spend budget: {key}=${limit:.2f} cannot be checked, {len(unknown)} run(s) of {what} "
                    f"of cost unknown ({e.get('role')}, {e.get('step')}: {e.get('cost_unknown')}); "
                    "the next attempt is not launched")
        spent = sum(e["cost_usd"] for e in counted)
        if spent >= limit:
            return (f"spend budget: {what} spent ${spent:.2f}, at or over {key}=${limit:.2f}; "
                    "the next attempt is not launched")
    return ""


# --- the report -----------------------------------------------------------


def when(at):
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None


def add(row, e):
    row["runs"] += 1
    if e.get("cost_usd") is None:
        row["unknown"] += 1
    else:
        row["cost_usd"] = round(row["cost_usd"] + e["cost_usd"], 6)
    # A replayed run reports the recorded run's duration, not its own (§5).
    if "agent_s" in row and e.get("replayed") is not True and isinstance(e.get("duration_s"), (int, float)):
        row["agent_s"] += e["duration_s"]


def _record_spend_entry(out, e, *, ticket_row=None):
    if ticket_row is not None:
        add(ticket_row, e)
    add(out["roles"].setdefault(e.get("role") or "unknown",
                                {"cost_usd": 0, "runs": 0, "unknown": 0, "agent_s": 0}), e)
    add(out["days"].setdefault(str(e.get("at") or "")[:10] or "unknown",
                               {"cost_usd": 0, "runs": 0, "unknown": 0}), e)
    add(out["total"], e)


def report(docs, release_runs=None):
    """{tickets, roles, days, total} from DOCS ({id: doc}) and optional release_runs (§5, ADR 0135)."""
    out = {"tickets": {}, "roles": {}, "days": {}, "total": {"cost_usd": 0, "runs": 0, "unknown": 0}}
    for tid, doc in docs.items():
        history = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
        first, last = (when(history[0].get("at")), when(history[-1].get("at"))) if history else (None, None)
        row = {"cost_usd": 0, "runs": 0, "unknown": 0, "agent_s": 0,
               "elapsed_s": round((last - first).total_seconds()) if first and last else None}
        for e in ledger(doc):
            _record_spend_entry(out, e, ticket_row=row)
        out["tickets"][tid] = row
    for e in release_runs or ():
        tid = e.get("ticket")
        ticket_row = out["tickets"].get(tid) if isinstance(tid, str) else None
        _record_spend_entry(out, e, ticket_row=ticket_row)
    return out


def hms(seconds):
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m{seconds % 60:02d}s"


def money(row):
    tail = f", {row['unknown']} of cost unknown" if row["unknown"] else ""
    return f"${row['cost_usd']:.2f} over {row['runs']} run(s){tail}"


def budget_line(lim):
    """The limits as one line; LIM is limits()'s dict, or the error of a malformed value."""
    if isinstance(lim, str):
        return f"budget: {lim}"
    return "budget: " + ", ".join(f"{key}=${lim[key]:.2f}" if lim.get(key) is not None else f"{key} no limit"
                                  for key in LIMIT_KEYS)


def lines(got, lim, b=None):
    """The words of ./go spend, without the [spend] prefix (amendment 2026-11-13, §2-§3): per
    ticket, per role, per day, the burn and the projection (docs/adr/0087 §3, B is burn()'s),
    then the budget. The logbook carries them for the console."""
    out = [f"ticket {tid}: {money(row)}; agents {hms(row['agent_s'])}, elapsed {hms(row['elapsed_s'])}"
           for tid, row in got["tickets"].items()]
    out += [f"role {role}: {money(row)}; {hms(row['agent_s'])}" for role, row in sorted(got["roles"].items())]
    out += [f"day {day}: {money(row)}" for day, row in sorted(got["days"].items())]
    return out + burn_lines(b, lim) + [budget_line(lim)]


# --- the burn and the day's projection (docs/adr/0087 §3) ----------------

# Provenance, decision: docs/adr/0087 §3: a rate over less than an hour is one
# run's cost spread over its own minutes (M24 phase D: tenacity-03's first run,
# $1.39 in 495 s, would read as $10/h); one hour is the bucket the panel draws.
MIN_HOURS = 1.0


def burn(docs, lim, release_runs=None):
    """The known cost per hour of the last day that has a run, and the day's
    spend at that rate until 24:00 UTC, as of its last run: no clock is read.
    None when no ledger has an entry. LIM is limits()'s dict, or the error of
    a malformed value (then the day has no limit to compare)."""
    raw_entries = [e for doc in docs.values() for e in ledger(doc)] + list(release_runs or ())
    entries = [(when(e.get("at")), e) for e in raw_entries]
    entries = [(t, e) for t, e in entries if t is not None]
    if not entries:
        return None
    end = max(t for t, _ in entries)
    day = end.date()
    today = [(t, e) for t, e in entries if t.date() == day]
    first = min(t for t, _ in today)
    hours = max(MIN_HOURS, (end - first).total_seconds() / 3600)
    spent = round(sum(e["cost_usd"] for _, e in today if e.get("cost_usd") is not None), 6)
    per_hour = spent / hours
    midnight = datetime(day.year, day.month, day.day, tzinfo=end.tzinfo) + timedelta(days=1)
    left = (midnight - end).total_seconds() / 3600
    limit = lim.get("SPEND_BUDGET_DAY_USD") if isinstance(lim, dict) else None
    projected = round(spent + per_hour * left, 6)
    hourly = [0] * 24
    for t, e in today:
        if e.get("cost_usd") is not None:
            hourly[t.hour] = round(hourly[t.hour] + e["cost_usd"], 6)
    stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    return {"day": day.isoformat(), "first": stamp(first), "end": stamp(end), "runs": len(today),
            "unknown": sum(1 for _, e in today if e.get("cost_usd") is None), "spent_usd": spent,
            "hours": round(hours, 6), "per_hour_usd": round(per_hour, 6), "left_h": round(left, 6),
            "projected_usd": projected, "limit_usd": limit, "over": None if limit is None else projected >= limit,
            "hourly": hourly}


def burn_lines(b, lim):
    """The two lines of §3, in the words ./go spend prints."""
    if b is None:
        return ["burn: no runs in the ledger", "projection: none without a run"]
    unknown = f", {b['unknown']} of cost unknown" if b["unknown"] else ""
    hm = lambda at: at[11:16]  # noqa: E731
    if isinstance(lim, str):
        against = "the day's limit cannot be read"
    elif b["limit_usd"] is None:
        against = "SPEND_BUDGET_DAY_USD no limit"
    else:
        against = f"SPEND_BUDGET_DAY_USD=${b['limit_usd']:.2f}: {'over' if b['over'] else 'under'}"
    return [f"burn: ${b['per_hour_usd']:.2f} per hour on {b['day']} (${b['spent_usd']:.2f} known in {b['runs']} run(s) "
            f"from {hm(b['first'])} to {hm(b['end'])} UTC, over {b['hours']:.1f} h{unknown})",
            f"projection: ${b['projected_usd']:.2f} by the end of {b['day']} UTC at that rate, as of {hm(b['end'])} UTC; {against}"]


def summary(got):
    t = got["total"]
    return f"{len(got['tickets'])} tickets, {t['runs']} runs, ${t['cost_usd']:.2f} known, {t['unknown']} of cost unknown"


def store_docs(store, tickets_dir):
    """{id: doc} for every ticket of TICKETS_DIR the store holds; ValueError on a store error."""
    docs = {}
    for path in sorted(glob.glob(os.path.join(tickets_dir, "*.md"))):
        tid = os.path.basename(path)[:-3].removesuffix(".intake")  # an intake's runs count (docs/adr/0090 §7)
        got = subprocess.run(["sh", f"adapters/{store}/get.sh", "tickets", tid], capture_output=True,
                             text=True, check=False)  # nosec B603 B607 - an argv list, the store's verb
        if got.returncode == 1:
            continue  # the tower never opened this ticket
        if got.returncode != 0:
            raise ValueError(f"adapters/{store}/get.sh tickets {tid} exited {got.returncode}: "
                             f"{got.stderr.strip().splitlines()[-1] if got.stderr.strip() else 'no reason'}")
        try:
            doc = json.loads(got.stdout)
        except ValueError:
            raise ValueError(f"adapters/{store}/get.sh printed no JSON for {tid}") from None
        if isinstance(doc, dict):
            docs[tid] = doc
    return docs


def main(argv, env=None):
    env = os.environ if env is None else env
    if len(argv) == 2 and argv[0] == "token-budget":
        # core/crew.sh and core/steps/review.sh: the run's TOKEN_BUDGET on stdout.
        try:
            budget = token_budget(role_keys(argv[1]), env.get("MODEL_PROVIDER", ""))
        except (OSError, UnicodeDecodeError) as exc:
            print(f"spend: {argv[1]}: {exc}", file=sys.stderr)
            return 1
        if budget is None:
            print(f"spend: {argv[1]} sets no whole-number TOKEN_BUDGET", file=sys.stderr)
            return 1
        print(budget)
        return 0
    if argv != ["report"]:
        print("usage: spend.py report | token-budget <role.env>", file=sys.stderr)
        return 2
    store = env.get("STATE_STORE")
    if not store:
        print("STATE_STORE not set; run this via ./go spend")
        return 1
    try:
        docs = store_docs(store, env.get("TICKETS_DIR") or "tickets")
    except ValueError as exc:
        print(exc)
        return 1
    rel_runs = release_ledger(env.get("RELEASES_LOG") or ".crew/releases.jsonl")
    got = report(docs, release_runs=rel_runs)
    try:
        lim = limits()
    except ValueError as exc:
        lim = str(exc)
    for line in lines(got, lim, burn(docs, lim, release_runs=rel_runs)):
        print(f"[spend] {line}", file=sys.stderr)
    print(summary(got))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
