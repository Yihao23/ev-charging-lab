"""Parser for ISO 15118 (V2G) session logs.
解析 ISO 15118 (V2G) 会话日志。

Status / 状态: SKELETON — this is your project-04 homework.
                骨架 —— 这是项目 04 的作业。

Why it is left unfinished / 为什么留白:
    There is no single V2G log format. Each stack prints its own. You will
    only know the right regex after you have run a real session in project 03
    and looked at what it actually prints. Writing the parser against a log
    you generated yourself is exactly the skill the job description calls
    "Analyse von Logdaten sowie Fehlerdiagnose".
    V2G 日志没有统一格式，每个协议栈各印各的。只有在项目 03 里真跑过一次会话、
    亲眼看过它到底打印什么之后，你才知道正则该怎么写。针对自己产生的日志写解析器，
    正是 JD 里"日志分析与故障诊断"这一条要的能力。

Suggested source / 建议的日志来源:
    EcoG-io/iso15118 SECC + EVCC, run locally (see ../03-iso15118-analysis).
    Its logger prints one line per V2G message, e.g.
        [SECC] Received SessionSetupReq
        [SECC] Sending SessionSetupRes
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from v2ganalyzer.models import Direction, Event, Kind

# The ISO 15118-2 request/response pairs, in the order a DC session uses them.
# ISO 15118-2 的请求/响应对，按直流会话的实际顺序排列。
# Keep this list — rules.py uses it to detect an out-of-order or truncated
# session, which is the most common real-world V2G failure.
# 保留这个列表 —— rules.py 用它来检测乱序或截断的会话，这是最常见的真实 V2G 故障。
ISO15118_2_DC_ORDER = [
    "SupportedAppProtocol",
    "SessionSetup",
    "ServiceDiscovery",
    "ServiceDetail",
    "PaymentServiceSelection",
    "PaymentDetails",
    "Authorization",
    "ChargeParameterDiscovery",
    "CableCheck",
    "PreCharge",
    "PowerDelivery",
    "CurrentDemand",
    "WeldingDetection",
    "SessionStop",
]

# Matches the two lines EcoG-io/iso15118 prints per message. It deliberately
# targets the exi_codec lines rather than the shorter comm_session ones
# ("SessionSetupReq received"), because only these carry the decoded payload —
# and the payload is what rules like R007 need. The regex only has to split the
# line; json.loads does the rest.
# 匹配 EcoG-io/iso15118 每条消息打印的两行。刻意选 exi_codec 那两行，而不是更短的
# comm_session 行("SessionSetupReq received")，因为只有它们带解码后的载荷 ——
# 而载荷正是 R007 这类规则需要的。正则只负责把行切开，剩下的交给 json.loads。
#
#   INFO    2026-08-23 08:03:22,463 - iso15118.shared.exi_codec (302): \
#       Decoded message (ns=urn:iso:15118:2:2013:MsgDef): {"V2G_Message":{...}}
#   INFO    2026-08-23 08:03:22,464 - iso15118.shared.exi_codec (248): \
#       Message to encode (ns=urn:iso:15118:2:2013:MsgDef): {"V2G_Message": {...}}
LINE = re.compile(
    r"^\w+\s+"                                                   # INFO / DEBUG / WARNING
    r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[,.]\d{3})"
    r".*?exi_codec \(\d+\): "                                    # skip the logger prefix
    r"(?P<dir>Decoded message|Message to encode) "                # received vs sent
    r"\(ns=(?P<ns>[^)]+)\): "                                     # which schema
    r"(?P<json>\{.*)$"                                            # the whole payload
)

# The log carries a third namespace, xmldsig, for the SalesTariff signature.
# Those lines are not V2G messages and must not become events.
# 日志里还有第三个命名空间 xmldsig，用于 SalesTariff 的签名。
# 那些行不是 V2G 消息，不能变成 Event。
V2G_NAMESPACES = frozenset({
    "urn:iso:15118:2:2013:MsgDef",
    "urn:iso:15118:2:2010:AppProtocol",
})


def sniff(lines: list[str]) -> bool:
    return any(LINE.search(ln) for ln in lines[:400])


def parse(lines: list[str]) -> list[Event]:
    """Map V2G messages onto the same Event model as OCPP.
    把 V2G 消息映射到与 OCPP 相同的 Event 模型上。

    The trick / 技巧:
        A `...Req` is a CALL and the matching `...Res` is a CALL_RESULT, so
        every rule and renderer written for OCPP works unchanged on V2G.
        Pair them by message name, since V2G has no message id.
        `...Req` 当作 CALL，对应的 `...Res` 当作 CALL_RESULT，这样为 OCPP 写的
        所有规则和渲染器都能原封不动地用在 V2G 上。V2G 没有消息 id，所以用消息名配对。
    """
    events: list[Event] = []
    for n, line in enumerate(lines, start=1):
        m = LINE.search(line)
        if not m or m.group("ns") not in V2G_NAMESPACES:
            continue

        # A truncated line is a torn log, not a protocol error — skip it rather
        # than take the whole file down.
        # 截断的行是日志被截断了，不是协议错误 —— 跳过，不要让整个文件解析失败。
        try:
            doc = json.loads(m.group("json"))
        except json.JSONDecodeError:
            continue

        ts = datetime.strptime(
            m.group("ts").replace(",", ".").replace("T", " "), "%Y-%m-%d %H:%M:%S.%f"
        )
        # Session messages are wrapped in V2G_Message/Body; the two handshake
        # messages are not, and carry the message name as the top-level key.
        # 会话消息包在 V2G_Message/Body 里；两条握手消息没有外壳，
        # 消息名就是顶层的键。
        body = doc["V2G_Message"]["Body"] if "V2G_Message" in doc else doc
        inbound = m.group("dir") == "Decoded message"

        for name, payload in body.items():
            # SalesTariff and its signature are encoded separately for signing
            # and share the V2G namespace, so the namespace filter above does
            # not catch them. Only Req/Res are messages.
            # SalesTariff 和它的签名是为签名单独编码的，且共用 V2G 命名空间，
            # 上面的命名空间过滤挡不住它们。只有 Req/Res 才是消息。
            if not name.endswith(("Req", "Res")):
                continue
            is_request = name.endswith("Req")
            events.append(
                Event(
                    ts=ts,
                    kind=Kind.CALL if is_request else Kind.CALL_RESULT,
                    # The log is written by whichever end we captured, so what
                    # it decoded is what it received.
                    # 日志由被抓的那一端产生，它解码的就是它收到的。
                    direction=Direction.IN if inbound else Direction.OUT,
                    action=name,
                    uid=name[:-3],  # drop Req/Res — the bare name is the pairing key
                    payload=payload if isinstance(payload, dict) else {},
                    raw=line.rstrip("\n"),
                    line_no=n,
                )
            )
    return events


# ----------------------------------------------------------------------
# TODO(you) — project 04, day 11-12
# ----------------------------------------------------------------------
# 1. Extract the ResponseCode from each ...Res. Anything that is not OK_*
#    is a finding. This alone makes the tool genuinely useful.
#    从每条 ...Res 里提取 ResponseCode，非 OK_* 的都算问题。仅此一项就能让
#    工具真正有用。
# 2. Extract EVSEMaximumCurrentLimit / EVTargetCurrent from CurrentDemand and
#    plot the negotiated vs requested current over the session. That plot is
#    the single most convincing artefact you can bring to the interview.
#    从 CurrentDemand 里提取双方的电流值，画出整个会话中"车请求 vs 桩允许"的曲线。
#    这张图是你能带进面试的最有说服力的东西。
# 3. Feed EXI hex blobs to the OpenV2Gx CLI decoder and inline the decoded XML:
#    https://github.com/uhi22/OpenV2Gx
#    把 EXI 十六进制块喂给 OpenV2Gx 的命令行解码器，把解出的 XML 内联进报告。
