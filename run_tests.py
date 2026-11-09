"""Redshift test runner: render test scenes, compare images and render times with references or another run.

Exit codes: 0 = everything passed, 1 = failed renders or image mismatches, 2 = invalid arguments or configuration.
"""

import sys
from pathlib import Path

import colorama
import pandas as pd
from colorama import Fore

from testrunner.cli import ExecutionParameters, parse_command_line_args
from testrunner.image_analysis import ImageAnalyzer
from testrunner.performance_analysis import PerformanceAnalyzer
from testrunner.render_tasks import RenderTask
from testrunner.utils import RunnerError, colored, print_error, print_warning, timestamp

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def comparison_side(params: ExecutionParameters) -> tuple[Path, str, str, str]:
    """(folder, file suffix, label, output tag) of what the analyzed results are compared against."""
    if params.baseline_path:
        return params.baseline_path, "result", "Baseline", f"vs_{params.baseline_path.name}"
    return params.references_path, "reference", "Reference", "vs_references"


def run_image_analysis(params: ExecutionParameters, results_path: Path) -> bool:
    """True if all images match. Writes analysis/image_<tag>_<date>.json and diff plots of the mismatches."""
    reference_path, suffix, _, tag = comparison_side(params)
    if not reference_path.is_dir():
        print_warning(
            f"no references in {reference_path}, skipping the image comparison. Generate them with --reference."
        )
        return True
    analysis_path = results_path / "analysis"
    analyzer = ImageAnalyzer(
        reference_path,
        results_path,
        params.threshold,
        params.min_ssim,
        crop_benchmark_footer=params.program == "redshiftBenchmark",
        reference_suffix=suffix,
        plot_folder=analysis_path / f"diffs_{tag}",
    )
    analyzer.analyze()
    stamp = timestamp()
    analysis_path.mkdir(parents=True, exist_ok=True)
    analyzer.save(analysis_path / f"image_{tag}_{stamp}.json")
    analyzer.save_mismatch(analysis_path / f"image_{tag}_mismatch_{stamp}.json")
    return not analyzer.failures


def run_performance_analysis(params: ExecutionParameters, results_path: Path) -> None:
    """Prints a summary and writes analysis/performance_<tag>_<date>.csv/.xlsx."""
    reference_path, suffix, label, tag = comparison_side(params)
    if not reference_path.is_dir():
        raise RunnerError(f"{label} folder does not exist: {reference_path}")
    analyzer = PerformanceAnalyzer(params.program, reference_path, results_path, suffix, label)
    records = analyzer.analyze()
    analyzer.print_summary()
    if not records:
        return
    analysis_path = results_path / "analysis"
    analysis_path.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    frame = pd.DataFrame(records)
    csv_file = analysis_path / f"performance_{tag}_{stamp}.csv"
    xlsx_file = analysis_path / f"performance_{tag}_{stamp}.xlsx"
    frame.to_csv(csv_file, index=False)
    frame.to_excel(xlsx_file, sheet_name="Performance", index=False)
    print(f"Saved {csv_file}\nSaved {xlsx_file}")


def main(argv: list[str] | None = None) -> int:
    colorama.just_fix_windows_console()
    print(colored("Redshift Unit Tests", Fore.BLUE))
    try:
        params = parse_command_line_args(argv)
        for line in params.describe():
            print(f"\t{line}")

        if params.is_analysis:
            ok = True
            if params.performance_analysis:
                run_performance_analysis(params, params.analysis_path)
            if params.image_analysis:
                ok = run_image_analysis(params, params.analysis_path)
        else:
            task = RenderTask(params)
            task.execute()
            ok = not task.failures
            if not params.reference:
                ok = run_image_analysis(params, task.results_path) and ok
    except RunnerError as err:
        print_error(str(err))
        return EXIT_USAGE

    print(colored("\nRedshift Unit Tests Finished", Fore.BLUE))
    return EXIT_OK if ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
