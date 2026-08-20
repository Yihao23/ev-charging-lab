"""Log parsers.  日志解析器。

Each parser exposes `sniff(lines) -> bool` and `parse(lines) -> list[Event]`,
so `cli.py` can pick the right one without being told.
每个解析器暴露 `sniff()` 和 `parse()`，这样 `cli.py` 不用被告知格式也能自动选择。
"""

from v2ganalyzer.parsers import ocpp_json, v2g_text

REGISTRY = [ocpp_json, v2g_text]


def autodetect(lines: list[str]):
    """Return the first parser that recognises these lines, or None.
    返回第一个能识别这些行的解析器，没有则返回 None。"""
    for parser in REGISTRY:
        if parser.sniff(lines):
            return parser
    return None
