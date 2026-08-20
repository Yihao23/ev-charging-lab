"""Tests for the log analyzer.  日志分析器的测试。

Run / 运行:  python -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime
from pathlib import Path

from v2ganalyzer import render, rules
from v2ganalyzer.correlate import correlate
from v2ganalyzer.models import Kind, Session
from v2ganalyzer.parsers import autodetect, ocpp_json

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

CALL = ('2026-08-19 09:00:00,100  ocpp   CP_7: send '
        '[2,"a1","BootNotification",{"chargePointVendor":"acme"}]')
RESULT = ('2026-08-19 09:00:00,140  ocpp   CP_7: receive message '
          '[3,"a1",{"status":"Accepted","interval":30}]')
ERROR = ('2026-08-19 09:00:01,000  ocpp   CP_7: receive message '
         '[4,"a2","NotSupported","nope",{}]')


class TestParser(unittest.TestCase):
    def test_parses_call(self):
        (e,) = ocpp_json.parse([CALL])
        self.assertIs(e.kind, Kind.CALL)
        self.assertEqual(e.action, "BootNotification")
        self.assertEqual(e.uid, "a1")
        self.assertEqual(e.ts, datetime(2026, 8, 19, 9, 0, 0, 100000))

    def test_parses_call_error(self):
        (e,) = ocpp_json.parse([ERROR])
        self.assertIs(e.kind, Kind.CALL_ERROR)
        self.assertEqual(e.payload["errorCode"], "NotSupported")

    def test_ignores_noise(self):
        self.assertEqual(ocpp_json.parse(["not a log line at all", ""]), [])

    def test_malformed_json_does_not_crash(self):
        bad = '2026-08-19 09:00:00,100  ocpp   CP_7: send [2,"a1",'
        self.assertEqual(ocpp_json.parse([bad]), [])

    def test_autodetect_picks_ocpp(self):
        self.assertIs(autodetect([CALL]), ocpp_json)

    def test_autodetect_returns_none_for_garbage(self):
        self.assertIsNone(autodetect(["hello", "world"]))


class TestCorrelate(unittest.TestCase):
    def test_pairs_and_measures_latency(self):
        events = ocpp_json.parse([CALL, RESULT])
        unanswered = correlate(events)
        self.assertEqual(unanswered, [])
        self.assertAlmostEqual(events[0].latency_ms, 40.0, places=3)

    def test_reports_unanswered(self):
        events = ocpp_json.parse([CALL])
        self.assertEqual([e.uid for e in correlate(events)], ["a1"])


class TestRules(unittest.TestCase):
    def _findings(self, path):
        lines = path.read_text().splitlines()
        events = ocpp_json.parse(lines)
        correlate(events)
        return events, rules.run(events, {"slow_ms": 2000.0})

    def test_clean_session_has_no_errors(self):
        """The captured happy-path session must come back clean. If this ever
        fails, either the station regressed or a rule is too eager.
        录制的正常会话必须是干净的。这条测试挂了，要么桩出了回归，要么规则太激进。"""
        _, findings = self._findings(SAMPLES / "ocpp_session.log")
        self.assertEqual([f for f in findings if f.severity == "error"], [])

    def test_faulty_session_triggers_every_rule(self):
        _, findings = self._findings(SAMPLES / "ocpp_session_faulty.log")
        fired = {f.rule for f in findings}
        for rule in ("R001", "R002", "R003", "R004", "R005", "R006"):
            with self.subTest(rule=rule):
                self.assertIn(rule, fired)

    def test_illegal_transition_is_named(self):
        _, findings = self._findings(SAMPLES / "ocpp_session_faulty.log")
        r006 = [f for f in findings if f.rule == "R006"]
        self.assertIn("Available -> Charging", r006[0].message)


class TestRender(unittest.TestCase):
    def setUp(self):
        events = ocpp_json.parse([CALL, RESULT])
        correlate(events)
        self.session = Session(source="t.log", events=events)
        self.session.findings = rules.run(events, {})

    def test_mermaid_is_a_sequence_diagram(self):
        out = render.mermaid(self.session)
        self.assertTrue(out.startswith("sequenceDiagram"))
        self.assertIn("A->>B: BootNotification", out)

    def test_markdown_contains_every_section(self):
        out = render.markdown(self.session)
        for heading in ("## Summary", "## Findings", "## Timeline", "```mermaid"):
            self.assertIn(heading, out)

    def test_json_round_trips(self):
        import json
        data = json.loads(render.as_json(self.session))
        self.assertEqual(data["events"][0]["action"], "BootNotification")


if __name__ == "__main__":
    unittest.main()
