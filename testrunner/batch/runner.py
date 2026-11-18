"""Runs a batch: references, renders of every run, analyses against the references and between runs, summary."""

import argparse
from datetime import datetime
from pathlib import Path

from colorama import Fore

from ..analysis_tasks import ComparisonSide, run_image_analysis, run_performance_analysis
from ..cli import ExecutionParameters, parameters_from_args
from ..render_tasks import RenderTask
from ..utils import PROJECT_ROOT, RunnerError, colored, print_warning, resolve_path, timestamp
from .config import BatchConfig, RunSpec
from .reports import print_console, write_reports
from .state import BatchState
from .summary import BatchSummary, ColumnSummary, build_column

BATCH_FILE = "batch.json"
SUMMARY_FOLDER = "summary"


class BatchRunner:
    def __init__(self, config: BatchConfig, folder: Path, base: ExecutionParameters):
        self.config = config
        self.folder = folder
        self.params = {step.name: config.parameters(base, step, folder) for step in config.steps()}
        for params in self.params.values():
            params.validate()
        self.state = BatchState(folder)

    def describe(self) -> list[str]:
        lines = [f"batch {self.config.name}: {self.folder}"]
        for step in self.config.steps():
            done = " (done)" if self.state.is_complete(step.name) else ""
            target = "references/" + self.config.program if self.params[step.name].reference else step.name
            lines.append(f"  {step.name}: {step.describe()} -> {target}{done}")
        lines += [f"  compare {p.result} with {p.baseline}" for p in self.config.pairs]
        return lines

    def render(self) -> None:
        interrupted = self.state.close_interrupted()
        if interrupted:
            print_warning(f"{interrupted} was rendering when the batch stopped; marked failed, not rendered again")
        for step in self.config.steps():
            if self.state.is_complete(step.name):
                print(f"\n{colored(step.name, Fore.MAGENTA)}: already done")
                continue
            print(f"\n{colored(step.name, Fore.MAGENTA)}: {step.describe()}")
            self._render_step(step)

    def _render_step(self, step: RunSpec) -> None:
        task = RenderTask(
            self.params[step.name],
            skip=self.state.done_tests(step.name),
            on_start=lambda scene: self.state.start(step.name, scene),
            on_outcome=lambda outcome: self.state.record(step.name, outcome),
        )
        task.execute()
        self.state.complete(step.name, task.redshift_version)

    def analyze(self) -> list[ColumnSummary]:
        columns = []
        tests = self.tests()
        any_params = next(iter(self.params.values()))
        references = ComparisonSide.references(any_params)
        has_references = references.path.is_dir()
        if not has_references:
            print_warning(f"no references in {references.path}: the runs cannot be compared with them")
        for run in self.config.runs:
            params, run_folder = self.params[run.name], self.folder / run.name
            print(f"\n{colored(run.name, Fore.MAGENTA)} vs references")
            images = run_image_analysis(params, run_folder, references) if has_references else None
            perf = run_performance_analysis(params, run_folder, references) if has_references else None
            column = build_column(
                run.name,
                "run",
                "references",
                run.describe(),
                tests,
                self.state.outcomes(run.name),
                images,
                perf,
                self.config.program,
                self.config.slowdown,
                self.folder,
            )
            column.redshift_version = self.state.data["redshift_version"].get(run.name)
            columns.append(column)
        for pair in self.config.pairs:
            params, result_folder = self.params[pair.result], self.folder / pair.result
            side = ComparisonSide.baseline(self.folder / pair.baseline, pair.baseline)
            print(f"\n{colored(pair.result, Fore.MAGENTA)} vs {pair.baseline}")
            column = build_column(
                pair.name,
                "pair",
                pair.baseline,
                f"{pair.result} vs {pair.baseline}",
                tests,
                self.state.outcomes(pair.result),
                run_image_analysis(params, result_folder, side),
                run_performance_analysis(params, result_folder, side),
                self.config.program,
                self.config.slowdown,
                self.folder,
                baseline_outcomes=self.state.outcomes(pair.baseline),
            )
            column.redshift_version = self.state.data["redshift_version"].get(pair.result)
            columns.append(column)
        return columns

    def tests(self) -> list[str]:
        """Every test the batch rendered, in render order."""
        names: dict[str, None] = {}
        for step in self.config.steps():
            names.update(dict.fromkeys(self.state.outcomes(step.name)))
        return list(names)

    def summarize(self, columns: list[ColumnSummary]) -> BatchSummary:
        config = self.config
        return BatchSummary(
            name=config.name,
            folder=self.folder,
            created=datetime.now().isoformat(timespec="seconds"),
            program=config.program,
            description=config.description,
            settings={
                "tests": ", ".join(config.tests),
                "gpus": ", ".join(config.gpus),
                "timeout [s]": config.timeout or "none",
                "image threshold (mse)": config.threshold,
                "min ssim": config.min_ssim if config.min_ssim is not None else "off",
                "slower below speedup": config.slowdown,
            },
            tests=self.tests(),
            reference=self.state.outcomes(config.reference.name) if config.reference else None,
            columns=columns,
        )

    def execute(self) -> bool:
        self.render()
        summary = self.summarize(self.analyze())
        files = write_reports(summary, self.folder / SUMMARY_FOLDER)
        print_console(summary)
        for file in files:
            print(f"\tSaved {file}")
        return summary.ok


def run_batch(args: argparse.Namespace) -> bool:
    """Starts (--batch) or resumes (--resume) a batch; True if it passed."""
    if args.resume:
        folder = resolve_path(args.resume)
        if not (folder / BATCH_FILE).is_file():
            raise RunnerError(f"Not a batch folder (no {BATCH_FILE}): {folder}")
        config = BatchConfig.load(folder / BATCH_FILE)
    else:
        config = BatchConfig.load(resolve_path(args.batch))
        folder = PROJECT_ROOT / "results" / f"batch_{timestamp()}_{config.name}"
    config.apply_cli(args)
    runner = BatchRunner(config, folder, parameters_from_args(args, validate=False))
    folder.mkdir(parents=True, exist_ok=True)
    config.save(folder / BATCH_FILE)
    for line in runner.describe():
        print(f"\t{line}")
    return runner.execute()
