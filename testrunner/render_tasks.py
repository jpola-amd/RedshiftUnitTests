"""Rendering the test scenes with redshiftCmdLine or redshiftBenchmark, for test runs and reference generation."""

import itertools
import json
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from colorama import Fore

from .cli import OPTIONS_FILES, ExecutionParameters
from .utils import (
    VARIANT_SEPARATOR,
    RunnerError,
    base_test_name,
    colored,
    find_log_error,
    find_redshift_version,
    get_os_tag,
    gpu_arguments,
    load_tests,
    print_error,
    print_warning,
    read_text,
    redshift_log_file,
    run_process,
    timestamp,
    variant_name,
    write_path_override_file,
)

BENCHMARK_OUTPUT = "redshiftBenchmarkOutput.png"

# Sections of a test entry's "render_options": "all" applies to every backend, a backend section wins over it.
RENDER_OPTION_SECTIONS = ("all", *OPTIONS_FILES)
# Written to the options file of every render (references included) of a test with "skipdenoise": true.
NO_DENOISE_OPTIONS = {"DenoiseEngine": "RS_DENOISEENGINE_NONE"}
RESOLUTION_PATTERN = re.compile(r"[1-9]\d*x[1-9]\d*")
# Entry of fixed_options (any case) passed to redshiftCmdLine as -ores <w>x<h> instead of the options file.
RESOLUTION_KEY = "resolution"


def _flag(entry: dict, key: str) -> bool:
    value = entry.get(key, False)
    return value is True or str(value).lower() == "true"


def _option_value(value) -> str:
    return str(value) if not isinstance(value, bool) else ("True" if value else "False")


def merge_render_options(base_text: str, overrides: dict[str, str]) -> str:
    """Options file text with the 'Name Value' lines of `overrides` replaced (case-insensitive name) or appended."""
    pending = {name.lower(): (name, value) for name, value in overrides.items()}
    lines = []
    for line in base_text.splitlines():
        parts = line.split(maxsplit=1)
        if parts and parts[0].lower() in pending:
            name, value = pending.pop(parts[0].lower())
            line = f"{name} {value}"
        lines.append(line)
    lines.extend(f"{name} {value}" for name, value in pending.values())
    return "\n".join(lines) + "\n"


def _valid_option_value(value, allow_list: bool = True) -> bool:
    scalar = (str, int, float, bool)
    if isinstance(value, list):
        return allow_list and bool(value) and all(isinstance(v, scalar) for v in value)
    return isinstance(value, scalar)


def _option_sections(entry: dict, key: str, allow_lists: bool) -> dict:
    """The {section: {option: value}} of a test entry key ('render_options' or 'fixed_options'), validated."""
    options = entry.get(key, {})
    if not isinstance(options, dict) or any(
        section not in RENDER_OPTION_SECTIONS
        or not isinstance(values, dict)
        or not all(_valid_option_value(v, allow_lists) for v in values.values())
        for section, values in options.items()
    ):
        value = "value or [values]" if allow_lists else "value (a sweep belongs in render_options)"
        raise RunnerError(
            f"Invalid {key} of test {entry['test_name']}: {options!r}; expected "
            f"{{section: {{option: {value}}}}} with sections {', '.join(RENDER_OPTION_SECTIONS)}"
        )
    for values in options.values():
        for name, value in values.items():
            if name.lower() != RESOLUTION_KEY:
                continue
            if allow_lists:
                raise RunnerError(
                    f"Invalid {key} of test {entry['test_name']}: '{name}' changes the image size, which then "
                    f"differs from the reference; set it in fixed_options"
                )
            if not isinstance(value, str) or not RESOLUTION_PATTERN.fullmatch(value):
                raise RunnerError(
                    f"Invalid {key} of test {entry['test_name']}: {name} {value!r}; expected '<w>x<h>', e.g. 960x540"
                )
    return options


@dataclass(frozen=True)
class Scene:
    name: str
    path: Path
    skip_postfx: bool = False
    render_options: dict = field(default_factory=dict, compare=False)  # {section: {option: value | [values]}}
    options: dict = field(default_factory=dict, compare=False)  # options applied by this render: {option: value}
    skip_denoise: bool = False
    fixed_options: dict = field(default_factory=dict, compare=False)  # {section: {option: value}}, every render
    skip_default: bool = False  # with render option variants: do not render the plain options file as well

    @classmethod
    def from_entry(cls, entry: dict, scenes_root: Path) -> "Scene":
        return cls(
            entry["test_name"],
            scenes_root / entry["path_to_scene"],
            _flag(entry, "skippostfx"),
            _option_sections(entry, "render_options", allow_lists=True),
            skip_denoise=_flag(entry, "skipdenoise"),
            fixed_options=_option_sections(entry, "fixed_options", allow_lists=False),
            skip_default=_flag(entry, "skipdefault"),
        )

    @property
    def base_name(self) -> str:
        return base_test_name(self.name)

    def _fixed(self, backend: str) -> dict:
        return {**self.fixed_options.get("all", {}), **self.fixed_options.get(backend, {})}

    def resolution(self, backend: str) -> str | None:
        """'<w>x<h>' of the 'resolution' entry of fixed_options, passed to -ores instead of the options file."""
        return next((v for k, v in self._fixed(backend).items() if k.lower() == RESOLUTION_KEY), None)

    def file_options(self, backend: str) -> dict[str, str]:
        """Options written over the options file for this render: the per-test ones of every render (skipdenoise,
        fixed_options), then the variant's."""
        fixed = {k: _option_value(v) for k, v in self._fixed(backend).items() if k.lower() != RESOLUTION_KEY}
        return {**(NO_DENOISE_OPTIONS if self.skip_denoise else {}), **fixed, **self.options}

    def options_for(self, backend: str) -> dict[str, list[str]]:
        """Values of each option for this backend: a list in the test entry is a sweep, a scalar a single value."""
        merged = {**self.render_options.get("all", {}), **self.render_options.get(backend, {})}
        return {
            name: [_option_value(v) for v in (value if isinstance(value, list) else [value])]
            for name, value in merged.items()
        }

    def option_sets(self, backend: str) -> list[dict[str, str]]:
        """Every combination of the option values for this backend; empty without render options."""
        options = self.options_for(backend)
        if not options:
            return []
        return [dict(zip(options, values, strict=True)) for values in itertools.product(*options.values())]

    def variants(self, backend: str) -> list["Scene"]:
        """This scene with the plain options file (unless skipdefault), then one render per option set."""
        variants = [
            replace(self, name=variant_name(self.name, options), options=options)
            for options in self.option_sets(backend)
        ]
        return variants if variants and self.skip_default else [self, *variants]


@dataclass
class TestOutcome:
    name: str
    scene: Path
    status: str  # 'success', 'failed' or 'skipped'
    message: str = ""
    duration: float = 0.0


class RenderTask:
    """Renders every scene of the test files into results/<date>_<run tag> (or references/<program>).

    `skip`: test names not to render (already done by an interrupted batch). `on_start`/`on_outcome` are called before
    and after each test.
    """

    def __init__(
        self,
        params: ExecutionParameters,
        skip: set[str] | None = None,
        on_outcome: Callable[[TestOutcome], None] | None = None,
        on_start: Callable[[Scene], None] | None = None,
    ):
        self.params = params
        self.suffix = "reference" if params.reference else "result"
        stamp = timestamp()
        if params.reference:
            self.output_path = params.references_path
            self.run_log = self.output_path / f"{params.program}_REFERENCE_{stamp}.json"
        else:
            self.output_path = params.output_path or params.root_path / "results" / f"{stamp}_{params.run_tag}"
            self.run_log = self.output_path / f"{params.program}_TEST_{stamp}.json"
        self.images_path = self.output_path / "images"
        self.logs_path = self.output_path / "logs"
        self.temp_path = self.output_path / "tmp"
        scenes = [Scene.from_entry(entry, params.scenes_root) for entry in load_tests(params.tests)]
        self.ignored_options = [s for s in scenes if s.option_sets(params.backend)] if not self.expands_options else []
        if self.expands_options:
            scenes = [variant for scene in scenes for variant in scene.variants(params.backend)]
        self.skipped_done = [s for s in scenes if skip and s.name in skip]
        self.scenes = [s for s in scenes if not (skip and s.name in skip)]
        self.on_outcome = on_outcome
        self.on_start = on_start
        self.env = self._environment()
        self.outcomes: list[TestOutcome] = []
        self.redshift_version: str | None = None
        self.applied_options: dict[str, dict[str, str]] = {}

    @property
    def expands_options(self) -> bool:
        """Per-scene render options are rendered as extra variants: test runs of redshiftCmdLine, not references."""
        return self.params.scene_options and not self.params.reference and self.params.program == "redshiftCmdLine"

    @property
    def results_path(self) -> Path:
        return self.output_path

    @property
    def failures(self) -> list[TestOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    def _environment(self) -> dict:
        env = os.environ.copy()
        env.pop("REDSHIFT_PATHOVERRIDE_STRING", None)
        env.pop("REDSHIFT_PATHOVERRIDE_FILE", None)
        if self.params.config.get("redshift_coredata_path"):
            env["REDSHIFT_COREDATAPATH"] = str(Path(self.params.config["redshift_coredata_path"]))
        if self.params.config.get("redshift_localdata_path"):
            env["REDSHIFT_LOCALDATAPATH"] = str(Path(self.params.config["redshift_localdata_path"]))
        return env

    def _prepare_folders(self) -> None:
        for folder in (self.images_path, self.logs_path, self.temp_path):
            folder.mkdir(parents=True, exist_ok=True)
        overrides = self.params.path_overrides_file
        if overrides:
            override_file = self.output_path / "path_overrides.txt"
            write_path_override_file(overrides, self.params.scenes_root.resolve(), override_file)
            self.env["REDSHIFT_PATHOVERRIDE_FILE"] = str(override_file)

    def _benchmark_output(self) -> Path:
        # redshiftBenchmark writes its image to the working directory on Windows and to $HOME on Linux.
        return (self.temp_path if get_os_tag() == "win" else Path.home()) / BENCHMARK_OUTPUT

    def _write_options_file(self, scene: Scene, options: dict[str, str]) -> Path:
        # Kept next to the logs so the exact options of the render can be checked afterwards.
        path = self.logs_path / f"{scene.name}.{self.suffix}.options.txt"
        path.write_text(merge_render_options(read_text(self.params.options_file), options), encoding="utf-8")
        return path

    def _command(self, scene: Scene, options_file: Path | None = None) -> list:
        gpus = gpu_arguments(self.params.gpus)
        if self.params.program == "redshiftBenchmark":
            # redshiftBenchmark enables RT by default and has no options file; --use_rt is rejected by validate().
            return [self.params.executable, scene.path, *gpus, "-nortx"]
        cmd = [
            self.params.executable,
            scene.path,
            "-oro",
            options_file or self.params.options_file,
            "-oif",
            "png",
            "-oip",
            self.temp_path,
            *gpus,
        ]
        if scene.skip_postfx:
            cmd.append("-skippostfx")
        resolution = scene.resolution(self.params.backend)
        if resolution:
            cmd += ["-ores", resolution]
        return cmd

    def _clear_temp(self) -> None:
        shutil.rmtree(self.temp_path, ignore_errors=True)
        self.temp_path.mkdir(parents=True, exist_ok=True)

    def _remove_previous_output(self, name: str) -> None:
        # References are regenerated in place; drop the old files of this test so no stale AOV/image survives.
        for folder in (self.images_path, self.logs_path):
            for old in folder.glob(f"{name}.*"):
                old.unlink()

    def _collect_images(self, name: str) -> list[Path]:
        if self.params.program == "redshiftBenchmark":
            images = [self._benchmark_output()] if self._benchmark_output().is_file() else []
        else:
            images = sorted(self.temp_path.rglob("*.png"))
        moved = []
        for image in images:
            # <test>.<original name without .png>.<result|reference>.png
            target = self.images_path / f"{name}.{image.stem}.{self.suffix}.png"
            shutil.move(str(image), str(target))
            moved.append(target)
        return moved

    def _render(self, scene: Scene) -> TestOutcome:
        if not scene.path.is_file():
            return TestOutcome(scene.name, scene.path, "failed", f"scene does not exist: {scene.path}")
        if scene.path.suffix.lower() != ".rs":
            return TestOutcome(scene.name, scene.path, "skipped", "not a Redshift scene (.rs)")

        self._clear_temp()
        if self.params.reference:
            self._remove_previous_output(scene.name)
        if self.params.program == "redshiftBenchmark":
            self._benchmark_output().unlink(missing_ok=True)

        options_file = None
        options = scene.file_options(self.params.backend)
        if options and self.params.program == "redshiftCmdLine":
            options_file = self._write_options_file(scene, options)
            self.applied_options[scene.name] = options

        console_log = self.logs_path / f"{scene.name}.{self.suffix}.console.txt"
        redshift_log = redshift_log_file(self.env)
        start = time.time()
        cwd = self.temp_path if self.params.program == "redshiftBenchmark" else None
        process = run_process(self._command(scene, options_file), self.env, console_log, self.params.timeout, cwd)
        if self.redshift_version is None:
            self.redshift_version = find_redshift_version(read_text(console_log))

        def failed(message: str) -> TestOutcome:
            return TestOutcome(scene.name, scene.path, "failed", message, process.duration)

        # Log.Latest.0 is shared by every Redshift run; only use it if this render wrote it.
        log_text = None
        if redshift_log.is_file() and redshift_log.stat().st_mtime >= start:
            shutil.copy2(redshift_log, self.logs_path / f"{scene.name}.{self.suffix}.html")
            log_text = read_text(redshift_log)

        if process.timed_out:
            return failed(f"killed after the {self.params.timeout:.0f}s timeout")
        if process.returncode != 0:
            error = find_log_error(log_text) if log_text else None
            return failed(error or f"exit code {process.returncode}, see {console_log.name}")
        if log_text is None:
            return failed(
                f"Redshift did not write a log for this render ({redshift_log}); check "
                f"redshift_localdata_path in the config"
            )
        if not self._collect_images(scene.name):
            return failed("no image was written")
        return TestOutcome(scene.name, scene.path, "success", "success", process.duration)

    def execute(self) -> list[TestOutcome]:
        self._prepare_folders()
        if self.ignored_options:
            names = ", ".join(s.name for s in self.ignored_options)
            reason = (
                "references are rendered with the plain options file"
                if self.params.reference
                else "redshiftBenchmark has no options file"
                if self.params.program == "redshiftBenchmark"
                else "--no-scene-options"
            )
            print(f"\tPer-scene render options not applied ({reason}): {names}")
        if self.params.program == "redshiftBenchmark":
            ignored = [s.name for s in self.scenes if s.skip_denoise or s.fixed_options]
            if ignored:
                print_warning(
                    "redshiftBenchmark has no options file and no -ores: skipdenoise and fixed_options "
                    f"are ignored for {', '.join(ignored)}"
                )
        variants = sum(VARIANT_SEPARATOR in s.name for s in self.scenes)
        if variants:
            print(f"\t{len(self.scenes) - variants} tests + {variants} render option variants")
        count = len(self.scenes)
        verb = "Generating" if self.params.reference else "Running test"
        if self.skipped_done:
            print(f"\t{len(self.skipped_done)} tests already done by the interrupted run, not rendered again")
        for index, scene in enumerate(self.scenes, 1):
            options = scene.file_options(self.params.backend) if self.params.program == "redshiftCmdLine" else {}
            resolution = scene.resolution(self.params.backend)
            if resolution and self.params.program == "redshiftCmdLine":
                options = {**options, "resolution": resolution}
            extra = colored(" +" + " +".join(f"{k}={v}" for k, v in options.items()), Fore.CYAN) if options else ""
            print(
                f"\t{verb} {colored(index, Fore.BLUE)}/{colored(count, Fore.BLUE)} [{scene.name}]{extra}: ",
                end="",
                flush=True,
            )
            if self.on_start:
                self.on_start(scene)
            outcome = self._render(scene)
            self.outcomes.append(outcome)
            if outcome.status == "success":
                print(f"{colored('Success', Fore.GREEN)} ({outcome.duration:.1f}s)")
            elif outcome.status == "skipped":
                print(colored(f"Skipped ({outcome.message})", Fore.YELLOW))
            else:
                print(colored("Failed", Fore.RED))
                print_error(f"\t{outcome.message}")
            # Saved after every test, so a crash or GPU hang does not lose what was already rendered.
            self.save()
            if self.on_outcome:
                self.on_outcome(outcome)
        shutil.rmtree(self.temp_path, ignore_errors=True)
        self.save()
        self.print_summary()
        return self.outcomes

    def save(self) -> None:
        by_status = {
            status: [[o.name, str(o.scene), o.message] for o in self.outcomes if o.status == status]
            for status in ("success", "failed", "skipped")
        }
        info = {
            "summary": {status: len(entries) for status, entries in by_status.items()},
            **by_status,
            "backend": self.params.backend,
            "run_tag": self.params.run_tag,
            "redshift_version": self.redshift_version,
            "durations": {o.name: round(o.duration, 3) for o in self.outcomes if o.status != "skipped"},
        }
        if self.params.program == "redshiftCmdLine":
            info["options_file"] = self.params.options_file.name
            info["scene_options"] = self.params.scene_options
            info["applied_options"] = self.applied_options
        try:
            self.run_log.write_text(json.dumps(info, indent=2), encoding="utf-8")
        except OSError as err:
            print_error(f"Could not save the run summary to {self.run_log}: {err}")

    def print_summary(self) -> None:
        counts = {s: sum(o.status == s for o in self.outcomes) for s in ("success", "failed", "skipped")}
        total = len(self.outcomes)
        title = "Generation completed" if self.params.reference else "Tests completed"
        print(
            f"{colored(title, Fore.MAGENTA)}: {self.output_path}\n"
            f"\tSuccess: {colored(counts['success'], Fore.GREEN)}/{total}\n"
            f"\tFailed:  {colored(counts['failed'], Fore.RED)}/{total}\n"
            f"\tSkipped: {colored(counts['skipped'], Fore.YELLOW)}/{total}"
        )
        if self.redshift_version:
            print(f"\tRedshift {self.redshift_version}")
        for outcome in self.failures:
            print_warning(f"failed {outcome.name}: {outcome.message}")
