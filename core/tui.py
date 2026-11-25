#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Interactive terminal cockpit TUI over the versioned logbook (ADR 0114).

Reads a local logbook file (`--logbook <path>`) or polls a live HTTP endpoint
(`--url <url>`) with `ETag` / `If-None-Match` (`--poll-s`, default 2.0 s).
Separates pure state, key reducer, and line-buffer rendering from `curses` so
headless unit tests and `--once` / `--json` modes run without a physical TTY.
"""

from __future__ import annotations

import argparse
import curses
import fnmatch
import hashlib
import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Callable

VIEWS: tuple[str, ...] = ("overview", "tickets", "workers", "gates", "canary", "thresholds")
VIEW_TITLES: tuple[str, ...] = (
    "1:Overview",
    "2:Tickets",
    "3:Workers",
    "4:Gates",
    "5:Canary",
    "6:Thresholds",
)

# Provenance, decision: ADR 0047 §4 / ADR 0035 §3 console 2.0 s poll cadence;
# 0.1 s floor prevents accidental zero-delay CPU spin loops.
DEFAULT_POLL_S: float = 2.0
MIN_POLL_S: float = 0.1

# Provenance, external: ECMA-48 / DEC VT100 standard 80x24 terminal grid.
MIN_COLS: int = 80
MIN_ROWS: int = 24

# Provenance, decision: ADR 0114 — 5.0 s HTTP timeout (half of console check
# budget) and 16 MiB payload ceiling (~150x golden-1.8.json).
DEFAULT_HTTP_TIMEOUT_S: float = 5.0
MAX_LOGBOOK_BYTES: int = 16 * 1024 * 1024

# Provenance, decision: ADR 0087 (80% near-budget warning) and ADR 0038 §3
# (REWORK_MAX_ATTEMPTS=4 fallback when thresholds are unread).
TOKEN_WARN_RATIO: float = 0.80
DEFAULT_REWORK_MAX: int = 4

CANONICAL_ROLES: tuple[str, ...] = (
    "architect",
    "dev-worker",
    "ops-triage",
    "product",
    "release",
    "test-reviewer",
)
CANONICAL_GATES: tuple[str, ...] = (
    "risk",
    "secrets",
    "test-weakening",
    "deps",
    "sast",
    "test",
    "mutation",
    "test-proof",
)
BAND_BY_STATE: dict[str, str] = {
    "requested": "Spec",
    "questions": "Spec",
    "spec-ready": "Spec",
    "opened": "Build",
    "pr-opened": "Build",
    "review": "Test and review",
    "ci-passed": "Test and review",
    "ci-failed": "Test and review",
    "needs-human": "Test and review",
    "merged": "Release",
    "main-green": "Release",
    "held": "Release",
    "rolled-back": "Release",
}
BAND_ORDER: tuple[str, ...] = ("Spec", "Build", "Test and review", "Release", "Other")
SPARK_CHARS: str = ".:-=+*#@"

_ASCII_MAP: dict[int, str] = {
    0x2014: "-",
    0x2013: "-",
    0x2212: "-",
    0x2192: "->",
    0x2190: "<-",
    0x00B7: ".",
    0x2026: "...",
    0x2713: "OK",
    0x2717: "NO",
}


def is_unread(value: Any) -> bool:
    return isinstance(value, dict) and value.get("ok") is False


def unread_reason(value: Any) -> str:
    if is_unread(value):
        return clean_text(str(value.get("error") or "unreadable"))
    return "unreadable"


def _sanitize_ascii(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value).translate(_ASCII_MAP)
    out: list[str] = []
    for ch in raw:
        code = ord(ch)
        if ch in ("\n", "\r", "\t"):
            out.append(" ")
        elif 32 <= code <= 126:
            out.append(ch)
        elif code > 126:
            out.append("?")
    return "".join(out)


def clean_text(value: Any) -> str:
    return " ".join(_sanitize_ascii(value).split())


def clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    cleaned = _sanitize_ascii(text).rstrip()
    if len(cleaned) <= width:
        return cleaned
    if width <= 3:
        return cleaned[:width]
    return cleaned[: width - 3] + "..."


def schema_version(book: dict[str, Any]) -> str:
    raw = str(book.get("schema") or "")
    if "/" in raw:
        return raw.rsplit("/", 1)[-1]
    return raw or "unknown"


def validate_logbook(obj: Any, label: str = "logbook") -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError(f"{label}: root JSON value is not an object")
    schema = str(obj.get("schema") or "")
    if not schema.startswith("lights-out/logbook/1."):
        raise ValueError(f"{label}: unsupported schema {schema!r} (expected lights-out/logbook/1.x)")
    return obj


# --- Polling Sources -------------------------------------------------------


@dataclass(frozen=True)
class PollResult:
    book: dict[str, Any] | None
    changed: bool
    etag: str | None = None
    error: str | None = None


class FileSource:
    """Polls a local logbook JSON file, skipping re-parses when unchanged."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.book: dict[str, Any] | None = None
        self.etag: str | None = None
        self._stat_sig: tuple[int, int] | None = None

    def poll(self) -> PollResult:
        try:
            st = os.stat(self.path)
            sig = (st.st_mtime_ns, st.st_size)
            if self.book is not None and sig == self._stat_sig:
                return PollResult(self.book, False, self.etag, None)
            if st.st_size > MAX_LOGBOOK_BYTES:
                raise ValueError(f"{self.path} exceeds {MAX_LOGBOOK_BYTES} bytes")
            with open(self.path, "rb") as fh:
                data = fh.read(MAX_LOGBOOK_BYTES + 1)
            if len(data) > MAX_LOGBOOK_BYTES:
                raise ValueError(f"{self.path} exceeds {MAX_LOGBOOK_BYTES} bytes")
            digest = '"' + hashlib.sha256(data).hexdigest()[:16] + '"'
            if self.book is not None and digest == self.etag:
                self._stat_sig = sig
                return PollResult(self.book, False, self.etag, None)
            parsed = validate_logbook(json.loads(data.decode("utf-8")), self.path)
            self.book = parsed
            self.etag = digest
            self._stat_sig = sig
            return PollResult(self.book, True, self.etag, None)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return PollResult(self.book, False, self.etag, clean_text(str(exc)))


def normalize_logbook_url(url: str) -> str:
    if any(ch in url for ch in ("\r", "\n", "\x00")):
        raise ValueError("URL must not contain control characters")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        raise ValueError(f"unsupported URL {url!r}: expected http:// or https:// with host")
    path = parsed.path or "/v1/logbook.json"
    if path == "/":
        path = "/v1/logbook.json"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def redact_url_label(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    if parsed.username:
        netloc = f"{parsed.username}:***@{netloc}"
    query = "..." if parsed.query else ""
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))


class HttpSource:
    """Polls GET /v1/logbook.json using ETag and If-None-Match."""

    def __init__(
        self,
        url: str,
        *,
        timeout_s: float = DEFAULT_HTTP_TIMEOUT_S,
        headers: dict[str, str] | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.url = normalize_logbook_url(url)
        self.timeout_s = max(0.5, float(timeout_s))
        self.headers = dict(headers or {})
        self._opener = opener or urllib.request.urlopen
        self.book: dict[str, Any] | None = None
        self.etag: str | None = None

    def poll(self) -> PollResult:
        req_headers = {"Accept": "application/json", **self.headers}
        if self.etag:
            req_headers["If-None-Match"] = self.etag
        req = urllib.request.Request(self.url, headers=req_headers, method="GET")
        try:
            with self._opener(req, timeout=self.timeout_s) as resp:  # nosec B310 - http/https verified in normalize_logbook_url
                raw = resp.read(MAX_LOGBOOK_BYTES + 1)
                if len(raw) > MAX_LOGBOOK_BYTES:
                    raise ValueError(f"response exceeds {MAX_LOGBOOK_BYTES} bytes")
                tag = resp.headers.get("ETag") or resp.headers.get("etag")
                parsed = validate_logbook(json.loads(raw.decode("utf-8")), self.url)
                changed = self.book is None or tag is None or tag != self.etag or parsed != self.book
                self.book = parsed
                if tag:
                    self.etag = tag
                return PollResult(self.book, changed, self.etag, None)
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and self.book is not None:
                tag = (exc.headers.get("ETag") if exc.headers else None) or self.etag
                self.etag = tag
                return PollResult(self.book, False, self.etag, None)
            return PollResult(self.book, False, self.etag, f"HTTP {exc.code}: {clean_text(exc.reason)}")
        except (OSError, urllib.error.URLError, UnicodeDecodeError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            return PollResult(self.book, False, self.etag, clean_text(str(reason)))


# --- Pure State & Key Reducer ----------------------------------------------


@dataclass(frozen=True)
class TuiState:
    book: dict[str, Any]
    view: int = 0
    selected_ticket_idx: int = 0
    selected_ticket_id: str | None = None
    scroll_offset: int = 0
    search_query: str = ""
    search_editing: bool = False
    etag: str | None = None
    poll_count: int = 0
    poll_error: str | None = None
    source_label: str = ""
    quit_requested: bool = False
    refresh_requested: bool = False


def parse_view_arg(view: str | int) -> int:
    if isinstance(view, int):
        if 0 <= view < len(VIEWS):
            return view
        raise ValueError(f"view index out of range: {view}")
    token = str(view).strip().lower()
    if token in ("1", "2", "3", "4", "5", "6"):
        return int(token) - 1
    if token in VIEWS:
        return VIEWS.index(token)
    raise ValueError(f"unknown view {view!r}; choose 1..6 or {', '.join(VIEWS)}")


def _tickets_list(book: dict[str, Any]) -> list[dict[str, Any]]:
    raw = book.get("tickets")
    if not isinstance(raw, list):
        return []
    return [t for t in raw if isinstance(t, dict)]


def _matches_filter(ticket: dict[str, Any], query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    haystacks = [
        str(ticket.get("id") or ""),
        str(ticket.get("title") or ""),
        str(ticket.get("state") or ""),
        str(ticket.get("feature") or ""),
        str(ticket.get("priority") or ""),
    ]
    perim = ticket.get("perimeter")
    globs = [str(g) for g in perim] if isinstance(perim, list) else []
    haystacks.extend(globs)
    if any(q in h.lower() for h in haystacks):
        return True
    return any(fnmatch.fnmatch(q, g.lower()) for g in globs)


def filtered_tickets(state: TuiState) -> list[dict[str, Any]]:
    return [t for t in _tickets_list(state.book) if _matches_filter(t, state.search_query)]


def _sync_selection(state: TuiState, items: list[dict[str, Any]] | None = None) -> TuiState:
    tickets = filtered_tickets(state) if items is None else items
    if not tickets:
        return replace(state, selected_ticket_idx=0, selected_ticket_id=None)
    if state.selected_ticket_id is not None:
        for idx, t in enumerate(tickets):
            if str(t.get("id")) == state.selected_ticket_id:
                return replace(state, selected_ticket_idx=idx)
    clamped = max(0, min(state.selected_ticket_idx, len(tickets) - 1))
    tid = str(tickets[clamped].get("id") or "") or None
    return replace(state, selected_ticket_idx=clamped, selected_ticket_id=tid)


def make_state(
    book: dict[str, Any],
    *,
    view: str | int = "overview",
    ticket: str | None = None,
    source_label: str = "",
    etag: str | None = None,
) -> TuiState:
    validated = validate_logbook(book)
    view_idx = parse_view_arg(view)
    base = TuiState(
        book=validated,
        view=view_idx,
        selected_ticket_id=ticket,
        etag=etag,
        source_label=source_label,
    )
    return _sync_selection(base)


def apply_poll(state: TuiState, result: PollResult) -> TuiState:
    nxt = replace(
        state,
        poll_count=state.poll_count + 1,
        etag=result.etag if result.etag is not None else state.etag,
        poll_error=result.error,
        refresh_requested=False,
    )
    if result.book is not None and result.changed:
        nxt = _sync_selection(replace(nxt, book=result.book))
    return nxt


def normalize_key(raw_key: str | int) -> str | None:
    if isinstance(raw_key, str):
        if raw_key == "\t":
            return "TAB"
        if raw_key in ("\n", "\r"):
            return "ENTER"
        if raw_key == "\x1b":
            return "ESCAPE"
        if raw_key in ("\x7f", "\x08"):
            return "BACKSPACE"
        return raw_key
    if raw_key < 0:
        return None
    special = {
        9: "TAB",
        10: "ENTER",
        13: "ENTER",
        27: "ESCAPE",
        127: "BACKSPACE",
        8: "BACKSPACE",
        getattr(curses, "KEY_BTAB", 353): "BTAB",
        getattr(curses, "KEY_UP", 259): "UP",
        getattr(curses, "KEY_DOWN", 258): "DOWN",
        getattr(curses, "KEY_PPAGE", 339): "PAGE_UP",
        getattr(curses, "KEY_NPAGE", 338): "PAGE_DOWN",
        getattr(curses, "KEY_HOME", 262): "HOME",
        getattr(curses, "KEY_END", 360): "END",
        getattr(curses, "KEY_BACKSPACE", 263): "BACKSPACE",
        getattr(curses, "KEY_ENTER", 343): "ENTER",
        getattr(curses, "KEY_RESIZE", 410): "RESIZE",
    }
    if raw_key in special:
        return special[raw_key]
    if 32 <= raw_key <= 126:
        return chr(raw_key)
    return None


def reduce_key(state: TuiState, raw_key: str | int) -> TuiState:
    key = normalize_key(raw_key)
    if key is None or key == "RESIZE":
        return state

    if state.search_editing:
        if key == "ENTER":
            return _sync_selection(replace(state, search_editing=False, scroll_offset=0))
        if key == "ESCAPE":
            return _sync_selection(replace(state, search_query="", search_editing=False, scroll_offset=0))
        if key == "BACKSPACE":
            q = state.search_query[:-1]
            return _sync_selection(replace(state, search_query=q, selected_ticket_id=None, scroll_offset=0))
        if len(key) == 1 and 32 <= ord(key) <= 126:
            q = state.search_query + key
            return _sync_selection(replace(state, search_query=q, selected_ticket_id=None, scroll_offset=0))
        return state

    if key in ("1", "2", "3", "4", "5", "6"):
        return replace(state, view=int(key) - 1, scroll_offset=0)
    if key == "TAB":
        return replace(state, view=(state.view + 1) % len(VIEWS), scroll_offset=0)
    if key in ("BTAB", "SHIFT_TAB"):
        return replace(state, view=(state.view - 1) % len(VIEWS), scroll_offset=0)
    if key == "/":
        return replace(state, search_editing=True)
    if key == "ESCAPE":
        if state.search_query:
            return _sync_selection(replace(state, search_query="", scroll_offset=0))
        return state
    if key == "q":
        return replace(state, quit_requested=True)
    if key == "r":
        return replace(state, refresh_requested=True)

    ticket_nav_view = VIEWS[state.view] in ("tickets", "gates")
    tickets = filtered_tickets(state)

    if key in ("j", "DOWN"):
        if ticket_nav_view and tickets:
            nxt_idx = min(len(tickets) - 1, state.selected_ticket_idx + 1)
            tid = str(tickets[nxt_idx].get("id") or "") or None
            return replace(state, selected_ticket_idx=nxt_idx, selected_ticket_id=tid, scroll_offset=0)
        return replace(state, scroll_offset=state.scroll_offset + 1)

    if key in ("k", "UP"):
        if ticket_nav_view and tickets:
            nxt_idx = max(0, state.selected_ticket_idx - 1)
            tid = str(tickets[nxt_idx].get("id") or "") or None
            return replace(state, selected_ticket_idx=nxt_idx, selected_ticket_id=tid, scroll_offset=0)
        return replace(state, scroll_offset=max(0, state.scroll_offset - 1))

    if key in ("g", "HOME"):
        if ticket_nav_view and tickets:
            tid = str(tickets[0].get("id") or "") or None
            return replace(state, selected_ticket_idx=0, selected_ticket_id=tid, scroll_offset=0)
        return replace(state, scroll_offset=0)

    if key in ("G", "END"):
        if ticket_nav_view and tickets:
            last = len(tickets) - 1
            tid = str(tickets[last].get("id") or "") or None
            return replace(state, selected_ticket_idx=last, selected_ticket_id=tid, scroll_offset=0)
        return replace(state, scroll_offset=state.scroll_offset + 50)

    if key == "PAGE_DOWN":
        return replace(state, scroll_offset=state.scroll_offset + 10)
    if key == "PAGE_UP":
        return replace(state, scroll_offset=max(0, state.scroll_offset - 10))

    return state


# --- Domain Projections (Logbook 1.2 - 1.8) --------------------------------


def compute_unread_sections(book: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in (
        "tickets",
        "kanban",
        "releases",
        "postmortems",
        "incidents",
        "thresholds",
        "agents",
        "adrs",
        "dora",
        "spend",
        "roles",
        "intakes",
        "perimeters_held",
        "docs_to_realign",
    ):
        if is_unread(book.get(key)):
            found.append(key)
    launch = book.get("launch")
    if isinstance(launch, dict):
        if is_unread(launch.get("record")):
            found.append("launch.record")
        if is_unread(launch.get("plane")):
            found.append("launch.plane")
    return sorted(found)


def compute_waiting_for_you(book: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    launch = book.get("launch") if isinstance(book.get("launch"), dict) else {}
    rec = launch.get("record")
    if isinstance(rec, dict) and not is_unread(rec):
        if rec.get("verdict") == "held" and "promoted" not in rec and "rejected" not in rec:
            ver = clean_text(rec.get("version") or "?")
            pct = rec.get("held_at")
            press = "ready to press" if launch.get("pressable") else clean_text(launch.get("disabled_reason") or "prepare brief")
            items.append({
                "kind": "go-for-launch",
                "id": ver,
                "state": "held",
                "action": "promote or rollback",
                "summary": f"release {ver} held at {pct}% ({press})",
            })

    releases = book.get("releases") if isinstance(book.get("releases"), list) else []
    answered_tickets = {
        str(r.get("ticket"))
        for r in releases
        if isinstance(r, dict) and isinstance(r.get("answer"), dict) and r.get("ticket")
    }

    for t in _tickets_list(book):
        tid = clean_text(t.get("id") or "?")
        state = clean_text(t.get("state") or "unopened")
        outcome = t.get("outcome") if isinstance(t.get("outcome"), dict) else {}
        if state == "questions":
            qs = [q for q in (t.get("questions") or []) if isinstance(q, dict) and not q.get("answer")]
            qtext = clean_text(qs[-1].get("question")) if qs else "open question"
            items.append({
                "kind": "ticket-question",
                "id": tid,
                "state": state,
                "action": "answer question",
                "summary": f"ticket {tid} [questions]: {qtext}",
            })
        elif state == "needs-human":
            why = clean_text(outcome.get("line") or "rework or spend cap reached")
            items.append({
                "kind": "needs-human",
                "id": tid,
                "state": state,
                "action": "human decision",
                "summary": f"ticket {tid} [needs-human]: {why}",
            })
        elif state == "held" and outcome.get("judge") != "human" and tid not in answered_tickets:
            items.append({
                "kind": "ticket-held",
                "id": tid,
                "state": state,
                "action": "go-for-launch",
                "summary": f"ticket {tid} [held]: waiting for go-for-launch",
            })
        elif outcome.get("verdict") == "stranded":
            why = clean_text(outcome.get("line") or "step failed")
            items.append({
                "kind": "stranded",
                "id": tid,
                "state": state,
                "action": "resume",
                "summary": f"ticket {tid} [{state}, stranded]: {why}",
            })

    intakes = book.get("intakes")
    if isinstance(intakes, list):
        for intake in intakes:
            if not isinstance(intake, dict):
                continue
            iid = clean_text(intake.get("id") or "?")
            istate = clean_text(intake.get("state") or "intake")
            if istate in ("intake-questions", "proposed") or intake.get("stranded"):
                action = "answer intake" if istate == "intake-questions" else ("confirm intake" if istate == "proposed" else "resume intake")
                qs = [q for q in (intake.get("questions") or []) if isinstance(q, dict) and not q.get("answer")]
                detail = clean_text(qs[-1].get("question")) if qs else clean_text(intake.get("text") or istate)
                items.append({
                    "kind": "intake",
                    "id": iid,
                    "state": istate,
                    "action": action,
                    "summary": f"intake {iid} [{istate}]: {detail}",
                })

    return items


def compute_bands(book: dict[str, Any]) -> dict[str, Any]:
    counts = {b: 0 for b in BAND_ORDER}
    states: dict[str, int] = {}
    tickets = _tickets_list(book)
    for t in tickets:
        st = clean_text(t.get("state") or "unopened")
        states[st] = states.get(st, 0) + 1
        band = BAND_BY_STATE.get(st, "Other")
        counts[band] = counts.get(band, 0) + 1
    return {"bands": counts, "states": states, "total": len(tickets)}


def max_rework_attempts(book: dict[str, Any]) -> int:
    thresholds = book.get("thresholds")
    if isinstance(thresholds, list):
        for item in thresholds:
            if isinstance(item, dict) and item.get("key") == "REWORK_MAX_ATTEMPTS":
                try:
                    return max(1, int(str(item.get("value"))))
                except ValueError:
                    break
    return DEFAULT_REWORK_MAX


def verify_thresholds(book: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    thresholds = book.get("thresholds")
    if isinstance(thresholds, list):
        for item in thresholds:
            if isinstance(item, dict) and item.get("key") in ("ERROR_RATE_MAX_DELTA", "LATENCY_P95_MAX_DELTA"):
                out[str(item["key"])] = clean_text(item.get("value") or "")
    return out


def derive_roles(book: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Returns (role_rows, derived_fallback)."""
    roles = book.get("roles")
    if isinstance(roles, list):
        return [r for r in roles if isinstance(r, dict)], False
    if is_unread(roles):
        return [], False

    # Schema 1.2 - 1.5 fallback: derive last-seen activity per canonical role from tickets.
    seen: dict[str, dict[str, Any]] = {}
    for t in _tickets_list(book):
        tid = clean_text(t.get("id") or "?")
        task = t.get("task")
        if isinstance(task, dict) and task.get("role"):
            rname = str(task["role"])
            tok = task.get("tokens")
            total_tok = tok.get("total") if isinstance(tok, dict) else None
            seen[rname] = {
                "ticket": tid,
                "harness": task.get("harness"),
                "model": task.get("model"),
                "tokens": total_tok,
            }
        rev = t.get("review")
        if isinstance(rev, dict) and rev.get("role"):
            seen[str(rev["role"])] = {"ticket": tid, "harness": rev.get("harness"), "model": rev.get("model"), "tokens": None}
        prod = t.get("product")
        if isinstance(prod, dict) and prod.get("consulted"):
            seen["product"] = {"ticket": tid, "harness": prod.get("harness"), "model": prod.get("model"), "tokens": None}

    rows: list[dict[str, Any]] = []
    for role in CANONICAL_ROLES:
        last = seen.get(role)
        rows.append({
            "role": role,
            "state": "ran" if last else "idle",
            "budget": None,
            "used": last.get("tokens") if last else None,
            "running": None,
            "configured": {"harness": last.get("harness") if last else None, "provider": None, "model": None, "thinking": None},
            "last": last,
            "line": f"ran on {last['ticket']}" if last else "no role matrix in schema",
        })
    return rows, True


def in_flight_runs(book: dict[str, Any]) -> list[dict[str, Any]]:
    flying: list[dict[str, Any]] = []
    seen_tickets: set[str] = set()
    for t in _tickets_list(book):
        live = t.get("live")
        if isinstance(live, dict):
            tid = clean_text(t.get("id") or "?")
            seen_tickets.add(tid)
            tok = live.get("tokens")
            bud = live.get("budget")
            warn = bool(live.get("warn")) or (
                isinstance(tok, (int, float)) and isinstance(bud, (int, float)) and bud > 0 and (tok / bud) >= TOKEN_WARN_RATIO
            )
            flying.append({
                "ticket": tid,
                "role": clean_text(live.get("role") or "dev-worker"),
                "attempt": live.get("attempt"),
                "tokens": tok,
                "budget": bud,
                "warn": warn,
            })
    roles = book.get("roles")
    if isinstance(roles, list):
        for r in roles:
            if not isinstance(r, dict):
                continue
            running = r.get("running")
            if isinstance(running, dict):
                tid = clean_text(running.get("ticket") or "?")
                if tid in seen_tickets:
                    continue
                tok = running.get("tokens")
                bud = running.get("budget")
                warn = bool(running.get("warn")) or (
                    isinstance(tok, (int, float)) and isinstance(bud, (int, float)) and bud > 0 and (tok / bud) >= TOKEN_WARN_RATIO
                )
                flying.append({
                    "ticket": tid,
                    "role": clean_text(r.get("role") or "worker"),
                    "attempt": None,
                    "tokens": tok,
                    "budget": bud,
                    "warn": warn,
                })
    return flying


def rework_pips(ticket: dict[str, Any], max_attempts: int) -> str:
    reworks = [r for r in (ticket.get("rework") or []) if isinstance(r, dict)]
    rejected = len(reworks)
    live = ticket.get("live")
    outcome = ticket.get("outcome") if isinstance(ticket.get("outcome"), dict) else {}
    ci = ticket.get("ci") if isinstance(ticket.get("ci"), dict) else {}
    slots = max(max_attempts, rejected, int(ticket.get("attempt") or 0))
    pips: list[str] = []
    for idx in range(1, slots + 1):
        if idx <= rejected:
            pips.append("X")
        elif isinstance(live, dict) and live.get("attempt") == idx:
            pips.append("*")
        elif ticket.get("attempt") == idx:
            if outcome.get("verdict") == "GO" or ci.get("verdict") == "GO":
                pips.append("G")
            elif outcome.get("verdict") in ("NO-GO", "needs-human", "stranded") or ci.get("verdict") == "NO-GO":
                pips.append("!")
            else:
                pips.append("o")
        else:
            pips.append(".")
    used = int(ticket.get("attempt") or rejected)
    return "[" + "".join(pips) + f"] {used}/{max_attempts}"


def format_meter(used: Any, budget: Any, width: int = 10) -> str:
    if not isinstance(used, (int, float)) or not isinstance(budget, (int, float)) or budget <= 0:
        return "[" + ("." * width) + "]   n/a"
    ratio = max(0.0, min(1.0, float(used) / float(budget)))
    filled = int(round(ratio * width))
    pct = int(round(ratio * 100))
    return "[" + ("#" * filled) + ("." * (width - filled)) + f"] {pct:3d}%"


def format_sparkline(hourly: Any) -> str:
    if not isinstance(hourly, list) or not hourly:
        return "." * 24
    vals = [float(v) if isinstance(v, (int, float)) and v > 0 else 0.0 for v in hourly[:24]]
    peak = max(vals) if vals else 0.0
    if peak <= 0:
        return "." * len(vals)
    chars: list[str] = []
    for v in vals:
        if v <= 0:
            chars.append(".")
        else:
            idx = min(len(SPARK_CHARS) - 1, max(1, int(round((v / peak) * (len(SPARK_CHARS) - 1)))))
            chars.append(SPARK_CHARS[idx])
    return "".join(chars)


# --- Line-Buffer Frame Renderer --------------------------------------------


@dataclass(frozen=True)
class FrameRow:
    text: str
    style: str = "body"


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    rows: tuple[FrameRow, ...]

    def lines(self) -> list[str]:
        return [r.text for r in self.rows]

    def plain_text(self) -> str:
        return "\n".join(r.text for r in self.rows)


def _verdict_tag(verdict: Any) -> str:
    v = str(verdict or "")
    if v == "GO":
        return "GO"
    if v == "NO-GO":
        return "NO-GO"
    if v == "running":
        return "RUN"
    return "--"


def _render_overview_body(state: TuiState) -> list[FrameRow]:
    book = state.book
    ver = schema_version(book)
    rows: list[FrameRow] = []

    waiting = compute_waiting_for_you(book)
    rows.append(FrameRow(f"Waiting for you ({len(waiting)}):", "section"))
    if not waiting:
        rows.append(FrameRow("  none - autonomous loop clear", "go"))
    else:
        for item in waiting[:5]:
            rows.append(FrameRow(f"  ! [{item['action']}] {item['summary']}", "wait"))
        if len(waiting) > 5:
            rows.append(FrameRow(f"  ... +{len(waiting) - 5} more human-turn item(s) (press 2 for Tickets)", "wait"))

    if is_unread(book.get("tickets")):
        rows.append(FrameRow(f"Tickets: unread - {unread_reason(book.get('tickets'))}", "nogo"))
    else:
        band_info = compute_bands(book)
        b = band_info["bands"]
        rows.append(
            FrameRow(
                f"Flow Bands (total={band_info['total']}): Spec={b['Spec']}  Build={b['Build']}  "
                f"Test/Review={b['Test and review']}  Release={b['Release']}  Other={b['Other']}",
                "section",
            )
        )
        st_parts = [f"{k}:{v}" for k, v in sorted(band_info["states"].items())]
        rows.append(FrameRow("  States: " + (", ".join(st_parts) if st_parts else "none"), "body"))

    dora = book.get("dora")
    if dora is None:
        rows.append(FrameRow(f"DORA: not recorded in schema {ver}", "muted"))
    elif is_unread(dora):
        rows.append(FrameRow(f"DORA: unread - {unread_reason(dora)}", "nogo"))
    elif isinstance(dora, dict):
        m = dora.get("measures") if isinstance(dora.get("measures"), dict) else {}
        df = m.get("deployment_frequency") or {}
        lt = m.get("lead_time") or {}
        cf = m.get("change_fail_rate") or {}
        rt = m.get("recovery_time") or {}
        df_s = f"{df.get('count', 0)} ({df.get('per_day', 0):.3f}/d)" if df.get("measured") else "unmeasured"
        lt_s = f"{(lt.get('median_s', 0) / 3600.0):.1f}h" if lt.get("measured") else "unmeasured"
        cf_s = f"{int(round((cf.get('rate') or 0) * 100))}% ({cf.get('failed', 0)}/{cf.get('decided', 0)})" if cf.get("measured") else "unmeasured"
        rt_s = f"{int(round((rt.get('median_s') or 0) / 60.0))}m" if rt.get("measured") else "unmeasured"
        rows.append(FrameRow(f"DORA ({dora.get('window_days', 30)}d): freq={df_s} | lead={lt_s} | fail={cf_s} | mttr={rt_s}", "body"))

    spend = book.get("spend")
    if spend is None:
        rows.append(FrameRow(f"Spend: not recorded in schema {ver}", "muted"))
    elif is_unread(spend):
        rows.append(FrameRow(f"Spend: unread - {unread_reason(spend)}", "nogo"))
    elif isinstance(spend, dict):
        tot = spend.get("total") if isinstance(spend.get("total"), dict) else {}
        cost = tot.get("cost_usd")
        cost_s = f"${cost:.2f}" if isinstance(cost, (int, float)) else "unknown"
        burn = spend.get("burn") if isinstance(spend.get("burn"), dict) else {}
        proj = burn.get("projected_usd")
        proj_s = f" | proj ${proj:.2f}/d" + (" [OVER]" if burn.get("over") else "") if isinstance(proj, (int, float)) else ""
        rows.append(FrameRow(f"Spend: {cost_s} ({tot.get('runs', 0)} runs, {tot.get('unknown', 0)} unknown){proj_s}", "body"))

    flying = in_flight_runs(book)
    if flying:
        f0 = flying[0]
        tok_s = f"{f0['tokens']:,}" if isinstance(f0["tokens"], int) else str(f0["tokens"])
        bud_s = f"{f0['budget']:,}" if isinstance(f0["budget"], int) else "n/a"
        warn_s = " NEAR TOKEN_BUDGET" if f0["warn"] else ""
        rows.append(FrameRow(f"In flight: ticket {f0['ticket']} ({f0['role']}) {tok_s}/{bud_s} tok{warn_s}", "wait" if f0["warn"] else "body"))

    unread = compute_unread_sections(book)
    if unread:
        rows.append(FrameRow("Unread sources: " + ", ".join(unread), "nogo"))
    return rows


def _render_flow_strip_lines(ticket: dict[str, Any], ver: str) -> list[FrameRow]:
    flow = ticket.get("flow")
    if isinstance(flow, list) and flow:
        short_names = {
            "ticket": "ticket",
            "perimeter": "perim",
            "risk": "risk",
            "secrets": "sec",
            "test-weakening": "weak",
            "deps": "deps",
            "sast": "sast",
            "test": "test",
            "mutation": "mut",
            "test-proof": "proof",
            "pr": "pr",
            "canary": "canary",
            "human": "human",
            "full": "full",
        }
        tokens: list[str] = []
        any_nogo = False
        for node in flow:
            if not isinstance(node, dict):
                continue
            key = str(node.get("gate") or node.get("node") or "?")
            label = short_names.get(key, key)
            vtag = _verdict_tag(node.get("verdict"))
            if vtag == "NO-GO":
                any_nogo = True
            tokens.append(f"{label}[{vtag}]")
        style = "nogo" if any_nogo else "body"
        mid = min(7, len(tokens))
        out = [FrameRow("Flow: " + " ".join(tokens[:mid]), style)]
        if len(tokens) > mid:
            out.append(FrameRow("      " + " ".join(tokens[mid:]), style))
        return out

    # Pre-1.6 derived flow strip
    spec = ticket.get("spec") if isinstance(ticket.get("spec"), dict) else {}
    ci = ticket.get("ci") if isinstance(ticket.get("ci"), dict) else {}
    rev = ticket.get("review") if isinstance(ticket.get("review"), dict) else {}
    rel = ticket.get("release") if isinstance(ticket.get("release"), dict) else {}
    out_v = (ticket.get("outcome") or {}).get("verdict") if isinstance(ticket.get("outcome"), dict) else None
    parts = [
        f"spec[{spec.get('verdict') or '--'}]",
        f"ci[{ci.get('verdict') or '--'}]",
        f"review[{rev.get('verdict') or '--'}]",
        f"canary[{rel.get('verdict') or '--'}]",
        f"outcome[{out_v or '--'}]",
    ]
    return [FrameRow(f"Flow (derived, schema {ver}): " + " ".join(parts), "body")]


def _render_tickets_body(state: TuiState, body_slots: int) -> list[FrameRow]:
    book = state.book
    ver = schema_version(book)
    if is_unread(book.get("tickets")):
        return [FrameRow(f"Tickets: unread - {unread_reason(book.get('tickets'))}", "nogo")]

    all_t = _tickets_list(book)
    tickets = filtered_tickets(state)
    rows: list[FrameRow] = []
    filt_note = f" filter={state.search_query!r}" if state.search_query else ""
    rows.append(FrameRow(f"Tickets ({len(tickets)}/{len(all_t)}){filt_note}:", "section"))

    if not tickets:
        rows.append(FrameRow("  no tickets match the current filter", "muted"))
        return rows

    synced = _sync_selection(state, tickets)
    sel_idx = synced.selected_ticket_idx
    list_cap = max(2, min(6, (body_slots - 8) if body_slots > 10 else 3))
    start = max(0, min(sel_idx - list_cap // 2, max(0, len(tickets) - list_cap)))
    window = tickets[start : start + list_cap]

    for offset, t in enumerate(window):
        idx = start + offset
        marker = ">" if idx == sel_idx else " "
        tid = clean_text(t.get("id") or "?")
        st = clean_text(t.get("state") or "unopened")
        band = BAND_BY_STATE.get(st, "Other")
        out_v = (t.get("outcome") or {}).get("verdict") if isinstance(t.get("outcome"), dict) else None
        live_tag = " [LIVE]" if isinstance(t.get("live"), dict) else ""
        title = clean_text(t.get("title") or "")
        style = "selected" if idx == sel_idx else ("nogo" if out_v == "NO-GO" else "body")
        rows.append(FrameRow(f"{marker} {tid} [{st}] {band} | {out_v or '-'}{live_tag} - {title}", style))

    chosen = tickets[sel_idx]
    tid = clean_text(chosen.get("id") or "?")
    st = clean_text(chosen.get("state") or "unopened")
    band = BAND_BY_STATE.get(st, "Other")
    rows.append(FrameRow(f"--- Detail: {tid} ({st} / {band}) | prio={chosen.get('priority') or '-'} feat={chosen.get('feature') or '-'} ---", "section"))
    rows.append(FrameRow(f"Title: {clean_text(chosen.get('title') or '')}", "body"))

    outcome = chosen.get("outcome") if isinstance(chosen.get("outcome"), dict) else {}
    if outcome.get("verdict") or outcome.get("line"):
        ostyle = "nogo" if outcome.get("verdict") == "NO-GO" else ("go" if outcome.get("verdict") == "GO" else "wait")
        rows.append(FrameRow(f"Outcome: {outcome.get('verdict') or '-'} ({outcome.get('judge') or '-'}) {clean_text(outcome.get('line') or '')}", ostyle))

    perim = chosen.get("perimeter")
    globs = ", ".join(clean_text(g) for g in perim) if isinstance(perim, list) and perim else "none"
    files = chosen.get("files")
    fcount = f" ({len(files)} touched)" if isinstance(files, list) else ""
    adrs = chosen.get("adrs")
    adr_s = f" | ADRs: {','.join(str(a) for a in adrs)}" if isinstance(adrs, list) and adrs else ""
    rows.append(FrameRow(f"Perimeter: {globs}{fcount}{adr_s}", "body"))

    wait = chosen.get("wait")
    if isinstance(wait, dict):
        holders = ",".join(str(h) for h in (wait.get("holders") or [])) or "none"
        rows.append(FrameRow(f"Wait: refusals={wait.get('refusals', 0)} holders={holders} since={wait.get('since') or '-'}", "wait"))

    rows.extend(_render_flow_strip_lines(chosen, ver))

    timeline = [e for e in (chosen.get("timeline") or chosen.get("history") or []) if isinstance(e, dict)]
    rows.append(FrameRow(f"Timeline ({len(timeline)} event(s)):", "section"))
    for ev in timeline[-3:]:
        at = clean_text(ev.get("at") or "--")
        phase = clean_text(ev.get("phase") or ev.get("step") or "-")
        ev_v = clean_text(ev.get("verdict") or ev.get("state") or "")
        line = clean_text(ev.get("line") or "")
        rows.append(FrameRow(f"  {at} [{phase}] {ev_v} {line}".rstrip(), "body"))
    return rows


def _render_workers_body(state: TuiState) -> list[FrameRow]:
    book = state.book
    ver = schema_version(book)
    rows: list[FrameRow] = []

    if is_unread(book.get("roles")):
        rows.append(FrameRow(f"Role Matrix: unread - {unread_reason(book.get('roles'))}", "nogo"))
    else:
        role_rows, derived = derive_roles(book)
        note = f" (derived from ticket runs; schema {ver} omits roles)" if derived else f" (schema {ver})"
        rows.append(FrameRow(f"Role Matrix ({len(role_rows)} roles){note}:", "section"))
        for r in role_rows:
            rname = clean_text(r.get("role") or "?").ljust(13)
            rstate = clean_text(r.get("state") or "idle").upper()[:4].ljust(4)
            used = r.get("used")
            budget = r.get("budget")
            meter = format_meter(used, budget, width=10)
            used_s = f"{used:,}" if isinstance(used, int) else "-"
            bud_s = f"{budget:,}" if isinstance(budget, int) else "-"
            running = r.get("running") if isinstance(r.get("running"), dict) else None
            warn = bool(running and running.get("warn")) or (
                isinstance(used, (int, float)) and isinstance(budget, (int, float)) and budget > 0 and (used / budget) >= TOKEN_WARN_RATIO
            )
            cfg = r.get("configured") if isinstance(r.get("configured"), dict) else {}
            harness = clean_text(cfg.get("harness") or "-")
            warn_tag = " WARN" if warn else ""
            style = "wait" if warn else ("go" if rstate.strip() == "RUN" else "body")
            rows.append(FrameRow(f"  {rname} [{rstate}] {meter} {used_s}/{bud_s}{warn_tag} ({harness})", style))

    flying = in_flight_runs(book)
    rows.append(FrameRow(f"Active Runs in Flight ({len(flying)}):", "section"))
    if not flying:
        rows.append(FrameRow("  none in flight", "muted"))
    else:
        for fl in flying:
            tok_s = f"{fl['tokens']:,}" if isinstance(fl["tokens"], int) else str(fl["tokens"])
            bud_s = f"{fl['budget']:,}" if isinstance(fl["budget"], int) else "no-budget"
            pct_s = (
                f" ({int(round((fl['tokens'] / fl['budget']) * 100))}%)"
                if isinstance(fl["tokens"], (int, float)) and isinstance(fl["budget"], (int, float)) and fl["budget"] > 0
                else ""
            )
            warn_s = " [NEAR TOKEN_BUDGET]" if fl["warn"] else ""
            att_s = f" attempt {fl['attempt']}" if fl.get("attempt") is not None else ""
            rows.append(
                FrameRow(
                    f"  ticket {fl['ticket']} {fl['role']}{att_s}: {tok_s} of {bud_s} tokens{pct_s}{warn_s}",
                    "wait" if fl["warn"] else "body",
                )
            )

    spend = book.get("spend")
    rows.append(FrameRow("Token & USD Spend / Daily Projection:", "section"))
    if spend is None:
        rows.append(FrameRow(f"  spend ledger not recorded in schema {ver}", "muted"))
    elif is_unread(spend):
        rows.append(FrameRow(f"  unread - {unread_reason(spend)}", "nogo"))
    elif isinstance(spend, dict):
        tot = spend.get("total") if isinstance(spend.get("total"), dict) else {}
        limits = spend.get("limits") if isinstance(spend.get("limits"), dict) else {}
        burn = spend.get("burn") if isinstance(spend.get("burn"), dict) else {}
        cost = tot.get("cost_usd")
        cost_s = f"${cost:.2f}" if isinstance(cost, (int, float)) else "unknown"
        tlim = limits.get("SPEND_BUDGET_TICKET_USD")
        dlim = limits.get("SPEND_BUDGET_DAY_USD")
        tlim_s = f"${tlim:.2f}" if isinstance(tlim, (int, float)) else "none"
        dlim_s = f"${dlim:.2f}" if isinstance(dlim, (int, float)) else "none"
        rows.append(FrameRow(f"  Total: {cost_s} over {tot.get('runs', 0)} run(s), {tot.get('unknown', 0)} unknown | limits: ticket={tlim_s} day={dlim_s}", "body"))
        if burn:
            spent_d = burn.get("spent_usd")
            rate_h = burn.get("per_hour_usd")
            proj_d = burn.get("projected_usd")
            lim_d = burn.get("limit_usd")
            spent_s = f"${spent_d:.2f}" if isinstance(spent_d, (int, float)) else "-"
            rate_s = f"${rate_h:.2f}/h" if isinstance(rate_h, (int, float)) else "-"
            proj_s = f"${proj_d:.2f}/d" if isinstance(proj_d, (int, float)) else "n/a"
            lim_s = f"${lim_d:.2f}" if isinstance(lim_d, (int, float)) else "none"
            over_tag = " [OVER BUDGET]" if burn.get("over") else ""
            bstyle = "nogo" if burn.get("over") else "body"
            rows.append(FrameRow(f"  Day {burn.get('day') or '-'}: spent={spent_s} rate={rate_s} -> projected={proj_s} (limit={lim_s}){over_tag}", bstyle))
            rows.append(FrameRow(f"  24h burn: [{format_sparkline(burn.get('hourly'))}] (left={burn.get('left_h') or 0}h)", "muted"))
    return rows


def _format_why_details(why: Any) -> list[str]:
    if not isinstance(why, dict):
        return []
    out: list[str] = []
    details = why.get("details")
    if is_unread(details):
        out.append(unread_reason(details))
    elif isinstance(details, list):
        for d in details[:2]:
            out.append(clean_text(d))
    sast = why.get("sast")
    if is_unread(sast):
        out.append(unread_reason(sast))
    elif isinstance(sast, list):
        for finding in sast[:2]:
            if isinstance(finding, dict):
                out.append(f"sast {finding.get('rule')} {finding.get('file')}:{finding.get('line')}")
    return out


def _render_gates_body(state: TuiState, body_slots: int) -> list[FrameRow]:
    book = state.book
    if is_unread(book.get("tickets")):
        return [FrameRow(f"Gates & Rework: tickets unread - {unread_reason(book.get('tickets'))}", "nogo")]

    max_att = max_rework_attempts(book)
    tickets = filtered_tickets(state)
    rows: list[FrameRow] = [FrameRow(f"Gates & Rework (REWORK_MAX_ATTEMPTS={max_att}, {len(tickets)} ticket(s)):", "section")]
    if not tickets:
        rows.append(FrameRow("  no tickets match filter", "muted"))
        return rows

    synced = _sync_selection(state, tickets)
    sel_idx = synced.selected_ticket_idx
    list_cap = max(2, min(5, (body_slots - 9) if body_slots > 11 else 3))
    start = max(0, min(sel_idx - list_cap // 2, max(0, len(tickets) - list_cap)))
    for offset, t in enumerate(tickets[start : start + list_cap]):
        idx = start + offset
        marker = ">" if idx == sel_idx else " "
        tid = clean_text(t.get("id") or "?")
        st = clean_text(t.get("state") or "-")
        pips = rework_pips(t, max_att)
        ci = t.get("ci") if isinstance(t.get("ci"), dict) else {}
        failed = clean_text(ci.get("failed") or (t.get("outcome") or {}).get("verdict") or "-")
        style = "selected" if idx == sel_idx else "body"
        rows.append(FrameRow(f"{marker} {tid} {pips} [{st}] {failed}", style))

    chosen = tickets[sel_idx]
    tid = clean_text(chosen.get("id") or "?")
    rows.append(FrameRow(f"--- Ticket {tid} Gate Verdicts & Attempt History ---", "section"))

    ci = chosen.get("ci") if isinstance(chosen.get("ci"), dict) else None
    if ci:
        cstyle = "nogo" if ci.get("verdict") == "NO-GO" else "go"
        rows.append(FrameRow(f"Current CI: {ci.get('verdict') or '-'} (failed={ci.get('failed') or 'none'}) {clean_text(ci.get('line') or '')}", cstyle))
        gates_list = ci.get("gates") if isinstance(ci.get("gates"), list) else []
        secs = ci.get("seconds") if isinstance(ci.get("seconds"), dict) else {}
        if gates_list:
            by_gate = {str(g.get("gate")): g for g in gates_list if isinstance(g, dict)}
            chips: list[str] = []
            for gname in CANONICAL_GATES:
                gobj = by_gate.get(gname)
                verdict = _verdict_tag(gobj.get("verdict")) if gobj else "--"
                dur = f"({secs[gname]}s)" if gname in secs else ""
                chips.append(f"{gname}:{verdict}{dur}")
            rows.append(FrameRow("  " + " ".join(chips[:4]), "body"))
            rows.append(FrameRow("  " + " ".join(chips[4:]), "body"))
        for detail in _format_why_details(ci.get("why")):
            rows.append(FrameRow(f"  why: {detail}", "nogo"))
    else:
        rows.append(FrameRow("Current CI: no gate run on current state", "muted"))

    review = chosen.get("review") if isinstance(chosen.get("review"), dict) else None
    if review and (review.get("verdict") or review.get("reason")):
        rstyle = "nogo" if review.get("verdict") == "changes-requested" else "body"
        rwhy = clean_text((review.get("reason") or "").splitlines()[0] if review.get("reason") else "")
        rows.append(FrameRow(f"Review ({review.get('role') or 'reviewer'}): {review.get('verdict') or '-'} {rwhy}".rstrip(), rstyle))

    reworks = [r for r in (chosen.get("rework") or []) if isinstance(r, dict)]
    if not reworks:
        rows.append(FrameRow("Rework history: 0 rejected attempts", "muted"))
    else:
        rows.append(FrameRow(f"Rework history ({len(reworks)} rejected attempt(s)):", "section"))
        for rw in reworks:
            att = rw.get("attempt")
            judge = clean_text(rw.get("judge") or "gates")
            fgate = clean_text(rw.get("failed") or (rw.get("why") or {}).get("gate") or "-")
            line = clean_text(rw.get("line") or "")
            rows.append(FrameRow(f"  attempt {att} ({judge}, gate={fgate}): {line}", "nogo"))
            for detail in _format_why_details(rw.get("why")):
                rows.append(FrameRow(f"    detail: {detail}", "wait"))
    return rows


def _format_stage_slo(stage: dict[str, Any]) -> str:
    slo_list = stage.get("slo")
    if isinstance(slo_list, list) and slo_list:
        parts: list[str] = []
        for entry in slo_list:
            if not isinstance(entry, dict):
                continue
            q = clean_text(entry.get("query") or "slo")
            can = entry.get("canary")
            stb = entry.get("stable")
            dlt = entry.get("delta")
            mxd = entry.get("max_delta")
            if all(isinstance(x, (int, float)) for x in (can, stb, dlt, mxd)):
                flag = "!" if dlt > mxd else "ok"
                parts.append(f"{q}={can:.3f} vs {stb:.3f} (d={dlt:+.3f}/{mxd} {flag})")
            else:
                parts.append(f"{q}={can}")
        if parts:
            return "; ".join(parts)
    nums = stage.get("numbers")
    if isinstance(nums, list) and nums:
        return "; ".join(clean_text(n) for n in nums[:2])
    return clean_text(stage.get("verdict") or "")


def _render_canary_body(state: TuiState) -> list[FrameRow]:
    book = state.book
    rows: list[FrameRow] = []
    launch = book.get("launch") if isinstance(book.get("launch"), dict) else {}
    plane = launch.get("plane")
    rec = launch.get("record")

    rows.append(FrameRow("Delivery Plane & Human Launch Gate:", "section"))
    if is_unread(plane):
        rows.append(FrameRow(f"  Plane: unread - {unread_reason(plane)}", "nogo"))
    elif isinstance(plane, dict):
        rows.append(
            FrameRow(
                f"  Plane: stable={plane.get('stable') or 'none'}  canary={plane.get('canary') or 'none'}  weight={plane.get('weight') if plane.get('weight') is not None else '-'}%",
                "body",
            )
        )
    else:
        rows.append(FrameRow("  Plane: none reported", "muted"))

    if is_unread(rec):
        rows.append(FrameRow(f"  Launch record: unread - {unread_reason(rec)}", "nogo"))
    elif isinstance(rec, dict):
        press = "pressable=yes" if launch.get("pressable") else f"pressable=no ({clean_text(launch.get('disabled_reason') or 'disabled')})"
        rows.append(
            FrameRow(
                f"  Launch: version={rec.get('version') or '-'} verdict={rec.get('verdict') or '-'} held_at={rec.get('held_at') or '-'}% | {press}",
                "wait" if launch.get("pressable") else "body",
            )
        )
    else:
        rows.append(FrameRow(f"  Launch: no active record ({clean_text(launch.get('disabled_reason') or 'idle')})", "muted"))

    vthresh = verify_thresholds(book)
    if is_unread(book.get("thresholds")):
        rows.append(FrameRow(f"  Thresholds: unread - {unread_reason(book.get('thresholds'))}", "nogo"))
    else:
        er = vthresh.get("ERROR_RATE_MAX_DELTA", "unset")
        lp = vthresh.get("LATENCY_P95_MAX_DELTA", "unset")
        rows.append(FrameRow(f"  Verify thresholds: ERROR_RATE_MAX_DELTA={er}  LATENCY_P95_MAX_DELTA={lp}", "body"))

    rows.append(FrameRow("Canary Stages & SLO Metrics:", "section"))
    if isinstance(rec, dict) and isinstance(rec.get("stages"), list) and rec["stages"]:
        ver = clean_text(rec.get("version") or "?")
        for stg in rec["stages"]:
            if not isinstance(stg, dict):
                continue
            pct = stg.get("pct")
            verd = clean_text(stg.get("verdict") or "-")
            style = "nogo" if verd.startswith("NO-GO") else "go"
            rows.append(FrameRow(f"  [{ver}] {pct}% {verd[:5]}: {_format_stage_slo(stg)}", style))

    releases = book.get("releases")
    if is_unread(releases):
        rows.append(FrameRow(f"  Releases log: unread - {unread_reason(releases)}", "nogo"))
    elif isinstance(releases, list):
        rollbacks = [r for r in releases if isinstance(r, dict) and r.get("verdict") == "rolled-back"]
        for rb in rollbacks[-2:]:
            rver = clean_text(rb.get("version") or "?")
            rtkt = clean_text(rb.get("ticket") or "-")
            rwhy = clean_text(rb.get("reason") or "rolled back")
            rows.append(FrameRow(f"  Rollback {rver} (ticket {rtkt}): {rwhy}", "nogo"))
            for stg in (rb.get("stages") or [])[-1:]:
                if isinstance(stg, dict):
                    rows.append(FrameRow(f"    stage {stg.get('pct')}%: {_format_stage_slo(stg)}", "nogo"))

    rows.append(FrameRow("Incidents & Postmortems:", "section"))
    incidents = book.get("incidents")
    if is_unread(incidents):
        rows.append(FrameRow(f"  Incidents: unread - {unread_reason(incidents)}", "nogo"))
    elif isinstance(incidents, list) and incidents:
        for inc in incidents[:2]:
            if isinstance(inc, dict):
                rows.append(FrameRow(f"  Incident {clean_text(inc.get('id') or inc.get('name') or '?')}: {clean_text(inc.get('summary') or inc.get('status') or '')}", "wait"))

    pms = book.get("postmortems")
    if is_unread(pms):
        rows.append(FrameRow(f"  Postmortems: unread - {unread_reason(pms)}", "nogo"))
    elif isinstance(pms, list) and pms:
        ticket_states = {str(t.get("id")): clean_text(t.get("state") or "unopened") for t in _tickets_list(book)}
        for pm in pms[:3]:
            if not isinstance(pm, dict):
                continue
            name = clean_text(pm.get("name") or "?")
            rel_v = clean_text((pm.get("release") or {}).get("version") or "-") if isinstance(pm.get("release"), dict) else "-"
            causes = pm.get("causes") if isinstance(pm.get("causes"), list) else []
            c0 = clean_text(causes[0].get("cause") if causes and isinstance(causes[0], dict) else (pm.get("title") or ""))
            actions = pm.get("actions") if isinstance(pm.get("actions"), list) else []
            act_s = ", ".join(
                f"#{a.get('n')}->{a.get('ticket')}[{ticket_states.get(str(a.get('ticket')), '?')}]"
                for a in actions
                if isinstance(a, dict)
            )
            suffix = f" | fix: {act_s}" if act_s else ""
            rows.append(FrameRow(f"  {name} ({rel_v}): {c0}{suffix}", "body"))
    else:
        rows.append(FrameRow("  no postmortems recorded", "muted"))

    return rows


def _summarize_thresholds(book: dict[str, Any]) -> dict[str, Any]:
    raw = book.get("thresholds")
    items = [t for t in raw if isinstance(t, dict)] if isinstance(raw, list) else []
    overridden = sum(1 for t in items if t.get("overridden"))
    changed = sum(1 for t in items if t.get("changed"))
    with_prov = sum(1 for t in items if isinstance(t.get("provenance"), dict))
    tickets = _tickets_list(book)
    jury_n = sum(1 for t in tickets if isinstance(t.get("jury"), dict))
    prov_n = sum(1 for t in tickets if isinstance(t.get("provenance_summary"), dict))
    return {
        "count": len(items),
        "overridden": overridden,
        "changed": changed,
        "with_provenance": with_prov,
        "jury_tickets": jury_n,
        "provenance_tickets": prov_n,
    }


def _render_thresholds_body(state: TuiState) -> list[FrameRow]:
    book = state.book
    rows: list[FrameRow] = []
    raw_thresholds = book.get("thresholds")
    if is_unread(raw_thresholds):
        rows.append(FrameRow("Thresholds & Provenance:", "section"))
        rows.append(FrameRow(f"  Thresholds unread: {unread_reason(raw_thresholds)}", "nogo"))
        return rows

    items = [t for t in raw_thresholds if isinstance(t, dict)] if isinstance(raw_thresholds, list) else []
    stats = _summarize_thresholds(book)
    rows.append(
        FrameRow(
            f"Thresholds & Provenance: {stats['count']} keys, {stats['overridden']} overridden, {stats['with_provenance']} with provenance",
            "section",
        )
    )
    if not items:
        rows.append(FrameRow("  (no threshold entries in logbook)", "muted"))
    else:
        ordered = sorted(
            items,
            key=lambda t: (0 if (t.get("changed") or t.get("overridden")) else 1, str(t.get("id") or t.get("key") or "")),
        )
        for item in ordered[:14]:
            name = clean_text(item.get("id") or item.get("key") or "?")
            active = clean_text(item.get("active") if "active" in item else item.get("value"))
            default = item.get("default")
            changed = bool(item.get("changed"))
            overridden = bool(item.get("overridden"))
            src = clean_text(item.get("source") or item.get("file") or "-")
            prov = item.get("provenance") if isinstance(item.get("provenance"), dict) else None
            p_kind = clean_text(prov.get("kind")) if prov else (clean_text(item.get("provenance_kind")) if item.get("provenance_kind") else "none")
            tag = "[MOD]" if changed else ("[OVR]" if overridden else "[DEF]")
            val_txt = f"{clean_text(default)} -> {active}" if (changed and default not in (None, "")) else (active or "(empty)")
            commit = item.get("last_commit") if isinstance(item.get("last_commit"), dict) else None
            sha_txt = f" @{clean_text(commit.get('short') or '')}" if commit and commit.get("short") else ""
            style = "wait" if (changed or overridden) else ("body" if prov else "muted")
            rows.append(FrameRow(f"  {tag} {name} = {val_txt} | {p_kind} | {src}{sha_txt}", style))
        if len(ordered) > 14:
            rows.append(FrameRow(f"  ... and {len(ordered) - 14} more threshold keys", "muted"))

    rows.append(FrameRow("Ticket Jury & Attestation Receipts:", "section"))
    tickets = filtered_tickets(state)
    att_rows = 0
    for t in tickets:
        tid = clean_text(t.get("id") or "?")
        jury = t.get("jury") if isinstance(t.get("jury"), dict) else None
        ps = t.get("provenance_summary") if isinstance(t.get("provenance_summary"), dict) else None
        if not jury and not ps:
            continue
        att_rows += 1
        parts: list[str] = [f"  {tid}:"]
        if jury:
            cross = "cross-vendor" if jury.get("cross_vendor") else clean_text(jury.get("mode") or "jury")
            ap = clean_text(jury.get("author_provider") or "-")
            rp = clean_text(jury.get("reviewer_provider") or "-")
            parts.append(f"jury={cross}({ap}->{rp})")
        if ps:
            status_lbl = "complete" if ps.get("complete") else f"missing={ps.get('missing_count', 0)}"
            sig_lbl = "signed" if ps.get("signed") else "unsigned"
            sha_lbl = clean_text(ps.get("statement_sha256") or "")[:12]
            parts.append(f"prov={status_lbl}/{sig_lbl}" + (f" sha={sha_lbl}" if sha_lbl else ""))
        row_style = "go" if (ps and ps.get("complete") and ps.get("signed")) else ("wait" if jury else "body")
        rows.append(FrameRow("  ".join(parts), row_style))
    if att_rows == 0:
        rows.append(FrameRow("  (no ticket provenance receipts or cross-provider jury records)", "muted"))
    return rows


def render_frame(state: TuiState, width: int = MIN_COLS, height: int = MIN_ROWS) -> Frame:
    w = max(1, int(width))
    h = max(1, int(height))
    book = state.book
    src = book.get("source") if isinstance(book.get("source"), dict) else {}
    ver = schema_version(book)
    commit = clean_text(src.get("commit") or "none")[:7]
    workload = clean_text(src.get("workload") or "-")
    level = clean_text(src.get("level") or "-")

    header_text = f"LIGHTS-OUT COCKPIT | schema {ver} | workload={workload} @{commit} | level={level}"
    tabs = [
        f"[{title}]" if idx == state.view else f" {title} "
        for idx, title in enumerate(VIEW_TITLES)
    ]
    tab_line = " ".join(tabs)
    if state.search_query and not state.search_editing:
        tab_line += f" | filter={state.search_query!r}"

    waiting_n = len(compute_waiting_for_you(book))
    tcount = len(_tickets_list(book)) if not is_unread(book.get("tickets")) else 0
    tag_short = clean_text(state.etag or "-")[:14]
    if state.poll_error:
        status_text = f"POLL ERROR: {state.poll_error} (showing last good logbook; etag={tag_short})"
        status_style = "nogo"
    else:
        lbl = clean_text(state.source_label or "logbook")
        status_text = f"src={lbl} | etag={tag_short} | tickets={tcount} | waiting={waiting_n}"
        status_style = "status"

    if state.search_editing:
        footer_text = f"/{state.search_query}_  (Enter:apply  Esc:clear)"
    else:
        footer_text = "1-6/Tab:view  j/k:move  /:filter  Esc:clear  r:refresh  q:quit"

    # Tiny terminal resize safety (< 6 rows)
    if h < 6:
        raw_tiny = [
            FrameRow(header_text, "header"),
            FrameRow(tab_line, "tabs"),
            FrameRow(status_text, status_style),
            FrameRow(footer_text, "footer"),
        ][:h]
        clamped_tiny = tuple(
            FrameRow(clip(r.text, max(0, w - 1) if i == h - 1 else w), r.style)
            for i, r in enumerate(raw_tiny)
        )
        return Frame(width=w, height=h, rows=clamped_tiny)

    body_slots = h - 5
    vname = VIEWS[state.view]
    if vname == "overview":
        body = _render_overview_body(state)
    elif vname == "tickets":
        body = _render_tickets_body(state, body_slots)
    elif vname == "workers":
        body = _render_workers_body(state)
    elif vname == "gates":
        body = _render_gates_body(state, body_slots)
    elif vname == "canary":
        body = _render_canary_body(state)
    else:
        body = _render_thresholds_body(state)

    max_scroll = max(0, len(body) - body_slots)
    scroll = max(0, min(state.scroll_offset, max_scroll))
    sliced = list(body[scroll : scroll + body_slots])
    while len(sliced) < body_slots:
        sliced.append(FrameRow("", "body"))

    rule = "-" * w
    if max_scroll > 0:
        scroll_badge = f"[rows {scroll + 1}-{min(len(body), scroll + body_slots)}/{len(body)}] "
        status_text = scroll_badge + status_text

    assembled = [
        FrameRow(header_text, "header"),
        FrameRow(tab_line, "tabs"),
        FrameRow(rule, "muted"),
        *sliced,
        FrameRow(status_text, status_style),
        FrameRow(footer_text, "footer"),
    ]
    final_rows = tuple(
        FrameRow(clip(r.text, max(0, w - 1) if idx == h - 1 else w), r.style)
        for idx, r in enumerate(assembled[:h])
    )
    return Frame(width=w, height=h, rows=final_rows)


# --- Structured JSON Summary -----------------------------------------------


def build_summary_json(state: TuiState) -> dict[str, Any]:
    book = state.book
    src = book.get("source") if isinstance(book.get("source"), dict) else {}
    band_info = compute_bands(book)
    max_att = max_rework_attempts(book)
    tickets_out = []
    for t in filtered_tickets(state):
        outcome = t.get("outcome") if isinstance(t.get("outcome"), dict) else {}
        tickets_out.append({
            "id": t.get("id"),
            "title": t.get("title"),
            "state": t.get("state"),
            "band": BAND_BY_STATE.get(str(t.get("state") or ""), "Other"),
            "verdict": outcome.get("verdict"),
            "attempt": t.get("attempt"),
            "rework_count": len(t.get("rework") or []) if isinstance(t.get("rework"), list) else 0,
            "in_flight": isinstance(t.get("live"), dict),
        })

    role_rows, derived_roles = derive_roles(book)
    spend = book.get("spend") if isinstance(book.get("spend"), dict) and not is_unread(book.get("spend")) else None
    launch = book.get("launch") if isinstance(book.get("launch"), dict) else {}
    releases = book.get("releases") if isinstance(book.get("releases"), list) else []
    rollbacks = [
        {"version": r.get("version"), "ticket": r.get("ticket"), "reason": r.get("reason")}
        for r in releases
        if isinstance(r, dict) and r.get("verdict") == "rolled-back"
    ]
    pms = book.get("postmortems") if isinstance(book.get("postmortems"), list) else []

    return {
        "schema": "lights-out/tui-summary/1.0",
        "logbook_schema": book.get("schema"),
        "view": VIEWS[state.view],
        "source": {
            "commit": src.get("commit"),
            "level": src.get("level"),
            "workload": src.get("workload"),
            "code_host": src.get("code_host"),
            "label": state.source_label,
            "etag": state.etag,
        },
        "waiting_for_you": compute_waiting_for_you(book),
        "bands": {**band_info["bands"], "total": band_info["total"], "states": band_info["states"]},
        "tickets": tickets_out,
        "workers": {
            "roles_derived": derived_roles,
            "roles_count": len(role_rows),
            "in_flight": in_flight_runs(book),
            "spend_total": spend.get("total") if spend else None,
            "spend_burn": spend.get("burn") if spend else None,
        },
        "gates_rework": {
            "max_attempts": max_att,
            "tickets_with_rework": sum(1 for t in tickets_out if t["rework_count"] > 0),
            "failed_tickets": [t["id"] for t in tickets_out if t["verdict"] == "NO-GO"],
        },
        "canary_ops": {
            "plane": None if is_unread(launch.get("plane")) else launch.get("plane"),
            "pressable": bool(launch.get("pressable")),
            "verify_thresholds": verify_thresholds(book),
            "releases_count": len(releases),
            "rollbacks": rollbacks,
            "postmortems_count": len(pms),
        },
        "thresholds_governance": _summarize_thresholds(book),
        "unread_sections": compute_unread_sections(book),
    }


# --- Curses Driver ---------------------------------------------------------


def _style_attr(style: str, *, use_color: bool) -> int:
    bold = getattr(curses, "A_BOLD", 0)
    rev = getattr(curses, "A_REVERSE", 0)
    dim = getattr(curses, "A_DIM", 0)
    if not use_color:
        if style in ("header", "selected", "footer"):
            return rev
        if style in ("tabs", "section", "nogo", "wait"):
            return bold
        if style == "muted":
            return dim
        return 0
    pair_map = {
        "header": 1,
        "tabs": 1,
        "selected": 1,
        "go": 2,
        "nogo": 3,
        "wait": 4,
        "section": 5,
    }
    pair = curses.color_pair(pair_map.get(style, 0))
    if style in ("header", "selected"):
        return pair | rev | bold
    if style in ("section", "nogo", "wait", "tabs"):
        return pair | bold
    if style == "footer":
        return rev
    return pair


def draw_curses_frame(stdscr: Any, frame: Frame, *, use_color: bool = False) -> None:
    """Blits Frame into a curses window with strict 80x24 / bottom-right clamping."""
    max_y, max_x = stdscr.getmaxyx()
    if max_y <= 0 or max_x <= 0:
        return
    stdscr.erase()
    rows_to_draw = min(len(frame.rows), max_y)
    for y in range(rows_to_draw):
        row = frame.rows[y]
        safe_limit = (max_x - 1) if y == max_y - 1 else max_x
        if safe_limit <= 0 or not row.text:
            continue
        text = row.text[:safe_limit]
        attr = _style_attr(row.style, use_color=use_color)
        try:
            stdscr.addnstr(y, 0, text, safe_limit, attr)
        except curses.error:
            pass
    stdscr.refresh()


def run_curses(initial_state: TuiState, source: FileSource | HttpSource, *, poll_s: float, no_color: bool) -> int:
    interval = max(MIN_POLL_S, float(poll_s))

    def _loop(stdscr: Any) -> int:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        stdscr.timeout(100)  # 100 ms input slice keeps 'q' responsive between HTTP polls

        use_color = False
        if not no_color and curses.has_colors():
            try:
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_CYAN, -1)
                curses.init_pair(2, curses.COLOR_GREEN, -1)
                curses.init_pair(3, curses.COLOR_RED, -1)
                curses.init_pair(4, curses.COLOR_YELLOW, -1)
                curses.init_pair(5, curses.COLOR_WHITE, -1)
                use_color = True
            except curses.error:
                use_color = False

        state = initial_state
        dirty = True
        next_poll = time.monotonic() + interval

        while not state.quit_requested:
            if dirty:
                max_y, max_x = stdscr.getmaxyx()
                frame = render_frame(state, width=max_x, height=max_y)
                draw_curses_frame(stdscr, frame, use_color=use_color)
                dirty = False

            ch = stdscr.getch()
            if ch != -1:
                if ch == getattr(curses, "KEY_RESIZE", 410):
                    dirty = True
                else:
                    nxt = reduce_key(state, ch)
                    if nxt != state:
                        state = nxt
                        dirty = True

            now = time.monotonic()
            if state.refresh_requested or now >= next_poll:
                res = source.poll()
                updated = apply_poll(state, res)
                if res.changed or updated.poll_error != state.poll_error or state.refresh_requested:
                    dirty = True
                state = updated
                next_poll = time.monotonic() + interval

        return 0

    return curses.wrapper(_loop)


# --- CLI Entrypoint --------------------------------------------------------


def _parse_header_kv(raw: str) -> tuple[str, str]:
    if any(ch in raw for ch in ("\r", "\n", "\x00")):
        raise ValueError("HTTP header must not contain CR/LF/NUL characters")
    if ":" not in raw:
        raise ValueError(f"invalid --header {raw!r}: expected 'Header-Name: value'")
    k, v = raw.split(":", 1)
    k, v = k.strip(), v.strip()
    if not k or any(ch.isspace() for ch in k):
        raise ValueError(f"invalid --header {raw!r}: invalid header name")
    return k, v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lights-out top", description=__doc__.splitlines()[0])
    src_group = p.add_mutually_exclusive_group()
    src_group.add_argument("--logbook", help="Path to local logbook JSON (default: LOGBOOK_FILE or .crew/logbook.json)")
    src_group.add_argument("--url", help="HTTP(S) logbook endpoint URL (e.g. http://127.0.0.1:8098/v1/logbook.json)")
    p.add_argument("--header", action="append", default=[], help="Extra HTTP header 'Name: Value' for --url")
    p.add_argument("--poll-s", type=float, default=DEFAULT_POLL_S, help=f"Poll cadence in seconds (default: {DEFAULT_POLL_S})")
    p.add_argument("--view", default="overview", help="Initial view: 1..6 or overview|tickets|workers|gates|canary|thresholds")
    p.add_argument("--ticket", default=None, help="Pre-selected ticket ID")
    p.add_argument("--width", type=int, default=None, help="Viewport width override for --once")
    p.add_argument("--height", type=int, default=None, help="Viewport height override for --once")
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="Render one plain-text cockpit snapshot and exit")
    mode_group.add_argument("--json", action="store_true", help="Emit structured summary JSON and exit")
    return p


def _is_tty(stream: Any) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(callable(isatty) and isatty())


def main(
    argv: list[str] | None = None,
    *,
    stdin: Any = None,
    stdout: Any = None,
    stderr: Any = None,
) -> int:
    in_s = sys.stdin if stdin is None else stdin
    out_s = sys.stdout if stdout is None else stdout
    err_s = sys.stderr if stderr is None else stderr
    args_list = sys.argv[1:] if argv is None else list(argv)

    parser = build_parser()
    try:
        args = parser.parse_args(args_list)
        if not math.isfinite(args.poll_s) or args.poll_s <= 0:
            raise ValueError("--poll-s must be a finite number > 0")
        if args.width is not None and args.width < 1:
            raise ValueError("--width must be >= 1")
        if args.height is not None and args.height < 1:
            raise ValueError("--height must be >= 1")
        view_idx = parse_view_arg(args.view)
        extra_headers = dict(_parse_header_kv(h) for h in args.header)
        url = args.url or (os.environ.get("LOGBOOK_URL") if not args.logbook else None)
        if url:
            source: FileSource | HttpSource = HttpSource(url, headers=extra_headers)
            label = redact_url_label(source.url)
        else:
            path = args.logbook or os.environ.get("LOGBOOK_FILE") or ".crew/logbook.json"
            source = FileSource(path)
            label = path
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    except ValueError as exc:
        print(f"tui: {exc}", file=err_s)
        return 2

    initial = source.poll()
    if initial.book is None:
        print(f"tui: cannot read logbook ({label}): {initial.error or 'unknown error'}", file=err_s)
        return 1

    state = make_state(
        initial.book,
        view=view_idx,
        ticket=args.ticket,
        source_label=label,
        etag=initial.etag,
    )

    if args.json:
        out_s.write(json.dumps(build_summary_json(state), sort_keys=True, indent=2) + "\n")
        return 0

    no_color = bool(os.environ.get("NO_COLOR"))
    interactive_tty = _is_tty(in_s) and _is_tty(out_s)
    if args.once or not interactive_tty:
        term = shutil.get_terminal_size((MIN_COLS, MIN_ROWS))
        cols = args.width if args.width is not None else max(MIN_COLS, term.columns)
        rows = args.height if args.height is not None else max(MIN_ROWS, term.lines)
        frame = render_frame(state, width=cols, height=rows)
        out_s.write(frame.plain_text() + "\n")
        return 0

    return run_curses(state, source, poll_s=args.poll_s, no_color=no_color)


if __name__ == "__main__":
    sys.exit(main())
