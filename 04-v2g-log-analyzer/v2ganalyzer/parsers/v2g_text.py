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

# TODO(you): replace this placeholder with the regex that matches YOUR log.
# TODO(你): 用能匹配**你自己**日志的正则替换这个占位符。
LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[,.]\d{3})"
    r".*?\[(?P<role>SECC|EVCC)\]\s+"
    r"(?P<dir>Sending|Received)\s+"
    r"(?P<msg>\w+?)(?P<rr>Req|Res)\b"
)


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
        if not m:
            continue
        ts = datetime.strptime(
            m.group("ts").replace(",", ".").replace("T", " "), "%Y-%m-%d %H:%M:%S.%f"
        )
        is_request = m.group("rr") == "Req"
        events.append(
            Event(
                ts=ts,
                kind=Kind.CALL if is_request else Kind.CALL_RESULT,
                direction=Direction.OUT if m.group("dir") == "Sending" else Direction.IN,
                action=m.group("msg") if is_request else "",
                uid=m.group("msg"),      # message name doubles as the pairing key
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
