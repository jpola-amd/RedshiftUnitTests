# RedshiftUnitTests

Regression and performance tests for Redshift (HIP compute and RT backends): render a set of test scenes, compare the
images with references or with another run, and compare the render times.
Based on Maxon's unit test package. **For internal use only.**

## Quick start

**First time:** the scenes and the machine-specific configuration are not in git. Follow [SETUP.md](SETUP.md) to
unpack `scenes.7z` and create `config/config.win.json` from `config/templates/`.

```bash
uv sync                                                                   # once: Python + dependencies in .venv
uv run run_tests.py --program redshiftCmdLine --reference --test tests/all.json   # generate references
uv run run_tests.py --program redshiftCmdLine --test tests/all.json               # test run + image comparison
uv run run_tests.py --program redshiftCmdLine --test tests/all.json --use_rt      # the same with the RT backend
```

Each test run writes `results/<date>_<backend>/` and compares its images with `references/<program>/`.
The exit code is `0` when everything passed, `1` when a render failed or an image differs, `2` for invalid arguments
or configuration, so the runner can be used from scripts and CI.

## Setup

The project is managed with [uv](https://docs.astral.sh/uv/): dependencies are in `pyproject.toml` / `uv.lock`, the
Python version in `.python-version`. `uv sync` creates `.venv`, `uv run <script>` runs inside it, `uv add <package>`
adds a dependency.

### Configuration

`config/config.<os>.json` (`win` or `linux`) holds the Redshift executables. For a local Redshift build (e.g. a
`deploy` folder) also set `redshift_coredata_path` / `redshift_localdata_path`: they are passed to Redshift as
`REDSHIFT_COREDATAPATH` / `REDSHIFT_LOCALDATAPATH`, and the render log is then read from
`<redshift_localdata_path>/Log/Log.Latest.0` instead of `C:\ProgramData\Redshift\Log`.

```json
{
	"required": {
		"redshiftBenchmark" : "D:/maxon/redshiftcore/deploy/bin/redshiftBenchmark.exe",
		"redshiftCmdLine" : "D:/maxon/redshiftcore/deploy/bin/redshiftCmdLine.exe"
	},
	"optional": {
		"redshift_coredata_path" : "D:/maxon/redshiftcore/deploy",
		"redshift_localdata_path" : "D:/maxon/redshiftcore/deploy",
		"path_overrides" : "config/path_overrides.json"
	}
}
```

Only the executable of the selected `--program` has to exist. Templates for Windows and Linux are in
`config/templates/`; the copies in `config/` are git-ignored because they hold machine-specific paths.
`config/config.<os>.user.json` is optional; its
`redshift_project_root` is the scenes folder (default: `scenes/` of this project). Relative paths on the command line
and in the config are taken from the current directory if they exist there, otherwise from the project folder, so
the runner can be started from anywhere.

### Scene resources (path overrides)

Many scenes contain absolute texture/proxy paths of the machines they were exported on (e.g.
`C:\Program Files\Autodesk\Softimage 2015 SP2\Application\rsrc\noIcon.pic`). Redshift first tries the path relative to
the scene, then the absolute path with the overrides of `REDSHIFT_PATHOVERRIDE_FILE` applied. The overrides are
defined in `config/path_overrides.json` (`to` is relative to the scenes folder); every run writes them to
`<output>/path_overrides.txt` and passes that file to Redshift. Set `"path_overrides": ""` to disable them.
Redshift compares path prefixes case-insensitively and only with backslashes, so absolute paths stored with `/` cannot
be overridden. Resources missing from the data set are listed in `scenes/README.md`.

## Running tests

```
usage: run_tests.py [-h] --program {redshiftCmdLine,redshiftBenchmark} [--test TEST] [--gpu GPU [GPU ...]]
                    [--reference] [--use_rt] [--override_features] [--timeout TIMEOUT] [--threshold THRESHOLD]
                    [--min-ssim MIN_SSIM] [--performance-analysis] [--image-analysis]
                    [--analysis-path ANALYSIS_PATH] [--baseline-path BASELINE_PATH] [--config CONFIG]
                    [--user-config USER_CONFIG]

  --program {redshiftCmdLine,redshiftBenchmark}
                        Redshift program used to render
  --test TEST           Test JSON file to run (repeatable), e.g. tests/all.json
  --gpu GPU [GPU ...]   GPU(s) to render on, e.g. --gpu 0 or --gpu 0,1 (default: 0)
  --reference           Generate references in references/<program> instead of a test run
  --use_rt, --use-rt    Render with the RT backend (options_rt.txt) instead of the compute backend
                        (options_compute.txt). redshiftCmdLine only
  --override_features, --override-features
                        Apply the per-scene render options ('render_options' of the test entries) on top of the
                        options file. redshiftCmdLine only
  --timeout TIMEOUT     Seconds after which a render is killed and reported as failed, 0 = no limit
                        (default: 3600)
  --threshold THRESHOLD
                        Images with a mean squared error (RGB, 0-255) above this value fail (default: 0.95)
  --min-ssim MIN_SSIM   Images with a structural similarity (SSIM) below this value fail as well (off by
                        default)
  --performance-analysis
                        Compare the render times of --analysis-path with the references (or --baseline-path)
  --image-analysis      Compare the images of --analysis-path with the references (or --baseline-path)
  --analysis-path ANALYSIS_PATH
                        Results folder to analyze, e.g. results/2026-11-09_231450_rt
  --baseline-path BASELINE_PATH
                        Another results folder to compare --analysis-path against, instead of the references
  --config CONFIG       Main configuration file
  --user-config USER_CONFIG
                        User configuration file (optional)
```

`--treshold` is still accepted as a deprecated spelling of `--threshold`.

### What a render run does

For every test: render the scene, save Redshift's console output and log, move the images to the output folder.
A test **fails** when the scene is missing, Redshift exits with an error or is killed after `--timeout`, Redshift did
not write a log for this render (e.g. a wrong `redshift_localdata_path`), or no image was written. Failures are listed
at the end of the run.

| Output | Test run: `results/<date>_<backend>/` | `--reference`: `references/<program>/` |
|---|---|---|
| Images | `images/<test>.<output name>.result.png` | `images/<test>.<output name>.reference.png` |
| Redshift log (timings) | `logs/<test>.result.html` | `logs/<test>.reference.html` |
| Console output | `logs/<test>.result.console.txt` | `logs/<test>.reference.console.txt` |
| Run summary | `<program>_TEST_<date>.json` | `<program>_REFERENCE_<date>.json` |

The run summary lists successful, failed and skipped tests, the backend, the options file, the per-scene render options
that were applied, the Redshift version and the duration of each render. Regenerating a reference first deletes the old
files of that test, so no stale image of a previous version survives.

### Compute and RT backends

`redshiftCmdLine` gets its render options from a file (`-oro`), selected with `--use_rt`:

| Backend | Switch | Options file |
|---|---|---|
| compute | (default) | `options_compute.txt` |
| rt | `--use_rt` | `options_rt.txt` (enables `EnableHARTRTOnSupportedGPUs` / `EnableOptiXRTOnSupportedGPUs`) |

With RT the Redshift log contains `Device 0 (...) uses HART for ray tracing`. Compute and RT share the references in
`references/<program>/`: generating references with one backend replaces those of the other, and a run with either
backend is compared with whatever is there. `redshiftBenchmark` has no options file and cannot enable HART RT: it
always runs with `-nortx` (compute), and `--use_rt` is rejected.

### Per-scene render options

Some scenes need extra render options, e.g. `BlockSize 256` for Cinebench with RT instead of the value Redshift computes.
A test entry declares them in `render_options`, per backend: `all` applies to both backends, `compute`/`rt` to one of them
and win over `all`. Set them through `overrides` in `tools/test_generation.json` so they survive regeneration:

```json
"overrides": {
    "production/cinebench_2022_11/Cinebench_Nov_23_2022.rs": {"render_options": {"rt": {"BlockSize": 256}}}
}
```

They are applied only with `--override_features`. For such a scene the runner writes the backend's options file with the
scene's options replaced (same name, case-insensitive) or appended to `logs/<test>.<result|reference>.options.txt` and
passes that file to `-oro`; the progress line shows them (`+BlockSize=256`). Generate references and test runs with the
same switch, otherwise they are compared with different settings. `redshiftBenchmark` has no options file: the options
are ignored with a warning.

## Comparing results

After a test run the images are compared with the references automatically. Both analyses can also be run on any
results folder, against the references or against another results folder (`--baseline-path`, e.g. RT vs compute):

```bash
uv run run_tests.py --program redshiftCmdLine --image-analysis --performance-analysis --analysis-path results/2026-11-09_231450_rt
uv run run_tests.py --program redshiftCmdLine --image-analysis --performance-analysis --analysis-path results/2026-11-09_231450_rt --baseline-path results/2026-11-09_231614_compute
```

Results are paired with the references/baseline by test and output name. Everything is written to
`<analysis-path>/analysis/`, with `<tag>` = `vs_references` or `vs_<baseline folder>`:

* **Images** (`image_<tag>_<date>.json`, `image_<tag>_mismatch_<date>.json`, `diffs_<tag>/`): mean squared error and
  SSIM over the RGB channels. An image fails when its MSE is above `--threshold`, its SSIM is below `--min-ssim` (if
  given), or it cannot be compared (unreadable, different size). For each failing image a diff plot (reference,
  difference, result) is written. `redshiftBenchmark` images are compared without the info strip at the bottom.
* **Performance** (`performance_<tag>_<date>.csv/.xlsx`, summary on the console): for `redshiftCmdLine` the render
  time ("Time to render N blocks", `GPU`) and the total time ("Rendering time"), for `redshiftBenchmark` its time
  (whole seconds only). `Speedup = reference time / result time`, so above 1 means faster. The summary shows the total
  and geometric-mean speedup and the slowest/fastest tests. Tests that failed to render on either side are excluded.
  Redshift logs times with 0.1 s resolution, so short scenes only give rough numbers.

## Scenes and tests

```
scenes/                     scene data (not in git), see scenes/README.md
  feature/<collection>/     small single-feature scenes, grouped by source set and category
  production/<scene>/       large production scenes
tests/                      generated by tools/generate_tests.py, do not edit by hand
  all.json                  every scene
  feature.json, production.json
  feature/<collection>.json             one collection
  feature/<collection>/<category>.json  one category, e.g. feature/xsi_unit_tests/gi.json
  production/<scene>.json               one production scene
  suites/<suite>.json                   curated suites (bugs_res, multi_output)
tools/
  generate_tests.py         regenerates tests/ from scenes/
  test_generation.json      exclusions, test name prefixes, per-scene overrides and suites
```

Test names are `<collection prefix>_<category>_<scene file name>` (production: `prod_<scene folder>_<scene file name>`),
e.g. `xsi_gi_GITest_BF`, `prod_attic_nvidia_Nvidia_Attic_RS_Nodes`. They are unique and contain no dots, because the
result, reference and log file names are derived from them.

### Adding tests

1. Copy the scene (`.rs` plus the textures it references, keeping their relative paths) into a collection under
   `scenes/feature/` or into a new folder `scenes/production/<scene>/`.
2. Run `uv run tools/generate_tests.py` (`--check` only reports whether `tests/` is out of date).
3. Optional: exclude proxies or broken scenes, rename a test, set `skippostfx` or `render_options` (`overrides`) or add
   the scene to a curated suite in `tools/test_generation.json`, then regenerate.

For an ad-hoc selection, create your own JSON file outside the generated folders (e.g. `tests/my_selection.json`) and
pass it with `--test`; the generator does not touch it. Format:

```json
{
    "tests": [
        {"include": "feature/hip_emission.json"},
        {"path_to_scene": "feature/hair/simple_hair_torus.rs", "test_name": "hair_simple_hair_torus"},
        {"path_to_scene": "feature/rt/rt_test.rs", "test_name": "rt_rt_test", "skippostfx": true}
    ]
}
```

`path_to_scene` is relative to the scenes folder, `include` relative to the JSON file. `skippostfx` passes
`-skippostfx` to `redshiftCmdLine`, `render_options` see [Per-scene render options](#per-scene-render-options). Each
`.rs` file is one frame; there is no frame range option.

## Development

```
run_tests.py                    entry point: CLI -> render and/or analyses, exit code
testrunner/cli.py               command line, configuration and validation (ExecutionParameters)
testrunner/render_tasks.py      RenderTask: renders the tests for both programs, test runs and references
testrunner/image_analysis.py    ImageAnalyzer: RGB MSE/SSIM comparison and diff plots
testrunner/performance_analysis.py  PerformanceAnalyzer: log parsing and speedups
testrunner/utils.py             helpers: paths, test files, processes, Redshift logs
testrunner/tests/               unit tests of the runner itself
```

```bash
uv run pytest                 # unit tests (no GPU or Redshift needed)
uv run ruff check .           # lint
uv run ruff format .          # format
```
