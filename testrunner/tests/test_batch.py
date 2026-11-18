import json

import cv2 as cv
import numpy as np
import pytest

from testrunner import render_tasks
from testrunner.batch.config import BatchConfig, RunSpec
from testrunner.batch.runner import BatchRunner
from testrunner.batch.state import INTERRUPTED_MESSAGE
from testrunner.cli import ExecutionParameters, parse_arguments
from testrunner.utils import RunnerError

LOG = (
    '<div class="DEBUG line">Time to render 1 blocks: {gpu}s</div>'
    '<div class="INFO line"><b>Rendering time: {total}s (1 devices)</b></div>'
)
# (step, test) -> (render seconds, gray value of the image); everything else renders in 10s with gray 100.
RENDERS = {
    ("rt", "a"): (5.0, 100),
    ("rt", "b"): (5.0, 160),
    ("rt", "a@BlockSize=128"): (20.0, 100),
    ("rt", "a@BlockSize=256"): (4.0, 100),
}
CONFIG = {"program": "redshiftCmdLine", "tests": ["t.json"], "runs": [{"name": "rt", "backend": "rt"}]}


def step_of(task) -> str:
    return "reference" if task.params.reference else task.output_path.name


def fake_render(self, scene):
    if (step_of(self), scene.name) == ("compute", "c"):
        return render_tasks.TestOutcome(scene.name, scene.path, "failed", "exit code 3", 1.0)
    seconds, gray = RENDERS.get((step_of(self), scene.name), (10.0, 100))
    cv.imwrite(str(self.images_path / f"{scene.name}.beauty.{self.suffix}.png"), np.full((16, 16, 3), gray, np.uint8))
    (self.logs_path / f"{scene.name}.{self.suffix}.html").write_text(LOG.format(gpu=seconds - 1, total=seconds))
    return render_tasks.TestOutcome(scene.name, scene.path, "success", "success", seconds)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """Batch reference + compute + rt + rt_plain and pair rt vs rt_plain over tests a, b, c (fake renders).

    Test a sweeps BlockSize 128/256 with RT: the rt run renders a, a@BlockSize=128, a@BlockSize=256, b, c."""
    exe = tmp_path / "redshiftCmdLine.exe"
    exe.write_text("")
    (tmp_path / "scenes").mkdir()
    for options in ("options_compute.txt", "options_rt.txt"):
        (tmp_path / options).write_text("AutomaticMemoryManagement True\n")
    tests = tmp_path / "t.json"
    entries = [{"path_to_scene": f"{n}.rs", "test_name": n} for n in "abc"]
    entries[0]["render_options"] = {"rt": {"BlockSize": [128, 256]}}
    tests.write_text(json.dumps({"tests": entries}))
    base = ExecutionParameters(
        "",
        config={"redshiftCmdLine": str(exe), "path_overrides": ""},
        user_config={"redshift_project_root": str(tmp_path / "scenes")},
        root_path=tmp_path,
    )
    config = BatchConfig.from_dict(
        {
            "program": "redshiftCmdLine",
            "tests": [str(tests)],
            "reference": {"backend": "compute"},
            "runs": [
                {"name": "compute"},
                {"name": "rt", "backend": "rt"},
                {"name": "rt_plain", "backend": "rt", "scene_options": False},
            ],
            "pairs": [{"baseline": "rt_plain", "result": "rt"}],
        },
        "test",
    )
    monkeypatch.setattr(render_tasks.RenderTask, "_render", fake_render)
    folder = tmp_path / "batch"
    folder.mkdir()
    return config, base, folder


def test_batch_verdicts_and_reports(batch):
    config, base, folder = batch
    assert BatchRunner(config, folder, base).execute() is False

    summary = json.loads((folder / "summary" / "summary.json").read_text())
    verdicts = {name: {t: r["verdict"] for t, r in c["tests"].items()} for name, c in summary["columns"].items()}
    variants = {"a@BlockSize=128": "not run", "a@BlockSize=256": "not run"}
    assert verdicts["compute"] == {"a": "ok", **variants, "b": "ok", "c": "failed"}
    assert verdicts["rt"] == {
        "a": "ok",
        "a@BlockSize=128": "slower",
        "a@BlockSize=256": "ok",
        "b": "image mismatch",
        "c": "ok",
    }
    assert verdicts["rt_plain"] == {"a": "ok", **variants, "b": "ok", "c": "ok"}
    # Variants of the result run are compared with the plain render of the baseline run.
    assert verdicts["rt_vs_rt_plain"] == {
        "a": "ok",
        "a@BlockSize=128": "slower",
        "a@BlockSize=256": "ok",
        "b": "image mismatch",
        "c": "ok",
    }
    rt = summary["columns"]["rt"]["tests"]
    assert rt["a"]["speedup"] == pytest.approx(2.0)
    assert rt["a@BlockSize=256"]["speedup"] == pytest.approx(2.5)
    assert summary["columns"]["rt_vs_rt_plain"]["tests"]["a@BlockSize=128"]["speedup"] == pytest.approx(0.5)
    assert rt["b"]["diff_plots"] == ["rt/analysis/diffs_vs_references/b.beauty.diff.png"]
    assert summary["best_configs"] == [
        {
            "run": "rt",
            "test": "a",
            "best": "a@BlockSize=256",
            "options": "BlockSize=256",
            "time": 4.0,
            "speedup": pytest.approx(2.5),
            "vs_plain": pytest.approx(1.25),
            "renders": 3,
            "passing": 3,
        }
    ]
    for extension in ("xlsx", "md", "html"):
        assert (folder / "summary" / f"summary.{extension}").is_file()
    markdown = (folder / "summary" / "summary.md").read_text()
    assert "rt_vs_rt_plain" in markdown and "## Best render options" in markdown
    assert "Best render options" in (folder / "summary" / "summary.html").read_text()
    references = sorted(p.name for p in (base.root_path / "references" / "redshiftCmdLine" / "images").iterdir())
    assert references == ["a.beauty.reference.png", "b.beauty.reference.png", "c.beauty.reference.png"]


def test_batch_resume_skips_done_and_interrupted_tests(batch, monkeypatch):
    config, base, folder = batch

    def crash(self, scene):
        if (step_of(self), scene.name) == ("rt", "b"):
            raise KeyboardInterrupt  # stands in for a crash or GPU hang during the render
        return fake_render(self, scene)

    monkeypatch.setattr(render_tasks.RenderTask, "_render", crash)
    with pytest.raises(KeyboardInterrupt):
        BatchRunner(config, folder, base).execute()

    rendered = []

    def track(self, scene):
        rendered.append((step_of(self), scene.name))
        return fake_render(self, scene)

    monkeypatch.setattr(render_tasks.RenderTask, "_render", track)
    BatchRunner(config, folder, base).execute()
    assert rendered == [("rt", "c"), ("rt_plain", "a"), ("rt_plain", "b"), ("rt_plain", "c")]
    state = json.loads((folder / "state.json").read_text())
    assert state["outcomes"]["rt"]["b"] == {"status": "failed", "message": INTERRUPTED_MESSAGE, "duration": 0.0}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"runz": []}, "unknown keys"),
        ({"runs": [{"name": "rt"}, {"name": "rt"}]}, "unique"),
        ({"runs": [{"name": "reference"}]}, "unique"),
        ({"runs": [{"name": "x", "backend": "optix"}]}, "backend"),
        ({"runs": [{"name": "x", "scene_options": "yes"}]}, "scene_options"),
        ({"pairs": [{"baseline": "rt", "result": "nope"}]}, "pair"),
        ({"program": "maya"}, "program"),
    ],
)
def test_batch_config_validation(change, message):
    BatchConfig.from_dict(CONFIG, "x")
    with pytest.raises(RunnerError, match=message):
        BatchConfig.from_dict({**CONFIG, **change}, "x")


def test_batch_command_line_overrides():
    config = BatchConfig.from_dict(CONFIG, "x")
    config.apply_cli(parse_arguments(["--batch", "b.json", "--gpu", "1", "--threshold", "2"]))
    assert (config.gpus, config.threshold, config.timeout) == (["1"], 2, 3600)
    with pytest.raises(RunnerError, match="--use_rt"):
        config.apply_cli(parse_arguments(["--batch", "b.json", "--use_rt"]))
    with pytest.raises(RunnerError, match="--no_scene_options"):
        config.apply_cli(parse_arguments(["--batch", "b.json", "--no-scene-options"]))


def test_batch_run_scene_options_default_and_deprecated_key():
    assert RunSpec.from_dict({"name": "rt"}).scene_options is True
    assert RunSpec.from_dict({"name": "rt", "override_features": False}).scene_options is False
