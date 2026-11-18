"""Redshift test runner: render test scenes, compare images and render times with references or another run.

Exit codes: 0 = everything passed, 1 = failed renders or image mismatches, 2 = invalid arguments or configuration.
"""

import sys

import colorama
from colorama import Fore

from testrunner.analysis_tasks import ComparisonSide, run_image_analysis, run_performance_analysis
from testrunner.batch import run_batch
from testrunner.cli import parameters_from_args, parse_arguments
from testrunner.render_tasks import RenderTask
from testrunner.utils import RunnerError, colored, print_error

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def main(argv: list[str] | None = None) -> int:
    colorama.just_fix_windows_console()
    print(colored("Redshift Unit Tests", Fore.BLUE))
    try:
        args = parse_arguments(argv)
        if args.batch or args.resume:
            ok = run_batch(args)
        else:
            params = parameters_from_args(args)
            for line in params.describe():
                print(f"\t{line}")
            if params.is_analysis:
                ok = True
                side = ComparisonSide.for_params(params)
                if params.performance_analysis:
                    run_performance_analysis(params, params.analysis_path, side)
                if params.image_analysis:
                    analyzer = run_image_analysis(params, params.analysis_path, side)
                    ok = analyzer is None or not analyzer.failures
            else:
                task = RenderTask(params)
                task.execute()
                ok = not task.failures
                if not params.reference:
                    analyzer = run_image_analysis(params, task.results_path, ComparisonSide.references(params))
                    ok = (analyzer is None or not analyzer.failures) and ok
    except RunnerError as err:
        print_error(str(err))
        return EXIT_USAGE

    print(colored("\nRedshift Unit Tests Finished", Fore.BLUE))
    return EXIT_OK if ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
