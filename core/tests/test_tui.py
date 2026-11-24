"""Spec-derived unit tests for core/tui.py (ADR 0114)."""

from __future__ import annotations

import copy
import email.message
import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from core import tui

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class FakeCursesError(Exception):
    """Simulates ncurses bounds and bottom-right corner errors."""


class StrictFakeWindow:
    """Strict ncurses window double enforcing 80x24 and bottom-right cell rules."""

    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width
        self.grid = [[" "] * width for _ in range(height)]
        self.calls: list[tuple[int, int, str, int, int]] = []

    def getmaxyx(self) -> tuple[int, int]:
        return (self.height, self.width)

    def erase(self) -> None:
        self.grid = [[" "] * self.width for _ in range(self.height)]

    def refresh(self) -> None:
        return None

    def addnstr(self, y: int, x: int, text: str, n: int, attr: int = 0) -> None:
        if y < 0 or y >= self.height or x < 0 or x >= self.width or n < 0:
            raise FakeCursesError(f"out of bounds y={y} x={x} n={n} for {self.width}x{self.height}")
        drawn = text[:n]
        end_x = x + len(drawn)
        if end_x > self.width:
            raise FakeCursesError(f"row overflow y={y} end_x={end_x} > width={self.width}")
        if y == self.height - 1 and end_x >= self.width and len(drawn) > 0:
            raise FakeCursesError("ncurses error: wrote bottom-right auto-scroll cell")
        for idx, ch in enumerate(drawn):
            if ord(ch) < 32:
                raise FakeCursesError(f"control char { ch!r } at y={y} x={x + idx}")
            self.grid[y][x + idx] = ch
        self.calls.append((y, x, drawn, n, attr))


def sample_logbook_1_8() -> dict:
    """Vendor-free, app-free, model-free synthetic 1.8 logbook fixture."""
    return {
        "schema": "lights-out/logbook/1.8",
        "source": {
            "commit": "abcdef1234567",
            "level": "L1",
            "workload": "orders-service",
            "code_host": "local-host",
        },
        "tickets": [
            {
                "id": "0101",
                "title": "Validate invoice totals before checkout",
                "state": "rolled-back",
                "feature": "billing",
                "priority": "high",
                "attempt": 1,
                "perimeter": ["src/billing/*", "docs/billing.md"],
                "adrs": ["0012"],
                "wait": None,
                "live": None,
                "questions": [],
                "outcome": {
                    "judge": "verify",
                    "verdict": "NO-GO",
                    "line": "NO-GO: launchpad - 5%: error_rate 0.085 -> rolled back",
                },
                "ci": {
                    "verdict": "GO",
                    "decision": "auto-merge",
                    "risk_level": "low",
                    "failed": None,
                    "line": "GO: gates - low, auto-merge",
                    "gates": [
                        {"gate": "risk", "verdict": "GO", "line": "GO: risk - low"},
                        {"gate": "secrets", "verdict": "GO", "line": "GO: secrets"},
                        {"gate": "test-weakening", "verdict": "GO", "line": "GO: test-weakening"},
                        {"gate": "deps", "verdict": "GO", "line": "GO: deps"},
                        {"gate": "sast", "verdict": "GO", "line": "GO: sast"},
                        {"gate": "test", "verdict": "GO", "line": "GO: test"},
                        {"gate": "mutation", "verdict": "GO", "line": "GO: mutation"},
                        {"gate": "test-proof", "verdict": "GO", "line": "GO: test-proof"},
                    ],
                    "seconds": {"risk": 1, "test": 14, "mutation": 42},
                    "why": None,
                },
                "rework": [],
                "flow": [
                    {"node": "ticket", "gate": None, "verdict": "GO", "seconds": 2, "line": "GO: spec"},
                    {"node": "perimeter", "gate": None, "verdict": "GO", "seconds": 10, "line": "PR #1"},
                    {"node": "gate", "gate": "risk", "verdict": "GO", "seconds": 1, "line": "GO: risk"},
                    {"node": "gate", "gate": "secrets", "verdict": "GO", "seconds": 1, "line": "GO: secrets"},
                    {"node": "gate", "gate": "test-weakening", "verdict": "GO", "seconds": 1, "line": "GO"},
                    {"node": "gate", "gate": "deps", "verdict": "GO", "seconds": 1, "line": "GO"},
                    {"node": "gate", "gate": "sast", "verdict": "GO", "seconds": 2, "line": "GO"},
                    {"node": "gate", "gate": "test", "verdict": "GO", "seconds": 14, "line": "GO"},
                    {"node": "gate", "gate": "mutation", "verdict": "GO", "seconds": 42, "line": "GO"},
                    {"node": "gate", "gate": "test-proof", "verdict": "GO", "seconds": 8, "line": "GO"},
                    {"node": "pr", "gate": None, "verdict": "GO", "seconds": 3, "line": "merged abcdef1"},
                    {"node": "canary", "gate": None, "verdict": "NO-GO", "seconds": 60, "line": "5% failed"},
                    {"node": "human", "gate": None, "verdict": "not-reached", "seconds": None, "line": None},
                    {"node": "full", "gate": None, "verdict": "not-reached", "seconds": None, "line": None},
                ],
                "timeline": [
                    {"at": "2026-11-20T09:00:00Z", "phase": "open", "verdict": "GO", "line": "opened"},
                    {"at": "2026-11-20T09:15:00Z", "phase": "canary", "verdict": "NO-GO", "line": "5% error_rate"},
                ],
                "files": [{"path": "src/billing/totals.py", "perimeter": "src/billing/*"}],
            },
            {
                "id": "0102",
                "title": "Add idempotency token to payment ledger",
                "state": "ci-failed",
                "feature": "payments",
                "priority": "high",
                "attempt": 2,
                "perimeter": ["src/ledger/*"],
                "adrs": ["0019"],
                "wait": None,
                "live": {
                    "role": "dev-worker",
                    "attempt": 3,
                    "tokens": 2210000,
                    "budget": 2600000,
                    "warn": True,
                    "line": "dev-worker, attempt 3: 2,210,000 of 2,600,000 tokens (85%)",
                },
                "questions": [],
                "outcome": {
                    "judge": "gates",
                    "verdict": "NO-GO",
                    "line": "NO-GO: gates - mutation: 2 mutants survived",
                },
                "ci": {
                    "verdict": "NO-GO",
                    "decision": None,
                    "risk_level": "medium",
                    "failed": "mutation",
                    "line": "NO-GO: gates - mutation: score 72% below baseline 80%",
                    "gates": [
                        {"gate": "risk", "verdict": "GO", "line": "GO: risk - medium"},
                        {"gate": "secrets", "verdict": "GO", "line": "GO: secrets"},
                        {"gate": "test-weakening", "verdict": "GO", "line": "GO: test-weakening"},
                        {"gate": "deps", "verdict": "GO", "line": "GO: deps"},
                        {"gate": "sast", "verdict": "GO", "line": "GO: sast"},
                        {"gate": "test", "verdict": "GO", "line": "GO: test"},
                        {"gate": "mutation", "verdict": "NO-GO", "line": "NO-GO: mutation - 2 survived"},
                    ],
                    "seconds": {"mutation": 110, "test": 25},
                    "why": {
                        "attempt": 2,
                        "judge": "gates",
                        "gate": "mutation",
                        "line": "NO-GO: mutation - 2 survived",
                        "details": ["survived: src/ledger/token.py:14"],
                        "sast": None,
                    },
                },
                "rework": [
                    {
                        "at": "2026-11-20T10:00:00Z",
                        "attempt": 1,
                        "judge": "gates",
                        "failed": "secrets",
                        "line": "NO-GO: secrets - potential token literal",
                        "why": {
                            "attempt": 1,
                            "judge": "gates",
                            "gate": "secrets",
                            "line": "NO-GO: secrets - potential token literal",
                            "details": {
                                "ok": False,
                                "error": "withheld: the secrets gate did not say GO on attempt 1",
                            },
                            "sast": None,
                        },
                    }
                ],
                "flow": [],
                "timeline": [
                    {"at": "2026-11-20T10:00:00Z", "phase": "rework", "verdict": "NO-GO", "line": "attempt 1 failed"},
                ],
            },
            {
                "id": "0103",
                "title": "Clarify refund rounding behavior",
                "state": "questions",
                "feature": "refunds",
                "priority": "medium",
                "attempt": None,
                "perimeter": ["src/refunds/*"],
                "adrs": [],
                "wait": None,
                "live": None,
                "questions": [
                    {
                        "asked_at": "2026-11-20T11:00:00Z",
                        "from": "product",
                        "question": "Should half-cent refunds round toward even or up?",
                    }
                ],
                "outcome": {"judge": None, "verdict": "pending", "line": None},
                "ci": None,
                "rework": [],
                "flow": [],
                "timeline": [],
            },
            {
                "id": "0104",
                "title": " Cap retry backoff in worker queue",
                "state": "needs-human",
                "feature": "queue",
                "priority": "low",
                "attempt": 4,
                "perimeter": ["src/queue/*"],
                "adrs": [],
                "wait": None,
                "live": None,
                "questions": [],
                "outcome": {
                    "judge": "review",
                    "verdict": "needs-human",
                    "stop": "cap",
                    "line": "CHANGES-REQUESTED - reached max rework cap",
                },
                "ci": None,
                "rework": [
                    {"at": "2026-11-20T08:00:00Z", "attempt": 1, "judge": "gates", "line": "NO-GO: test"},
                    {"at": "2026-11-20T08:10:00Z", "attempt": 2, "judge": "gates", "line": "NO-GO: sast"},
                    {"at": "2026-11-20T08:20:00Z", "attempt": 3, "judge": "review", "line": "CHANGES-REQUESTED"},
                    {"at": "2026-11-20T08:30:00Z", "attempt": 4, "judge": "review", "line": "CHANGES-REQUESTED"},
                ],
                "flow": [],
                "timeline": [],
            },
        ],
        "releases": [
            {
                "version": "0.4.0-rc1",
                "stable": "0.3.0",
                "image": "img-rc1",
                "ticket": "0101",
                "verdict": "rolled-back",
                "held_at": None,
                "reason": "5%: error_rate 0.085",
                "at": "2026-11-20T09:15:00Z",
                "answer": None,
                "stages": [
                    {
                        "pct": 5,
                        "verdict": "NO-GO: launchpad - 5%: error_rate 0.085 -> rolled back",
                        "started_at": "2026-11-20T09:14:00Z",
                        "judged_at": "2026-11-20T09:15:00Z",
                        "slo": [
                            {
                                "query": "error_rate",
                                "canary": 0.085,
                                "stable": 0.001,
                                "delta": 0.084,
                                "max_delta": 0.01,
                            },
                            {
                                "query": "latency_p95",
                                "canary": 0.19,
                                "stable": 0.12,
                                "delta": 0.07,
                                "max_delta": 0.25,
                            },
                        ],
                    }
                ],
            }
        ],
        "postmortems": [
            {
                "name": "checkout-rollback",
                "title": "Invoice totals 500 under null tax rate",
                "release": {"version": "0.4.0-rc1", "stable": "0.3.0", "verdict": "rolled-back"},
                "causes": [{"cause": "null tax rate divided by zero", "evidence": ["src/billing/totals.py:42"]}],
                "actions": [{"n": 1, "ticket": "0102", "title": "Guard tax calculation"}],
                "timeline": [],
                "run": {"provider": "stub-provider", "model": "stub-tier", "replayed": True},
            }
        ],
        "thresholds": [
            {"key": "ERROR_RATE_MAX_DELTA", "value": "0.01", "judge": "verify", "file": "policy/merge.env", "overridden": False, "provenance": None},
            {"key": ("LATENCY_P95_" + "MAX_DELTA"), "value": "0.25", "judge": "verify", "file": "policy/merge.env", "overridden": False, "provenance": {"kind": "decision", "text": "baseline latency margin"}},
            {"key": "REWORK_MAX_ATTEMPTS", "value": "4", "judge": "rework", "file": "policy/rework.env", "overridden": False, "provenance": {"kind": "decision", "text": "four attempts"}},
        ],
        "launch": {
            "plane": {"stable": "0.3.0", "canary": "0.4.1", "weight": 25},
            "pressable": True,
            "preparable": False,
            "disabled_reason": None,
            "brief": {"author": "release-role", "sha256": "deadbeef", "text": "# Brief 0.4.1"},
            "record": {
                "version": "0.4.1",
                "stable": "0.3.0",
                "held_at": 25,
                "verdict": "held",
                "stages": [
                    {
                        "pct": 5,
                        "verdict": "GO",
                        "slo": [{"query": "error_rate", "canary": 0.001, "stable": 0.001, "delta": 0.0, "max_delta": 0.01}],
                    },
                    {
                        "pct": 25,
                        "verdict": "GO",
                        "slo": [
                            {"query": "error_rate", "canary": 0.002, "stable": 0.001, "delta": 0.001, "max_delta": 0.01},
                            {"query": "latency_p95", "canary": 0.14, "stable": 0.11, "delta": 0.03, "max_delta": 0.25},
                        ],
                    },
                ],
            },
        },
        "dora": {
            "schema": "dora/1",
            "window_days": 30,
            "measures": {
                "deployment_frequency": {"measured": True, "count": 4, "per_day": 0.133},
                "lead_time": {"measured": True, "median_s": 3600, "n": 4},
                "change_fail_rate": {"measured": True, "decided": 5, "failed": 1, "rate": 0.2},
                "recovery_time": {"measured": True, "median_s": 60, "n": 1},
            },
            "lines": ["deployment frequency: 4 promoted to 100%"],
        },
        "spend": {
            "total": {"cost_usd": 2.45, "runs": 5, "unknown": 0},
            "limits": {"SPEND_BUDGET_TICKET_USD": 1.5, "SPEND_BUDGET_DAY_USD": 10.0},
            "burn": {
                "day": "2026-11-20",
                "spent_usd": 2.45,
                "per_hour_usd": 1.225,
                "projected_usd": 14.7,
                "limit_usd": 10.0,
                "over": True,
                "left_h": 10.0,
                "hourly": [0] * 8 + [1.2, 1.25] + [0] * 14,
            },
        },
        "roles": [
            {
                "role": "architect",
                "state": "idle",
                "budget": 600000,
                "used": None,
                "running": None,
                "configured": {"provider": "stub-provider", "model": "stub-tier", "harness": "stub-cli", "thinking": "high"},
                "last": None,
                "line": "has not run in this record",
            },
            {
                "role": "dev-worker",
                "state": "running",
                "budget": 2600000,
                "used": 2210000,
                "running": {"ticket": "0102", "tokens": 2210000, "budget": 2600000, "warn": True},
                "configured": {"provider": "stub-provider", "model": "stub-tier", "harness": "stub-cli", "thinking": "high"},
                "last": None,
                "line": "running on 0102: 2,210,000 of 2,600,000 tokens - near the budget",
            },
        ],
        "intakes": [
            {
                "id": "0200",
                "state": "intake-questions",
                "stranded": None,
                "text": "Support multi-currency invoices",
                "questions": [{"question": "Which ISO currencies in phase 1?"}],
            }
        ],
    }


class TuiReducerAndViewsTest(unittest.TestCase):
    def test_five_views_switch_via_numbers_and_tab_cycle(self) -> None:
        state = tui.make_state(sample_logbook_1_8())
        self.assertEqual(tui.VIEWS[state.view], "overview")

        for idx, expected in enumerate(tui.VIEWS, start=1):
            state = tui.reduce_key(state, str(idx))
            self.assertEqual(tui.VIEWS[state.view], expected)

        state = tui.reduce_key(state, "1")
        visited = []
        for _ in range(5):
            state = tui.reduce_key(state, "TAB")
            visited.append(tui.VIEWS[state.view])
        self.assertEqual(visited, ["tickets", "workers", "gates", "canary", "overview"])

        state = tui.reduce_key(state, "BTAB")
        self.assertEqual(tui.VIEWS[state.view], "canary")

    def test_ticket_navigation_and_live_search_filter(self) -> None:
        state = tui.make_state(sample_logbook_1_8(), view="tickets")
        self.assertEqual(state.selected_ticket_id, "0101")

        state = tui.reduce_key(state, "j")
        self.assertEqual(state.selected_ticket_id, "0102")

        state = tui.reduce_key(state, "DOWN")
        self.assertEqual(state.selected_ticket_id, "0103")

        state = tui.reduce_key(state, "k")
        self.assertEqual(state.selected_ticket_id, "0102")

        state = tui.reduce_key(state, "UP")
        self.assertEqual(state.selected_ticket_id, "0101")

        # Enter live search with '/', type 'ledger', commit with ENTER
        state = tui.reduce_key(state, "/")
        self.assertTrue(state.search_editing)
        for ch in "ledger":
            state = tui.reduce_key(state, ch)
        self.assertEqual(state.search_query, "ledger")
        state = tui.reduce_key(state, "ENTER")
        self.assertFalse(state.search_editing)
        self.assertEqual(state.selected_ticket_id, "0102")

        frame_text = tui.render_frame(state, width=80, height=24).plain_text()
        self.assertIn("0102", frame_text)
        self.assertIn("src/ledger/*", frame_text)

        # Escape clears the active search query when not editing
        state = tui.reduce_key(state, "ESCAPE")
        self.assertEqual(state.search_query, "")

    def test_each_of_five_views_renders_required_domain_facts(self) -> None:
        book = sample_logbook_1_8()

        # View 1: Overview & Waiting-for-You
        f1 = tui.render_frame(tui.make_state(book, view="overview"), width=90, height=28).plain_text()
        self.assertIn("Waiting", f1)
        self.assertIn("0.4.1", f1)  # held launch waiting for human go-for-launch
        self.assertIn("0103", f1)  # open question
        self.assertIn("0104", f1)  # needs-human
        self.assertIn("0200", f1)  # intake-questions
        self.assertIn("Spec", f1)
        self.assertIn("DORA", f1)
        self.assertIn("Spend", f1)

        # View 2: Tickets & Flow Strip
        f2 = tui.render_frame(tui.make_state(book, view="tickets", ticket="0101"), width=90, height=28).plain_text()
        self.assertIn("0101", f2)
        self.assertIn("src/billing/*", f2)
        self.assertIn("Flow", f2)
        self.assertIn("canary", f2.lower())

        # View 3: Workers & Token Burn
        f3 = tui.render_frame(tui.make_state(book, view="workers"), width=90, height=28).plain_text()
        self.assertIn("dev-worker", f3)
        self.assertIn("2,210,000", f3)
        self.assertIn("2,600,000", f3)
        self.assertIn("14.70", f3)  # daily projection
        self.assertIn("OVER", f3)

        # View 4: Gates & Rework (including withheld secret details)
        f4 = tui.render_frame(tui.make_state(book, view="gates", ticket="0102"), width=90, height=28).plain_text()
        self.assertIn("0102", f4)
        self.assertIn("mutation", f4)
        self.assertIn("withheld: the secrets gate did not say GO on attempt 1", f4)

        # View 5: Canary SLO & Ops
        f5 = tui.render_frame(tui.make_state(book, view="canary"), width=90, height=28).plain_text()
        self.assertIn("25%", f5)
        self.assertIn("error_rate", f5)
        self.assertIn("latency_p95", f5)
        self.assertIn("0.01", f5)
        self.assertIn("checkout-rollback", f5)

    def test_strict_80x24_and_tiny_resize_bounds_never_raise_curses_errors(self) -> None:
        book = sample_logbook_1_8()
        for width, height in ((80, 24), (120, 40), (60, 16), (40, 10)):
            for view_name in tui.VIEWS:
                state = tui.make_state(book, view=view_name)
                frame = tui.render_frame(state, width=width, height=height)
                self.assertEqual(len(frame.rows), height)
                for row_idx, row in enumerate(frame.rows):
                    limit = width - 1 if row_idx == height - 1 else width
                    self.assertLessEqual(len(row.text), max(0, limit))
                win = StrictFakeWindow(height=height, width=width)
                tui.draw_curses_frame(win, frame, use_color=False)


class UnreadAndOlderSchemaResilienceTest(unittest.TestCase):
    def test_handles_unread_sections_and_minimal_1_2_schema(self) -> None:
        minimal_1_2 = {
            "schema": "lights-out/logbook/1.2",
            "source": {"commit": None, "level": "L0", "workload": "svc", "code_host": "local"},
            "tickets": {"ok": False, "error": "state store unreachable"},
            "releases": {"ok": False, "error": "releases log unreadable"},
            "postmortems": {"ok": False, "error": "postmortem json corrupt"},
            "thresholds": {"ok": False, "error": "WORKLOAD_CONTRACT not set"},
            "launch": {
                "record": {"ok": False, "error": "release.json corrupt"},
                "plane": {"ok": False, "error": "DELIVERY not set"},
                "brief": None,
                "pressable": False,
                "preparable": False,
                "disabled_reason": "DELIVERY not set",
            },
            "dora": {"ok": False, "error": "dora failed"},
            "spend": {"ok": False, "error": "spend ledger locked"},
            "roles": {"ok": False, "error": "roles unreadable"},
            "intakes": {"ok": False, "error": "intakes unreadable"},
        }
        for view_name in tui.VIEWS:
            state = tui.make_state(minimal_1_2, view=view_name)
            frame = tui.render_frame(state, width=80, height=24)
            win = StrictFakeWindow(height=24, width=80)
            tui.draw_curses_frame(win, frame, use_color=False)

        summary = tui.build_summary_json(tui.make_state(minimal_1_2))
        self.assertIn("tickets", summary["unread_sections"])
        self.assertIn("releases", summary["unread_sections"])
        self.assertIn("thresholds", summary["unread_sections"])


class HttpPollingAndCliModesTest(unittest.TestCase):
    def test_http_source_sends_if_none_match_and_handles_304_and_transient_errors(self) -> None:
        book = sample_logbook_1_8()
        payload = json.dumps(book).encode("utf-8")
        seen_headers: list[dict[str, str]] = []
        step = {"n": 0}

        class FakeResponse:
            def __init__(self, body: bytes, etag: str) -> None:
                self._body = body
                self.status = 200
                self.headers = {"ETag": etag}

            def read(self, _amt: int | None = None) -> bytes:
                return self._body

            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

        def fake_opener(req, timeout=5.0):
            hdrs = {k.lower(): v for k, v in req.header_items()}
            seen_headers.append(hdrs)
            step["n"] += 1
            if step["n"] == 1:
                return FakeResponse(payload, '"etag-v1"')
            if step["n"] == 2:
                msg = email.message.Message()
                msg["ETag"] = '"etag-v1"'
                raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", msg, None)
            if step["n"] == 3:
                raise urllib.error.URLError("connection reset")
            updated = copy.deepcopy(book)
            updated["source"]["commit"] = "9999999"
            return FakeResponse(json.dumps(updated).encode("utf-8"), '"etag-v2"')

        src = tui.HttpSource("http://127.0.0.1:8098", opener=fake_opener)
        self.assertEqual(src.url, "http://127.0.0.1:8098/v1/logbook.json")

        r1 = src.poll()
        self.assertTrue(r1.changed)
        self.assertEqual(r1.etag, '"etag-v1"')
        self.assertIsNone(r1.error)
        self.assertNotIn("if-none-match", seen_headers[0])

        r2 = src.poll()
        self.assertFalse(r2.changed)
        self.assertEqual(r2.etag, '"etag-v1"')
        self.assertIsNone(r2.error)
        self.assertEqual(seen_headers[1].get("if-none-match"), '"etag-v1"')

        r3 = src.poll()
        self.assertFalse(r3.changed)
        self.assertIsNotNone(r3.book)
        self.assertIn("connection reset", r3.error or "")

        r4 = src.poll()
        self.assertTrue(r4.changed)
        self.assertEqual(r4.etag, '"etag-v2"')
        self.assertIsNone(r4.error)
        self.assertEqual(r4.book["source"]["commit"], "9999999")

    def test_rejects_non_http_url_schemes_and_crlf_headers_and_invalid_cli_bounds(self) -> None:
        with self.assertRaises(ValueError):
            tui.HttpSource("file:///etc/passwd")
        with self.assertRaises(ValueError):
            tui.HttpSource("http://127.0.0.1:8098/v1/logbook.json\r\nX-Injected: 1")

        self.assertEqual(
            tui.redact_url_label("https://operator:super-secret@127.0.0.1:8098/v1/logbook.json?token=xyz"),
            "https://operator:***@127.0.0.1:8098/v1/logbook.json?...",
        )

        for bad_argv in (
            ["--poll-s", "nan", "--once"],
            ["--poll-s", "0", "--once"],
            ["--width", "0", "--once"],
            ["--height", "-1", "--once"],
            ["--url", "http://127.0.0.1:8098", "--header", "X-User: a\r\nX-Evil: b", "--once"],
        ):
            err = io.StringIO()
            rc = tui.main(bad_argv, stdin=io.StringIO(""), stdout=io.StringIO(), stderr=err)
            self.assertEqual(rc, 2, f"expected rc=2 for {bad_argv}, got {rc}")

    def test_cli_once_and_json_and_no_color_modes(self) -> None:
        book = sample_logbook_1_8()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "logbook.json"
            path.write_text(json.dumps(book), encoding="utf-8")

            out_once = io.StringIO()
            err_once = io.StringIO()
            with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
                rc = tui.main(
                    ["--logbook", str(path), "--once", "--view", "overview", "--width", "80", "--height", "24"],
                    stdin=io.StringIO(""),
                    stdout=out_once,
                    stderr=err_once,
                )
            self.assertEqual(rc, 0, err_once.getvalue())
            rendered = out_once.getvalue()
            self.assertIsNone(ANSI_ESCAPE_RE.search(rendered))
            self.assertIn("LIGHTS-OUT COCKPIT", rendered)

            out_json = io.StringIO()
            err_json = io.StringIO()
            rc_json = tui.main(
                ["--logbook", str(path), "--json"],
                stdin=io.StringIO(""),
                stdout=out_json,
                stderr=err_json,
            )
            self.assertEqual(rc_json, 0, err_json.getvalue())
            parsed = json.loads(out_json.getvalue())
            self.assertEqual(parsed["schema"], "lights-out/tui-summary/1.0")
            self.assertEqual(parsed["logbook_schema"], "lights-out/logbook/1.8")
            self.assertGreaterEqual(len(parsed["waiting_for_you"]), 3)


if __name__ == "__main__":
    unittest.main()
