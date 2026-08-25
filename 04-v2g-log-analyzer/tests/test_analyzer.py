"""Tests for the log analyzer.  日志分析器的测试。

Run / 运行:  python -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime
from pathlib import Path

from v2ganalyzer import render, rules
from v2ganalyzer.correlate import correlate
from v2ganalyzer.models import Direction, Finding, Kind, Session
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


class TestR011ResponseCode(unittest.TestCase):
    """ISO 15118 has no CALLERROR frame. A failure is an ordinary `...Res`
    whose ResponseCode is not OK — so `Kind.CALL_ERROR` never appears in a V2G
    log and R002 can never fire on one. Something has to read the field.
    ISO 15118 没有 CALLERROR 这种帧。失败就是一条普通的 `...Res`，只是里面的
    ResponseCode 不是 OK —— 所以 V2G 日志里永远不会出现 `Kind.CALL_ERROR`，
    R002 也就永远报不出东西。总得有人去读那个字段。

    The rule whitelists success rather than blacklisting failure: ISO 15118-2
    defines 4 OK codes against 22 FAILED ones, plus `Failed_NoNegotiation` in
    the handshake schema. A blacklist would go stale the moment a code is added.
    规则用白名单而不是黑名单: ISO 15118-2 定义了 4 个 OK 码、22 个 FAILED 码，
    握手 schema 里还有一个 `Failed_NoNegotiation`。黑名单一加新码就过期。
    """

    @staticmethod
    def _with_code(code):
        line = V2G_RES.replace('"ResponseCode": "OK_NewSessionEstablished"',
                               f'"ResponseCode": "{code}"')
        return [f for f in rules.run(v2g_text.parse([line])) if f.rule == "R011"]

    def test_flags_a_failed_response(self):
        (f,) = self._with_code("FAILED_CertificateExpired")
        self.assertEqual(f.severity, "error")
        self.assertIn("SessionSetupRes", f.message)
        self.assertIn("FAILED_CertificateExpired", f.message)

    def test_flags_the_handshake_spelling_too(self):
        """The AppProtocol schema spells it `Failed_NoNegotiation`, not FAILED_*.
        握手 schema 拼的是 `Failed_NoNegotiation`，不是 FAILED_*。"""
        (f,) = self._with_code("Failed_NoNegotiation")
        self.assertEqual(f.severity, "error")

    def test_silent_on_every_success_code(self):
        for code in ("OK", "OK_NewSessionEstablished", "OK_OldSessionJoined",
                     "OK_SuccessfulNegotiation"):
            with self.subTest(code=code):
                self.assertEqual(self._with_code(code), [])

    def test_certificate_expiring_is_a_warning_not_an_error(self):
        """`OK_CertificateExpiresSoon` is a success — the session proceeds — but
        it is the one OK code that asks the operator to do something.
        `OK_CertificateExpiresSoon` 是成功码，会话照常进行，
        但它是唯一一个要求运营方去做点什么的 OK 码。"""
        (f,) = self._with_code("OK_CertificateExpiresSoon")
        self.assertEqual(f.severity, "warning")

    def test_clean_sessions_produce_no_findings(self):
        for log in ("v2g_session.log", "v2g_session_fault.log"):
            with self.subTest(log=log):
                events = v2g_text.parse((SAMPLES / log).read_text().splitlines())
                self.assertEqual([f for f in rules.run(events) if f.rule == "R011"], [])


class TestR008TruncatedSession(unittest.TestCase):
    """A session that started charging and never said goodbye. This is what the
    driver reports as "the car stopped charging by itself" — they never say
    "SessionStop was missing", so something has to make the translation.
    一次开始充电却没有道别的会话。司机报的是"车自己停止充电了" ——
    没人会说"缺了 SessionStop"，总得有人做这个翻译。
    """

    @staticmethod
    def _r008(path):
        events = v2g_text.parse((SAMPLES / path).read_text().splitlines())
        return [f for f in rules.run(events) if f.rule == "R008"]

    def test_flags_a_session_cut_off_mid_charge(self):
        """samples/v2g_session_truncated.log is the healthy capture cut after the
        last ChargingStatus — what a pulled cable looks like in the log.
        截断样本是把正常抓包在最后一条 ChargingStatus 之后剪断 ——
        拔枪在日志里就长这样。"""
        (f,) = self._r008("v2g_session_truncated.log")
        self.assertEqual(f.severity, "error")
        self.assertIn("ChargingStatus", f.message)

    def test_silent_on_a_session_that_stopped_properly(self):
        for log in ("v2g_session.log", "v2g_session_fault.log"):
            with self.subTest(log=log):
                self.assertEqual(self._r008(log), [])

    def test_silent_when_charging_never_started(self):
        """A session that failed at Authorization never energised anything, so a
        missing SessionStop is not the interesting fact — R011 already reports
        the refusal.
        在 Authorization 就失败的会话根本没有带电，缺 SessionStop 不是重点 ——
        拒绝本身已经由 R011 报告了。"""
        early = v2g_text.parse([V2G_REQ, V2G_RES])
        self.assertEqual([f for f in rules.run(early) if f.rule == "R008"], [])


class TestR010OutOfOrder(unittest.TestCase):
    """ISO 15118-2 fixes the message sequence. A deviation means one end's state
    machine is wrong, which on a real car shows up as "nothing happens when I
    plug in" or "it restarts halfway".
    ISO 15118-2 规定了消息顺序。偏差意味着某一端的状态机实现有问题，
    在真车上表现为"插枪没反应"或"充到一半重来"。
    """

    @staticmethod
    def _r010(events):
        return [f for f in rules.run(events) if f.rule == "R010"]

    def test_the_healthy_ac_session_is_not_flagged(self):
        """The reference list is DC-shaped; an AC session legitimately skips
        CableCheck, PreCharge, CurrentDemand and WeldingDetection. Judging it
        against the DC order would report four deviations that are not.
        参考表是直流形状的；交流会话合法地跳过那四条消息。
        拿直流顺序去判会报出四处根本不存在的"偏差"。"""
        events = v2g_text.parse((SAMPLES / "v2g_session.log").read_text().splitlines())
        self.assertEqual(self._r010(events), [])

    def test_optional_messages_may_be_absent(self):
        """ServiceDetail and PaymentDetails are optional. Absence is not a
        deviation — only wrong *relative* order is.
        ServiceDetail 和 PaymentDetails 是可选的。缺席不算偏差，
        只有**相对顺序**错了才算。"""
        events = v2g_text.parse((SAMPLES / "v2g_session_fault.log").read_text().splitlines())
        self.assertEqual(self._r010(events), [])

    def test_flags_a_swapped_pair(self):
        """Authorization before ServiceDiscovery — both present, wrong order.
        Authorization 排在 ServiceDiscovery 前面 —— 两条都在，顺序反了。"""
        def call(name):
            return V2G_REQ.replace(
                '"SessionSetupReq":{"EVCCID":"D2AA04792E9F"}', f'"{name}":{{}}')
        events = v2g_text.parse([
            call("SessionSetupReq"), call("AuthorizationReq"),
            call("ServiceDiscoveryReq"),
        ])
        (f,) = self._r010(events)
        self.assertEqual(f.severity, "error")
        self.assertIn("ServiceDiscovery", f.message)
        self.assertIn("Authorization", f.message)


class TestHtmlRender(unittest.TestCase):
    """One file you can attach to an email. Interviewers do not run your code.
    一个能直接附在邮件里的文件。面试官不会跑你的代码。
    """

    def _session(self, name="v2g_session_fault.log"):
        from v2ganalyzer.cli import analyse
        return analyse(SAMPLES / name, 2000.0)

    def test_is_a_complete_document(self):
        out = render.html(self._session())
        self.assertTrue(out.startswith("<!DOCTYPE html>"))
        self.assertIn("<html", out)
        self.assertIn("</html>", out)

    def test_is_self_contained(self):
        """No CDN, no external stylesheet, no fetch. It has to open on a laptop
        with no network, from an attachment.
        没有 CDN、没有外部样式表、没有网络请求。它得能在断网的笔记本上、
        从一个附件里直接打开。"""
        out = render.html(self._session())
        for forbidden in ("http://", "https://", "<script src", "<link "):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, out)
        self.assertIn("<style>", out)

    def test_carries_the_findings(self):
        out = render.html(self._session())
        self.assertIn("R007", out)
        self.assertIn("EVSEMaxCurrent=5 A", out)

    def test_escapes_html_in_payloads(self):
        """Log content is untrusted input. A message containing markup must not
        become markup.
        日志内容是外部输入。消息里的尖括号不能变成标签。"""
        session = self._session()
        session.findings.append(
            Finding("R999", "error", "<script>alert(1)</script>", line_no=1))
        out = render.html(session)
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_cli_accepts_the_format(self):
        import tempfile
        from v2ganalyzer.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.html"
            rc = main([str(SAMPLES / "v2g_session_fault.log"),
                       "--format", "html", "-o", str(target)])
            self.assertEqual(rc, 0)
            self.assertIn("<!DOCTYPE html>", target.read_text())
