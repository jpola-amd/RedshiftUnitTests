"""Batch summary: a verdict per test and run (vs references) or pair (run vs run), plus an overview per column."""

import math
from dataclasses import dataclass, field
from pathlib import Path

from ..image_analysis import ImageAnalyzer
from ..performance_analysis import PerformanceAnalyzer, speedup

# Time used for speedups: the full render of redshiftCmdLine, the only time redshiftBenchmark reports.
MAIN_TIME = {"redshiftCmdLine": "Total", "redshiftBenchmark": "Time"}
BAD = ("failed", "image mismatch")
WARN = ("no reference", "slower")
VERDICTS = (*BAD, *WARN, "ok", "skipped", "not run")
WORST = 5


@dataclass
class TestResult:
    verdict: str
    detail: str = ""
    time: float | None = None  # main time of the run (result side of a pair)
    against_time: float | None = None  # main time of the reference (baseline run of a pair)
    speedup: float | None = None  # against_time / time: above 1 = faster
    gpu_time: float | None = None
    images: int = 0
    image_failures: int = 0
    max_mse: float | None = None
    min_ssim: float | None = None
    diff_plots: list[str] = field(default_factory=list)  # relative to the batch folder


@dataclass
class ColumnSummary:
    name: str
    kind: str  # 'run' (vs references) or 'pair' (run vs run)
    against: str  # 'references' or the baseline run
    description: str
    tests: dict[str, TestResult]
    redshift_version: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        return {v: sum(r.verdict == v for r in self.tests.values()) for v in VERDICTS}

    @property
    def images(self) -> tuple[int, int]:
        return sum(r.images for r in self.tests.values()), sum(r.image_failures for r in self.tests.values())

    def _timed(self) -> list[tuple[str, float, float]]:
        return [(n, r.against_time, r.time) for n, r in self.tests.items() if r.speedup is not None]

    @property
    def total_speedup(self) -> float | None:
        timed = self._timed()
        return speedup(sum(a for _, a, _ in timed), sum(t for _, _, t in timed)) if timed else None

    @property
    def geomean_speedup(self) -> float | None:
        timed = self._timed()
        return math.exp(sum(math.log(a / t) for _, a, t in timed) / len(timed)) if timed else None

    @property
    def timed_tests(self) -> int:
        return len(self._timed())

    @property
    def slowest(self) -> list[tuple[str, float]]:
        return sorted(((n, r.speedup) for n, r in self.tests.items() if r.speedup is not None), key=lambda x: x[1])[
            :WORST
        ]

    @property
    def has_failures(self) -> bool:
        return any(r.verdict in BAD for r in self.tests.values())


@dataclass
class Problem:
    column: str
    test: str
    verdict: str
    detail: str


@dataclass
class BatchSummary:
    name: str
    folder: Path
    created: str
    program: str
    description: str
    settings: dict  # threshold, min_ssim, slowdown, gpus, tests
    tests: list[str]
    reference: dict[str, dict] | None  # outcomes of the reference step, None if the batch used existing references
    columns: list[ColumnSummary]

    @property
    def reference_failures(self) -> list[tuple[str, str]]:
        return [(n, o["message"]) for n, o in (self.reference or {}).items() if o["status"] == "failed"]

    @property
    def ok(self) -> bool:
        """No failed reference render, no failed render or image mismatch of a run (pairs are informative)."""
        return not self.reference_failures and not any(c.has_failures for c in self.columns if c.kind == "run")

    def problems(self) -> list[Problem]:
        found = [Problem("reference", n, "failed", m) for n, m in self.reference_failures]
        for verdicts in (BAD, WARN):
            for column in self.columns:
                for test, result in column.tests.items():
                    if result.verdict in verdicts:
                        found.append(Problem(column.name, test, result.verdict, result.detail))
        return found


def test_of(image_name: str) -> str:
    """'<test>.<output name>' -> '<test>' (test names contain no dots)."""
    return image_name.split(".", 1)[0]


def build_column(
    name: str,
    kind: str,
    against: str,
    description: str,
    tests: list[str],
    outcomes: dict[str, dict],
    images: ImageAnalyzer | None,
    perf: PerformanceAnalyzer | None,
    program: str,
    slowdown: float,
    batch_folder: Path,
    baseline_outcomes: dict[str, dict] | None = None,
) -> ColumnSummary:
    """Verdict per test. `outcomes`: render outcomes of the run (of the result run for a pair, plus its baseline's)."""
    by_test: dict[str, list] = {}
    for comparison in images.comparisons if images else []:
        by_test.setdefault(test_of(comparison.name), []).append(comparison)
    missing = {test_of(p.name) for p in images.missing_references} if images else set()
    timings = {c.name: c for c in perf.comparisons} if perf else {}
    kind_of_time = MAIN_TIME[program]

    results = {}
    for test in tests:
        result = TestResult("ok")
        comparisons = by_test.get(test, [])
        result.images = len(comparisons)
        result.image_failures = sum(c.failed for c in comparisons)
        mses = [c.mse for c in comparisons if c.mse is not None]
        ssims = [c.ssim for c in comparisons if c.ssim is not None]
        result.max_mse = max(mses) if mses else None
        result.min_ssim = min(ssims) if ssims else None
        if images and images.plot_folder:
            for c in comparisons:
                plot = images.plot_folder / f"{c.name}.diff.png"
                if c.failed and plot.is_file():
                    result.diff_plots.append(plot.relative_to(batch_folder).as_posix())
        timing = timings.get(test)
        if timing:
            result.time = timing.result.times.get(kind_of_time)
            result.against_time = timing.reference.times.get(kind_of_time)
            result.gpu_time = timing.result.times.get("GPU")
            result.speedup = speedup(result.against_time, result.time)

        outcome = outcomes.get(test)
        baseline = baseline_outcomes.get(test) if baseline_outcomes is not None else None
        errors = [c.error for c in comparisons if c.error]
        if outcome is None or (baseline_outcomes is not None and baseline is None):
            result.verdict = "not run"
        elif outcome["status"] == "failed":
            result.verdict, result.detail = "failed", outcome["message"]
        elif baseline is not None and baseline["status"] == "failed":
            result.verdict, result.detail = "failed", f"baseline {against}: {baseline['message']}"
        elif outcome["status"] == "skipped" or (baseline is not None and baseline["status"] == "skipped"):
            result.verdict, result.detail = "skipped", outcome["message"] or (baseline or {}).get("message", "")
        elif result.image_failures:
            result.verdict = "image mismatch"
            result.detail = errors[0] if errors else f"max mse {result.max_mse:.3f}, min ssim {result.min_ssim:.4f}"
        elif not comparisons or test in missing:
            result.verdict, result.detail = "no reference", f"no {against} image to compare with"
        elif result.speedup is not None and result.speedup < slowdown:
            result.verdict, result.detail = (
                "slower",
                f"{result.speedup:.3f}x ({result.against_time:.2f}s -> {result.time:.2f}s)",
            )
        results[test] = result
    return ColumnSummary(name, kind, against, description, results)
