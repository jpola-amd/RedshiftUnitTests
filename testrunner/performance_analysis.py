"""Render time comparison of a results folder with the references or with another results folder."""

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup
from colorama import Fore

from .utils import colored, parse_redshift_time, print_warning, read_text


def speedup(baseline: float | None, result: float | None) -> float | None:
    """baseline / result: above 1 means the analyzed results are faster than the baseline/reference."""
    if baseline is not None and result is not None and result > 0:
        return baseline / result
    return None


@dataclass
class LogTimes:
    times: dict[str, float | None] = field(default_factory=dict)  # 'GPU'/'Total' (cmdline) or 'Time' (benchmark)
    devices: str = ""
    gpu_names: list[str] = field(default_factory=list)


def parse_cmdline_log(html: str) -> LogTimes:
    """Times of a redshiftCmdLine log.html: GPU = 'Time to render N blocks', Total = 'Rendering time'."""
    soup = BeautifulSoup(html, "html.parser")
    log = LogTimes(times={"GPU": None, "Total": None})
    for line in soup.find_all("div", class_="DEBUG line"):
        match = re.search(r"Time to render \d+ blocks:\s*(.+)$", line.text.strip())
        if match:  # the last one is the final render
            log.times["GPU"] = parse_redshift_time(match.group(1))
    for line in soup.find_all("div", class_="INFO line"):
        text = line.b.string if line.b else None
        match = re.match(r"Rendering time:\s*(.+?)\s*\((\d+) device", text or "")
        if match:
            log.times["Total"] = parse_redshift_time(match.group(1))
            log.devices = match.group(2)
    for line in soup.find_all("div", class_="DETAILED line"):
        match = re.search(r"Device \d+/\d+ : (.*)", line.text)
        if match:
            log.gpu_names.append(match.group(1).strip())
    return log


def parse_benchmark_log(html: str) -> LogTimes:
    """Time of a redshiftBenchmark log.html ('Time: 00h:01m:23s', whole seconds only)."""
    time_match = re.search(r"Time:\s(\d{2}h:\d{2}m:\d{2}s)", html)
    gpus_match = re.search(r"Rendering with:\s\[(.*?)\]", html)
    gpu_names = [g.strip() for g in gpus_match.group(1).split(",")] if gpus_match else []
    return LogTimes(
        times={"Time": parse_redshift_time(time_match.group(1)) if time_match else None},
        devices=str(len(gpu_names)) if gpu_names else "",
        gpu_names=gpu_names,
    )


def failed_tests(folder: Path) -> set[str]:
    """Tests whose latest render in this results/references folder failed (from the *_TEST_/*_REFERENCE_ json)."""
    status = {}
    for run_log in sorted(folder.glob("*_TEST_*.json")) + sorted(folder.glob("*_REFERENCE_*.json")):
        try:
            info = json.loads(run_log.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for state in ("success", "failed"):
            for entry in info.get(state, []):
                status[entry[0]] = state
    return {name for name, state in status.items() if state == "failed"}


@dataclass
class TimingComparison:
    name: str
    reference: LogTimes
    result: LogTimes


class PerformanceAnalyzer:
    """Pairs <name>.result.html in <results>/logs with <name>.<reference_suffix>.html in <reference_path>/logs."""

    def __init__(
        self,
        program: str,
        reference_path: Path,
        results_path: Path,
        reference_suffix: str = "reference",
        reference_label: str = "Reference",
    ):
        self.parse = parse_cmdline_log if program == "redshiftCmdLine" else parse_benchmark_log
        self.reference_path = reference_path
        self.results_path = results_path
        self.reference_suffix = reference_suffix
        self.label = reference_label
        self.comparisons: list[TimingComparison] = []

    def analyze(self) -> list[dict]:
        excluded = failed_tests(self.results_path) | failed_tests(self.reference_path)
        for result in sorted((self.results_path / "logs").glob("*.result.html")):
            name = result.name[: -len(".result.html")]
            if name in excluded:
                print_warning(f"{name} failed to render, excluded from the performance comparison")
                continue
            reference = self.reference_path / "logs" / f"{name}.{self.reference_suffix}.html"
            if not reference.is_file():
                print_warning(f"no {self.label.lower()} log {reference}")
                continue
            self.comparisons.append(
                TimingComparison(name, self.parse(read_text(reference)), self.parse(read_text(result)))
            )
        return [self._record(c) for c in self.comparisons]

    def _record(self, c: TimingComparison) -> dict:
        record = {"Name": c.name}
        for side, log in (("Result", c.result), (self.label, c.reference)):
            for kind, seconds in log.times.items():
                record[f"{side} {kind} Time[s]"] = seconds
            record[f"{side} N GPU(s)"] = log.devices
            record[f"{side} GPU Names"] = log.gpu_names
        for kind in c.result.times:
            record[f"Speedup {kind}"] = speedup(c.reference.times.get(kind), c.result.times[kind])
        return record

    def print_summary(self, worst: int = 5) -> None:
        label = self.label.lower()
        print(
            f"\n{colored('Performance', Fore.MAGENTA)}: {self.results_path.name} vs {label} "
            f"{self.reference_path.name} ({len(self.comparisons)} matched tests, "
            f"speedup > 1 = faster than {label})"
        )
        if not self.comparisons:
            return
        for kind in self.comparisons[0].result.times:
            valid = [(c.name, c.reference.times.get(kind), c.result.times[kind]) for c in self.comparisons]
            valid = [(n, b, r) for n, b, r in valid if speedup(b, r) is not None]
            if not valid:
                print(f"\t{kind}: no comparable times")
                continue
            total_ref = sum(b for _, b, _ in valid)
            total_res = sum(r for _, _, r in valid)
            geomean = math.exp(sum(math.log(b / r) for _, b, r in valid) / len(valid))
            print(
                f"\t{kind:5} time: {label} {total_ref:10.2f}s  result {total_res:10.2f}s  "
                f"total speedup {colored(f'{total_ref / total_res:.3f}x', Fore.BLUE)}  "
                f"geomean {colored(f'{geomean:.3f}x', Fore.BLUE)}  ({len(valid)} tests)"
            )
            if kind not in ("Total", "Time"):
                continue
            ranked = sorted(valid, key=lambda x: x[1] / x[2])
            groups = (
                [("Per test (slowest first)", ranked)]
                if len(ranked) <= 2 * worst
                else [
                    (f"Slowest relative to {label}", ranked[:worst]),
                    (f"Fastest relative to {label}", ranked[::-1][:worst]),
                ]
            )
            for title, rows in groups:
                print(f"\t{title}:")
                for n, b, r in rows:
                    print(f"\t\t{b / r:7.3f}x  {label} {b:8.2f}s  result {r:8.2f}s  {n}")
