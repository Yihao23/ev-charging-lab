"""Renderers: Markdown report, Mermaid sequence diagram, JSON, HTML.
渲染器: Markdown 报告、Mermaid 时序图、JSON、HTML。

The Mermaid output is the point. A sequence diagram generated from a real log
is the artefact that makes an interviewer lean forward.
Mermaid 输出是重点。从真实日志自动生成的时序图，是能让面试官身体前倾的东西。
"""

from __future__ import annotations

import html as _html
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


# Inlined rather than linked, because the whole point of the HTML output is that
# it survives being an email attachment on a laptop with no network.
# 样式内联而不是外链，因为 HTML 输出的全部意义就是: 它得能作为邮件附件，
# 在一台断网的笔记本上打开。
_CSS = """
  :root { --err:#b3261e; --warn:#8a6100; --ok:#1b6b3a; --line:#d7d7db; --muted:#5f6368; }
  body { font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         margin:0; padding:2rem 1.5rem; color:#1b1b1f; background:#fff; }
  main { max-width:60rem; margin:0 auto; }
  h1 { font-size:1.35rem; margin:0 0 .25rem; }
  h2 { font-size:1rem; margin:2rem 0 .6rem; text-transform:uppercase;
       letter-spacing:.06em; color:var(--muted); }
  .src { color:var(--muted); font-family:ui-monospace,Menlo,Consolas,monospace; }
  table { border-collapse:collapse; width:100%; font-size:.9rem; }
  th,td { text-align:left; padding:.4rem .6rem; border-bottom:1px solid var(--line);
          vertical-align:top; }
  th { font-weight:600; color:var(--muted); font-size:.8rem; text-transform:uppercase;
       letter-spacing:.04em; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .cards { display:flex; flex-wrap:wrap; gap:.75rem; margin:.5rem 0 0; }
  .card { border:1px solid var(--line); border-radius:.5rem; padding:.6rem .9rem; min-width:7rem; }
  .card b { display:block; font-size:1.5rem; font-variant-numeric:tabular-nums; }
  .card span { color:var(--muted); font-size:.78rem; }
  .card.err b { color:var(--err); }
  .card.warn b { color:var(--warn); }
  .sev { font-weight:600; font-size:.78rem; letter-spacing:.04em; }
  .sev.error { color:var(--err); }
  .sev.warning { color:var(--warn); }
  .none { color:var(--ok); }
  code,pre { font-family:ui-monospace,Menlo,Consolas,monospace; }
  pre { background:#f6f6f7; border:1px solid var(--line); border-radius:.5rem;
        padding:.8rem; overflow-x:auto; font-size:.82rem; }
  details { margin-top:.6rem; }
  summary { cursor:pointer; color:var(--muted); }
  footer { margin-top:2.5rem; color:var(--muted); font-size:.8rem; }
"""


# Which MeterValues measurand answers the limit, per unit. Comparing a limit in
# amps against a curve in watts would be meaningless, so the plot follows
# whatever unit SetChargingProfile was written in.
# 按单位决定该拿哪个 MeterValues 测量值来对照限值。拿 A 的限值去比 W 的曲线毫无意义，
# 所以图跟随 SetChargingProfile 自己用的单位。
_MEASURAND_FOR = {"A": "Current.Import", "W": "Power.Active.Import"}


def _limit_steps(session: Session):
    """(profile offset in seconds, [(seconds, limit), ...], unit) or None.
    从 SetChargingProfile 取出 (时间偏移, [(秒, 限值), ...], 单位)。"""
    start = session.events[0].ts
    for e in session.events:
        if e.action != "SetChargingProfile":
            continue
        schedule = (e.payload.get("csChargingProfiles") or {}).get("chargingSchedule") or {}
        periods = schedule.get("chargingSchedulePeriod") or []
        unit = schedule.get("chargingRateUnit")
        if not periods or unit not in _MEASURAND_FOR:
            continue
        steps = [(float(p["startPeriod"]), float(p["limit"])) for p in periods]
        return (e.ts - start).total_seconds(), steps, unit
    return None


def _measured(session: Session, unit: str) -> list:
    """[(seconds since session start, value), ...] from MeterValues.
    从 MeterValues 取出 [(距会话开始的秒数, 数值), ...]。"""
    wanted = _MEASURAND_FOR[unit]
    start = session.events[0].ts
    out = []
    for e in session.events:
        if e.action != "MeterValues":
            continue
        for entry in e.payload.get("meterValue") or []:
            for sample in entry.get("sampledValue") or []:
                if sample.get("measurand") == wanted:
                    out.append(((e.ts - start).total_seconds(), float(sample["value"])))
    return out


def svg(session: Session, role: str = "cp"):
    """What the backend allowed, drawn against what the car actually drew.
    后台允许多少，和车实际拉了多少，画在一起。

    Returns None when the log carries no meter data — a V2G session has no
    MeterValues at all, and an empty pair of axes is worse than no plot.
    日志里没有电表数据时返回 None —— V2G 会话根本没有 MeterValues，
    画一对空坐标轴比不画更糟。

    SVG is text, so this needs no plotting library and stays inside the
    standard-library-only rule that lets the tool run on a station's embedded
    Linux without a package manager.
    SVG 就是文本，所以不需要绘图库，也就守住了"只用标准库"那条规则 ——
    正是它让这个工具能在没有包管理器的嵌入式 Linux 上跑起来。
    """
    profile = _limit_steps(session)
    if profile is None:
        return None
    offset, steps, unit = profile
    points = _measured(session, unit)
    if not points:
        return None

    W, H = 720, 260
    L, R, T, B = 52, 16, 20, 34
    span = max(session.duration_s, steps[-1][0] + offset, 1.0)
    top = max(max(v for _, v in points), max(v for _, v in steps)) * 1.2

    def x(sec):
        return L + (W - L - R) * min(sec, span) / span

    def y(val):
        return T + (H - T - B) * (1 - val / top)

    # The limit is a step function, not a slope: it holds until the next period
    # begins. A straight line between periods would draw a ramp the station
    # never commanded.
    # 限值是阶梯函数而不是斜坡: 它一直保持到下一个时段开始。
    # 在两个时段之间画直线，会画出一个桩从未下达过的渐变。
    step_pts = []
    for i, (sec, limit) in enumerate(steps):
        at = offset + sec
        if i:
            step_pts.append("%.1f,%.1f" % (x(at), y(steps[i - 1][1])))
        step_pts.append("%.1f,%.1f" % (x(at), y(limit)))
    step_pts.append("%.1f,%.1f" % (x(span), y(steps[-1][1])))

    grid = "".join(
        '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e6e6e9"/>'
        '<text x="%d" y="%.1f" text-anchor="end" font-size="10" fill="#5f6368">%.0f</text>'
        % (L, y(top * f), W - R, y(top * f), L - 8, y(top * f) + 4, top * f)
        for f in (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    ticks = "".join(
        '<text x="%.1f" y="%d" text-anchor="middle" font-size="10" fill="#5f6368">%.0fs</text>'
        % (x(span * f), H - B + 18, span * f)
        for f in (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    dots = "".join('<circle cx="%.1f" cy="%.1f" r="2.5" fill="#1a73e8"/>' % (x(t), y(v))
                   for t, v in points)
    curve = " ".join("%.1f,%.1f" % (x(t), y(v)) for t, v in points)

    return (
        '<svg viewBox="0 0 %d %d" width="100%%" role="img" '
        'aria-label="measured %s against the SetChargingProfile limit" '
        'xmlns="http://www.w3.org/2000/svg" font-family="sans-serif">'
        '%s%s'
        '<text x="%d" y="%d" text-anchor="end" font-size="10" fill="#5f6368">%s</text>'
        '<polyline points="%s" fill="none" stroke="#b3261e" stroke-width="1.5" '
        'stroke-dasharray="5 3"/>'
        '<polyline points="%s" fill="none" stroke="#1a73e8" stroke-width="2"/>'
        '%s'
        '<g font-size="11">'
        '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#b3261e" stroke-width="1.5" '
        'stroke-dasharray="5 3"/>'
        '<text x="%d" y="%d" fill="#5f6368">limit (SetChargingProfile)</text>'
        '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#1a73e8" stroke-width="2"/>'
        '<text x="%d" y="%d" fill="#5f6368">measured (%s)</text>'
        '</g></svg>'
        % (W, H, _MEASURAND_FOR[unit], grid, ticks,
           L - 8, T - 6, unit,
           " ".join(step_pts), curve, dots,
           L, H - 10, L + 18, H - 10, L + 24, H - 6,
           L + 190, H - 10, L + 208, H - 10, L + 214, H - 6, _MEASURAND_FOR[unit])
    )


def html(session: Session, role: str = "cp") -> str:
    """One self-contained file: no CDN, no stylesheet, no script.
    一个自包含的文件: 没有 CDN、没有外部样式表、没有脚本。

    The Mermaid diagram is embedded as its source rather than rendered, because
    rendering it would mean loading a library from the network — and a report
    that needs the internet is not an attachment, it is a link that rots.
    Mermaid 图以源码形式嵌入而不渲染，因为渲染就得从网上加载库 ——
    而一份需要联网的报告不是附件，是一条会失效的链接。
    """
    errors = [f for f in session.findings if f.severity == "error"]
    warnings = [f for f in session.findings if f.severity == "warning"]
    me, peer = _peer_names(role)

    if session.findings:
        rows = "".join(
            f"<tr><td><code>{_h(f.rule)}</code></td>"
            f"<td><span class='sev {_h(f.severity)}'>{_h(f.severity.upper())}</span></td>"
            f"<td class='num'>{f.line_no or ''}</td>"
            f"<td>{_h(f.message)}</td></tr>"
            for f in session.findings
        )
        findings = ("<table><tr><th>Rule</th><th>Severity</th><th class='num'>Line</th>"
                    f"<th>Message</th></tr>{rows}</table>")
    else:
        findings = "<p class='none'>No findings. 没有发现问题。</p>"

    chart = svg(session, role)
    plot = f"<h2>Delivered vs allowed / 实际 vs 允许</h2>{chart}" if chart else ""

    start = session.events[0].ts if session.events else None
    timeline = "".join(
        f"<tr><td class='num'>{n}</td>"
        f"<td class='num'>{(e.ts - start).total_seconds():.2f}s</td>"
        f"<td>{'&rarr;' if e.direction is Direction.OUT else '&larr;'}</td>"
        f"<td>{_h(e.label)}</td>"
        f"<td class='num'>{f'{e.latency_ms:.0f} ms' if e.latency_ms is not None else ''}</td>"
        f"</tr>"
        for n, e in enumerate(session.events, start=1)
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Session report — {_h(session.source)}</title>
<style>{_CSS}</style></head><body><main>
<h1>Session report</h1>
<p class="src">{_h(session.source)} &middot; {_h(me)} &rarr; {_h(peer)}</p>

<h2>Summary / 摘要</h2>
<div class="cards">
  <div class="card"><b>{len(session.events)}</b><span>frames / 帧</span></div>
  <div class="card"><b>{len(session.calls)}</b><span>requests / 请求</span></div>
  <div class="card"><b>{session.duration_s:.1f}s</b><span>duration / 时长</span></div>
  <div class="card err"><b>{len(errors)}</b><span>errors / 错误</span></div>
  <div class="card warn"><b>{len(warnings)}</b><span>warnings / 警告</span></div>
</div>

{plot}<h2>Findings / 问题清单</h2>
{findings}

<h2>Timeline / 时间线</h2>
<table><tr><th class="num">#</th><th class="num">Time</th><th>Dir</th>
<th>Message</th><th class="num">Latency</th></tr>{timeline}</table>

<details><summary>Sequence diagram source (Mermaid) / 时序图源码</summary>
<pre>{_h(mermaid(session, role))}</pre></details>

<footer>Generated by v2ganalyzer &middot; standard library only, no network.</footer>
</main></body></html>
"""


def _h(text) -> str:
    """Log content is untrusted input; markup in it must stay text.
    日志内容是外部输入，里面的标签必须保持为文本。"""
    return _html.escape(str(text), quote=True)


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
