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
uv run run_tests.py --batch batches/production.json                               # references + several runs + report
```

Each test run writes `results/<date>_<backend>/` and compares its images with `references/<program>/`. A batch runs
several of them and writes one summary report, see [Batches](#batches).
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
usage: run_tests.py [-h] [--program {redshiftCmdLine,redshiftBenchmark}] [--test TEST] [--gpu GPU [GPU ...]]
                    [--reference] [--use_rt] [--no-scene-options] [--timeout TIMEOUT] [--threshold THRESHOLD]
                    [--min-ssim MIN_SSIM] [--performance-analysis] [--image-analysis]
                    [--analysis-path ANALYSIS_PATH] [--baseline-path BASELINE_PATH] [--config CONFIG]
                    [--user-config USER_CONFIG] [--batch BATCH | --resume RESUME]

  --program {redshiftCmdLine,redshiftBenchmark}
                        Redshift program used to render (required without --batch)
  --test TEST           Test JSON file to run (repeatable), e.g. tests/all.json
  --gpu GPU [GPU ...]   GPU(s) to render on, e.g. --gpu 0 or --gpu 0,1 (default: 0)
  --reference           Generate references in references/<program> instead of a test run
  --use_rt, --use-rt    Render with the RT backend (options_rt.txt) instead of the compute backend
                        (options_compute.txt). redshiftCmdLine only
  --no-scene-options, --no_scene_options
                        Render only with the options file. By default a test with 'render_options' is also rendered
                        once per option set (a list of values is a sweep) as '<test>@<option>=<value>', compared with
                        the references of <test>. References always ignore them. redshiftCmdLine only
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
  --batch BATCH         Batch file (e.g. batches/production.json): references, several runs, analyses and a summary
                        report. --program/--test/--gpu/--timeout/--threshold/--min-ssim given on the command line
                        override the file
  --resume RESUME       Continue an interrupted batch, e.g. results/batch_2026-11-12_184215_production
```

`--treshold` is still accepted as a deprecated spelling of `--threshold`, `--override_features` is accepted and ignored
(per-scene render options are rendered by default).

### What a render run does

For every test: render the scene, save Redshift's console output and log, move the images to the output folder.
A test **fails** when the scene is missing, Redshift exits with an error or is killed after `--timeout`, Redshift did
not write a log for this render (e.g. a wrong `redshift_localdata_path`), or no image was written. Failures are listed
at the end of the run.

| Output | Test run: `results/<date>_<backend>[_plain]/` | `--reference`: `references/<program>/` |
|---|---|---|
| Images | `images/<test>.<output name>.result.png` | `images/<test>.<output name>.reference.png` |
| Redshift log (timings) | `logs/<test>.result.html` | `logs/<test>.reference.html` |
| Console output | `logs/<test>.result.console.txt` | `logs/<test>.reference.console.txt` |
| Run summary | `<program>_TEST_<date>.json` | `<program>_REFERENCE_<date>.json` |

The run summary lists successful, failed and skipped tests, the backend, the options file, the per-scene render options
that were applied, the Redshift version and the duration of each render. It is saved after every test, so it survives a
crash. With `--no-scene-options` the folder is named `<date>_<backend>_plain`. Regenerating a reference first
deletes the old files of that test, so no stale image of a previous version survives.

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

Some scenes are worth rendering with extra render options, e.g. Cinebench with RT and different `BlockSize` values
instead of the one Redshift computes. A test entry declares them in `render_options`, per backend: `all` applies to both
backends, `compute`/`rt` to one of them and win over `all`. A value is either a single value or a list of values to
sweep. Set them through `overrides` in `tools/test_generation.json` so they survive regeneration:

```json
"overrides": {
    "production/cinebench_2022_11/Cinebench_Nov_23_2022.rs": {"render_options": {"rt": {"BlockSize": [128, 256, 384]}}}
}
```

A test run (not `--reference`) renders such a test first with the plain options file as `<test>`, then once per option
set as `<test>@<option>=<value>[+<option>=<value>...]`: the example gives `<test>`, `<test>@BlockSize=128`,
`<test>@BlockSize=256` and `<test>@BlockSize=384`. Several lists are combined (every combination is rendered), single
values are part of every option set. Dots in values become `p` in the name (`0.5` -> `0p5`). Each variant is a test of
its own (images `images/<test>@BlockSize=128.<output name>.result.png`, logs, verdicts), and every variant is compared
with the reference of `<test>`, images and times alike. Comparing with another results folder (`--baseline-path`,
batch pairs) uses the same variant there if it exists, otherwise the plain `<test>`.

For a variant the runner writes the backend's options file with the variant's options replaced (same name,
case-insensitive) or appended to `logs/<variant>.result.options.txt` and passes that file to `-oro`; the progress line
shows them (`+BlockSize=128`). References always ignore the per-scene render options, so all variants share one
reference. `--no-scene-options` renders only the plain tests. `redshiftBenchmark` has no options file: the options are
ignored.

With `"skipdefault": true` in the test entry the plain `<test>` is not rendered when the test has option sets for the
run's backend: `"BlockSize": [256, 384]` in the `rt` section then renders only `<test>@BlockSize=256` and
`<test>@BlockSize=384` with RT. Compute runs (no option sets), `--no-scene-options` runs and references still render
the plain test. Without the plain render the best render options table has no speedup vs plain.

### Per-test settings of every render

Settings that change the image must be the same for the reference and the test runs, so these keys of a test entry
apply to every render of the test (references, plain renders and all render option variants) and do not change its name:

| Key | Effect |
|---|---|
| `fixed_options` | render options written over the options file, same sections as `render_options` (`all`, `compute`, `rt`) but single values only, e.g. `{"all": {"UnifiedMinSamples": 4, "UnifiedMaxSamples": 32}}`. The entry `resolution` (`"<w>x<h>"`, e.g. `"1920x1080"`) is not written to the file but passed as `-ores` to override the output resolution |
| `skipdenoise` | `true`: `DenoiseEngine RS_DENOISEENGINE_NONE` in the options file |
| `skippostfx` | `true`: passes `-skippostfx` |

Options are applied in this order, later ones win: options file, `skipdenoise`, `fixed_options`, the variant's
`render_options`. Prefer the `all` section of `fixed_options`: references are rendered with one backend, a
backend-only setting would make the other backend's runs differ from them. Changing any of these settings requires
regenerating the references of the test. `redshiftBenchmark` has no options file and no `-ores`: they are ignored with
a warning.

```json
{
    "path_to_scene": "feature/c4d_feature_scenes/bugs/.../Brians Guitar Pedal Scene.rs",
    "test_name": "c4d_bugs_Brians_Guitar_Pedal_Scene",
    "skipdenoise": true,
    "fixed_options": {"all": {"UnifiedMinSamples": 4, "UnifiedMaxSamples": 32, "resolution": "1920x1080"}},
    "render_options": {"rt": {"BlockSize": [256, 384, 512]}}
}
```

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

## Batches

A batch renders the references (optional) and several runs one after the other, compares every run with the references
and selected runs with each other, and writes one summary report. It is described by a JSON file in `batches/`:

```json
{
    "description": "References with compute, then compute and RT (with the render option variants) and RT vs compute.",
    "program": "redshiftCmdLine",
    "tests": ["tests/production.json"],
    "gpus": ["0"],
    "timeout": 3600,
    "threshold": 0.95,
    "slowdown": 0.9,
    "reference": {"backend": "compute"},
    "runs": [
        {"name": "compute", "backend": "compute"},
        {"name": "rt", "backend": "rt"},
        {"name": "rt_plain", "backend": "rt", "scene_options": false}
    ],
    "pairs": [
        {"baseline": "compute", "result": "rt"},
        {"baseline": "rt_plain", "result": "rt"}
    ]
}
```

| Key | Meaning |
|---|---|
| `program`, `tests`, `gpus`, `timeout`, `threshold`, `min_ssim` | as the command line options; `--program`, `--test`, `--gpu`, `--timeout`, `--threshold`, `--min-ssim` given on the command line override them |
| `reference` | optional: regenerate `references/<program>` first, with this `backend` (per-scene render options are never applied to references). Without it the runs are compared with the existing references |
| `runs` | the test runs: `name` (folder name), `backend` (`compute`/`rt`), `scene_options` (default `true`: also render the [render option variants](#per-scene-render-options); `false` = plain tests only, like `--no-scene-options`) |
| `pairs` | optional: also compare run `result` with run `baseline` (images and times, speedup > 1 = `result` is faster) |
| `slowdown` | a test whose speedup is below this value is reported as slower (default 0.9) |
| `name`, `description` | optional: batch name (default: file name) and a description for the report |

```bash
uv run run_tests.py --batch batches/production.json --gpu 1
uv run run_tests.py --resume results/batch_2026-11-12_184215_production
```

Everything goes to `results/batch_<date>_<name>/`: `batch.json` (the batch as run), `state.json` (progress),
one folder per run (a normal results folder with its `analysis/`) and `summary/`:

| File | Content |
|---|---|
| `summary.html` | overview, problems with links to the diff plots, best render options, test x run matrix (colored verdicts, speedups; hover for details) |
| `summary.md` | overview, problems and best render options, e.g. for a ticket or chat |
| `summary.xlsx` | sheets Overview, Problems, Best config, Verdicts, Performance (times and speedups per run), Images (MSE/SSIM per run), Settings |
| `summary.json` | everything, per test and run |

**Best render options:** for each run and each test with render option variants, the summary picks the fastest render
(plain, unless `skipdefault`, or variant) whose images match the references (verdict `ok` or `slower`) and shows its
options, time, speedup vs the references, speedup vs the plain render of the same run (> 1 = the options are faster)
and how many of the renders passed. The batch log on the console lists them as well.

Each test gets one verdict per run (and pair): `failed` (render failed), `image mismatch`, `no reference`, `slower`
(speedup below `slowdown`), `ok`, `skipped` or `not run`. The batch passes (exit code 0) when no reference failed to
render and no run has a failed render or an image mismatch against the references; pairs are informative only.

**Resume:** `state.json` is saved after every test. `--resume <batch folder>` continues where the batch stopped: finished
runs and tests are not rendered again, and the analyses and the summary are redone for all runs. The test that was
rendering when the batch stopped (crash, GPU hang, reboot) is marked failed and not rendered again, so a scene that
hangs the GPU does not stop the batch twice; to retry it, remove it from `outcomes` in `state.json`.

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
3. Optional: exclude proxies or broken scenes, rename a test, set `skippostfx`, `skipdenoise` or `render_options`
   (`overrides`) or add the scene to a curated suite in `tools/test_generation.json`, then regenerate.

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
`-skippostfx` to `redshiftCmdLine`; `skipdenoise` and `fixed_options` see
[Per-test settings of every render](#per-test-settings-of-every-render), `render_options` see
[Per-scene render options](#per-scene-render-options) (with `skipdefault`). Each
`.rs` file is one frame; there is no frame range option.

## Development

```
run_tests.py                    entry point: CLI -> render and/or analyses, exit code
testrunner/cli.py               command line, configuration and validation (ExecutionParameters)
testrunner/render_tasks.py      RenderTask: renders the tests for both programs, test runs and references
testrunner/analysis_tasks.py    image/performance analysis of a results folder vs references or another run
testrunner/batch/               batches: config (batch file), state (resume), runner, summary, reports (json/xlsx/md/html)
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
