"""Generic helpers: console output, paths, test files, processes and Redshift logs."""

import json
import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from colorama import Fore, Style

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TIMESTAMP_FORMAT = "%Y-%m-%d_%H%M%S"


class RunnerError(Exception):
    """A user-facing error (bad arguments, missing files); main() prints it and exits with code 2."""


# --- console -------------------------------------------------------------------------------------


def print_error(msg: str) -> None:
    print(f"{Fore.RED}ERROR: {msg}{Style.RESET_ALL}")


def print_warning(msg: str) -> None:
    print(f"{Fore.YELLOW}Warning:{Style.RESET_ALL} {msg}")


def colored(text, color: str) -> str:
    return f"{color}{text}{Style.RESET_ALL}"


# --- paths and time ------------------------------------------------------------------------------


def get_os_tag() -> str:
    system = platform.system()
    if system == "Windows":
        return "win"
    if system == "Linux":
        return "linux"
    raise RunnerError(f"{system} is not supported")


def resolve_path(path, base: Path = PROJECT_ROOT) -> Path:
    """Absolute path; a relative path is taken from the current directory if it exists there, else from base."""
    path = Path(path)
    if path.is_absolute():
        return path
    cwd_path = Path.cwd() / path
    return cwd_path.resolve() if cwd_path.exists() else (base / path).resolve()


def timestamp() -> str:
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def parse_redshift_time(text: str) -> float | None:
    """Seconds from a Redshift FormatTime() string: '45ms', '1.2s', '2m:3s' or '1h:2m:3s'. None if not parsable."""
    if not text:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*ms\s*", text)
    if match:
        return int(match.group(1)) / 1000.0
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*([hms])\b", text)
    if not parts:
        return None
    return sum(float(value) * {"h": 3600, "m": 60, "s": 1}[unit] for value, unit in parts)


def gpu_arguments(gpus: list[str]) -> list[str]:
    """['0', '1,2'] -> ['-gpu', '0', '-gpu', '1', '-gpu', '2']"""
    args = []
    for value in gpus:
        for gpu in value.split(","):
            if gpu.strip():
                args += ["-gpu", gpu.strip()]
    return args


# --- test files ----------------------------------------------------------------------------------


def load_tests(test_files: list[Path]) -> list[dict]:
    """Test entries ({'path_to_scene', 'test_name', ...}) from test JSON files, following 'include' directives."""
    tests: list[dict] = []

    def load(file: Path, stack: tuple):
        file = file.resolve()
        if file in stack:
            raise RunnerError(f"Test file include cycle: {' -> '.join(str(f) for f in stack + (file,))}")
        try:
            entries = json.loads(file.read_text(encoding="utf-8"))["tests"]
        except FileNotFoundError:
            raise RunnerError(f"Test file does not exist: {file}") from None
        except (json.JSONDecodeError, KeyError, TypeError) as err:
            raise RunnerError(f"Invalid test file {file}: {err!r}") from None
        for entry in entries:
            if "include" in entry:
                load(file.parent / entry["include"], stack + (file,))
            elif "path_to_scene" in entry and "test_name" in entry:
                tests.append(entry)
            else:
                raise RunnerError(f"Invalid entry in {file}: {entry}")

    for test_file in test_files:
        load(test_file, ())
    return tests


# --- processes -----------------------------------------------------------------------------------


@dataclass
class ProcessResult:
    returncode: int | None  # None when the process was killed after the timeout
    duration: float
    timed_out: bool


def run_process(
    cmd: list, env: dict, console_log: Path, timeout: float | None, cwd: Path | None = None
) -> ProcessResult:
    """Runs cmd, writing stdout and stderr to console_log; kills it after timeout seconds."""
    start = time.time()
    with open(console_log, "w", encoding="utf-8", errors="replace") as out:
        process = subprocess.Popen([str(c) for c in cmd], env=env, cwd=cwd, stdout=out, stderr=subprocess.STDOUT)
        try:
            returncode = process.wait(timeout=timeout)
            return ProcessResult(returncode, time.time() - start, False)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return ProcessResult(None, time.time() - start, True)


# --- Redshift logs -------------------------------------------------------------------------------

UNRECOVERABLE_ERROR = "Redshift encountered an unrecoverable error during rendering and has been disabled."


def redshift_log_file(env: dict) -> Path:
    """log.html of the latest Redshift run; it is under REDSHIFT_LOCALDATAPATH when that is set (custom builds)."""
    local_data = env.get("REDSHIFT_LOCALDATAPATH")
    if local_data:
        folder = Path(local_data) / "Log" / "Log.Latest.0"
    elif get_os_tag() == "win":
        folder = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Redshift" / "Log" / "Log.Latest.0"
    else:
        folder = Path.home() / "redshift" / "log" / "log.latest.0"
    return folder / "log.html"


def read_text(file: Path) -> str:
    return file.read_text(encoding="utf-8", errors="ignore")


def find_log_error(log: str) -> str | None:
    """The fatal error reported in a Redshift log, if any."""
    if UNRECOVERABLE_ERROR in log:
        return UNRECOVERABLE_ERROR
    match = re.search(r"=\n(ASSERT FAILED.*?)\n=", log, re.DOTALL)
    return match.group(1) if match else None


def find_redshift_version(console_output: str) -> str | None:
    match = re.search(r"\(version ([^\s)]+)", console_output)
    return match.group(1) if match else None


def write_path_override_file(overrides_json: Path, scenes_root: Path, out_file: Path) -> int:
    """Writes a REDSHIFT_PATHOVERRIDE_FILE ('"from" "to"' per line) from config/path_overrides.json."""
    overrides = json.loads(overrides_json.read_text(encoding="utf-8"))["overrides"]
    lines = []
    for override in overrides:
        source = override["from"]
        if "relative_to" in override:
            # Redshift resolves the stored relative path against the scene folder, mirror that here.
            source = os.path.normpath(os.path.join(scenes_root, override["relative_to"], source.replace("\\", os.sep)))
        target = os.path.normpath(os.path.join(scenes_root, override["to"]))
        lines.append(f'"{source}" "{target}"')
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)
