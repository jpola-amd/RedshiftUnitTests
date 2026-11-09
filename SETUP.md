# Setting up the Redshift test suite

The git repository contains only the test runner: source code, test lists (`tests/`), render options
(`options_*.txt`) and configuration templates. Three things are **not** in git and have to be set up locally:

| What | Why it is not in git | Where it comes from |
|---|---|---|
| `scenes/` (about 12 GB) | too large, third-party scene data | `scenes.7z` from the team share |
| `config/config.win.json`, `config/config.win.user.json` | paths of your machine | copied from `config/templates/` |
| `references/` | rendered on your machine, with your Redshift build and GPU | generated with `--reference` |

`results/` (your test runs) is local as well. All of these are listed in `.gitignore`, so they cannot be committed by
accident.

## 1. Prerequisites

* Windows with an AMD GPU and driver that your Redshift build supports
* A Redshift build: your own `redshiftcore` deploy folder or an installed Redshift
* [git](https://git-scm.com/), [uv](https://docs.astral.sh/uv/getting-started/installation/) and [7-Zip](https://www.7-zip.org/)
* About 20 GB of free disk space (archive, extracted scenes, references and results)

## 2. Get the code and the Python environment

```bat
git clone <repository URL> D:\maxon\RedshiftUnitTest
cd /d D:\maxon\RedshiftUnitTest
uv sync
```

`uv sync` downloads the right Python version and all dependencies into `.venv`. No other Python setup is needed.

## 3. Unpack the scenes

Copy `scenes.7z` from `<team share>\RedshiftUnitTest\scenes.7z` and extract it **into the project folder**. The
archive contains a top-level `scenes` folder:

```bat
"C:\Program Files\7-Zip\7z.exe" x scenes.7z -oD:\maxon\RedshiftUnitTest
```

Result: `D:\maxon\RedshiftUnitTest\scenes\feature`, `...\scenes\production` and `...\scenes\README.md`.
You can delete `scenes.7z` afterwards (or keep it outside the project folder).

* Do not move individual scenes: they find their textures through relative paths (see `scenes/README.md`).
* The test lists in `tests/` match the folder layout of this archive. When the archive is updated, pull the latest code
  as well.
* To keep the scenes somewhere else, extract them there and set `redshift_project_root` in step 4.

## 4. Create your configuration

```bat
copy config\templates\config.win.json config\config.win.json
```

Edit `config\config.win.json`:

| Key | Set it to |
|---|---|
| `redshiftCmdLine` | your `redshiftCmdLine.exe`, e.g. `D:/maxon/redshiftcore/deploy/bin/redshiftCmdLine.exe` |
| `redshiftBenchmark` | your `redshiftBenchmark.exe` (only needed if you run `--program redshiftBenchmark`) |
| `redshift_coredata_path`, `redshift_localdata_path` | **deploy build:** the deploy folder, e.g. `D:/maxon/redshiftcore/deploy` |
| | **installed Redshift:** remove both lines (Redshift then uses `C:\ProgramData\Redshift`) |
| `path_overrides` | leave as is (maps texture paths baked into the scenes to the `scenes` folder) |

Use forward slashes (or doubled backslashes) in the JSON paths.

`config\config.win.user.json` is only needed if the scenes are **not** in the `scenes` folder of the project:

```bat
copy config\templates\config.win.user.json config\config.win.user.json
```

and set `redshift_project_root` to your scenes folder.

## 5. Check the setup

```bat
uv run run_tests.py --program redshiftCmdLine --test tests/feature/hip_emission.json
```

This renders the 6 small emission scenes (about a minute). Expected: `Success: 6/6` and a warning that there are
no references yet. If something is wrong, the runner stops with a clear `ERROR:` line and exit code 2 (configuration)
or reports the failed tests with exit code 1:

| Message | Fix |
|---|---|
| `Config file does not exist: ...config.win.json` | step 4 was skipped: copy the template |
| `Config: redshiftCmdLine does not exist` | executable path in `config\config.win.json` |
| `Redshift did not write a log for this render` | `redshift_localdata_path` must be the folder Redshift writes its `Log` folder to (the deploy folder for a deploy build; remove the key for an installed Redshift) |
| `scene does not exist` | the scenes were not extracted to `scenes\` (or `redshift_project_root` is wrong) |
| `killed after the 3600s timeout` | the render hung; check the console log in `results\...\logs\<test>.result.console.txt` |

Then delete the test output: `rmdir /s /q results`.

## 6. Generate references

References are the images and render times that later runs are compared with. Generate them once with a Redshift build
you trust (e.g. the current release branch), on the GPU you will test on:

```bat
uv run run_tests.py --program redshiftCmdLine --reference --test tests/all.json
```

`tests/all.json` renders all 442 scenes and takes several hours; `tests/feature.json` (small scenes) is much faster.
The references go to `references\redshiftCmdLine\`. Regenerate them when the reference build or the GPU changes.

## 7. Daily use

```bat
rem test run with your current build (compute backend) + image comparison with the references
uv run run_tests.py --program redshiftCmdLine --test tests/all.json

rem the same with the RT backend
uv run run_tests.py --program redshiftCmdLine --test tests/all.json --use_rt

rem compare two runs: images and render times (e.g. RT against compute)
uv run run_tests.py --program redshiftCmdLine --image-analysis --performance-analysis --analysis-path results/<rt run> --baseline-path results/<compute run>
```

* Each run goes to `results\<date>_<backend>\`; the analyses are written to its `analysis\` folder (diff plots of
  differing images, CSV/XLSX with timings and speedups).
* Exit code `0` = all passed, `1` = failed renders or differing images, `2` = configuration problem.
* Smaller selections: `tests/feature/<collection>.json`, `tests/feature/<collection>/<category>.json`,
  `tests/production/<scene>.json`, `tests/suites/*.json`, or your own JSON file (see `README.md`).

See `README.md` for all options and for adding scenes.

## For the maintainer: updating the scene archive

After changing `scenes/`, regenerate the test lists, commit them and rebuild the archive from the project folder:

```bat
uv run tools/generate_tests.py
"C:\Program Files\7-Zip\7z.exe" a -mx=5 scenes.7z scenes
```

Upload `scenes.7z` to the team share, replacing the old one, and tell colleagues to pull the code and re-extract the
archive (delete their old `scenes` folder first).
