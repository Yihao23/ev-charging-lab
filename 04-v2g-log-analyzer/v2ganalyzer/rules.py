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


def _phys(value) -> float | None:
    """An ISO 15118 PhysicalValue as a plain number.
    把 ISO 15118 的 PhysicalValue 变成普通数字。

    Every physical quantity on the wire is `Value * 10**Multiplier` — there are
    no floats in EXI, which is exactly why the multiplier exists. Reading Value
    alone turns the car's 32 A into 32000 A.
    线路上每个物理量都是 `Value * 10**Multiplier` —— EXI 里没有浮点数，
    这正是 multiplier 存在的理由。只读 Value 会把车报的 32 A 读成 32000 A。
    """
    if not isinstance(value, dict) or "Value" not in value:
        return None
    try:
        return float(value["Value"]) * (10 ** int(value.get("Multiplier", 0)))
    except (TypeError, ValueError):
        return None


def r007_power_limit_mismatch(events: list[Event], cfg: dict) -> list[Finding]:
    """The station states its ceiling twice and the two disagree.
    桩把自己的上限说了两遍，而两遍对不上。

    ChargeParameterDiscoveryRes carries PMax inside the SAScheduleList and
    EVSEMaxCurrent inside AC_EVSEChargeParameter. ISO 15118-2 sets no precedence
    between them and nothing in the protocol notices when they contradict each
    other, so a car is free to believe whichever it happens to read. In the
    captured sessions it reads PMax and ignores the current entirely — which
    means the station's current limit is a suggestion, and the only thing left
    protecting the feeder is the upstream breaker.
    ChargeParameterDiscoveryRes 里，PMax 在 SAScheduleList 中，EVSEMaxCurrent 在
    AC_EVSEChargeParameter 中。ISO 15118-2 没有规定谁优先，协议也不会在两者矛盾时
    报错，所以车爱信哪个信哪个。在抓到的会话里它只读 PMax、完全忽略电流 ——
    也就是说桩的电流限值只是个建议，剩下的唯一保护是上级断路器。

    Whole-session rule: the phase count comes from the request, the limits from
    the response, so no single log line can express it.
    跨消息规则: 相数来自请求，限值来自响应，任何单独一行日志都表达不了。
    """
    out: list[Finding] = []

    # Line voltage on three phases, so P = sqrt(3) * U * I. Single phase is
    # P = U * I. The mode is only ever stated in the request.
    # 三相报的是线电压，所以 P = sqrt(3) * U * I；单相是 P = U * I。
    # 能量传输模式只在请求里出现过。
    factor = None
    for e in events:
        if e.action == "ChargeParameterDiscoveryReq":
            mode = e.payload.get("RequestedEnergyTransferMode", "")
            factor = 3 ** 0.5 if "three_phase" in mode else 1.0
            break
    if factor is None:
        return out

    tolerance = cfg.get("power_tolerance", 0.1)
    for e in events:
        if e.action != "ChargeParameterDiscoveryRes":
            continue

        # SAScheduleList is minOccurs="0" in the XSD, and a DC session has no
        # AC_EVSEChargeParameter at all. Missing data is not a finding.
        # SAScheduleList 在 XSD 里是 minOccurs="0"，直流会话则根本没有
        # AC_EVSEChargeParameter。数据缺失不算问题。
        tuples = (e.payload.get("SAScheduleList") or {}).get("SAScheduleTuple") or []
        ac_params = e.payload.get("AC_EVSEChargeParameter") or {}
        if not tuples or not ac_params:
            continue
        entries = (tuples[0].get("PMaxSchedule") or {}).get("PMaxScheduleEntry") or []
        if not entries:
            continue

        scheduled = _phys(entries[0].get("PMax"))
        voltage = _phys(ac_params.get("EVSENominalVoltage"))
        current = _phys(ac_params.get("EVSEMaxCurrent"))
        if None in (scheduled, voltage, current) or scheduled <= 0:
            continue

        implied = factor * voltage * current
        # Relative to the larger of the two, so the direction of the mismatch
        # does not change the verdict.
        # 除以两者中较大的那个，这样谁大谁小不影响判定。
        if abs(scheduled - implied) / max(scheduled, implied) > tolerance:
            out.append(
                Finding(
                    "R007",
                    "error",
                    f"station advertises PMax={scheduled:.0f} W but "
                    f"EVSEMaxCurrent={current:.0f} A at {voltage:.0f} V "
                    f"implies {implied:.0f} W",
                    line_no=e.line_no,
                    ts=e.ts,
                )
            )
    return out


RULES = [
    r001_unanswered_calls,
    r002_call_errors,
    r003_rejections,
    r004_slow_responses,
    r005_heartbeat_gaps,
    r006_illegal_state_transitions,
    r007_power_limit_mismatch,
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
# R010  Out-of-order V2G session: compare the observed message order against
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
