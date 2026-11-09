"""Generate the test JSON files in tests/ from the scene tree in scenes/.

The scenes folder is organised as  scenes/<group>/<collection>/...  (see scenes/README.md).
Every *.rs file becomes a test unless it is listed in "exclude" of tools/test_generation.json.

Output (all paths relative to tests/):
    all.json                         includes every group
    <group>.json                     includes every collection of the group
    <group>/<collection>.json        tests of the collection, or includes of its categories
    <group>/<collection>/<cat>.json  tests of one category (groups with layout "categories")
    suites/<suite>.json              curated suites defined in the config

Usage:
    python tools/generate_tests.py            # (re)generate tests/
    python tools/generate_tests.py --check    # only verify that tests/ is up to date
"""

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED_DIRS = ("suites",)


def snake_case(text: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def sanitize_test_name(text: str) -> str:
    # Test names end up in file names and the analysis splits them on '.', so keep them dot-free.
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")


class Generator:
    def __init__(self, scenes: Path, tests: Path, config: dict):
        self.scenes = scenes
        self.tests = tests
        self.config = config
        self.files = {}  # tests-relative posix path -> json data
        self.by_scene = {}  # scene path -> test entry
        self.by_name = {}  # test name -> scene path
        self.errors = []

    def scene_files(self, folder: Path):
        return sorted(
            (p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".rs"),
            key=lambda p: p.as_posix().lower(),
        )

    def make_test(self, scene_rel: str, name_parts: list) -> dict:
        name = sanitize_test_name("_".join(p for p in name_parts if p))
        test = {"path_to_scene": scene_rel, "test_name": name}
        test.update(self.config.get("overrides", {}).get(scene_rel, {}))
        if test["test_name"] in self.by_name:
            self.errors.append(
                f"duplicate test name '{test['test_name']}': {self.by_name[test['test_name']]} and {scene_rel}"
            )
        self.by_name[test["test_name"]] = scene_rel
        self.by_scene[scene_rel] = test
        return test

    def add_file(self, rel: str, description: str, tests: list):
        self.files[rel] = {"description": description, "tests": tests}

    def collection(self, group: str, gconf: dict, folder: Path) -> str:
        key = f"{group}/{folder.name}"
        prefix = self.config.get("collection_name_prefix", {}).get(key)
        if prefix is None:
            prefix = "_".join(p for p in (gconf.get("name_prefix"), folder.name) if p)
        skip = set(self.config.get("category_skip_dirs", []))
        excluded = self.config.get("exclude", {})

        categories = {}
        for scene in self.scene_files(folder):
            scene_rel = scene.relative_to(self.scenes).as_posix()
            if scene_rel in excluded:
                continue
            category = None
            if gconf.get("layout") == "categories":
                dirs = [d for d in scene.relative_to(folder).parts[:-1] if d not in skip]
                category = snake_case(dirs[0]) if dirs else None
            categories.setdefault(category, []).append((scene_rel, scene.stem))
        if not categories:
            print(f"warning: no scenes in {folder}")
            return None

        rel = f"{group}/{folder.name}.json"
        if list(categories) == [None]:
            tests = [self.make_test(s, [prefix, stem]) for s, stem in categories[None]]
            self.add_file(rel, f"All scenes of scenes/{key}", tests)
            return rel
        includes = []
        for category in sorted(categories, key=lambda c: c or "general"):
            cname = category or "general"
            tests = [self.make_test(s, [prefix, cname, stem]) for s, stem in categories[category]]
            crel = f"{group}/{folder.name}/{cname}.json"
            self.add_file(crel, f"Category '{cname}' of scenes/{key}", tests)
            includes.append({"include": f"{folder.name}/{cname}.json"})
        self.add_file(rel, f"All categories of scenes/{key}", includes)
        return rel

    def suites(self):
        for name, suite in sorted(self.config.get("suites", {}).items()):
            tests = []
            for item in suite["tests"]:
                if isinstance(item, dict):
                    if item["include"] not in self.files:
                        self.errors.append(f"suite '{name}': unknown include {item['include']}")
                    tests.append({"include": f"../{item['include']}"})
                elif item in self.by_scene:
                    tests.append(self.by_scene[item])
                else:
                    self.errors.append(f"suite '{name}': no generated test for scene {item}")
            self.add_file(f"suites/{name}.json", suite.get("description", ""), tests)

    def generate(self):
        top = []
        for group, gconf in self.config["groups"].items():
            gdir = self.scenes / group
            if not gdir.is_dir():
                print(f"warning: missing scene group {gdir}")
                continue
            includes = []
            for folder in sorted((d for d in gdir.iterdir() if d.is_dir()), key=lambda d: d.name.lower()):
                rel = self.collection(group, gconf, folder)
                if rel:
                    includes.append({"include": rel})
            self.add_file(f"{group}.json", gconf.get("description", ""), includes)
            top.append({"include": f"{group}.json"})
        self.add_file("all.json", "Every scene in scenes/", top)
        self.suites()
        for scene_rel in self.config.get("exclude", {}):
            if not (self.scenes / scene_rel).exists():
                print(f"warning: excluded scene does not exist: {scene_rel}")

    def owned(self, rel: str) -> bool:
        first = PurePosixPath(rel).parts[0]
        return (
            first in GENERATED_DIRS
            or first in self.config["groups"]
            or first[:-5] in self.config["groups"]
            or rel == "all.json"
        )

    def existing(self) -> dict:
        out = {}
        for path in self.tests.rglob("*.json"):
            rel = path.relative_to(self.tests).as_posix()
            if self.owned(rel):
                out[rel] = path.read_text(encoding="utf-8")
        return out

    def render(self) -> dict:
        return {rel: json.dumps(data, indent=4) + "\n" for rel, data in self.files.items()}

    def write(self):
        rendered = self.render()
        for rel in set(self.existing()) - set(rendered):
            (self.tests / rel).unlink()
        for rel, text in rendered.items():
            path = self.tests / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for d in sorted((p for p in self.tests.rglob("*") if p.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenes", type=Path, default=REPO_ROOT / "scenes", help="scenes root")
    parser.add_argument("--tests", type=Path, default=REPO_ROOT / "tests", help="output folder")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "tools" / "test_generation.json")
    parser.add_argument("--check", action="store_true", help="fail if tests/ is not up to date, write nothing")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    gen = Generator(args.scenes.resolve(), args.tests.resolve(), config)
    gen.generate()
    if gen.errors:
        for e in gen.errors:
            print(f"error: {e}")
        return 1

    if args.check:
        rendered, existing = gen.render(), gen.existing()
        stale = sorted(r for r in set(rendered) | set(existing) if rendered.get(r) != existing.get(r))
        for r in stale:
            print(f"out of date: tests/{r}")
        return 1 if stale else 0

    gen.write()
    print(f"{len(gen.by_scene)} tests in {len(gen.files)} files written to {gen.tests}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
