"""Pair requests with responses and compute latencies.
把请求和响应配对并计算时延。

Separated from the parsers on purpose: OCPP pairs by message id, ISO 15118
pairs by message name, but the *algorithm* is identical.
刻意与解析器分开: OCPP 按消息 id 配对，ISO 15118 按消息名配对，
但**算法**是同一个。
"""

from __future__ import annotations

from v2ganalyzer.models import Event, Kind


def correlate(events: list[Event]) -> list[Event]:
    """Attach `answered_by` and `latency_ms` to every CALL. Returns the list of
    CALLs that were never answered.
    给每个 CALL 挂上 `answered_by` 和 `latency_ms`，返回始终没有得到响应的 CALL。
    """
    pending: dict[str, Event] = {}
    for e in events:
        if e.kind is Kind.CALL:
            pending[e.uid] = e
        elif e.kind in (Kind.CALL_RESULT, Kind.CALL_ERROR):
            call = pending.pop(e.uid, None)
            if call is not None:
                call.answered_by = e
                call.latency_ms = (e.ts - call.ts).total_seconds() * 1000.0
    return list(pending.values())
