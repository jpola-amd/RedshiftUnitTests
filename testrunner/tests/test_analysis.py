import json

import cv2 as cv
import numpy as np
import pytest

from testrunner.image_analysis import ImageAnalyzer, compare_images, load_rgb
from testrunner.performance_analysis import PerformanceAnalyzer, parse_benchmark_log, parse_cmdline_log, speedup
from testrunner.render_tasks import Scene


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
