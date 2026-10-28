"""The toolchain pins agree: .python-version, and toolchain.toml's uv version in every setup-uv step."""

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_YML = ROOT / ".github" / "workflows" / "product.yml"


def setup_uv_versions(workflow):
    """The `version:` each astral-sh/setup-uv step passes (None if it passes none), read without YAML."""
    steps = re.split(r"^\s*- (?=uses:|name:|run:)", workflow, flags=re.M)
    versions = []
    for step in steps:
        if re.search(r"^\s*uses:\s*astral-sh/setup-uv@", step, re.M):
            m = re.search(r"^\s+version:\s*[\"']?([^\"'\s#]+)", step, re.M)
            versions.append(m.group(1) if m else None)
    return versions


class ToolchainTest(unittest.TestCase):
    def test_python_version(self):
        self.assertEqual((ROOT / ".python-version").read_text().strip(), "3.11")

    def test_uv_version_is_pinned(self):
        version = tomllib.loads((ROOT / "toolchain.toml").read_text())["uv"]["version"]
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_every_setup_uv_step_uses_the_pinned_version(self):
        version = tomllib.loads((ROOT / "toolchain.toml").read_text())["uv"]["version"]
        versions = setup_uv_versions(PRODUCT_YML.read_text())
        self.assertGreaterEqual(len(versions), 2, "the readme and test jobs each set up uv")
        self.assertEqual(versions, [version] * len(versions))

    def test_reader_sees_a_missing_or_other_version(self):
        workflow = """
      - uses: astral-sh/setup-uv@abc # v7
        with:
          version: "0.9.1"
      - name: Second
        uses: astral-sh/setup-uv@abc
        with:
          enable-cache: true
      - run: echo version: 1.0.0
"""
        self.assertEqual(setup_uv_versions(workflow), ["0.9.1", None])


if __name__ == "__main__":
    unittest.main()
