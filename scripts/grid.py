#!/usr/bin/env python3
"""Render a box-drawing text grid with wrapped cells.

Reads a JSON spec on stdin and prints the grid:

    {"columns": [["SPACE Name", 14], ["Pane", 7], ...],
     "rows": [["MY-PROJECT", "w13:p1", ...], ...]}

Each column is [header, width]; width is the wrapped text width, not counting
padding. Pure formatting: stdlib only, no I/O beyond stdin/stdout.
"""
import json
import sys
import textwrap


def _wrap(text, width):
    lines = []
    for para in str(text if text is not None else "").splitlines() or [""]:
        lines.extend(textwrap.wrap(para, width, break_long_words=True,
                                   break_on_hyphens=True) or [""])
    return lines


def render(columns, rows):
    widths = [int(w) for _, w in columns]
    if any(w < 1 for w in widths):
        raise ValueError("column widths must be >= 1")

    def rule(left, mid, right):
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def block(cells):
        if len(cells) != len(widths):
            raise ValueError(f"row has {len(cells)} cells, expected {len(widths)}")
        wrapped = [_wrap(c, w) for c, w in zip(cells, widths)]
        height = max(len(c) for c in wrapped)
        out = []
        for i in range(height):
            parts = [(c[i] if i < len(c) else "").ljust(w) for c, w in zip(wrapped, widths)]
            out.append("│ " + " │ ".join(parts) + " │")
        return out

    lines = [rule("┌", "┬", "┐")]
    lines += block([h for h, _ in columns])
    for row in rows:
        lines.append(rule("├", "┼", "┤"))
        lines += block(row)
    lines.append(rule("└", "┴", "┘"))
    return "\n".join(lines)


def main():
    spec = json.load(sys.stdin)
    print(render(spec["columns"], spec.get("rows", [])))


if __name__ == "__main__":
    main()
