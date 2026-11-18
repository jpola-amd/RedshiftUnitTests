import json

import cv2 as cv
import numpy as np
import pytest

from testrunner.cli import ExecutionParameters
from testrunner.image_analysis import ImageAnalyzer, analysis_workers, compare_images, load_rgb
from testrunner.performance_analysis import PerformanceAnalyzer, parse_benchmark_log, parse_cmdline_log, speedup
from testrunner.render_tasks import RenderTask, Scene, merge_render_options
from testrunner.utils import RunnerError, base_test_name


def gradient(height=64, width=64) -> np.ndarray:
    x = np.linspace(0, 255, width, dtype=np.uint8)
    return np.stack([np.tile(x, (height, 1))] * 3, axis=2)


def test_compare_identical_images():
    image = gradient()
    assert compare_images(image, image.copy()) == (0.0, pytest.approx(1.0))


def test_color_change_with_same_luminance_is_detected():
    # Green (0,100,0) and purple (150,0,121) have the same luminance: the old grayscale comparison saw no difference.
    reference = np.full((64, 64, 3), (0, 100, 0), np.uint8)
    result = np.full((64, 64, 3), (150, 0, 121), np.uint8)
    gray_ref = cv.cvtColor(reference, cv.COLOR_RGB2GRAY).astype(float)
    gray_res = cv.cvtColor(result, cv.COLOR_RGB2GRAY).astype(float)
    assert np.mean((gray_ref - gray_res) ** 2) <= 1.0
    mse, _ = compare_images(reference, result)
    assert mse > 1000


def test_size_mismatch_raises():
    with pytest.raises(ValueError, match="size differs"):
        compare_images(gradient(64, 64), gradient(32, 64))


def test_load_rgb_crops_benchmark_footer(tmp_path):
    path = tmp_path / "bench.png"
    cv.imwrite(str(path), np.zeros((256, 32, 3), np.uint8))
    assert load_rgb(path, crop_benchmark_footer=True).shape[0] == 163
    assert load_rgb(path, crop_benchmark_footer=False).shape[0] == 256


def make_run(folder, images: dict, suffix: str):
    (folder / "images").mkdir(parents=True)
    for name, image in images.items():
        target = folder / "images" / f"{name}.{suffix}.png"
        if isinstance(image, bytes):
            target.write_bytes(image)
        else:
            cv.imwrite(str(target), image)


def test_image_analyzer(tmp_path):
    good, shifted = gradient(), gradient()
    shifted[:, :, 2] = 0
    make_run(tmp_path / "ref", {"same": good, "changed": good, "broken": good, "resized": good}, "reference")
    make_run(
        tmp_path / "res",
        {"same": good, "changed": shifted, "broken": b"not a png", "resized": gradient(32, 32), "new": good},
        "result",
    )
    analyzer = ImageAnalyzer(tmp_path / "ref", tmp_path / "res", plot_folder=tmp_path / "plots")
    by_name = {c.name: c for c in analyzer.analyze()}

    assert not by_name["same"].failed
    assert by_name["changed"].failed and by_name["changed"].error is None
    assert by_name["broken"].failed and "cannot read" in by_name["broken"].error
    assert by_name["resized"].failed and "size differs" in by_name["resized"].error
    assert [p.name for p in analyzer.missing_references] == ["new.reference.png"]
    assert (tmp_path / "plots" / "changed.diff.png").is_file()


def test_min_ssim_gate(tmp_path):
    rng = np.random.default_rng(0)
    reference = gradient()
    noisy = np.clip(reference.astype(int) + rng.integers(-1, 2, reference.shape), 0, 255).astype(np.uint8)
    make_run(tmp_path / "ref", {"t": reference}, "reference")
    make_run(tmp_path / "res", {"t": noisy}, "result")
    assert not ImageAnalyzer(tmp_path / "ref", tmp_path / "res", threshold=5).analyze()[0].failed
    assert ImageAnalyzer(tmp_path / "ref", tmp_path / "res", threshold=5, min_ssim=0.99999).analyze()[0].failed


CMDLINE_LOG = """
<div class="DETAILED line"><div>x</div><div>\tDevice 0/1 : AMD Radeon RX 9070 XT
</div></div>
<div class="DEBUG line"><div>x</div><div>\tTime to render 40 blocks: {gpu}
</div></div>
<div class="INFO line"><div>x</div><div><b>Rendering time: {total} (1 device(s) used)</b></div></div>
"""


def test_parse_cmdline_log():
    log = parse_cmdline_log(CMDLINE_LOG.format(gpu="2m:3s", total="2m:5s"))
    assert log.times == {"GPU": 123, "Total": 125}
    assert log.devices == "1"
    assert log.gpu_names == ["AMD Radeon RX 9070 XT"]


def test_parse_benchmark_log():
    log = parse_benchmark_log("Rendering with: [GPU A, GPU B] ... Time: 00h:01m:05s")
    assert log.times == {"Time": 65}
    assert log.gpu_names == ["GPU A", "GPU B"]


def test_speedup():
    assert speedup(2.0, 1.0) == 2.0
    assert speedup(None, 1.0) is None
    assert speedup(1.0, 0.0) is None


def test_performance_analyzer_skips_failed_renders(tmp_path):
    for folder, suffix, total in ((tmp_path / "base", "result", "2.0s"), (tmp_path / "run", "result", "1.0s")):
        (folder / "logs").mkdir(parents=True)
        for name in ("ok", "crashed"):
            (folder / "logs" / f"{name}.{suffix}.html").write_text(CMDLINE_LOG.format(gpu="1.0s", total=total))
    (tmp_path / "run" / "redshiftCmdLine_TEST_x.json").write_text(
        json.dumps({"success": [["ok", "", ""]], "failed": [["crashed", "", "exit code 1"]]})
    )

    analyzer = PerformanceAnalyzer("redshiftCmdLine", tmp_path / "base", tmp_path / "run", "result", "Baseline")
    records = analyzer.analyze()
    assert [r["Name"] for r in records] == ["ok"]
    assert records[0]["Speedup Total"] == pytest.approx(2.0)
    assert records[0]["Baseline Total Time[s]"] == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), ("true", True), ("True", True), (False, False), ("false", False), (None, False)],
)
def test_scene_skippostfx(tmp_path, value, expected):
    entry = {"test_name": "t", "path_to_scene": "a/b.rs"}
    if value is not None:
        entry["skippostfx"] = value
    scene = Scene.from_entry(entry, tmp_path)
    assert scene.skip_postfx is expected
    assert scene.path == tmp_path / "a" / "b.rs"


def test_scene_render_options_per_backend(tmp_path):
    entry = {
        "test_name": "t",
        "path_to_scene": "a.rs",
        "render_options": {"all": {"A": 1, "Flag": True}, "rt": {"A": 2, "BlockSize": 256}},
    }
    scene = Scene.from_entry(entry, tmp_path)
    assert scene.option_sets("rt") == [{"A": "2", "Flag": "True", "BlockSize": "256"}]
    assert scene.option_sets("compute") == [{"A": "1", "Flag": "True"}]
    for invalid in ({"optix": {"A": 1}}, {"rt": {"A": []}}, {"rt": {"A": {"x": 1}}}, {"rt": {"A": [[1]]}}):
        with pytest.raises(RunnerError, match="Invalid render_options"):
            Scene.from_entry({**entry, "render_options": invalid}, tmp_path)


def test_scene_variants_sweep_every_combination(tmp_path):
    entry = {
        "test_name": "t",
        "path_to_scene": "a.rs",
        "render_options": {"all": {"Flag": True}, "rt": {"BlockSize": [128, 256], "Ratio": [0.5, 1]}},
    }
    scene = Scene.from_entry(entry, tmp_path)
    variants = scene.variants("rt")
    assert [v.name for v in variants] == [
        "t",
        "t@Flag=True+BlockSize=128+Ratio=0p5",
        "t@Flag=True+BlockSize=128+Ratio=1",
        "t@Flag=True+BlockSize=256+Ratio=0p5",
        "t@Flag=True+BlockSize=256+Ratio=1",
    ]
    assert variants[0].options == {}
    assert variants[1].options == {"Flag": "True", "BlockSize": "128", "Ratio": "0.5"}
    assert {v.base_name for v in variants} == {"t"}
    assert {v.path for v in variants} == {scene.path}
    assert [v.name for v in Scene.from_entry({"test_name": "u", "path_to_scene": "a.rs"}, tmp_path).variants("rt")] == [
        "u"
    ]


@pytest.mark.parametrize(
    ("backend", "expected"),
    [("rt", ["t@BlockSize=256", "t@BlockSize=384"]), ("compute", ["t"])],
)
def test_skipdefault_renders_only_the_variants(tmp_path, backend, expected):
    entry = {
        "test_name": "t",
        "path_to_scene": "a.rs",
        "skipdefault": True,
        "render_options": {"rt": {"BlockSize": [256, 384]}},
    }
    assert [v.name for v in Scene.from_entry(entry, tmp_path).variants(backend)] == expected


def test_base_test_name():
    assert base_test_name("t") == "t"
    assert base_test_name("t@BlockSize=128") == "t"
    assert base_test_name("t@BlockSize=128+Ratio=0p5.beauty") == "t.beauty"
    assert base_test_name("t.beauty") == "t.beauty"


def test_merge_render_options():
    base = "EnableHARTRTOnSupportedGPUs True\nblocksize 128\nAutomaticMemoryManagement True\n"
    merged = merge_render_options(base, {"BlockSize": "256", "New": "1"})
    assert merged == "EnableHARTRTOnSupportedGPUs True\nBlockSize 256\nAutomaticMemoryManagement True\nNew 1\n"


@pytest.mark.parametrize(
    ("scene_options", "reference", "expected"),
    [(True, False, ["t", "t@BlockSize=128", "t@BlockSize=256"]), (False, False, ["t"]), (True, True, ["t"])],
)
def test_render_task_scene_option_variants(tmp_path, scene_options, reference, expected):
    (tmp_path / "options_rt.txt").write_text("AutomaticMemoryManagement True\n")
    tests = tmp_path / "t.json"
    entry = {"test_name": "t", "path_to_scene": "a.rs", "render_options": {"rt": {"BlockSize": [128, 256]}}}
    tests.write_text(json.dumps({"tests": [entry]}))
    params = ExecutionParameters(
        "redshiftCmdLine",
        tests=[tests],
        use_rt=True,
        scene_options=scene_options,
        reference=reference,
        root_path=tmp_path,
    )
    task = RenderTask(params)
    assert [s.name for s in task.scenes] == expected
    assert bool(task.ignored_options) is (expected == ["t"])
    task.logs_path.mkdir(parents=True)
    for scene in task.scenes:
        options_file = task._write_options_file(scene, scene.options) if scene.options else None
        used = task._command(scene, options_file)[3]
        if scene.options:
            assert used == task.logs_path / f"{scene.name}.{task.suffix}.options.txt"
            assert used.read_text() == f"AutomaticMemoryManagement True\nBlockSize {scene.options['BlockSize']}\n"
        else:
            assert used == tmp_path / "options_rt.txt"


def test_variants_are_compared_with_the_base_test(tmp_path):
    image = gradient()
    make_run(tmp_path / "ref", {"t.beauty": image}, "reference")
    make_run(tmp_path / "res", {"t.beauty": image, "t@BlockSize=128.beauty": image}, "result")
    comparisons = ImageAnalyzer(tmp_path / "ref", tmp_path / "res").analyze()
    assert [(c.name, c.reference.name) for c in comparisons] == [
        ("t.beauty", "t.beauty.reference.png"),
        ("t@BlockSize=128.beauty", "t.beauty.reference.png"),
    ]

    for folder, names, total in ((tmp_path / "ref", ["t"], "4.0s"), (tmp_path / "res", ["t", "t@B=1"], "2.0s")):
        (folder / "logs").mkdir()
        for name in names:
            suffix = "reference" if folder.name == "ref" else "result"
            (folder / "logs" / f"{name}.{suffix}.html").write_text(CMDLINE_LOG.format(gpu="1.0s", total=total))
    records = PerformanceAnalyzer("redshiftCmdLine", tmp_path / "ref", tmp_path / "res").analyze()
    assert [(r["Name"], r["Speedup Total"]) for r in records] == [("t", 2.0), ("t@B=1", 2.0)]
    # A failed reference render excludes its variants as well.
    (tmp_path / "ref" / "redshiftCmdLine_REFERENCE_x.json").write_text(json.dumps({"failed": [["t", "", "x"]]}))
    assert PerformanceAnalyzer("redshiftCmdLine", tmp_path / "ref", tmp_path / "res").analyze() == []


@pytest.mark.parametrize("reference", [False, True])
def test_skipdenoise_applies_to_every_render(tmp_path, reference):
    (tmp_path / "options_rt.txt").write_text("AutomaticMemoryManagement True\n")
    tests = tmp_path / "t.json"
    entries = [
        {
            "test_name": "t",
            "path_to_scene": "a.rs",
            "skipdenoise": True,
            "render_options": {"rt": {"BlockSize": [128]}},
        },
        {"test_name": "u", "path_to_scene": "b.rs", "skipdenoise": "false"},
    ]
    tests.write_text(json.dumps({"tests": entries}))
    params = ExecutionParameters("redshiftCmdLine", tests=[tests], use_rt=True, reference=reference, root_path=tmp_path)
    task = RenderTask(params)
    expected = {"t": {"DenoiseEngine": "RS_DENOISEENGINE_NONE"}, "u": {}}
    if not reference:
        expected["t@BlockSize=128"] = {"DenoiseEngine": "RS_DENOISEENGINE_NONE", "BlockSize": "128"}
    assert {s.name: s.file_options("rt") for s in task.scenes} == expected
    task.logs_path.mkdir(parents=True)
    written = task._write_options_file(task.scenes[0], task.scenes[0].file_options("rt")).read_text()
    assert written == "AutomaticMemoryManagement True\nDenoiseEngine RS_DENOISEENGINE_NONE\n"


@pytest.mark.parametrize("reference", [False, True])
def test_fixed_options_and_resolution_apply_to_every_render(tmp_path, reference):
    (tmp_path / "options_rt.txt").write_text("UnifiedMaxSamples 256\n")
    tests = tmp_path / "t.json"
    entry = {
        "test_name": "t",
        "path_to_scene": "a.rs",
        "fixed_options": {
            "all": {"UnifiedMinSamples": 4, "UnifiedMaxSamples": 32, "resolution": "960x540"},
            "compute": {"X": 1},
        },
        "render_options": {"rt": {"BlockSize": [128]}},
    }
    tests.write_text(json.dumps({"tests": [entry]}))
    params = ExecutionParameters("redshiftCmdLine", tests=[tests], use_rt=True, reference=reference, root_path=tmp_path)
    task = RenderTask(params)
    samples = {"UnifiedMinSamples": "4", "UnifiedMaxSamples": "32"}
    expected = {"t": samples} if reference else {"t": samples, "t@BlockSize=128": {**samples, "BlockSize": "128"}}
    assert {s.name: s.file_options("rt") for s in task.scenes} == expected
    task.logs_path.mkdir(parents=True)
    for scene in task.scenes:
        command = task._command(scene, task._write_options_file(scene, scene.file_options("rt")))
        assert command[command.index("-ores") + 1] == "960x540"
        assert command[3].read_text() == "UnifiedMaxSamples 32\nUnifiedMinSamples 4\n" + (
            "BlockSize 128\n" if scene.options else ""
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"fixed_options": {"all": {"UnifiedMaxSamples": [16, 32]}}}, "Invalid fixed_options"),
        ({"fixed_options": {"optix": {"A": 1}}}, "Invalid fixed_options"),
        ({"fixed_options": {"all": {"resolution": "1920*1080"}}}, "Invalid fixed_options"),
        ({"fixed_options": {"all": {"Resolution": 1920}}}, "Invalid fixed_options"),
        ({"render_options": {"rt": {"resolution": ["960x540", "1920x1080"]}}}, "set it in fixed_options"),
    ],
)
def test_fixed_options_and_resolution_validation(tmp_path, change, message):
    with pytest.raises(RunnerError, match=message):
        Scene.from_entry({"test_name": "t", "path_to_scene": "a.rs", **change}, tmp_path)


@pytest.mark.parametrize(("cpus", "jobs", "expected"), [(192, 500, 32), (192, 5, 5), (8, 500, 8), (None, 500, 1)])
def test_analysis_workers_are_capped(monkeypatch, cpus, jobs, expected):
    monkeypatch.setattr("testrunner.image_analysis.os.cpu_count", lambda: cpus)
    assert analysis_workers(jobs) == expected
