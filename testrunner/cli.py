"""Command line and configuration: everything a run needs, validated up front."""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .utils import PROJECT_ROOT, RunnerError, get_os_tag, print_warning, resolve_path

PROGRAMS = ("redshiftCmdLine", "redshiftBenchmark")

# Render options file passed to redshiftCmdLine (-oro) per GPU backend, relative to the project root.
OPTIONS_FILES = {
    "compute": "options_compute.txt",
    "rt": "options_rt.txt",
}

DEFAULT_PATH_OVERRIDES = "config/path_overrides.json"
DEFAULT_TIMEOUT = 3600.0


@dataclass
class ExecutionParameters:
    program: str
    tests: list[Path] = field(default_factory=list)
    gpus: list[str] = field(default_factory=lambda: ["0"])
    reference: bool = False
    use_rt: bool = False
    override_features: bool = False
    timeout: float | None = DEFAULT_TIMEOUT
    threshold: float = 0.95
    min_ssim: float | None = None
    performance_analysis: bool = False
    image_analysis: bool = False
    analysis_path: Path | None = None
    baseline_path: Path | None = None
    config: dict = field(default_factory=dict)  # merged 'required' and 'optional' sections of config.<os>.json
    user_config: dict = field(default_factory=dict)  # merged sections of config.<os>.user.json
    root_path: Path = PROJECT_ROOT
    output_path: Path | None = None  # test run folder, default results/<date>_<run tag>

    @property
    def is_analysis(self) -> bool:
        return self.performance_analysis or self.image_analysis

    @property
    def backend(self) -> str:
        return "rt" if self.use_rt else "compute"

    @property
    def run_tag(self) -> str:
        """Backend, plus '_override' with --override_features: names the results folder of a test run."""
        return f"{self.backend}_override" if self.override_features else self.backend

    @property
    def options_file(self) -> Path:
        return self.root_path / OPTIONS_FILES[self.backend]

    @property
    def scenes_root(self) -> Path:
        return Path(self.user_config.get("redshift_project_root") or self.root_path / "scenes")

    @property
    def executable(self) -> Path:
        return Path(self.config.get(self.program, ""))

    @property
    def references_path(self) -> Path:
        return self.root_path / "references" / self.program

    @property
    def path_overrides_file(self) -> Path | None:
        # Defaults to config/path_overrides.json; set "path_overrides": "" in the config to disable.
        value = self.config.get("path_overrides", DEFAULT_PATH_OVERRIDES)
        return resolve_path(value) if value else None

    def validate(self) -> None:
        if self.is_analysis:
            self._validate_analysis()
        else:
            self._validate_render()

    def _validate_analysis(self) -> None:
        if self.reference:
            raise RunnerError("--reference cannot be combined with --performance-analysis/--image-analysis")
        if not self.analysis_path:
            raise RunnerError("--analysis-path is required with --performance-analysis/--image-analysis")
        if not self.analysis_path.is_dir():
            raise RunnerError(f"Results folder does not exist: {self.analysis_path}")
        if self.baseline_path and not self.baseline_path.is_dir():
            raise RunnerError(f"Baseline results folder does not exist: {self.baseline_path}")

    def _validate_render(self) -> None:
        if not self.tests:
            raise RunnerError("--test is required to render (or use --performance-analysis/--image-analysis)")
        if self.baseline_path or self.analysis_path:
            raise RunnerError("--baseline-path/--analysis-path require --performance-analysis or --image-analysis")
        if self.use_rt and self.program == "redshiftBenchmark":
            raise RunnerError(
                "--use_rt is only supported with --program redshiftCmdLine: redshiftBenchmark has no "
                "render options file and cannot enable the HART RT backend"
            )
        if not self.config.get(self.program):
            raise RunnerError(f"Config: '{self.program}' (path to the executable) is missing")
        if not self.executable.is_file():
            raise RunnerError(f"Config: {self.program} does not exist: {self.executable}")
        if self.program == "redshiftCmdLine" and not self.options_file.is_file():
            raise RunnerError(f"Render options file for the {self.backend} backend does not exist: {self.options_file}")
        if not self.scenes_root.is_dir():
            raise RunnerError(f"User config: scenes folder [redshift_project_root] does not exist: {self.scenes_root}")
        for key in ("redshift_coredata_path", "redshift_localdata_path"):
            value = self.config.get(key)
            if value and not Path(value).is_dir():
                raise RunnerError(f"Config: [{key}] does not exist: {value}")
        overrides = self.path_overrides_file
        if overrides and not overrides.is_file():
            raise RunnerError(f"Config: path overrides file [path_overrides] does not exist: {overrides}")

    def describe(self) -> list[str]:
        if self.is_analysis:
            against = self.baseline_path or self.references_path
            kinds = " + ".join(
                k for k, on in (("performance", self.performance_analysis), ("image", self.image_analysis)) if on
            )
            return [f"{kinds} analysis of {self.analysis_path}", f"compared against {against}"]
        mode = "reference generation" if self.reference else "test run"
        lines = [
            f"{mode}: {self.program} [{self.backend}] on GPU {','.join(self.gpus)}",
            f"executable: {self.executable}",
            f"tests: {', '.join(str(t) for t in self.tests)}",
            f"scenes: {self.scenes_root}",
        ]
        if self.program == "redshiftCmdLine":
            lines.append(f"options: {self.options_file.name}")
        if self.override_features:
            lines.append("per-scene render options: on (--override_features)")
        return lines


def _load_config(path: Path, required: bool) -> dict:
    """Merges the 'required' and 'optional' sections (plain top-level keys are accepted too)."""
    if not path.is_file():
        if required:
            raise RunnerError(f"Config file does not exist: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise RunnerError(f"Invalid config file {path}: {err}") from None
    merged = {k: v for k, v in data.items() if not isinstance(v, dict)}
    merged.update(data.get("required", {}))
    merged.update(data.get("optional", {}))
    return merged


def build_parser() -> argparse.ArgumentParser:
    os_tag = get_os_tag()
    parser = argparse.ArgumentParser(
        description="Run Redshift test scenes, compare the images and timings with references or another run."
    )
    parser.add_argument(
        "--program", choices=PROGRAMS, help="Redshift program used to render (required without --batch)"
    )
    parser.add_argument(
        "--test", action="append", default=[], help="Test JSON file to run (repeatable), e.g. tests/all.json"
    )
    parser.add_argument(
        "--gpu", nargs="+", action="extend", help="GPU(s) to render on, e.g. --gpu 0 or --gpu 0,1 (default: 0)"
    )
    parser.add_argument(
        "--reference", action="store_true", help="Generate references in references/<program> instead of a test run"
    )
    parser.add_argument(
        "--use_rt",
        "--use-rt",
        dest="use_rt",
        action="store_true",
        help=f"Render with the RT backend ({OPTIONS_FILES['rt']}) instead of the compute backend "
        f"({OPTIONS_FILES['compute']}). redshiftCmdLine only",
    )
    parser.add_argument(
        "--override_features",
        "--override-features",
        dest="override_features",
        action="store_true",
        help="Apply the per-scene render options ('render_options' of the test entries) on top of the options "
        "file. redshiftCmdLine only",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Seconds after which a render is killed and reported as failed, 0 = no limit "
        f"(default: {DEFAULT_TIMEOUT:.0f})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.95,
        help="Images with a mean squared error (RGB, 0-255) above this value fail (default: 0.95)",
    )
    parser.add_argument("--treshold", dest="threshold", type=float, help=argparse.SUPPRESS)  # deprecated spelling
    parser.add_argument(
        "--min-ssim",
        type=float,
        help="Images with a structural similarity (SSIM) below this value fail as well (off by default)",
    )
    parser.add_argument(
        "--performance-analysis",
        action="store_true",
        help="Compare the render times of --analysis-path with the references (or --baseline-path)",
    )
    parser.add_argument(
        "--image-analysis",
        action="store_true",
        help="Compare the images of --analysis-path with the references (or --baseline-path)",
    )
    parser.add_argument("--analysis-path", help="Results folder to analyze, e.g. results/2026-11-09_231450_rt")
    parser.add_argument(
        "--baseline-path", help="Another results folder to compare --analysis-path against, instead of the references"
    )
    parser.add_argument("--config", default=f"config/config.{os_tag}.json", help="Main configuration file")
    parser.add_argument(
        "--user-config", default=f"config/config.{os_tag}.user.json", help="User configuration file (optional)"
    )
    batch = parser.add_mutually_exclusive_group()
    batch.add_argument(
        "--batch",
        help="Batch file (e.g. batches/production.json): references, several runs, analyses and a summary report. "
        "--program/--test/--gpu/--timeout/--threshold/--min-ssim given on the command line override the file",
    )
    batch.add_argument(
        "--resume", help="Continue an interrupted batch, e.g. results/batch_2026-11-12_184215_production"
    )
    parser.add_argument("--no-delete", action="store_true", help=argparse.SUPPRESS)  # deprecated, no effect
    return parser


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    if "--treshold" in argv:
        print_warning("--treshold is deprecated, use --threshold")
    if args.no_delete:
        print_warning("--no-delete is deprecated and has no effect")
    return args


def parameters_from_args(args: argparse.Namespace, validate: bool = True) -> ExecutionParameters:
    if validate and not args.program:
        raise RunnerError("--program is required")
    user_config_default = build_parser().get_default("user_config")
    params = ExecutionParameters(
        program=args.program or "",
        tests=[resolve_path(t) for t in args.test],
        gpus=args.gpu or ["0"],
        reference=args.reference,
        use_rt=args.use_rt,
        override_features=args.override_features,
        timeout=args.timeout if args.timeout and args.timeout > 0 else None,
        threshold=args.threshold,
        min_ssim=args.min_ssim,
        performance_analysis=args.performance_analysis,
        image_analysis=args.image_analysis,
        analysis_path=resolve_path(args.analysis_path) if args.analysis_path else None,
        baseline_path=resolve_path(args.baseline_path) if args.baseline_path else None,
        config=_load_config(resolve_path(args.config), required=True),
        user_config=_load_config(resolve_path(args.user_config), required=args.user_config != user_config_default),
    )
    if validate:
        params.validate()
    return params


def parse_command_line_args(argv: list[str] | None = None) -> ExecutionParameters:
    return parameters_from_args(parse_arguments(argv))
