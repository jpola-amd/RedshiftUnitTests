"""Batch summary as JSON, xlsx and markdown files and as a console overview."""

import dataclasses
import json
from pathlib import Path

import pandas as pd
from colorama import Fore
from openpyxl.utils import get_column_letter

from ..utils import colored
from .html_report import write_html
from .summary import BAD, BatchSummary
from .tables import (
    fmt_speedup,
    image_rows,
    overview_rows,
    performance_rows,
    problem_rows,
    settings_rows,
    setup_of,
    verdict_rows,
)


def write_json(summary: BatchSummary, file: Path) -> None:
    data = {
        "name": summary.name,
        "created": summary.created,
        "program": summary.program,
        "folder": str(summary.folder),
        "description": summary.description,
        "settings": summary.settings,
        "ok": summary.ok,
        "reference": summary.reference,
        "overview": overview_rows(summary),
        "problems": problem_rows(summary),
        "columns": {
            c.name: {
                "kind": c.kind,
                "against": c.against,
                "setup": setup_of(c),
                "redshift_version": c.redshift_version,
                "tests": {t: dataclasses.asdict(r) for t, r in c.tests.items()},
            }
            for c in summary.columns
        },
    }
    file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_xlsx(summary: BatchSummary, file: Path) -> None:
    sheets = {
        "Overview": overview_rows(summary),
        "Problems": problem_rows(summary),
        "Verdicts": verdict_rows(summary),
        "Performance": performance_rows(summary),
        "Images": image_rows(summary),
        "Settings": settings_rows(summary),
    }
    with pd.ExcelWriter(file) as writer:
        for sheet, rows in sheets.items():
            frame = pd.DataFrame(rows) if rows else pd.DataFrame({"": ["none"]})
            frame.to_excel(writer, sheet_name=sheet, index=False)
            for index, column in enumerate(frame.columns, 1):
                width = max([len(str(column)), *(len(str(v)) for v in frame[column].head(200))])
                writer.sheets[sheet].column_dimensions[get_column_letter(index)].width = min(60, width + 2)


def _md_table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["none", ""]
    keys = list(rows[0])

    def cell(value) -> str:
        if isinstance(value, float):  # the only floats of the markdown tables are speedups
            value = fmt_speedup(value)
        return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(cell(row[k]) for k in keys) + " |" for row in rows]
    return [*lines, ""]


def write_markdown(summary: BatchSummary, file: Path) -> None:
    status = "PASSED" if summary.ok else "FAILED"
    lines = [f"# Batch {summary.name}: {status}", ""]
    if summary.description:
        lines += [summary.description, ""]
    lines += ["## Settings", "", *_md_table(settings_rows(summary))]
    keys = ("Run", "Compared with", "Setup", "Tests", "OK", "Failed", "Image mismatch", "No reference", "Slower")
    overview = [
        {**{k: row[k] for k in keys}, "Total speedup": row["Total speedup"], "Geomean speedup": row["Geomean speedup"]}
        for row in overview_rows(summary)
    ]
    lines += ["## Overview", "", "Speedup > 1 = faster than what the run is compared with.", ""]
    lines += _md_table(overview)
    for column in summary.columns:
        if column.slowest:
            slowest = ", ".join(f"{n} {fmt_speedup(s)}" for n, s in column.slowest)
            lines.append(f"- Slowest of **{column.name}**: {slowest}")
    lines += ["", "## Problems", "", *_md_table(problem_rows(summary))]
    lines += ["Full tables: `summary.xlsx`, per test and run: `summary.html`.", ""]
    file.write_text("\n".join(lines), encoding="utf-8")


def print_console(summary: BatchSummary) -> None:
    print(f"\n{colored('Batch summary', Fore.MAGENTA)}: {summary.name} ({summary.folder})")
    if summary.reference is not None:
        failed = len(summary.reference_failures)
        color = Fore.RED if failed else Fore.GREEN
        print(f"\treference: {colored(f'{len(summary.reference) - failed} ok, {failed} failed', color)}")
    for row, column in zip(overview_rows(summary), summary.columns, strict=True):
        bad = row["Failed"] + row["Image mismatch"]
        color = Fore.RED if bad else (Fore.YELLOW if row["No reference"] + row["Slower"] else Fore.GREEN)
        name = f"{row['Run']:<28}"
        print(
            f"\t{colored(name, color)} vs {row['Compared with']:<12} "
            f"ok {row['OK']:>4}  failed {row['Failed']:>3}  image {row['Image mismatch']:>3}  "
            f"no ref {row['No reference']:>3}  slower {row['Slower']:>3}  "
            f"speedup total {fmt_speedup(row['Total speedup'])} geomean {fmt_speedup(row['Geomean speedup'])}"
        )
        bad_tests = [t for t, r in column.tests.items() if r.verdict in BAD]
        if bad_tests and column.kind == "run":
            more = f" (+{len(bad_tests) - 5} more)" if len(bad_tests) > 5 else ""
            print(f"\t\t{colored('bad', Fore.RED)}: {', '.join(bad_tests[:5])}{more}")
    result = colored("PASSED", Fore.GREEN) if summary.ok else colored("FAILED", Fore.RED)
    print(f"\tBatch {result}")


def write_reports(summary: BatchSummary, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for name, writer in (
        ("summary.json", write_json),
        ("summary.xlsx", write_xlsx),
        ("summary.md", write_markdown),
        ("summary.html", write_html),
    ):
        writer(summary, folder / name)
        files.append(folder / name)
    return files
