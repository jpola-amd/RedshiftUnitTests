"""Rendering the test scenes with redshiftCmdLine or redshiftBenchmark, for test runs and reference generation."""

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from colorama import Fore

from .cli import ExecutionParameters
from .utils import (
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
    write_path_override_file,
)

BENCHMARK_OUTPUT = "redshiftBenchmarkOutput.png"


@dataclass(frozen=True)
class Scene:
    name: str
    path: Path
    skip_postfx: bool = False

    @classmethod
    def from_entry(cls, entry: dict, scenes_root: Path) -> "Scene":
        skip = entry.get("skippostfx", False)
        return cls(
            entry["test_name"], scenes_root / entry["path_to_scene"], skip is True or str(skip).lower() == "true"
        )


@dataclass
class TestOutcome:
    name: str
    scene: Path
    status: str  # 'success', 'failed' or 'skipped'
    message: str = ""
    duration: float = 0.0


class RenderTask:
    """Renders every scene of the test files into results/<date>_<backend> (or references/<program>)."""

    def __init__(self, params: ExecutionParameters):
        self.params = params
        self.suffix = "reference" if params.reference else "result"
        stamp = timestamp()
        if params.reference:
            self.output_path = params.references_path
            self.run_log = self.output_path / f"{params.program}_REFERENCE_{stamp}.json"
        else:
            self.output_path = params.root_path / "results" / f"{stamp}_{params.backend}"
            self.run_log = self.output_path / f"{params.program}_TEST_{stamp}.json"
        self.images_path = self.output_path / "images"
        self.logs_path = self.output_path / "logs"
        self.temp_path = self.output_path / "tmp"
        self.scenes = [Scene.from_entry(entry, params.scenes_root) for entry in load_tests(params.tests)]
        self.env = self._environment()
        self.outcomes: list[TestOutcome] = []
        self.redshift_version: str | None = None

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

    def _command(self, scene: Scene) -> list:
        gpus = gpu_arguments(self.params.gpus)
        if self.params.program == "redshiftBenchmark":
            # redshiftBenchmark enables RT by default and has no options file; --use_rt is rejected by validate().
            return [self.params.executable, scene.path, *gpus, "-nortx"]
        cmd = [
            self.params.executable,
            scene.path,
            "-oro",
            self.params.options_file,
            "-oif",
            "png",
            "-oip",
            self.temp_path,
            *gpus,
        ]
        if scene.skip_postfx:
            cmd.append("-skippostfx")
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

        console_log = self.logs_path / f"{scene.name}.{self.suffix}.console.txt"
        redshift_log = redshift_log_file(self.env)
        start = time.time()
        cwd = self.temp_path if self.params.program == "redshiftBenchmark" else None
        process = run_process(self._command(scene), self.env, console_log, self.params.timeout, cwd)
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
        count = len(self.scenes)
        verb = "Generating" if self.params.reference else "Running test"
        for index, scene in enumerate(self.scenes, 1):
            print(
                f"\t{verb} {colored(index, Fore.BLUE)}/{colored(count, Fore.BLUE)} [{scene.name}]: ", end="", flush=True
            )
            outcome = self._render(scene)
            self.outcomes.append(outcome)
            if outcome.status == "success":
                print(f"{colored('Success', Fore.GREEN)} ({outcome.duration:.1f}s)")
            elif outcome.status == "skipped":
                print(colored(f"Skipped ({outcome.message})", Fore.YELLOW))
            else:
                print(colored("Failed", Fore.RED))
                print_error(f"\t{outcome.message}")
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
            "redshift_version": self.redshift_version,
            "durations": {o.name: round(o.duration, 3) for o in self.outcomes if o.status != "skipped"},
        }
        if self.params.program == "redshiftCmdLine":
            info["options_file"] = self.params.options_file.name
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
