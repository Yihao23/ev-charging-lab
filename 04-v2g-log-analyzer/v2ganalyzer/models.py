"""Data model shared by parsers, rules and renderers.
解析器 / 规则 / 渲染器共用的数据模型。

Design note / 设计说明:
    Parsers produce `Event`s and nothing else. Rules read `Event`s and produce
    `Finding`s. Renderers read both. Because no stage knows about the others,
    adding an ISO 15118 parser later does not touch the rules or the renderers.
    解析器只产出 `Event`，规则只消费 `Event` 并产出 `Finding`，渲染器读两者。
    各阶段互不知情，所以以后加一个 ISO 15118 解析器时，规则和渲染器一行都不用改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Kind(str, Enum):
    """What a frame is.  一帧是什么。"""

    CALL = "CALL"              # a request           | 请求
    CALL_RESULT = "CALLRESULT" # a successful reply  | 成功响应
    CALL_ERROR = "CALLERROR"   # a protocol error    | 协议错误
    NOTE = "NOTE"              # free-text log line  | 自由文本日志行


class Direction(str, Enum):
    OUT = "out"  # written by the process that produced the log | 日志产生方发出
    IN = "in"    # received by that process                     | 日志产生方接收


@dataclass
class Event:
    """One protocol frame, or one interesting text line.
    一帧协议消息，或一行值得记录的文本。"""

    ts: datetime
    kind: Kind
    direction: Direction
    action: str = ""                 # BootNotification, CurrentDemandReq, ...
    uid: str = ""                    # message id used for request/response pairing
    payload: dict = field(default_factory=dict)
    raw: str = ""
    line_no: int = 0

    # Filled in by the correlator.  由关联器回填。
    latency_ms: float | None = None
    answered_by: "Event | None" = None

    @property
    def label(self) -> str:
        return self.action or self.kind.value


@dataclass
class Finding:
    """Something a human should look at.  需要人来看一眼的东西。"""

    rule: str          # R001, R002, ...
    severity: str      # error | warning | info
    message: str
    line_no: int = 0
    ts: datetime | None = None

    def __str__(self) -> str:
        where = f"line {self.line_no}" if self.line_no else "-"
        return f"[{self.severity.upper():7}] {self.rule}  {where:>9}  {self.message}"


@dataclass
class Session:
    """Everything parsed out of one log file.  从一个日志文件里解析出的一切。"""

    source: str
    events: list[Event] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def calls(self) -> list[Event]:
        return [e for e in self.events if e.kind is Kind.CALL]

    @property
    def duration_s(self) -> float:
        if len(self.events) < 2:
            return 0.0
        return (self.events[-1].ts - self.events[0].ts).total_seconds()
