"""Image and performance analysis of a results folder against the references or another results folder."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .cli import ExecutionParameters
from .image_analysis import ImageAnalyzer
from .performance_analysis import PerformanceAnalyzer
from .utils import RunnerError, print_warning, timestamp


@dataclass(frozen=True)
class ComparisonSide:
    """What the analyzed results are compared against."""

    path: Path
    suffix: str  # file suffix of its images and logs: 'reference' or 'result'
    label: str  # 'Reference' or 'Baseline'
    tag: str  # part of the analysis file names, e.g. 'vs_references'

    @classmethod
    def references(cls, params: ExecutionParameters) -> "ComparisonSide":
        return cls(params.references_path, "reference", "Reference", "vs_references")

    @classmethod
    def baseline(cls, path: Path, name: str | None = None) -> "ComparisonSide":
        return cls(path, "result", "Baseline", f"vs_{name or path.name}")

    @classmethod
    def for_params(cls, params: ExecutionParameters) -> "ComparisonSide":
        return cls.baseline(params.baseline_path) if params.baseline_path else cls.references(params)


def run_image_analysis(params: ExecutionParameters, results_path: Path, side: ComparisonSide) -> ImageAnalyzer | None:
    """Writes analysis/image_<tag>_<date>.json and diff plots of the mismatches. None without references."""
    if not side.path.is_dir():
        print_warning(f"no references in {side.path}, skipping the image comparison. Generate them with --reference.")
        return None
    analysis_path = results_path / "analysis"
    analyzer = ImageAnalyzer(
        side.path,
        results_path,
        params.threshold,
        params.min_ssim,
        crop_benchmark_footer=params.program == "redshiftBenchmark",
        reference_suffix=side.suffix,
        plot_folder=analysis_path / f"diffs_{side.tag}",
    )
    analyzer.analyze()
    stamp = timestamp()
    analysis_path.mkdir(parents=True, exist_ok=True)
    analyzer.save(analysis_path / f"image_{side.tag}_{stamp}.json")
    analyzer.save_mismatch(analysis_path / f"image_{side.tag}_mismatch_{stamp}.json")
    return analyzer


def run_performance_analysis(
    params: ExecutionParameters, results_path: Path, side: ComparisonSide
) -> PerformanceAnalyzer:
    """Prints a summary and writes analysis/performance_<tag>_<date>.csv/.xlsx."""
    if not side.path.is_dir():
        raise RunnerError(f"{side.label} folder does not exist: {side.path}")
    analyzer = PerformanceAnalyzer(params.program, side.path, results_path, side.suffix, side.label)
    records = analyzer.analyze()
    analyzer.print_summary()
    if not records:
        return analyzer
    analysis_path = results_path / "analysis"
    analysis_path.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    frame = pd.DataFrame(records)
    csv_file = analysis_path / f"performance_{side.tag}_{stamp}.csv"
    xlsx_file = analysis_path / f"performance_{side.tag}_{stamp}.xlsx"
    frame.to_csv(csv_file, index=False)
    frame.to_excel(xlsx_file, sheet_name="Performance", index=False)
    print(f"Saved {csv_file}\nSaved {xlsx_file}")
    return analyzer
