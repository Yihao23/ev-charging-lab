"""Anomaly rules. Each rule is a small pure function over the event list.
异常检测规则。每条规则都是作用于事件列表的小纯函数。

Adding a rule means appending one function to RULES — nothing else.
新增规则 = 往 RULES 里追加一个函数，仅此而已。
"""

from __future__ import annotations

from v2ganalyzer.correlate import correlate
from v2ganalyzer.models import Event, Finding, Kind
from v2ganalyzer.parsers.v2g_text import ISO15118_2_AC_ORDER, ISO15118_2_DC_ORDER

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


# Success in ISO 15118-2 is a short whitelist — 4 codes in the session schema
# and 2 in the handshake — against 23 ways to fail. Blacklisting failure would
# go stale the moment the standard adds a code.
# ISO 15118-2 的成功码是个短白名单 —— 会话 schema 里 4 个、握手里 2 个 ——
# 对应 23 种失败。用黑名单列失败码，标准一加新码就过期。
_OK_PREFIX = "OK"
# The one success code that still wants a human to act on it.
# 唯一一个仍然需要人去处理的成功码。
_OK_BUT_ACT = {"OK_CertificateExpiresSoon"}


def r011_failed_response_code(events: list[Event], cfg: dict) -> list[Finding]:
    """A V2G response refused the request.
    一条 V2G 响应拒绝了请求。

    ISO 15118 has no CALLERROR frame: a failure is an ordinary `...Res` whose
    ResponseCode is not OK. That means Kind.CALL_ERROR never occurs in a V2G log
    and R002 can never fire on one, while R003's BAD_STATUS list is OCPP
    vocabulary and matches none of the FAILED_* codes. Without this rule a
    session that died on an expired certificate passes the analyzer in silence.
    ISO 15118 没有 CALLERROR 帧: 失败就是一条普通的 `...Res`，只是 ResponseCode
    不是 OK。于是 V2G 日志里永远不会出现 Kind.CALL_ERROR，R002 永远报不出东西；
    而 R003 的 BAD_STATUS 是 OCPP 词汇，一个 FAILED_* 都匹配不上。没有这条规则，
    一次因证书过期而失败的会话会静默通过分析器。
    """
    out: list[Finding] = []
    for e in events:
        if e.kind is not Kind.CALL_RESULT:
            continue
        code = e.payload.get("ResponseCode")
        if not isinstance(code, str):
            continue
        what = e.action or "response"
        if code in _OK_BUT_ACT:
            out.append(Finding("R011", "warning",
                               f"{what} returned ResponseCode={code}",
                               line_no=e.line_no, ts=e.ts))
        elif not code.startswith(_OK_PREFIX):
            out.append(Finding("R011", "error",
                               f"{what} returned ResponseCode={code}",
                               line_no=e.line_no, ts=e.ts))
    return out


def _first_seen(events: list[Event]) -> list[str]:
    """Message names in the order they first appeared.
    消息名，按首次出现的先后排列。"""
    order: list[str] = []
    for e in events:
        if e.kind is Kind.CALL and e.uid and e.uid not in order:
            order.append(e.uid)
    return order


def _reference_order(seen: list[str]) -> list[str]:
    """Which of the two sequences this session should be judged against.
    这次会话该拿哪张表来判。

    Inferred from the charging-loop message, because that is the one place the
    two sequences differ observably. Before ChargeParameterDiscovery the prefixes
    are identical, so a session that died early can take either.
    从充电循环用的那条消息推断，因为那是两张表唯一可观察的分歧点。
    ChargeParameterDiscovery 之前前缀完全相同，早夭的会话用哪张都一样。
    """
    return ISO15118_2_DC_ORDER if "CurrentDemand" in seen else ISO15118_2_AC_ORDER


def r008_truncated_session(events: list[Event], cfg: dict) -> list[Finding]:
    """Charging started and the session never ended.
    充电开始了，会话却没有结束。

    A driver reports this as "the car stopped charging by itself"; in the log it
    is simply the absence of SessionStop after a charging loop. The two are the
    same event seen from different ends, and nothing else in the analyzer makes
    that connection.
    司机报的是"车自己停止充电了"；在日志里它只是充电循环之后缺了 SessionStop。
    两者是同一件事的两个视角，而分析器里没有别的东西会把它们联系起来。

    Only fires once charging actually began. A session refused at Authorization
    also lacks SessionStop, but there the refusal is the finding — R011 reports
    it, and a second alarm about the missing goodbye would only add noise.
    只在充电真的开始之后才报。在 Authorization 就被拒的会话同样没有 SessionStop，
    但那里的问题是"被拒"本身 —— R011 会报，再为缺少道别报一次只是噪音。
    """
    seen = _first_seen(events)
    charging = next((m for m in ("CurrentDemand", "ChargingStatus") if m in seen), None)
    if charging is None or "SessionStop" in seen:
        return []
    last = [e for e in events if e.kind is Kind.CALL][-1]
    return [
        Finding(
            "R008",
            "error",
            f"session reached {charging} but never sent SessionStop — "
            f"log ends at {last.action}",
            line_no=last.line_no,
            ts=last.ts,
        )
    ]


def r010_out_of_order(events: list[Event], cfg: dict) -> list[Finding]:
    """The messages arrived in an order ISO 15118-2 does not allow.
    消息到达的顺序不是 ISO 15118-2 允许的。

    Checks relative order only, never completeness. ServiceDetail and
    PaymentDetails are optional, an AC session legitimately omits the four DC
    messages, and a session can stop early for perfectly good reasons — so
    absence is never a deviation. Only two messages that both appeared, in the
    wrong order relative to each other, are.
    只检查相对顺序，从不检查完整性。ServiceDetail 和 PaymentDetails 是可选的，
    交流会话合法地省掉四条直流消息，会话也可能因为正当理由提前结束 ——
    所以缺席从来不算偏差。只有两条都出现了、彼此顺序却反了，才算。

    Reports the first deviation only. After one, everything downstream is
    suspect and listing it all buries the one place worth looking at.
    只报第一处偏差。出现一处之后，后面全都可疑，全列出来反而淹没了真正该看的地方。
    """
    seen = _first_seen(events)
    reference = _reference_order(seen)
    # Anything the reference does not know about cannot be judged — MeteringReceipt
    # and the -20 messages among others.
    # 参考表不认识的消息无从判断 —— 比如 MeteringReceipt 和 -20 的那些消息。
    observed = [m for m in seen if m in reference]
    expected = [m for m in reference if m in observed]
    if observed == expected:
        return []

    at = next(i for i, (a, b) in enumerate(zip(observed, expected)) if a != b)
    culprit = observed[at]
    first = next(e for e in events if e.kind is Kind.CALL and e.uid == culprit)
    return [
        Finding(
            "R010",
            "error",
            f"{culprit} arrived before {expected[at]}, "
            f"but ISO 15118-2 orders them the other way round",
            line_no=first.line_no,
            ts=first.ts,
        )
    ]


# DataTransfer statuses that mean the peer did not act on the message. The two
# "Unknown" ones are worse than Rejected: Rejected means the peer understood and
# declined this payload, while Unknown* means the extension is not wired up at
# all on that side.
# 表示对端没有处理这条消息的 DataTransfer 状态。两个 "Unknown" 比 Rejected 更严重:
# Rejected 表示对端听懂了、拒绝了这次的内容，Unknown* 表示那一端根本没接上这个扩展。
_DT_NOT_PROCESSED = {
    "UnknownVendorId": "error",
    "UnknownMessageId": "error",
    "Rejected": "warning",
}


def r012_data_transfer_not_processed(events: list[Event], cfg: dict) -> list[Finding]:
    """A DataTransfer the peer did not act on.
    对端没有处理的 DataTransfer。

    DataTransfer is OCPP's extension point, and its failure mode is silence. The
    CALL goes out, a CALLRESULT comes back, and the sender's own logs read as a
    success — but the status says the peer never processed it, so whatever the
    extension was supposed to do simply did not happen. Nobody finds out until
    someone asks why the data never arrived.
    DataTransfer 是 OCPP 的扩展点，而它的失败方式是"静默"。请求发出去了，
    响应也回来了，发送方自己的日志读起来一切正常 —— 但 status 说对端根本没处理，
    于是这个扩展该做的事就是没做。直到有人问"数据怎么一直没到"才会发现。

    No existing rule catches it: R002 needs a CALLERROR frame, and R003's
    BAD_STATUS is a fixed list that never included UnknownVendorId.
    现有规则一条都抓不到: R002 要 CALLERROR 帧，而 R003 的 BAD_STATUS 是一张固定
    清单，里面从来没有 UnknownVendorId。
    """
    correlate(events)
    out: list[Finding] = []
    for e in events:
        if e.kind is not Kind.CALL or e.action != "DataTransfer":
            continue
        reply = e.answered_by
        if reply is None:
            continue                       # unanswered is R001's business
        status = reply.payload.get("status")
        severity = _DT_NOT_PROCESSED.get(status)
        if severity is None:
            continue
        vendor = e.payload.get("vendorId") or "?"
        message = e.payload.get("messageId") or "?"
        out.append(
            Finding(
                "R012",
                severity,
                f"DataTransfer {vendor}/{message} -> {status}: "
                f"the peer did not process it",
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
    r008_truncated_session,
    r010_out_of_order,
    r011_failed_response_code,
    r012_data_transfer_not_processed,
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
# R009  Energy sanity: integrate Power.Active.Import over time and compare with
#       the Energy.Active.Import.Register delta. A mismatch means a meter or a
#       unit bug — and unit bugs in this domain are expensive.
#       能量自洽: 对功率积分并与电表读数差值比较。不一致意味着电表问题或单位 bug ——
#       这个领域的单位 bug 代价很高。
