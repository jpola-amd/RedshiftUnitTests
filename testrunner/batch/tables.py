"""Rows of the summary tables, shared by the xlsx, markdown, HTML and console reports."""

from .summary import BatchSummary, ColumnSummary


def fmt_speedup(value: float | None) -> str:
    return f"{value:.3f}x" if value is not None else "-"


def fmt_seconds(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "-"


def setup_of(column: ColumnSummary) -> str:
    return column.description if column.kind == "run" else f"{column.name.split('_vs_')[0]} vs {column.against}"


def overview_rows(summary: BatchSummary) -> list[dict]:
    rows = []
    for column in summary.columns:
        counts = column.counts
        images, image_failures = column.images
        rows.append(
            {
                "Run": column.name,
                "Compared with": column.against,
                "Setup": setup_of(column),
                "Tests": len(column.tests),
                "OK": counts["ok"],
                "Failed": counts["failed"],
                "Image mismatch": counts["image mismatch"],
                "No reference": counts["no reference"],
                "Slower": counts["slower"],
                "Skipped": counts["skipped"],
                "Not run": counts["not run"],
                "Images": images,
                "Image failures": image_failures,
                "Timed tests": column.timed_tests,
                "Total speedup": column.total_speedup,
                "Geomean speedup": column.geomean_speedup,
                "Slowest": "; ".join(f"{n} {fmt_speedup(s)}" for n, s in column.slowest),
                "Redshift": column.redshift_version or "",
            }
        )
    return rows


def verdict_rows(summary: BatchSummary) -> list[dict]:
    return [{"Test": t, **{c.name: c.tests[t].verdict for c in summary.columns}} for t in summary.tests]


def performance_rows(summary: BatchSummary) -> list[dict]:
    rows = []
    runs = [c for c in summary.columns if c.kind == "run"]
    pairs = [c for c in summary.columns if c.kind == "pair"]
    for test in summary.tests:
        reference = next((c.tests[test].against_time for c in runs if c.tests[test].against_time is not None), None)
        row = {"Test": test, "Reference [s]": reference}
        for column in runs:
            result = column.tests[test]
            row[f"{column.name} [s]"] = result.time
            row[f"{column.name} GPU [s]"] = result.gpu_time
            row[f"{column.name} speedup"] = result.speedup
        for column in pairs:
            row[f"{column.name} speedup"] = column.tests[test].speedup
        rows.append(row)
    return rows


def image_rows(summary: BatchSummary) -> list[dict]:
    rows = []
    for test in summary.tests:
        row = {"Test": test}
        for column in summary.columns:
            result = column.tests[test]
            row[f"{column.name} max MSE"] = result.max_mse
            row[f"{column.name} min SSIM"] = result.min_ssim
            row[f"{column.name} failed images"] = f"{result.image_failures}/{result.images}" if result.images else ""
        rows.append(row)
    return rows


def problem_rows(summary: BatchSummary) -> list[dict]:
    return [{"Run": p.column, "Test": p.test, "Verdict": p.verdict, "Detail": p.detail} for p in summary.problems()]


def settings_rows(summary: BatchSummary) -> list[dict]:
    rows = [
        {"Setting": "Batch", "Value": summary.name},
        {"Setting": "Created", "Value": summary.created},
        {"Setting": "Program", "Value": summary.program},
        {"Setting": "Folder", "Value": str(summary.folder)},
    ]
    if summary.description:
        rows.append({"Setting": "Description", "Value": summary.description})
    rows += [{"Setting": key, "Value": str(value)} for key, value in summary.settings.items()]
    reference = summary.reference
    if reference is None:
        rows.append({"Setting": "References", "Value": "existing references (no reference step)"})
    else:
        failed = len(summary.reference_failures)
        rows.append({"Setting": "References", "Value": f"generated: {len(reference) - failed} ok, {failed} failed"})
    return rows
