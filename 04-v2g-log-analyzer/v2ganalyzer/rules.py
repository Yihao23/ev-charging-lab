"""Anomaly rules. Each rule is a small pure function over the event list.
异常检测规则。每条规则都是作用于事件列表的小纯函数。

Adding a rule means appending one function to RULES — nothing else.
新增规则 = 往 RULES 里追加一个函数，仅此而已。
"""

from __future__ import annotations

from v2ganalyzer.correlate import correlate
from v2ganalyzer.models import Event, Finding, Kind

# Status values that mean "the other side said no".
# 表示"对方拒绝了"的状态值。
BAD_STATUS = {
    "Rejected", "Invalid", "Blocked", "Expired", "Faulted",
    "NotSupported", "ConcurrentTx", "Unknown",
}

# OCPP 1.6 connector status transitions that are legal.
# OCPP 1.6 合法的接口状态迁移。
# Duplicated from project 01 on purpose — a diagnostic tool must not depend on
# the implementation it is diagnosing.
# 刻意从项目 01 复制而非 import —— 诊断工具不能依赖它所诊断的实现。
LEGAL_TRANSITIONS = {
    "Available": {"Preparing", "Reserved", "Unavailable", "Faulted"},
    "Preparing": {"Charging", "Available", "Finishing", "Faulted"},
    "Charging": {"SuspendedEV", "SuspendedEVSE", "Finishing", "Faulted"},
    "SuspendedEV": {"Charging", "SuspendedEVSE", "Finishing", "Faulted"},
    "SuspendedEVSE": {"Charging", "SuspendedEV", "Finishing", "Faulted"},
    "Finishing": {"Available", "Faulted"},
    "Reserved": {"Available", "Preparing", "Faulted"},
    "Unavailable": {"Available", "Faulted"},
    "Faulted": {"Available"},
}


def r001_unanswered_calls(events: list[Event], cfg: dict) -> list[Finding]:
    """A request that never got a reply. In OCPP this eventually trips the
    30 s message timeout and drops the session.
    发出后没有收到响应的请求。在 OCPP 里这最终会触发 30 秒超时并断开会话。"""
    return [
        Finding("R001", "error", f"{c.label} (uid={c.uid[:8]}) was never answered",
                line_no=c.line_no, ts=c.ts)
        for c in correlate(events)
    ]


def r002_call_errors(events: list[Event], cfg: dict) -> list[Finding]:
    """The peer replied with a protocol-level error.  对端回了协议级错误。"""
    out = []
    for e in events:
        if e.kind is Kind.CALL_ERROR:
            out.append(Finding(
                "R002", "error",
                f"CALLERROR {e.payload.get('errorCode')}: "
                f"{e.payload.get('errorDescription')}",
                line_no=e.line_no, ts=e.ts))
    return out


def r003_rejections(events: list[Event], cfg: dict) -> list[Finding]:
    """A reply carried a refusing status. This is the rule that would have
    caught our own `SetChargingProfile -> Rejected` bug in project 01.
    响应里带了拒绝性状态。项目 01 里我们自己的 `SetChargingProfile -> Rejected`
    这个 bug，就是被这条规则抓到的。"""
    out = []
    for e in events:
        if e.kind is not Kind.CALL_RESULT:
            continue
        for key, value in _walk(e.payload):
            if isinstance(value, str) and value in BAD_STATUS:
                what = e.action or "response"
                out.append(Finding("R003", "warning",
                                   f"{what} carries {key}={value}",
                                   line_no=e.line_no, ts=e.ts))
    return out


def r004_slow_responses(events: list[Event], cfg: dict) -> list[Finding]:
    """Latency above the threshold. On a real station this usually means the
    backend link, not the station, is the bottleneck.
    时延超阈值。在真实充电桩上这通常意味着瓶颈在后台链路而不是桩本身。"""
    threshold = cfg.get("slow_ms", 2000.0)
    correlate(events)
    out = []
    for e in events:
        if e.kind is Kind.CALL and e.latency_ms is not None and e.latency_ms > threshold:
            out.append(Finding("R004", "warning",
                               f"{e.label} took {e.latency_ms:.0f} ms (> {threshold:.0f} ms)",
                               line_no=e.line_no, ts=e.ts))
    return out


def r005_heartbeat_gaps(events: list[Event], cfg: dict) -> list[Finding]:
    """Heartbeats stopped for longer than the negotiated interval allows.
    心跳间隔超过协商值允许的范围。

    The interval comes from the BootNotification response, so the rule
    configures itself from the log instead of a hard-coded constant.
    间隔取自 BootNotification 的响应 —— 规则从日志里自我配置，而不是写死常量。
    """
    interval = None
    for e in events:
        if e.kind is Kind.CALL_RESULT and "interval" in e.payload:
            interval = float(e.payload["interval"])
            break
    if not interval:
        return []

    beats = [e for e in events if e.kind is Kind.CALL and e.action == "Heartbeat"]
    out = []
    for prev, cur in zip(beats, beats[1:]):
        gap = (cur.ts - prev.ts).total_seconds()
        if gap > interval * 2:
            out.append(Finding("R005", "warning",
                               f"heartbeat gap of {gap:.0f}s (interval is {interval:.0f}s)",
                               line_no=cur.line_no, ts=cur.ts))
    return out


def r006_illegal_state_transitions(events: list[Event], cfg: dict) -> list[Finding]:
    """A StatusNotification sequence the OCPP 1.6 state model forbids.
    OCPP 1.6 状态模型禁止的 StatusNotification 序列。

    Catching this from the outside is powerful: it finds the bug even when the
    station's own code believes it is behaving.
    从外部抓这个很有威力: 即便桩自己的代码认为一切正常，这条规则也能发现问题。
    """
    out = []
    per_connector: dict[int, str] = {}
    for e in events:
        if e.kind is not Kind.CALL or e.action != "StatusNotification":
            continue
        cid = e.payload.get("connectorId", e.payload.get("connector_id", 0))
        status = e.payload.get("status", "")
        prev = per_connector.get(cid)
        if prev and status != prev and status not in LEGAL_TRANSITIONS.get(prev, set()):
            out.append(Finding("R006", "error",
                               f"connector {cid}: illegal transition {prev} -> {status}",
                               line_no=e.line_no, ts=e.ts))
        per_connector[cid] = status
    return out


RULES = [
    r001_unanswered_calls,
    r002_call_errors,
    r003_rejections,
    r004_slow_responses,
    r005_heartbeat_gaps,
    r006_illegal_state_transitions,
]


def run(events: list[Event], cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or {}
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(events, cfg))
    findings.sort(key=lambda f: (f.ts or _MIN, f.rule))
    return findings


def _walk(obj, prefix=""):
    """Yield (dotted_key, value) for every leaf in a nested dict/list.
    遍历嵌套结构，产出每个叶子的 (点分键, 值)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


from datetime import datetime as _dt  # noqa: E402
_MIN = _dt.min


# ----------------------------------------------------------------------
# TODO(you) — project 04, day 11
# ----------------------------------------------------------------------
# R007  Out-of-order V2G session: compare the observed message order against
#       parsers.v2g_text.ISO15118_2_DC_ORDER and report the first deviation.
#       V2G 会话乱序: 把观察到的消息顺序与 ISO15118_2_DC_ORDER 比对，报告第一处偏差。
# R008  Truncated session: a session that reaches CurrentDemand but never
#       SessionStop ended abnormally — the most common real complaint
#       ("the car stopped charging by itself").
#       会话截断: 走到 CurrentDemand 却没有 SessionStop = 异常结束，
#       这正是最常见的真实投诉("车自己停止充电了")。
# R009  Energy sanity: integrate Power.Active.Import over time and compare with
#       the Energy.Active.Import.Register delta. A mismatch means a meter or a
#       unit bug — and unit bugs in this domain are expensive.
#       能量自洽: 对功率积分并与电表读数差值比较。不一致意味着电表问题或单位 bug ——
#       这个领域的单位 bug 代价很高。
