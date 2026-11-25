"""Cross-provider jury reviewer selection and inspection (docs/adr/0130): stdlib only.

Selects an independent cross-vendor `(provider, model, harness)` for
`test-reviewer` and `architect` on medium/high-risk PRs when at least
`JURY_MIN_PROVIDERS` (2) configured provider keys exist in the environment,
inspecting boolean key presence only (never logging or returning secret values).

    python3 core/jury.py select-env [--author-provider P] [--author-harness H]
                                    [--risk R] [--role ROLE] [--ticket ID]
                                    [--run-json PATH] [--doc PATH]
                                    [--providers PATH]
    python3 core/jury.py inspect    [--ticket ID] [--run-json PATH]
                                    [--review-run-json PATH] [--doc PATH]
                                    [--risk R] [--providers PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

JURY_MIN_PROVIDERS = 2
JURY_DEFAULT_MODE = "auto"
MODES = ("off", "auto", "strict")
REVIEWER_ROLES = ("test-reviewer", "architect")
RISK_LEVELS = ("low", "medium", "high")
CROSS_PROVIDER_RISKS = ("medium", "high")
PLACEHOLDER_VALUES = frozenset({"", "replace-me", "changeme", "todo"})
DEFAULT_PROVIDERS_PATH = "profiles/providers.json"


class Refused(Exception):
    """Raised when strict jury policy or invalid configuration refuses a run."""


def load_providers(path: str = DEFAULT_PROVIDERS_PATH) -> dict:
    """Load the `providers` mapping from profiles/providers.json."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read providers metadata {path}: {exc}") from None
    providers = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(providers, dict) or not providers:
        raise Refused(f"{path} has no providers mapping")
    return providers


def _normalize_providers(providers_cfg) -> dict:
    if providers_cfg is None:
        return load_providers()
    if isinstance(providers_cfg, dict) and isinstance(providers_cfg.get("providers"), dict):
        providers = providers_cfg["providers"]
    elif isinstance(providers_cfg, dict):
        providers = providers_cfg
    else:
        raise Refused("providers_cfg must be a JSON object")
    if not providers:
        raise Refused("providers_cfg has no providers")
    return providers


def _is_env_present(env: dict, var_name: str) -> bool:
    """Boolean presence check for VAR_NAME; never exposes or copies the value."""
    if not var_name:
        return False
    val = env.get(var_name)
    if val is None or isinstance(val, bool):
        return bool(val)
    if isinstance(val, str):
        stripped = val.strip()
        return bool(stripped) and stripped.lower() not in PLACEHOLDER_VALUES
    return bool(val)


def configured_providers(providers_cfg=None, env=None) -> dict[str, bool]:
    """Return {provider: bool_present} for every keyed provider in providers_cfg."""
    env_map = os.environ if env is None else env
    providers = _normalize_providers(providers_cfg)
    presence: dict[str, bool] = {}
    for pname, pinfo in providers.items():
        if not isinstance(pinfo, dict):
            continue
        env_var = str(pinfo.get("env_var") or "").strip()
        if not env_var:
            continue
        presence[str(pname)] = _is_env_present(env_map, env_var)
    return presence


def _provider_defaults(providers: dict, provider: str, env_map: dict, same_as_author: bool,
                       author_harness: str, jury_pref: str) -> tuple[str, str]:
    info = providers.get(provider) if isinstance(providers.get(provider), dict) else {}
    if provider == "replay":
        return "", "replay"
    if same_as_author and str(env_map.get("MODEL") or "").strip():
        model = str(env_map.get("MODEL") or "").strip()
    elif jury_pref and provider == jury_pref and str(env_map.get("JURY_MODEL") or "").strip():
        model = str(env_map.get("JURY_MODEL") or "").strip()
    else:
        model = str(info.get("default_model") or "")

    if same_as_author and author_harness:
        harness = author_harness
    elif jury_pref and provider == jury_pref and str(env_map.get("JURY_HARNESS") or "").strip():
        harness = str(env_map.get("JURY_HARNESS") or "").strip()
    else:
        harness = str(info.get("default_harness") or "")
    return model, harness


def _build_result(mode: str, cross_vendor: bool, author_provider: str,
                  reviewer_provider: str, reviewer_model: str,
                  reviewer_harness: str, reason: str) -> dict:
    return {
        "mode": mode,
        "cross_vendor": bool(cross_vendor),
        "author_provider": author_provider,
        "reviewer_provider": reviewer_provider,
        "reviewer_model": reviewer_model,
        "reviewer_harness": reviewer_harness,
        "reason": reason,
    }


def select_reviewer(author_provider, author_harness, risk, providers_cfg=None, env=None) -> dict:
    """Deterministically select reviewer (provider, model, harness) for test-reviewer/architect."""
    env_map = os.environ if env is None else env
    providers = _normalize_providers(providers_cfg)

    raw_mode = env_map.get("JURY_CROSS_PROVIDER")
    mode = JURY_DEFAULT_MODE if raw_mode is None or not str(raw_mode).strip() else str(raw_mode).strip().lower()
    if mode not in MODES:
        raise Refused(f"JURY_CROSS_PROVIDER={mode!r} is not one of {', '.join(MODES)}")

    if risk is None or not str(risk).strip():
        risk_level = "low"
    else:
        risk_level = str(risk).strip().lower()
    if risk_level not in RISK_LEVELS:
        raise Refused(f"unknown risk level {risk!r}; expected one of {', '.join(RISK_LEVELS)}")

    auth_harness = str(author_harness or env_map.get("AGENT_HARNESS") or "").strip()
    auth_prov = str(author_provider or "").strip().lower()
    model_pref = str(env_map.get("MODEL_PROVIDER") or "").strip().lower()
    profile = str(env_map.get("PROFILE") or "").strip().lower()

    if not auth_prov:
        if auth_harness == "replay" or (not model_pref and profile == "l0"):
            auth_prov = "replay"
        elif model_pref:
            auth_prov = model_pref
        elif profile == "l0":
            auth_prov = "replay"

    if not auth_harness and auth_prov in providers and isinstance(providers[auth_prov], dict):
        auth_harness = str(providers[auth_prov].get("default_harness") or "")

    is_replay = (
        auth_prov == "replay"
        or auth_harness == "replay"
        or (not auth_prov and profile == "l0")
    )

    jury_pref = str(env_map.get("JURY_PROVIDER") or "").strip().lower()
    if jury_pref and jury_pref not in providers:
        raise Refused(f"JURY_PROVIDER={jury_pref!r} is not defined in providers configuration")

    if jury_pref:
        default_provider = jury_pref
    elif is_replay:
        default_provider = "replay" if "replay" in providers else (auth_prov or "replay")
    elif auth_prov in providers:
        default_provider = auth_prov
    elif model_pref in providers:
        default_provider = model_pref
    else:
        default_provider = auth_prov or model_pref or ("replay" if "replay" in providers else "")

    def_model, def_harness = _provider_defaults(
        providers,
        default_provider,
        env_map,
        same_as_author=(default_provider == auth_prov),
        author_harness=auth_harness,
        jury_pref=jury_pref,
    )

    if mode == "off":
        return _build_result(
            mode,
            False,
            auth_prov,
            default_provider,
            def_model,
            def_harness,
            "JURY_CROSS_PROVIDER=off: cross-provider jury disabled; keeping default reviewer",
        )

    if is_replay:
        if mode == "strict":
            raise Refused("JURY_CROSS_PROVIDER=strict cannot run in replay mode at L0")
        return _build_result(
            mode,
            False,
            auth_prov or "replay",
            default_provider,
            def_model,
            def_harness,
            "replay mode at L0: keeping default replay reviewer",
        )

    if risk_level not in CROSS_PROVIDER_RISKS:
        if mode == "strict":
            raise Refused(f"JURY_CROSS_PROVIDER=strict requires medium or high risk PR; got {risk_level}")
        return _build_result(
            mode,
            False,
            auth_prov,
            default_provider,
            def_model,
            def_harness,
            f"{risk_level}-risk PR: keeping default reviewer under {mode} mode",
        )

    presence = configured_providers(providers, env_map)
    available = [pname for pname, is_set in presence.items() if is_set]
    alternates = [pname for pname in available if pname != auth_prov]

    if len(available) < JURY_MIN_PROVIDERS:
        if mode == "strict":
            raise Refused(
                f"JURY_CROSS_PROVIDER=strict requires at least {JURY_MIN_PROVIDERS} configured provider keys; "
                f"found {len(available)}"
            )
        return _build_result(
            mode,
            False,
            auth_prov,
            default_provider,
            def_model,
            def_harness,
            f"fewer than {JURY_MIN_PROVIDERS} vendor keys configured ({len(available)} present): keeping default reviewer",
        )

    if not alternates:
        if mode == "strict":
            raise Refused(f"JURY_CROSS_PROVIDER=strict found no alternate vendor key different from {auth_prov}")
        return _build_result(
            mode,
            False,
            auth_prov,
            default_provider,
            def_model,
            def_harness,
            f"no alternate vendor key different from {auth_prov}: keeping default reviewer",
        )

    pref_note = ""
    if jury_pref and jury_pref != auth_prov:
        if jury_pref in alternates:
            chosen = jury_pref
        elif mode == "strict":
            raise Refused(f"JURY_PROVIDER={jury_pref} key is not configured under JURY_CROSS_PROVIDER=strict")
        else:
            chosen = alternates[0]
            pref_note = f" (preferred JURY_PROVIDER={jury_pref} key unset; fell back deterministically)"
    else:
        chosen = alternates[0]

    rev_model, rev_harness = _provider_defaults(
        providers,
        chosen,
        env_map,
        same_as_author=False,
        author_harness=auth_harness,
        jury_pref=jury_pref,
    )
    reason = (
        f"{risk_level}-risk PR: selected cross-vendor reviewer {chosen} ({rev_harness}) "
        f"different from author {auth_prov}{pref_note}"
    )
    return _build_result(mode, True, auth_prov, chosen, rev_model, rev_harness, reason)


def _read_json(path: str | None) -> dict | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read JSON {path}: {exc}") from None
    if not isinstance(obj, dict):
        raise Refused(f"{path} must be a JSON object")
    return obj


def _extract_run_actor(run_obj: dict | None) -> tuple[str, str]:
    if not isinstance(run_obj, dict):
        return "", ""
    harness = str(run_obj.get("harness") or "").strip()
    if harness == "replay":
        return "replay", "replay"
    provider = str(run_obj.get("provider") or "").strip()
    if not provider and isinstance(run_obj.get("recorded"), dict):
        provider = str(run_obj["recorded"].get("provider") or "").strip()
    return provider, harness


def _extract_doc_author(doc: dict | None) -> tuple[str, str]:
    if not isinstance(doc, dict):
        return "", ""
    tasks = doc.get("tasks")
    last_task = None
    if isinstance(tasks, list) and tasks:
        for item in reversed(tasks):
            if isinstance(item, dict):
                last_task = item
                break
    elif isinstance(doc.get("task"), dict):
        last_task = doc["task"]
    if not isinstance(last_task, dict):
        return "", ""
    harness = str(last_task.get("harness") or "").strip()
    if harness == "replay" or last_task.get("replayed") is True:
        return "replay", "replay"
    return str(last_task.get("provider") or "").strip(), harness


def _extract_doc_risk(doc: dict | None) -> str:
    if not isinstance(doc, dict):
        return ""
    for section in ("ci", "merge"):
        sub = doc.get(section)
        if isinstance(sub, dict) and isinstance(sub.get("risk_level"), str) and sub["risk_level"].strip():
            return sub["risk_level"].strip()
    if isinstance(doc.get("risk_level"), str):
        return doc["risk_level"].strip()
    return ""


def _normalize_jury_dict(raw: dict) -> dict:
    return _build_result(
        str(raw.get("mode") or JURY_DEFAULT_MODE),
        bool(raw.get("cross_vendor")),
        str(raw.get("author_provider") or ""),
        str(raw.get("reviewer_provider") or ""),
        str(raw.get("reviewer_model") or ""),
        str(raw.get("reviewer_harness") or ""),
        str(raw.get("reason") or "recorded jury decision"),
    )


def _resolve_paths(args, env_map: dict) -> tuple[str | None, str | None, str | None]:
    ticket = (args.ticket or env_map.get("TICKET") or "").strip()
    doc_path = args.doc or env_map.get("DOC_FILE") or None
    run_path = args.run_json
    if not run_path and ticket:
        candidate = os.path.join(".crew", ticket, "out", "run.json")
        if os.path.isfile(candidate):
            run_path = candidate
    review_run_path = getattr(args, "review_run_json", None)
    if not review_run_path and ticket:
        candidate = os.path.join(".crew", ticket, "review", "out", "run.json")
        if os.path.isfile(candidate):
            review_run_path = candidate
    return doc_path, run_path, review_run_path


def cmd_select_env(args, env_map: dict) -> dict:
    if args.role and args.role not in REVIEWER_ROLES:
        raise Refused(f"role {args.role!r} is not a jury role ({', '.join(REVIEWER_ROLES)})")
    providers = load_providers(args.providers)
    doc_path, run_path, _ = _resolve_paths(args, env_map)
    doc = _read_json(doc_path)
    run_obj = _read_json(run_path)

    run_prov, run_harness = _extract_run_actor(run_obj)
    doc_prov, doc_harness = _extract_doc_author(doc)
    doc_risk = _extract_doc_risk(doc)

    author_provider = (
        args.author_provider
        or env_map.get("AUTHOR_PROVIDER")
        or run_prov
        or doc_prov
        or ""
    )
    author_harness = (
        args.author_harness
        or env_map.get("AUTHOR_HARNESS")
        or run_harness
        or doc_harness
        or ""
    )
    risk = args.risk or env_map.get("RISK_LEVEL") or env_map.get("RISK") or doc_risk or "low"
    return select_reviewer(author_provider, author_harness, risk, providers, env_map)


def cmd_inspect(args, env_map: dict) -> dict:
    providers = load_providers(args.providers)
    doc_path, run_path, review_run_path = _resolve_paths(args, env_map)
    doc = _read_json(doc_path)
    run_obj = _read_json(run_path)
    review_run = _read_json(review_run_path)

    for candidate in (
        (doc.get("review") or {}).get("jury") if isinstance(doc, dict) and isinstance(doc.get("review"), dict) else None,
        (doc.get("architect") or {}).get("jury") if isinstance(doc, dict) and isinstance(doc.get("architect"), dict) else None,
        doc.get("jury") if isinstance(doc, dict) else None,
        review_run.get("jury") if isinstance(review_run, dict) else None,
        run_obj.get("jury") if isinstance(run_obj, dict) else None,
    ):
        if isinstance(candidate, dict):
            return _normalize_jury_dict(candidate)

    if doc is None and run_obj is None and review_run is None and not args.author_provider:
        raise Refused("nothing to inspect: provide --ticket, --run-json, or --doc / DOC_FILE")

    run_prov, run_harness = _extract_run_actor(run_obj)
    doc_prov, doc_harness = _extract_doc_author(doc)
    author_provider = (args.author_provider or env_map.get("AUTHOR_PROVIDER") or run_prov or doc_prov or "").strip().lower()
    author_harness = (args.author_harness or env_map.get("AUTHOR_HARNESS") or run_harness or doc_harness or "").strip()

    rev_obj = None
    if isinstance(doc, dict) and isinstance(doc.get("review"), dict):
        rev_obj = doc["review"]
    elif isinstance(doc, dict) and isinstance(doc.get("architect"), dict):
        rev_obj = doc["architect"]
    elif isinstance(review_run, dict):
        rev_obj = review_run

    if isinstance(rev_obj, dict) and any(rev_obj.get(k) for k in ("provider", "harness", "model")):
        rev_prov, rev_harness = _extract_run_actor(rev_obj)
        if not rev_prov:
            rev_prov = str(rev_obj.get("provider") or "").strip().lower()
        if not rev_harness:
            rev_harness = str(rev_obj.get("harness") or "").strip()
        rev_model = "" if rev_prov == "replay" else str(rev_obj.get("model") or "")
        mode = str(env_map.get("JURY_CROSS_PROVIDER") or JURY_DEFAULT_MODE).strip().lower()
        if mode not in MODES:
            mode = JURY_DEFAULT_MODE
        cross_vendor = bool(
            author_provider
            and rev_prov
            and author_provider != rev_prov
            and "replay" not in (author_provider, rev_prov)
        )
        reason = (
            f"recorded cross-vendor review: {rev_prov} ({rev_harness}) reviewed author {author_provider}"
            if cross_vendor
            else f"recorded single-vendor/replay review: {rev_prov or 'unknown'} ({rev_harness or 'unknown'})"
        )
        return _build_result(mode, cross_vendor, author_provider, rev_prov, rev_model, rev_harness, reason)

    risk = args.risk or env_map.get("RISK_LEVEL") or env_map.get("RISK") or _extract_doc_risk(doc) or "low"
    return select_reviewer(author_provider, author_harness, risk, providers, env_map)


def main(argv=None, env=None) -> int:
    env_map = dict(os.environ if env is None else env)
    parser = argparse.ArgumentParser(prog="jury.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("select-env", "inspect"):
        p = sub.add_parser(name)
        p.add_argument("--author-provider", default="")
        p.add_argument("--author-harness", default="")
        p.add_argument("--risk", default="")
        p.add_argument("--role", default=env_map.get("ROLE") or "test-reviewer")
        p.add_argument("--ticket", default="")
        p.add_argument("--run-json", default="")
        if name == "inspect":
            p.add_argument("--review-run-json", default="")
        p.add_argument("--doc", default="")
        p.add_argument("--providers", default=DEFAULT_PROVIDERS_PATH)

    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    try:
        result = cmd_select_env(ns, env_map) if ns.cmd == "select-env" else cmd_inspect(ns, env_map)
    except Refused as exc:
        print(f"NO-GO: jury — {exc}")
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
