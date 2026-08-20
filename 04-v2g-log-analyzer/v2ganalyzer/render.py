"""Renderers: Markdown report, Mermaid sequence diagram, JSON.
渲染器: Markdown 报告、Mermaid 时序图、JSON。

The Mermaid output is the point. A sequence diagram generated from a real log
is the artefact that makes an interviewer lean forward.
Mermaid 输出是重点。从真实日志自动生成的时序图，是能让面试官身体前倾的东西。
"""

from __future__ import annotations

import json
from dataclasses import asdict

from v2ganalyzer.models import Direction, Finding, Kind, Session


def _peer_names(role: str) -> tuple[str, str]:
    """Who is `self` and who is the peer, from the log owner's point of view.
    从日志产生方的视角看，谁是自己、谁是对端。"""
    return ("ChargePoint", "CSMS") if role == "cp" else ("CSMS", "ChargePoint")


def mermaid(session: Session, role: str = "cp") -> str:
    """A Mermaid `sequenceDiagram`. Paste it into any Markdown viewer.
    Mermaid 时序图，粘进任意 Markdown 阅读器即可渲染。"""
    me, peer = _peer_names(role)
    lines = [
        "sequenceDiagram",
        "    autonumber",
        f"    participant A as {me}",
        f"    participant B as {peer}",
    ]
    for e in session.events:
        if e.kind is Kind.NOTE:
            lines.append(f"    Note over A: {_esc(e.action)}")
            continue
        if e.kind is Kind.CALL:
            arrow = "A->>B" if e.direction is Direction.OUT else "B->>A"
            suffix = f" ({e.latency_ms:.0f} ms)" if e.latency_ms is not None else ""
            lines.append(f"    {arrow}: {_esc(e.action)}{suffix}")
        elif e.kind is Kind.CALL_RESULT:
            arrow = "A-->>B" if e.direction is Direction.OUT else "B-->>A"
            lines.append(f"    {arrow}: {_esc(_summarise(e.payload)) or 'result'}")
        elif e.kind is Kind.CALL_ERROR:
            arrow = "A-xB" if e.direction is Direction.OUT else "B-xA"
            lines.append(f"    {arrow}: ERROR {_esc(str(e.payload.get('errorCode')))}")
    return "\n".join(lines)


def markdown(session: Session, role: str = "cp") -> str:
    """The full report: summary, timeline, findings, diagram.
    完整报告: 摘要 + 时间线 + 问题清单 + 时序图。"""
    errors = [f for f in session.findings if f.severity == "error"]
    warnings = [f for f in session.findings if f.severity == "warning"]

    out = [
        f"# Session report — `{session.source}`",
        "",
        "## Summary / 摘要",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Frames parsed / 解析帧数 | {len(session.events)} |",
        f"| Requests / 请求数 | {len(session.calls)} |",
        f"| Duration / 时长 | {session.duration_s:.1f} s |",
        f"| Errors / 错误 | {len(errors)} |",
        f"| Warnings / 警告 | {len(warnings)} |",
        "",
        "## Findings / 问题清单",
        "",
    ]
    if session.findings:
        out += ["| Rule | Severity | Line | Message |", "|---|---|---|---|"]
        out += [f"| {f.rule} | {f.severity} | {f.line_no or '-'} | {f.message} |"
                for f in session.findings]
    else:
        out.append("_No anomalies detected. 未发现异常。_")

    out += ["", "## Timeline / 时间线", "",
            "| # | Time | Dir | Message | Latency |", "|---|---|---|---|---|"]
    t0 = session.events[0].ts if session.events else None
    for i, e in enumerate(session.events, 1):
        if e.kind is Kind.NOTE:
            continue
        rel = f"+{(e.ts - t0).total_seconds():6.2f}s" if t0 else ""
        arrow = "->" if e.direction is Direction.OUT else "<-"
        lat = f"{e.latency_ms:.0f} ms" if e.latency_ms is not None else ""
        out.append(f"| {i} | {rel} | {arrow} | {e.label} | {lat} |")

    out += ["", "## Sequence diagram / 时序图", "", "```mermaid",
            mermaid(session, role), "```", ""]
    return "\n".join(out)


def as_json(session: Session, role: str = "cp") -> str:
    """Machine-readable output, for CI or a Node-RED flow to consume.
    机器可读输出，供 CI 或 Node-RED 流程消费。"""
    return json.dumps(
        {
            "source": session.source,
            "duration_s": round(session.duration_s, 3),
            "events": [
                {
                    "ts": e.ts.isoformat(),
                    "kind": e.kind.value,
                    "direction": e.direction.value,
                    "action": e.action,
                    "uid": e.uid,
                    "latency_ms": e.latency_ms,
                    "line_no": e.line_no,
                }
                for e in session.events
            ],
            "findings": [
                {**{k: v for k, v in asdict(f).items() if k != "ts"},
                 "ts": f.ts.isoformat() if f.ts else None}
                for f in session.findings
            ],
        },
        indent=2,
    )


def _summarise(payload: dict) -> str:
    """One short string describing a response payload.
    用一个短字符串描述响应载荷。"""
    for key in ("status", "transactionId", "transaction_id", "interval", "currentTime"):
        if key in payload:
            value = payload[key]
            if isinstance(value, dict):
                value = value.get("status", value)
            return f"{key}={value}"
    if not payload:
        return "ok"
    first = next(iter(payload.items()))
    return f"{first[0]}=…"


def _esc(text: str) -> str:
    """Mermaid chokes on a few characters inside labels.
    Mermaid 对标签里的某些字符会解析失败。"""
    return str(text).replace(";", ",").replace("\n", " ").replace("#", "no.")


# ----------------------------------------------------------------------
# TODO(you) — project 04, day 12
# ----------------------------------------------------------------------
# 1. Add an SVG power/current plot (stdlib only — SVG is just text). Reading
#    Power.Active.Import out of MeterValues and drawing it next to the
#    profile limit makes the smart-charging story visual.
#    加一张 SVG 功率/电流曲线图 (只用标准库 —— SVG 就是文本)。把 MeterValues 里的
#    功率和曲线限值画在一起，智能充电的故事就有画面了。
# 2. Add `--format html` producing one self-contained file you can attach to
#    an email. Interviewers do not run your code; they open your artefact.
#    加 `--format html`，产出一个可以直接附在邮件里的自包含文件。面试官不会跑你的
#    代码，他们只会打开你的产物。
