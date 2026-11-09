import json
from pathlib import Path

import pytest

from testrunner.utils import (
    RunnerError,
    find_log_error,
    find_redshift_version,
    gpu_arguments,
    load_tests,
    parse_redshift_time,
    redshift_log_file,
    resolve_path,
    write_path_override_file,
)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("45ms", 0.045),
        (" 7ms", 0.007),
        ("0.6s", 0.6),
        ("12.5s", 12.5),
        ("2m:3s", 123),
        ("1h:2m:3s", 3723),
        ("00h:01m:05s", 65),
    ],
)
def test_parse_redshift_time(text, seconds):
    assert parse_redshift_time(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", None, "abc", "s"])
def test_parse_redshift_time_invalid(text):
    assert parse_redshift_time(text) is None


def test_gpu_arguments():
    assert gpu_arguments(["0"]) == ["-gpu", "0"]
    assert gpu_arguments(["0,1", "2"]) == ["-gpu", "0", "-gpu", "1", "-gpu", "2"]
    assert gpu_arguments([" 1 , "]) == ["-gpu", "1"]


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_tests_follows_includes(tmp_path):
    write_json(tmp_path / "sub" / "a.json", {"tests": [{"path_to_scene": "a.rs", "test_name": "a"}]})
    top = write_json(
        tmp_path / "all.json", {"tests": [{"include": "sub/a.json"}, {"path_to_scene": "b.rs", "test_name": "b"}]}
    )
    assert [t["test_name"] for t in load_tests([top])] == ["a", "b"]


def test_load_tests_errors(tmp_path):
    with pytest.raises(RunnerError, match="does not exist"):
        load_tests([tmp_path / "missing.json"])
    bad = write_json(tmp_path / "bad.json", {"tests": [{"path_to_scene": "a.rs"}]})
    with pytest.raises(RunnerError, match="Invalid entry"):
        load_tests([bad])
    cycle = write_json(tmp_path / "cycle.json", {"tests": [{"include": "cycle.json"}]})
    with pytest.raises(RunnerError, match="cycle"):
        load_tests([cycle])


def test_write_path_override_file(tmp_path):
    overrides = write_json(
        tmp_path / "overrides.json",
        {
            "overrides": [
                {"from": "C:\\old\\rsrc", "to": "feature/xsi"},
                {
                    "relative_to": "production/robot/scenes",
                    "from": "..\\..\\..\\Users\\maya",
                    "to": "production/robot/tex",
                },
            ]
        },
    )
    scenes = tmp_path / "scenes"
    out = tmp_path / "overrides.txt"
    assert write_path_override_file(overrides, scenes, out) == 2
    lines = out.read_text().splitlines()
    assert lines[0] == f'"C:\\old\\rsrc" "{scenes / "feature" / "xsi"}"'
    assert lines[1] == f'"{scenes / "Users" / "maya"}" "{scenes / "production" / "robot" / "tex"}"'


def test_find_log_error():
    assert find_log_error("all good") is None
    assert "unrecoverable" in find_log_error(
        "x Redshift encountered an unrecoverable error during rendering and has been disabled. y"
    )
    assert find_log_error("====\n=\nASSERT FAILED in foo.cpp\n=\n") == "ASSERT FAILED in foo.cpp"


def test_find_redshift_version():
    assert find_redshift_version("Redshift Command-Line Renderer (version 2027.0.0 - API: 202700)") == "2027.0.0"
    assert find_redshift_version("nothing") is None


def test_redshift_log_file_follows_local_data_path(tmp_path):
    assert (
        redshift_log_file({"REDSHIFT_LOCALDATAPATH": str(tmp_path)}) == tmp_path / "Log" / "Log.Latest.0" / "log.html"
    )


def test_resolve_path_prefers_cwd_then_base(tmp_path, monkeypatch):
    base = tmp_path / "base"
    (base / "tests").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert resolve_path("tests", base) == (base / "tests").resolve()
    (work / "tests").mkdir()
    assert resolve_path("tests", base) == (work / "tests").resolve()
    assert resolve_path(tmp_path) == tmp_path
