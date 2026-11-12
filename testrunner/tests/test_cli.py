import json

import pytest

from testrunner.cli import parse_command_line_args
from testrunner.utils import RunnerError


@pytest.fixture
def setup(tmp_path):
    """Config files pointing at a dummy executable and scenes folder, plus a test JSON and a results folder."""
    exe = tmp_path / "redshiftCmdLine.exe"
    exe.write_text("")
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "required": {"redshiftCmdLine": str(exe), "redshiftBenchmark": str(exe)},
                "optional": {"path_overrides": ""},
            }
        )
    )
    user_config = tmp_path / "user.json"
    user_config.write_text(json.dumps({"required": {"redshift_project_root": str(scenes)}}))
    tests = tmp_path / "t.json"
    tests.write_text(json.dumps({"tests": []}))
    results = tmp_path / "results" / "run"
    results.mkdir(parents=True)

    def args(*extra):
        return ["--config", str(config), "--user-config", str(user_config), *extra]

    return args, tests, results


def test_render_defaults(setup):
    args, tests, _ = setup
    params = parse_command_line_args(args("--program", "redshiftCmdLine", "--test", str(tests)))
    assert params.gpus == ["0"]
    assert params.backend == "compute"
    assert params.options_file.name == "options_compute.txt"
    assert params.timeout == 3600


def test_render_options(setup):
    args, tests, _ = setup
    params = parse_command_line_args(
        args(
            "--program",
            "redshiftCmdLine",
            "--test",
            str(tests),
            "--gpu",
            "0,1",
            "2",
            "--use_rt",
            "--timeout",
            "0",
            "--treshold",
            "2.5",
        )
    )
    assert params.gpus == ["0,1", "2"]
    assert params.options_file.name == "options_rt.txt"
    assert params.timeout is None
    assert params.threshold == 2.5
    assert not params.override_features
    assert parse_command_line_args(
        args("--program", "redshiftCmdLine", "--test", str(tests), "--override_features")
    ).override_features


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--program", "redshiftBenchmark"], "--test is required"),
        (["--program", "redshiftBenchmark", "--test", "{tests}", "--use_rt"], "only supported with --program"),
        (["--program", "redshiftCmdLine", "--test", "{tests}", "--baseline-path", "."], "require --performance"),
    ],
)
def test_render_validation(setup, extra, message):
    args, tests, _ = setup
    with pytest.raises(RunnerError, match=message):
        parse_command_line_args(args(*[a.format(tests=tests) for a in extra]))


def test_unknown_program_is_rejected(setup):
    args, tests, _ = setup
    with pytest.raises(SystemExit):
        parse_command_line_args(args("--program", "maya", "--test", str(tests)))


def test_analysis_validation(setup):
    args, _, results = setup
    with pytest.raises(RunnerError, match="--analysis-path is required"):
        parse_command_line_args(args("--program", "redshiftCmdLine", "--image-analysis"))
    with pytest.raises(RunnerError, match="does not exist"):
        parse_command_line_args(
            args("--program", "redshiftCmdLine", "--image-analysis", "--analysis-path", str(results / "missing"))
        )
    params = parse_command_line_args(
        args(
            "--program",
            "redshiftCmdLine",
            "--image-analysis",
            "--performance-analysis",
            "--analysis-path",
            str(results),
            "--baseline-path",
            str(results),
        )
    )
    assert params.is_analysis and params.baseline_path == results


def test_missing_executable(setup, tmp_path):
    args, tests, _ = setup
    config = tmp_path / "config2.json"
    config.write_text(json.dumps({"redshiftCmdLine": str(tmp_path / "nope.exe")}))
    with pytest.raises(RunnerError, match="does not exist"):
        parse_command_line_args(["--config", str(config), "--program", "redshiftCmdLine", "--test", str(tests)])
