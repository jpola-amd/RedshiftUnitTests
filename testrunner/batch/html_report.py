"""Batch summary as one self-contained HTML page: overview, problems with diff plots, test x run matrix."""

from html import escape
from pathlib import Path

from .summary import BAD, WARN, BatchSummary, TestResult
from .tables import fmt_seconds, fmt_speedup, overview_rows, settings_rows

STYLE = """
body { font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #222; }
h1 .passed { color: #1a7f37; } h1 .failed { color: #cf222e; }
table { border-collapse: collapse; margin-bottom: 24px; font-size: 13px; }
th, td { border: 1px solid #d0d7de; padding: 3px 8px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; position: sticky; top: 0; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.ok { background: #dafbe1; } .bad { background: #ffd8d3; } .warn { background: #fff3c4; } .none { color: #8c959f; }
#only-problems:checked ~ table.matrix tr.clean { display: none; }
"""
SHOWN_OVERVIEW = (
    "Run",
    "Compared with",
    "Setup",
    "Tests",
    "OK",
    "Failed",
    "Image mismatch",
    "No reference",
    "Slower",
    "Skipped",
    "Not run",
    "Total speedup",
    "Geomean speedup",
    "Slowest",
    "Redshift",
)


def css_class(verdict: str) -> str:
    if verdict in BAD:
        return "bad"
    if verdict in WARN:
        return "warn"
    return "ok" if verdict == "ok" else "none"


def _cell(value) -> str:
    if isinstance(value, float):
        return f'<td class="num">{fmt_speedup(value)}</td>'
    if isinstance(value, int):
        return f'<td class="num">{value}</td>'
    return f"<td>{escape(str(value))}</td>"


def _table(rows: list[dict], keys: tuple | None = None) -> str:
    if not rows:
        return "<p>none</p>"
    keys = keys or tuple(rows[0])
    head = "".join(f"<th>{escape(k)}</th>" for k in keys)
    body = "".join("<tr>" + "".join(_cell(row[k]) for k in keys) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _plots(result: TestResult) -> str:
    # The page is in <batch>/summary, plot paths are relative to the batch folder.
    return " ".join(f'<a href="../{escape(p)}">{escape(Path(p).stem)}</a>' for p in result.diff_plots)


def _problems(summary: BatchSummary) -> str:
    rows = []
    results = {c.name: c.tests for c in summary.columns}
    for p in summary.problems():
        result = results.get(p.column, {}).get(p.test)
        plots = _plots(result) if result else ""
        rows.append(
            f'<tr class="{css_class(p.verdict)}"><td>{escape(p.column)}</td><td>{escape(p.test)}</td>'
            f"<td>{escape(p.verdict)}</td><td>{escape(p.detail)}</td><td>{plots}</td></tr>"
        )
    if not rows:
        return "<p>none</p>"
    head = "<tr><th>Run</th><th>Test</th><th>Verdict</th><th>Detail</th><th>Diff plots</th></tr>"
    return f"<table>{head}{''.join(rows)}</table>"


def _matrix_cell(result: TestResult) -> str:
    tip = [result.detail] if result.detail else []
    if result.max_mse is not None:
        tip.append(f"max mse {result.max_mse:.3f}, min ssim {result.min_ssim:.4f}, {result.images} image(s)")
    if result.time is not None:
        tip.append(f"{fmt_seconds(result.against_time)}s -> {fmt_seconds(result.time)}s")
    speed = f" {fmt_speedup(result.speedup)}" if result.speedup is not None else ""
    return (
        f'<td class="{css_class(result.verdict)}" title="{escape(" | ".join(tip))}">'
        f"{escape(result.verdict)}{speed}</td>"
    )


def _matrix(summary: BatchSummary) -> str:
    head = "<tr><th>Test</th>" + "".join(f"<th>{escape(c.name)}</th>" for c in summary.columns) + "</tr>"
    rows = []
    for test in summary.tests:
        results = [c.tests[test] for c in summary.columns]
        clean = all(r.verdict not in BAD + WARN for r in results)
        cells = "".join(_matrix_cell(r) for r in results)
        rows.append(f'<tr class="{"clean" if clean else ""}"><td>{escape(test)}</td>{cells}</tr>')
    return f'<table class="matrix">{head}{"".join(rows)}</table>'


def write_html(summary: BatchSummary, file: Path) -> None:
    status = '<span class="passed">PASSED</span>' if summary.ok else '<span class="failed">FAILED</span>'
    description = f"<p>{escape(summary.description)}</p>" if summary.description else ""
    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Batch {escape(summary.name)}</title><style>{STYLE}</style></head>
<body>
<h1>Batch {escape(summary.name)}: {status}</h1>
{description}
<h2>Overview</h2>
<p>Runs are compared with the references, pairs with another run. Speedup &gt; 1 = faster.</p>
{_table(overview_rows(summary), SHOWN_OVERVIEW)}
<h2>Problems</h2>
{_problems(summary)}
<h2>Tests</h2>
<input type="checkbox" id="only-problems"><label for="only-problems"> only tests with problems</label>
{_matrix(summary)}
<h2>Settings</h2>
{_table(settings_rows(summary))}
</body></html>
"""
    file.write_text(page, encoding="utf-8")
