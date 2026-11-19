"""Batch file: which references and runs to render, which runs to compare with each other."""

import argparse
import dataclasses
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..cli import DEFAULT_TIMEOUT, OPTIONS_FILES, PROGRAMS, ExecutionParameters, build_parser
from ..utils import RunnerError, resolve_path

REFERENCE_STEP = "reference"
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")  # used as folder names
BATCH_KEYS = {
    "name",
    "description",
    "program",
    "tests",
    "gpus",
    "timeout",
    "threshold",
    "min_ssim",
    "slowdown",
    "reference",
    "runs",
    "pairs",
}
# Flags that describe a single run; in a batch they come from the batch file.
SINGLE_RUN_FLAGS = (
    "reference",
    "use_rt",
    "no_scene_options",
    "override_features",
    "performance_analysis",
    "image_analysis",
    "analysis_path",
    "baseline_path",
)


def parse_gpus(value, where: str) -> tuple[str, ...]:
    """1, "1", "0,1" or [0, 1] -> ("1",) / ("0,1",) / ("0", "1"): device indices of redshiftCmdLine -listdevices."""
    values = value if isinstance(value, list) else [value]
    gpus = tuple(str(v).strip() for v in values if not isinstance(v, bool))
    indices = [g.strip() for gpu in gpus for g in gpu.split(",")]
    if not gpus or len(gpus) != len(values) or not all(i.isdigit() for i in indices):
        raise RunnerError(f"Batch: {where}: gpu must be a GPU index or a list of them, e.g. 1 or [0, 1], got {value!r}")
    return gpus


@dataclass(frozen=True)
class RunSpec:
    name: str
    backend: str = "compute"
    scene_options: bool = True  # also render the per-scene render option variants (never for the reference)
    gpu: tuple[str, ...] | None = None  # None: the --gpu option, else the batch's 'gpus'

    @classmethod
    def from_dict(cls, data: dict, default_name: str | None = None) -> "RunSpec":
        if not isinstance(data, dict):
            raise RunnerError(f"Batch: a run must be an object, got {data!r}")
        unknown = set(data) - {"name", "backend", "scene_options", "override_features", "gpu"}
        if unknown:
            raise RunnerError(f"Batch: unknown keys {sorted(unknown)} in run {data}")
        # 'override_features' is the deprecated name of 'scene_options' (batch folders of older versions).
        scene_options = data.get("scene_options", data.get("override_features", True))
        name = data.get("name", default_name)
        gpu = parse_gpus(data["gpu"], f"run {name}") if data.get("gpu") is not None else None
        spec = cls(name, data.get("backend", "compute"), scene_options, gpu)
        if not isinstance(spec.name, str) or not NAME_PATTERN.fullmatch(spec.name):
            raise RunnerError(f"Batch: invalid run name {spec.name!r} (letters, digits, '_' and '-')")
        if spec.backend not in OPTIONS_FILES:
            raise RunnerError(f"Batch: run {spec.name}: backend must be one of {', '.join(OPTIONS_FILES)}")
        if not isinstance(spec.scene_options, bool):
            raise RunnerError(f"Batch: run {spec.name}: scene_options must be true or false")
        return spec

    def describe(self) -> str:
        return self.backend + ("" if self.scene_options else " without scene options")


@dataclass(frozen=True)
class PairSpec:
    """Compares the results of run `result` with those of run `baseline` (speedup > 1 = result is faster)."""

    baseline: str
    result: str

    @property
    def name(self) -> str:
        return f"{self.result}_vs_{self.baseline}"


@dataclass
class BatchConfig:
    name: str
    program: str
    tests: list[str]
    runs: list[RunSpec]
    reference: RunSpec | None = None
    pairs: list[PairSpec] = field(default_factory=list)
    gpus: list[str] = field(default_factory=lambda: ["0"])
    timeout: float | None = DEFAULT_TIMEOUT
    threshold: float = 0.95
    min_ssim: float | None = None
    slowdown: float = 0.9  # a speedup below this marks the test as slower
    description: str = ""

    @classmethod
    def load(cls, path: Path) -> "BatchConfig":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RunnerError(f"Batch file does not exist: {path}") from None
        except json.JSONDecodeError as err:
            raise RunnerError(f"Invalid batch file {path}: {err}") from None
        return cls.from_dict(data, path.stem)

    @classmethod
    def from_dict(cls, data: dict, default_name: str) -> "BatchConfig":
        unknown = set(data) - BATCH_KEYS
        if unknown:
            raise RunnerError(f"Batch: unknown keys {sorted(unknown)}, expected some of {sorted(BATCH_KEYS)}")
        reference = data.get("reference")
        tests = data.get("tests", [])
        pairs = data.get("pairs", [])
        if any(not isinstance(p, dict) or set(p) != {"baseline", "result"} for p in pairs):
            raise RunnerError(f"Batch: pairs must be {{'baseline': <run>, 'result': <run>}}, got {pairs}")
        config = cls(
            name=data.get("name", default_name),
            program=data.get("program", ""),
            tests=[tests] if isinstance(tests, str) else list(tests),
            runs=[RunSpec.from_dict(run) for run in data.get("runs", [])],
            reference=RunSpec.from_dict(reference, REFERENCE_STEP) if reference is not None else None,
            pairs=[PairSpec(p["baseline"], p["result"]) for p in pairs],
            gpus=[str(g) for g in data.get("gpus", ["0"])],
            timeout=data.get("timeout", DEFAULT_TIMEOUT) or None,
            threshold=data.get("threshold", 0.95),
            min_ssim=data.get("min_ssim"),
            slowdown=data.get("slowdown", 0.9),
            description=data.get("description", ""),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not NAME_PATTERN.fullmatch(str(self.name)):
            raise RunnerError(f"Batch: invalid name {self.name!r} (letters, digits, '_' and '-')")
        if self.program not in PROGRAMS:
            raise RunnerError(f"Batch: 'program' must be one of {', '.join(PROGRAMS)}")
        if not self.tests:
            raise RunnerError("Batch: 'tests' (test JSON files) is empty")
        if not self.runs:
            raise RunnerError("Batch: 'runs' is empty")
        names = [run.name for run in self.runs]
        invalid = sorted({n for n in names if names.count(n) > 1} | ({REFERENCE_STEP} & set(names)))
        if invalid:
            raise RunnerError(f"Batch: run names must be unique and not '{REFERENCE_STEP}': {invalid}")
        for pair in self.pairs:
            if pair.baseline not in names or pair.result not in names or pair.baseline == pair.result:
                raise RunnerError(f"Batch: pair {pair} must name two different runs of {names}")

    def apply_cli(self, args: argparse.Namespace) -> None:
        """Command line values override the batch file; single-run flags are rejected."""
        given = [f"--{flag}" for flag in SINGLE_RUN_FLAGS if getattr(args, flag)]
        if given:
            raise RunnerError(f"{', '.join(given)} cannot be used with a batch, set the runs in the batch file")
        defaults = build_parser()
        if args.program:
            self.program = args.program
        if args.test:
            self.tests = list(args.test)
        if args.gpu:  # does not replace the 'gpu' of a run, which names the GPU that run needs
            self.gpus = list(args.gpu)
        if args.timeout != defaults.get_default("timeout"):
            self.timeout = args.timeout if args.timeout and args.timeout > 0 else None
        if args.threshold != defaults.get_default("threshold"):
            self.threshold = args.threshold
        if args.min_ssim is not None:
            self.min_ssim = args.min_ssim
        self.validate()

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        if self.reference is None:
            del data["reference"]
        for step in data["runs"] + ([data["reference"]] if self.reference else []):
            if step["gpu"] is None:
                del step["gpu"]
        return data

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=4) + "\n", encoding="utf-8")

    def steps(self) -> list[RunSpec]:
        """Render steps in order: the references (if any), then the runs."""
        return ([self.reference] if self.reference else []) + self.runs

    def step_gpus(self, step: RunSpec) -> list[str]:
        return list(step.gpu) if step.gpu else list(self.gpus)

    def parameters(self, base: ExecutionParameters, step: RunSpec, batch_folder: Path) -> ExecutionParameters:
        is_reference = self.reference is not None and step.name == self.reference.name
        return dataclasses.replace(
            base,
            program=self.program,
            tests=[resolve_path(t) for t in self.tests],
            gpus=self.step_gpus(step),
            reference=is_reference,
            use_rt=step.backend == "rt",
            scene_options=step.scene_options,
            timeout=self.timeout,
            threshold=self.threshold,
            min_ssim=self.min_ssim,
            output_path=None if is_reference else batch_folder / step.name,
        )
