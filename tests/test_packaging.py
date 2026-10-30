"""The direct dependencies are pinned to uv.lock's versions (design/internal/g3-stage1.md §4a, 4a1).

packaging, which reads the markers and versions, is pytest's own dependency, so it is there wherever this runs.
"""

import tomllib
import unittest
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

import lmt

ROOT = Path(__file__).resolve().parents[1]
PINNED = ["faster-whisper", "ctranslate2", "pyannote-audio", "torch", "torchaudio", "av", "numpy",
          "huggingface-hub"]
PLATFORMS = [{"sys_platform": "darwin", "platform_machine": "arm64"},
             {"sys_platform": "linux", "platform_machine": "x86_64"}]
PYTHONS = ["3.11.9", "3.12.0", "3.13.0", "3.14.0"]


def environments():
    """One environment per platform and Python minor version, enough to decide every marker the lock uses."""
    for platform in PLATFORMS:
        for full in PYTHONS:
            yield {**platform, "python_full_version": full, "python_version": full.rsplit(".", 1)[0],
                   "os_name": "posix", "implementation_name": "cpython", "extra": ""}


def pin_problems(pyproject, lock):
    """What's wrong with the eight pins against the lock, as messages (none when they agree)."""
    pins = {}
    for line in pyproject["project"]["dependencies"]:
        req = Requirement(line)
        pins.setdefault(canonicalize_name(req.name), []).append(req)
    supported = Marker(" or ".join(f"({m})" for m in lock["resolution-markers"]))
    problems = []
    for name in PINNED:
        reqs = pins.get(name, [])
        if not reqs:
            problems.append(f"{name}: not a direct dependency")
        for req in reqs:
            specs = list(req.specifier)
            if len(specs) != 1 or specs[0].operator != "==" or "+" in specs[0].version:
                problems.append(f"{name}: {req} is not one == pin without a local label")
        for entry in (p for p in lock["package"] if p["name"] == name):
            version = Version(entry["version"]).public
            markers = [Marker(m) for m in entry.get("resolution-markers", lock["resolution-markers"])]
            for env in environments():
                if not supported.evaluate(env) or not any(m.evaluate(env) for m in markers):
                    continue
                where = f"{env['sys_platform']} Python {env['python_version']}"
                applying = [r for r in reqs if r.marker is None or r.marker.evaluate(env)]
                if len(applying) != 1:
                    problems.append(f"{name} {version} on {where}: {len(applying)} pins apply")
                elif not applying[0].specifier.contains(version, prereleases=True):
                    problems.append(f"{name} {version} on {where}: pinned {applying[0].specifier}")
    return problems


class PackagingTest(unittest.TestCase):
    def setUp(self):
        self.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.lock = tomllib.loads((ROOT / "uv.lock").read_text())

    def test_the_eight_are_pinned_to_the_lock(self):
        self.assertEqual(pin_problems(self.pyproject, self.lock), [])

    def test_every_one_of_the_eight_is_in_the_lock(self):
        locked = {p["name"] for p in self.lock["package"]}
        self.assertEqual([n for n in PINNED if n not in locked], [])

    def test_version_matches_pyproject(self):
        self.assertEqual(lmt.__version__, self.pyproject["project"]["version"])

    def test_checker_sees_a_range_a_wrong_version_and_a_missing_platform(self):
        lock = {"resolution-markers": ["sys_platform == 'darwin'", "sys_platform == 'linux'"],
                "package": [{"name": "torch", "version": "2.8.0",
                             "resolution-markers": ["sys_platform == 'darwin'"]},
                            {"name": "torch", "version": "2.8.0+cpu",
                             "resolution-markers": ["sys_platform == 'linux'"]},
                            {"name": "av", "version": "18.1.0"},
                            {"name": "numpy", "version": "2.4.6"}]}
        deps = ["torch==2.8.0", "av>=18", "numpy==2.4.6; sys_platform == 'darwin'"]
        problems = pin_problems({"project": {"dependencies": deps}}, lock)
        self.assertIn("av: av>=18 is not one == pin without a local label", problems)
        self.assertIn("numpy 2.4.6 on linux Python 3.11: 0 pins apply", problems)
        self.assertIn("faster-whisper: not a direct dependency", problems)
        self.assertFalse([p for p in problems if p.startswith("torch ")], problems)
        wrong = pin_problems({"project": {"dependencies": ["torch==2.7.1"]}}, lock)
        self.assertIn("torch 2.8.0 on darwin Python 3.11: pinned ==2.7.1", wrong)


if __name__ == "__main__":
    unittest.main()
