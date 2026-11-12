"""Progress of a batch (state.json in the batch folder), saved after every test so a batch can be resumed."""

import json
import os
from pathlib import Path

from ..render_tasks import Scene, TestOutcome
from ..utils import RunnerError

STATE_FILE = "state.json"
INTERRUPTED_MESSAGE = "the batch stopped while rendering this test (crash or GPU hang?); not rendered again on resume"


class BatchState:
    """{'completed': [step], 'running': {'step', 'test'} | None, 'outcomes': {step: {test: outcome}},
    'redshift_version': {step: version}}"""

    def __init__(self, folder: Path):
        self.file = folder / STATE_FILE
        self.data: dict = {"completed": [], "running": None, "outcomes": {}, "redshift_version": {}}
        if self.file.is_file():
            try:
                self.data.update(json.loads(self.file.read_text(encoding="utf-8")))
            except json.JSONDecodeError as err:
                raise RunnerError(f"Invalid batch state {self.file}: {err}") from None

    def save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.file)

    def is_complete(self, step: str) -> bool:
        return step in self.data["completed"]

    def complete(self, step: str, redshift_version: str | None) -> None:
        if redshift_version:
            self.data["redshift_version"][step] = redshift_version
        if step not in self.data["completed"]:
            self.data["completed"].append(step)
        self.save()

    def outcomes(self, step: str) -> dict[str, dict]:
        return self.data["outcomes"].get(step, {})

    def done_tests(self, step: str) -> set[str]:
        return set(self.outcomes(step))

    def start(self, step: str, scene: Scene) -> None:
        self.data["running"] = {"step": step, "test": scene.name}
        self.save()

    def record(self, step: str, outcome: TestOutcome) -> None:
        self.data["outcomes"].setdefault(step, {})[outcome.name] = {
            "status": outcome.status,
            "message": outcome.message,
            "duration": round(outcome.duration, 3),
        }
        self.data["running"] = None
        self.save()

    def close_interrupted(self) -> str | None:
        """Marks the test that was rendering when the batch died as failed; returns '<step>/<test>'."""
        running = self.data.get("running")
        if not running:
            return None
        self.data["outcomes"].setdefault(running["step"], {})[running["test"]] = {
            "status": "failed",
            "message": INTERRUPTED_MESSAGE,
            "duration": 0.0,
        }
        self.data["running"] = None
        self.save()
        return f"{running['step']}/{running['test']}"
