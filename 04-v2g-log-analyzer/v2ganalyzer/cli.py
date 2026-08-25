"""Command line entry point.  命令行入口。

    python -m v2ganalyzer samples/ocpp_session.log
    python -m v2ganalyzer samples/ocpp_session_faulty.log --format md -o report.md
    python -m v2ganalyzer samples/ocpp_session.log --format mermaid
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from v2ganalyzer import parsers, render, rules
from v2ganalyzer.correlate import correlate
from v2ganalyzer.models import Session


def analyse(path: Path, slow_ms: float = 2000.0) -> Session:
    lines = path.read_text(errors="replace").splitlines()
    parser = parsers.autodetect(lines)
    if parser is None:
        raise SystemExit(
            f"{path}: no parser recognised this log format.\n"
            f"  Supported: OCPP-J logs from the `ocpp` Python library, and\n"
            f"  ISO 15118 logs once you finish parsers/v2g_text.py.\n"
            f"  没有解析器认识这个日志格式。"
        )
    events = parser.parse(lines)
    correlate(events)
    session = Session(source=str(path), events=events)
    session.findings = rules.run(events, {"slow_ms": slow_ms})
    return session


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="v2ganalyzer",
        description="Turn a charging-session log into a timeline, a sequence "
                    "diagram and a list of findings.",
    )
    ap.add_argument("logfile", type=Path)
    ap.add_argument("--format", choices=["md", "mermaid", "json", "text", "html"], default="text")
    ap.add_argument("--role", choices=["cp", "csms"], default="cp",
                    help="whose point of view the log was written from | 日志由哪一端产生")
    ap.add_argument("--slow-ms", type=float, default=2000.0,
                    help="latency above which a response is flagged | 超过多少毫秒判为慢响应")
    ap.add_argument("-o", "--out", type=Path, help="write to a file instead of stdout")
    ap.add_argument("--fail-on-error", action="store_true",
                    help="exit 1 if any error-severity finding exists (for CI) | 供 CI 使用")
    args = ap.parse_args(argv)

    session = analyse(args.logfile, args.slow_ms)

    if args.format == "md":
        output = render.markdown(session, args.role)
    elif args.format == "mermaid":
        output = render.mermaid(session, args.role)
    elif args.format == "html":
        output = render.html(session, args.role)
    elif args.format == "json":
        output = render.as_json(session, args.role)
    else:
        output = _text(session)

    if args.out:
        args.out.write_text(output)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(output)

    if args.fail_on_error and any(f.severity == "error" for f in session.findings):
        return 1
    return 0


def _text(session: Session) -> str:
    lines = [
        f"{session.source}: {len(session.events)} frames, "
        f"{len(session.calls)} requests, {session.duration_s:.1f}s",
        "",
    ]
    t0 = session.events[0].ts if session.events else None
    for e in session.events:
        rel = f"+{(e.ts - t0).total_seconds():7.2f}s" if t0 else ""
        arrow = "-->" if e.direction.value == "out" else "<--"
        lat = f"  ({e.latency_ms:.0f} ms)" if e.latency_ms is not None else ""
        lines.append(f"{rel}  {arrow} {e.label}{lat}")
    lines += ["", f"findings: {len(session.findings)}"]
    lines += [f"  {f}" for f in session.findings] or ["  none"]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
