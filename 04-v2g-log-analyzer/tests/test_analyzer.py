"""Tests for the log analyzer.  日志分析器的测试。

Run / 运行:  python -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime
from pathlib import Path

from v2ganalyzer import render, rules
from v2ganalyzer.correlate import correlate
from v2ganalyzer.models import Direction, Kind, Session
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


# ----------------------------------------------------------------------
# Project 04, day 11 — the V2G parser and rule R007.
# 项目 04，第 11 天 —— V2G 解析器与规则 R007。
#
# These are written failing-first against real logs captured in project 03.
# `v2g_text.parse` is still a skeleton whose regex matches zero lines of them,
# so every test below fails until it is rewritten.
# 这些测试是对着项目 03 抓到的真实日志、以失败优先的方式写的。
# `v2g_text.parse` 目前的正则在这些日志上匹配 0 行，所以下面每个测试都会失败，
# 直到解析器被重写为止。
# ----------------------------------------------------------------------

from v2ganalyzer.parsers import v2g_text  # noqa: E402

V2G_REQ = (
    'INFO    2026-08-23 08:03:22,463 - iso15118.shared.exi_codec (302): '
    'Decoded message (ns=urn:iso:15118:2:2013:MsgDef): '
    '{"V2G_Message":{"Header":{"SessionID":"00"},'
    '"Body":{"SessionSetupReq":{"EVCCID":"D2AA04792E9F"}}}}'
)
V2G_RES = (
    'INFO    2026-08-23 08:03:22,464 - iso15118.shared.exi_codec (248): '
    'Message to encode (ns=urn:iso:15118:2:2013:MsgDef): '
    '{"V2G_Message": {"Header": {"SessionID": "3DEF33CB99939822"}, '
    '"Body": {"SessionSetupRes": {"ResponseCode": "OK_NewSessionEstablished", '
    '"EVSEID": "UK123E1234"}}}}'
)
# The handshake carries no V2G_Message envelope — the message name is the
# top-level key. Both shapes have to parse.
# 握手没有 V2G_Message 外壳，消息名就是顶层的键。两种形状都要能解析。
V2G_HANDSHAKE = (
    'INFO    2026-08-23 08:03:22,284 - iso15118.shared.exi_codec (302): '
    'Decoded message (ns=urn:iso:15118:2:2010:AppProtocol): '
    '{"supportedAppProtocolReq":{"AppProtocol":[{"ProtocolNamespace":'
    '"urn:iso:15118:2:2013:MsgDef","VersionNumberMajor":2,'
    '"VersionNumberMinor":0,"SchemaID":1,"Priority":1}]}}'
)
# Not every "Message to encode" line is a protocol message: the SalesTariff and
# its XML signature are encoded separately for signing.
# 不是每条 "Message to encode" 都是协议消息：SalesTariff 和它的 XML 签名
# 是为了签名而单独编码的。
V2G_FRAGMENT = (
    'INFO    2026-08-23 08:03:22,873 - iso15118.shared.exi_codec (248): '
    'Message to encode (ns=urn:iso:15118:2:2013:MsgDef): '
    '{"SalesTariff": {"Id": "id1", "SalesTariffID": 10}}'
)
V2G_XMLDSIG = (
    'INFO    2026-08-23 08:03:22,911 - iso15118.shared.exi_codec (248): '
    'Message to encode (ns=http://www.w3.org/2000/09/xmldsig#): '
    '{"SignedInfo": {"CanonicalizationMethod": {}}}'
)
V2G_NOISE = (
    'INFO    2026-08-23 08:03:22,875 - iso15118.shared.states (139): '
    'Entered state PowerDelivery'
)


class TestV2GParser(unittest.TestCase):
    def test_parses_decoded_request(self):
        (e,) = v2g_text.parse([V2G_REQ])
        self.assertIs(e.kind, Kind.CALL)
        self.assertEqual(e.action, "SessionSetupReq")
        self.assertEqual(e.ts, datetime(2026, 8, 23, 8, 3, 22, 463000))
        self.assertEqual(e.payload["EVCCID"], "D2AA04792E9F")

    def test_decoded_is_inbound_encoded_is_outbound(self):
        """The log is the SECC's, so "Decoded" is what it received.
        日志是桩产生的，所以 "Decoded" 是它收到的。"""
        (req,) = v2g_text.parse([V2G_REQ])
        (res,) = v2g_text.parse([V2G_RES])
        self.assertIs(req.direction, Direction.IN)
        self.assertIs(res.direction, Direction.OUT)

    def test_response_carries_its_response_code(self):
        (e,) = v2g_text.parse([V2G_RES])
        self.assertIs(e.kind, Kind.CALL_RESULT)
        self.assertEqual(e.payload["ResponseCode"], "OK_NewSessionEstablished")

    def test_uid_drops_the_req_res_suffix_so_correlate_can_pair(self):
        """V2G has no message id, so the bare message name is the pairing key.
        V2G 没有消息 id，所以去掉后缀的消息名就是配对键。"""
        (req,) = v2g_text.parse([V2G_REQ])
        (res,) = v2g_text.parse([V2G_RES])
        self.assertEqual(req.uid, "SessionSetup")
        self.assertEqual(req.uid, res.uid)

    def test_correlate_pairs_a_v2g_request_with_its_response(self):
        events = v2g_text.parse([V2G_REQ, V2G_RES])
        unanswered = correlate(events)
        self.assertEqual(unanswered, [])
        self.assertAlmostEqual(events[0].latency_ms, 1.0, places=3)

    def test_parses_handshake_without_the_v2g_message_envelope(self):
        (e,) = v2g_text.parse([V2G_HANDSHAKE])
        self.assertEqual(e.action, "supportedAppProtocolReq")
        self.assertIs(e.kind, Kind.CALL)
        self.assertEqual(e.payload["AppProtocol"][0]["SchemaID"], 1)

    def test_skips_signing_fragments_and_xmldsig(self):
        self.assertEqual(v2g_text.parse([V2G_FRAGMENT, V2G_XMLDSIG]), [])

    def test_skips_state_machine_noise(self):
        self.assertEqual(v2g_text.parse([V2G_NOISE]), [])

    def test_malformed_json_does_not_crash(self):
        bad = V2G_REQ[: V2G_REQ.index("{") + 20]
        self.assertEqual(v2g_text.parse([bad]), [])

    def test_sniff_recognises_the_format(self):
        self.assertTrue(v2g_text.sniff([V2G_NOISE, V2G_REQ]))
        self.assertFalse(v2g_text.sniff([CALL, RESULT]))

    def test_autodetect_picks_v2g(self):
        self.assertIs(autodetect([V2G_REQ]), v2g_text)

    def test_real_session_yields_one_event_per_message(self):
        """19 requests in, 19 responses out — the two signing fragments and the
        xmldsig line must not become events.
        收 19 条请求、发 19 条响应；两条签名片段和 xmldsig 那行不能变成 Event。"""
        lines = (SAMPLES / "v2g_session.log").read_text().splitlines()
        events = v2g_text.parse(lines)
        self.assertEqual(len(events), 38)
        self.assertEqual(sum(1 for e in events if e.kind is Kind.CALL), 19)
        self.assertEqual(correlate(events), [])


class TestR007PowerLimitMismatch(unittest.TestCase):
    """The station states its ceiling twice — as PMax in the SAScheduleList and
    as EVSEMaxCurrent in AC_EVSEChargeParameter — and ISO 15118-2 sets no
    precedence between them. Nothing in the protocol catches a disagreement, so
    the rule has to. It is a whole-session rule: the phase count lives in the
    *request*, the limits in the *response*.
    桩把自己的上限说了两遍 —— SAScheduleList 里的 PMax 和 AC_EVSEChargeParameter
    里的 EVSEMaxCurrent —— 而规范没有规定谁优先。协议本身抓不到矛盾，只能靠规则。
    它是一条跨消息规则: 相数在**请求**里，限值在**响应**里。
    """

    @staticmethod
    def _session(path):
        lines = (SAMPLES / path).read_text().splitlines()
        events = v2g_text.parse(lines)
        correlate(events)
        return events

    def _r007(self, events):
        return [f for f in rules.run(events) if f.rule == "R007"]

    def test_flags_the_injected_fault(self):
        """5 A at 400 V three-phase is 3.46 kW against a scheduled 11 kW.
        三相 400 V 下 5 A 是 3.46 kW，而计划是 11 kW。"""
        found = self._r007(self._session("v2g_session_fault.log"))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "error")
        self.assertIn("11000", found[0].message)
        self.assertIn("3464", found[0].message)

    def test_flags_the_healthy_session_too(self):
        """The contradiction predates the injected fault: 32 A three-phase is
        22.2 kW, still double the scheduled 11 kW. A rule that only fires on the
        broken log would have been fitted to it.
        矛盾在注入故障之前就存在: 三相 32 A 是 22.2 kW，仍是计划 11 kW 的两倍。
        一条只在故障日志上报警的规则，说明它是照着那份日志凑出来的。"""
        found = self._r007(self._session("v2g_session.log"))
        self.assertEqual(len(found), 1)
        self.assertIn("22170", found[0].message)

    def test_silent_when_the_two_limits_agree(self):
        req = V2G_REQ.replace(
            '"SessionSetupReq":{"EVCCID":"D2AA04792E9F"}',
            '"ChargeParameterDiscoveryReq":{"RequestedEnergyTransferMode":'
            '"AC_three_phase_core"}',
        )
        res = V2G_RES.replace(
            '"SessionSetupRes": {"ResponseCode": "OK_NewSessionEstablished", '
            '"EVSEID": "UK123E1234"}',
            '"ChargeParameterDiscoveryRes": {"ResponseCode": "OK", '
            '"SAScheduleList": {"SAScheduleTuple": [{"PMaxSchedule": '
            '{"PMaxScheduleEntry": [{"PMax": {"Value": 22170, "Multiplier": 0, '
            '"Unit": "W"}}]}}]}, "AC_EVSEChargeParameter": '
            '{"EVSENominalVoltage": {"Value": 400, "Multiplier": 0, "Unit": "V"}, '
            '"EVSEMaxCurrent": {"Value": 32, "Multiplier": 0, "Unit": "A"}}}',
        )
        self.assertEqual(self._r007(v2g_text.parse([req, res])), [])
