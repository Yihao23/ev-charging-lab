"""Parser for OCPP-J logs produced by the `ocpp` Python library.
解析 `ocpp` Python 库产生的 OCPP-J 日志。

Recognised shape / 识别的格式:
    2026-08-19 22:35:10,562  ocpp   CP_1: send [2,"<uid>","BootNotification",{...}]
    2026-08-19 22:35:10,565  ocpp   CP_1: receive message [3,"<uid>",{...}]
    2026-08-19 22:35:11,000  ocpp   CP_1: receive message [4,"<uid>","NotSupported","msg",{}]

OCPP-J frame types / OCPP-J 帧类型 (RFC-style, see OCPP-J spec §4.2):
    [2, uid, action, payload]                  CALL
    [3, uid, payload]                          CALLRESULT
    [4, uid, errorCode, errorDescription, {}]  CALLERROR
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from v2ganalyzer.models import Direction, Event, Kind

LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[,.]\d{3})"
    r"\s+\S+\s+"                                  # logger name | 日志器名
    r"(?P<cp>[\w.\-]+):\s+"                       # charge point id | 桩 id
    r"(?P<dir>send|receive message)\s+"
    r"(?P<frame>\[.*\])\s*$"
)

# Free-text lines worth keeping as context markers.
# 值得作为上下文标记保留的自由文本行。
NOTE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[,.]\d{3})"
    r"\s+\S+\s+(?P<text>state -> .*|connecting to .*|.* disconnected)\s*$"
)


def _ts(raw: str) -> datetime:
    return datetime.strptime(raw.replace(",", ".").replace("T", " "), "%Y-%m-%d %H:%M:%S.%f")


def sniff(lines: list[str]) -> bool:
    """True if at least one line looks like an OCPP-J frame.
    只要有一行看起来像 OCPP-J 帧就返回 True。"""
    return any(LINE.match(ln) for ln in lines[:400])


def parse(lines: list[str]) -> list[Event]:
    events: list[Event] = []
    for n, line in enumerate(lines, start=1):
        line = line.rstrip("\n")

        m = LINE.match(line)
        if m:
            try:
                frame = json.loads(m.group("frame"))
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, list) or not frame:
                continue

            direction = Direction.OUT if m.group("dir") == "send" else Direction.IN
            ts = _ts(m.group("ts"))
            msg_type = frame[0]

            if msg_type == 2 and len(frame) >= 4:
                events.append(Event(ts=ts, kind=Kind.CALL, direction=direction,
                                    action=frame[2], uid=frame[1],
                                    payload=frame[3] if isinstance(frame[3], dict) else {},
                                    raw=line, line_no=n))
            elif msg_type == 3 and len(frame) >= 3:
                events.append(Event(ts=ts, kind=Kind.CALL_RESULT, direction=direction,
                                    uid=frame[1],
                                    payload=frame[2] if isinstance(frame[2], dict) else {},
                                    raw=line, line_no=n))
            elif msg_type == 4 and len(frame) >= 3:
                events.append(Event(ts=ts, kind=Kind.CALL_ERROR, direction=direction,
                                    uid=frame[1],
                                    payload={"errorCode": frame[2],
                                             "errorDescription": frame[3] if len(frame) > 3 else ""},
                                    raw=line, line_no=n))
            continue

        m = NOTE.match(line)
        if m:
            events.append(Event(ts=_ts(m.group("ts")), kind=Kind.NOTE,
                                direction=Direction.OUT, action=m.group("text").strip(),
                                raw=line, line_no=n))

    return events


# ----------------------------------------------------------------------
# TODO(you) — project 04, day 10
# TODO(你) — 项目 04 第 10 天
# ----------------------------------------------------------------------
# 1. Accept a raw pcap of the OCPP WebSocket instead of a text log.
#    `websocket` frames are easy to reassemble with the stdlib once you have
#    the TCP payload; try `tshark -T json -Y websocket` as a first step.
#    支持直接读 OCPP WebSocket 的 pcap 而不是文本日志。拿到 TCP 载荷后用标准库
#    重组 websocket 帧并不难; 先试试 `tshark -T json -Y websocket`。
# 2. Support SteVe's and CitrineOS's own log formats — they differ. Adding a
#    third parser must NOT require touching rules.py or render.py. If it does,
#    your abstraction is wrong.
#    支持 SteVe 和 CitrineOS 各自的日志格式。加第三个解析器时不应该动 rules.py
#    和 render.py —— 如果动了，说明抽象错了。
