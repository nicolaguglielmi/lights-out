# SPDX-License-Identifier: Apache-2.0
"""gates/production-readiness.sh's judgement (docs/adr/0057 §10).

Whether this setup may take real traffic. GO only when all four hold;
otherwise one NO-GO naming every item that fails, `item (why)` joined by
", ", and each item's line on stderr, `production-readiness: <item> FAIL,
<why>` or `... ok, <what>`:

  delivery    DELIVERY's adapter has a rollback.sh, and a conformance record
              ($CONFORMANCE_DIR/delivery-<adapter>.env, written by the
              delivery suite on GO, docs/adr/0057 §9) whose HASH is the
              adapter's hash today: changed since it passed is not passed
  metrics     METRICS=promql (with METRICS_URL) or METRICS=managed-prometheus
              (with METRICS_URL or GCP_PROJECT), and that endpoint answers
              the derived readiness query from active slo.promql with >= 1 sample
  secrets     the env file holds no value, other than empty or replace-me,
              for a key ending in _KEY, _TOKEN, _SECRET or _PASSWORD, or for
              METRICS_AUTH_HEADER
  human gate  GO_FOR_LAUNCH is not preset, in the environment or the env
              file, and the console names who presses: CONSOLE_AUTH=proxy,
              CONSOLE_IDENTITY_HEADER and a CONSOLE_LAUNCHERS that is more
              than commas and spaces (docs/adr/0047)

READINESS_ENV_FILE (default .env) and CONFORMANCE_DIR (default
.crew/conformance) name what is read. `adapter_hash` is the record's digest,
shared with the delivery suite that writes it, and equal to ci/lib.sh's
function of the same name (core/tests/test_gates_readiness.py holds them
equal).
"""

import hashlib
import json
import math
import os
import re

from .. import oidc_rbac
from . import NoGo, main, proc, say

SECRET = re.compile(r"^([A-Za-z0-9_]*(_KEY|_TOKEN|_SECRET|_PASSWORD)|METRICS_AUTH_HEADER)=")


def adapter_hash(directory):
    """sha256 over `sha256sum` lines of every file under DIRECTORY ("./rel"
    paths, byte order, __pycache__ left out), as ci/lib.sh's adapter_hash."""
    paths = []
    for top, _dirs, files in os.walk(directory):
        for f in files:
            full = os.path.join(top, f)
            rel = os.path.relpath(full, directory)
            if os.path.islink(full) or "/__pycache__/" in "/" + rel.replace(os.sep, "/"):
                continue
            paths.append("./" + rel.replace(os.sep, "/"))
    paths.sort(key=lambda p: p.encode())
    lines = []
    for p in paths:
        with open(os.path.join(directory, p[2:]), "rb") as fh:
            lines.append(f"{hashlib.sha256(fh.read()).hexdigest()}  {p}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def env_values(path, key):
    """Every value of KEY= in the file at PATH, in order ([] when absent)."""
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [ln.rstrip("\n")[len(key) + 1:] for ln in fh if ln.startswith(key + "=")]


def query_promql(expr, adapter="promql", env=None):
    """The metrics adapter's answer to EXPR, or None when it fails."""
    run_env = os.environ if env is None else env
    got = proc(["sh", f"adapters/{adapter}/query.sh", expr], env=run_env, capture_output=True, text=True)
    return got.stdout if got.returncode == 0 else None


def samples(answer):
    """The sum of an instant query's sample values, 0 when it is not a count."""
    try:
        total = sum(float(r["value"][1]) for r in json.loads(answer)["data"]["result"])
    except (TypeError, ValueError, KeyError, IndexError):
        return 0
    return int(total) if math.isfinite(total) and total > 0 and total == int(total) else 0


def delivery(env, records):
    d = env.get("DELIVERY", "")
    if not d:
        return False, "DELIVERY is not set"
    if not os.path.isfile(f"adapters/{d}/rollback.sh"):
        return False, f"DELIVERY={d} has no adapters/{d}/rollback.sh, so no rollback"
    record = f"{records}/delivery-{d}.env"
    if not os.path.isfile(record):
        return False, f"{d} has no conformance record in {records}/: its suite has not passed here"
    at = "\n".join(env_values(record, "AT"))
    if "\n".join(env_values(record, "HASH")) != adapter_hash(f"adapters/{d}"):
        return False, f"adapters/{d} changed since its conformance passed at {at}; run its suite again"
    return True, f"delivery {d}, conformance passed {at}"


def _active_promql_path(env):
    slo = (env.get("SLO_PROMQL") or os.environ.get("SLO_PROMQL") or "").strip()
    if slo:
        return slo
    delivery = (env.get("DELIVERY") or os.environ.get("DELIVERY") or "").strip()
    if delivery:
        adapter_promql = os.path.join("adapters", delivery, "slo.promql")
        if os.path.isfile(adapter_promql):
            return adapter_promql
    local_promql = os.path.join("verify", "slo.promql")
    if os.path.isfile(local_promql):
        return local_promql
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "verify", "slo.promql")


def _readiness_query(env):
    path = _active_promql_path(env)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if re.search(r"\btarget_info\b", text):
        return "target_info", "count(target_info)"
    for block in text.split("\n\n"):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines and re.match(r"^#\s*name:\s*traffic\b", lines[0]):
            qtext = " ".join(lines[1:])
            m = re.search(r"\brate\s*\(\s*([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(?:\{[^}]*\})?\s*\[", qtext)
            if m:
                metric_name = m.group(1)
                return metric_name, f"count({metric_name})"
    raise ValueError(f"cannot derive readiness query from {path}")


def metrics(env, query):
    m = env.get("METRICS", "")
    if m not in ("promql", "managed-prometheus"):
        return False, f"METRICS={m} is not promql or managed-prometheus on an endpoint you name"
    url = (env.get("METRICS_URL") or "").strip()
    proj = (env.get("GCP_PROJECT") or env.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if m == "promql" and not url:
        return False, "METRICS_URL is not set"
    if m == "managed-prometheus" and not url and (not proj or proj == "replace-me"):
        return False, "METRICS_URL or GCP_PROJECT is not set"
    try:
        metric_name, expr = _readiness_query(env)
    except (OSError, ValueError) as exc:
        return False, str(exc)
    raw = query(expr, adapter=m, env=env) if query is query_promql else query(expr)
    n = samples(raw or "")
    source = "METRICS_URL" if url else f"managed-prometheus ({proj})"
    if n > 0:
        return True, f"metrics from {source}, {n} {metric_name} series"
    return False, f"{source} answers no {metric_name} series, or does not answer"


def secrets(envfile):
    leaked = []
    if os.path.isfile(envfile):
        with open(envfile, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.rstrip("\n")
                if SECRET.match(ln):
                    key, value = ln.split("=", 1)
                    if value and value != "replace-me":
                        leaked.append(key)
    if leaked:
        return False, f"{envfile} holds {' '.join(leaked)}; take them from the environment"
    return True, f"no secrets in {envfile}"


def human_gate(env, envfile):
    launchers = env.get("CONSOLE_LAUNCHERS", "")
    preset = env_values(envfile, "GO_FOR_LAUNCH")
    if env.get("GO_FOR_LAUNCH") or (preset and preset[-1]):
        return False, "GO_FOR_LAUNCH is preset, so no person answers"
    auth = env.get("CONSOLE_AUTH") or "loopback"
    if auth != "proxy":
        return False, f"CONSOLE_AUTH={auth}, not proxy: nobody named presses"
    verify_jwt = (env.get("CONSOLE_VERIFY_JWT") or "").strip()
    jwks_url = (env.get("CONSOLE_JWKS_URL") or "").strip()
    jwks_file = (env.get("CONSOLE_JWKS_FILE") or "").strip()
    jwt_secret = (env.get("CONSOLE_JWT_SECRET") or "").strip()
    rbac = (env.get("CONSOLE_RBAC") or "").strip()
    oidc_active = (
        verify_jwt.lower() in ("on", "1", "true", "yes")
        or bool(jwks_url)
        or bool(jwks_file)
        or rbac.lower() == "strict"
    )
    if not env.get("CONSOLE_IDENTITY_HEADER") and not (oidc_active and env.get("CONSOLE_JWT_HEADER")):
        return False, "CONSOLE_AUTH=proxy without CONSOLE_IDENTITY_HEADER"
    if not launchers.replace(" ", "").replace(",", ""):
        return False, "CONSOLE_LAUNCHERS names nobody"
    if verify_jwt and verify_jwt.lower() not in ("on", "off", "1", "0", "true", "false", "yes", "no"):
        return False, f"CONSOLE_VERIFY_JWT={verify_jwt} is not on or off"
    if rbac and rbac.lower() not in ("legacy", "strict"):
        return False, f"CONSOLE_RBAC={rbac} is not legacy or strict"
    if not oidc_active:
        return True, f"launchers {launchers}"
    if verify_jwt and verify_jwt.lower() in ("off", "0", "false", "no"):
        return False, "CONSOLE_VERIFY_JWT is off while OIDC JWKS or CONSOLE_RBAC=strict is active"
    issuer = (env.get("CONSOLE_JWT_ISSUER") or "").strip()
    if not issuer:
        return False, "CONSOLE_JWT_ISSUER is not set"
    audience = (env.get("CONSOLE_JWT_AUDIENCE") or "").strip()
    if not audience:
        return False, "CONSOLE_JWT_AUDIENCE is not set"
    has_secret = bool(jwt_secret and jwt_secret != "replace-me")
    if not (jwks_url or jwks_file or has_secret):
        return False, "no JWT key source (set CONSOLE_JWKS_URL, CONSOLE_JWKS_FILE or CONSOLE_JWT_SECRET)"
    if jwks_url:
        ok, why = oidc_rbac.validate_jwks_url(jwks_url)
        if not ok:
            return False, f"unsafe CONSOLE_JWKS_URL ({why})"
    if env.get("CONSOLE_ROLE_MAP"):
        try:
            oidc_rbac.parse_role_map(env.get("CONSOLE_ROLE_MAP"))
        except oidc_rbac.RbacConfigError as exc:
            return False, str(exc)
    return True, f"launchers {launchers}, oidc issuer {issuer}"


def judge(argv, env=None, query=query_promql):
    env = os.environ if env is None else env
    envfile = env.get("READINESS_ENV_FILE") or ".env"
    records = env.get("CONFORMANCE_DIR") or ".crew/conformance"
    results = [
        ("delivery", delivery(env, records)),
        ("metrics", metrics(env, query)),
        ("secrets", secrets(envfile)),
        ("human gate", human_gate(env, envfile)),
    ]
    failed, said = [], []
    for item, (ok, why) in results:
        say(f"production-readiness: {item:<10} {'ok' if ok else 'FAIL'}, {why}")
        (said if ok else failed).append(why if ok else f"{item} ({why})")
    if failed:
        raise NoGo(", ".join(failed))
    return ", ".join(said)


if __name__ == "__main__":
    raise SystemExit(main(judge))
