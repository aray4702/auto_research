"""Tests for check_licences.py (G1-15): a fake site directory of *.dist-info folders and empty libraries."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_licences

ALLOW = """
[licences]
distributions = ["MIT", "BSD-3-Clause", "Apache-2.0"]
bundled = ["LGPL-2.1-or-later", "GPL-3.0-or-later WITH GCC-exception-3.1"]
"""


class FakeSite:
    """A site-packages directory and a toolchain.toml, both temporary."""

    def __init__(self, tmp):
        self.root = Path(tmp)
        self.site = self.root / "site"
        self.site.mkdir()
        self.toolchain = self.root / "toolchain.toml"
        self.toolchain.write_text(ALLOW)

    def add(self, name, version="1.0", headers=(), files=()):
        info = self.site / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir()
        lines = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}", *headers]
        (info / "METADATA").write_text("\n".join(lines) + "\n\n")
        for f in files:
            path = self.site / f
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        record = [f"{f},," for f in files] + [f"{info.name}/METADATA,,", f"{info.name}/RECORD,,"]
        (info / "RECORD").write_text("\n".join(record) + "\n")

    def config(self, extra=""):
        self.toolchain.write_text(ALLOW + extra)

    def run(self, platform="linux-x86_64"):
        out = io.StringIO()
        with redirect_stdout(out):
            code = check_licences.main(["--site", str(self.site), "--platform", platform,
                                        "--toolchain", str(self.toolchain)])
        return code, out.getvalue()


class TestSpdx(unittest.TestCase):
    def passes(self, expression, allow):
        return check_licences.passes(expression, allow)

    def test_or_passes_on_one_allowed_branch(self):
        self.assertTrue(self.passes("GPL-2.0-only OR MIT", ["MIT"]))
        self.assertTrue(self.passes("(MIT OR GPL-2.0-only) AND Apache-2.0", ["MIT", "Apache-2.0"]))

    def test_and_needs_both(self):
        self.assertFalse(self.passes("MIT AND GPL-2.0-only", ["MIT"]))
        self.assertTrue(self.passes("MIT AND BSD-3-Clause", ["MIT", "BSD-3-Clause"]))

    def test_with_passes_only_when_listed_whole(self):
        listed = ["GPL-3.0-or-later WITH GCC-exception-3.1"]
        self.assertTrue(self.passes("GPL-3.0-or-later WITH GCC-exception-3.1", listed))
        self.assertFalse(self.passes("GPL-3.0-or-later WITH GCC-exception-3.1", ["GPL-3.0-or-later"]))
        self.assertFalse(self.passes("GPL-3.0-or-later", listed))

    def test_licenceref(self):
        self.assertTrue(self.passes("LicenseRef-Intel-Simplified-Software-License",
                                    ["LicenseRef-Intel-Simplified-Software-License"]))

    def test_unparseable_fails(self):
        for text in ["", "MIT AND", "(MIT", "MIT OR OR BSD-3-Clause", "Apache 2.0", "MIT WITH"]:
            with self.subTest(text=text):
                self.assertFalse(self.passes(text, ["MIT", "Apache", "2.0"]))


class TestDistributions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeSite(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_allowed_licences_pass(self):
        self.fake.add("alpha", headers=["License-Expression: MIT"])
        self.fake.add("beta", headers=["License-Expression: Apache-2.0 OR BSD-3-Clause"])
        code, out = self.fake.run()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS distribution alpha 1.0 MIT License-Expression", out)

    def test_unlisted_licence_fails(self):
        self.fake.add("alpha", headers=["License-Expression: MIT"])
        self.fake.add("gamma", headers=["License-Expression: GPL-3.0-only"])
        code, out = self.fake.run()
        self.assertEqual(code, 1)
        self.assertIn("FAIL distribution gamma 1.0 GPL-3.0-only", out)

    def test_lgpl_distribution_fails(self):
        self.fake.add("delta", headers=["License-Expression: LGPL-2.1-or-later"])
        code, out = self.fake.run()
        self.assertEqual(code, 1)
        self.assertIn("FAIL distribution delta", out)

    def test_classifier_maps(self):
        self.fake.add("alpha", headers=["Classifier: License :: OSI Approved :: MIT License",
                                        "Classifier: License :: OSI Approved :: Apache Software License"])
        code, out = self.fake.run()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS distribution alpha 1.0 MIT AND Apache-2.0 classifier", out)

    def test_license_field_used_when_it_parses(self):
        self.fake.add("alpha", headers=["License: BSD-3-Clause"])
        self.fake.add("beta", headers=["License: Apache License, Version 2.0"])
        code, out = self.fake.run()
        self.assertEqual(code, 1)
        self.assertIn("PASS distribution alpha 1.0 BSD-3-Clause License", out)
        self.assertIn("FAIL distribution beta 1.0 ? no clear SPDX licence", out)

    def test_bsd_licence_classifier_without_override_fails(self):
        self.fake.add("epsilon", "2.0", headers=["Classifier: License :: OSI Approved :: BSD License"])
        code, out = self.fake.run()
        self.assertEqual(code, 1)
        self.assertIn("FAIL distribution epsilon 2.0 ? no clear SPDX licence", out)

    def test_override_at_its_version_passes(self):
        self.fake.add("epsilon", "2.0", headers=["Classifier: License :: OSI Approved :: BSD License"])
        self.fake.config(OVERRIDE.format(version="2.0"))
        code, out = self.fake.run()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS distribution epsilon 2.0 BSD-3-Clause override", out)

    def test_override_at_another_version_fails(self):
        self.fake.add("epsilon", "2.1", headers=["Classifier: License :: OSI Approved :: BSD License"])
        self.fake.config(OVERRIDE.format(version="2.0"))
        code, out = self.fake.run()
        self.assertEqual(code, 1)
        self.assertIn("FAIL distribution epsilon 2.1", out)

    def test_inventory_exits_0_with_a_table(self):
        self.fake.add("gamma", headers=["License-Expression: GPL-3.0-only"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = check_licences.main(["--site", str(self.fake.site), "--platform", "linux-x86_64",
                                        "--toolchain", str(self.fake.toolchain), "--inventory"])
        self.assertEqual(code, 0)
        self.assertIn("| FAIL | distribution | gamma | 1.0 | GPL-3.0-only | License-Expression |", out.getvalue())


OVERRIDE = """
[[licences.override]]
distribution = "epsilon"
version = "{version}"
licence = "BSD-3-Clause"
source = "https://example.org/epsilon/LICENSE"
"""

LIBRARY = """
[[licences.library]]
distribution = "av"
files = "av.libs/libavcodec-*.so*"
library = "FFmpeg libavcodec"
licence = "{licence}"
platforms = ["linux-x86_64"]
source = "https://example.org/ffmpeg"
"""


class TestLibraries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeSite(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def add_av(self):
        self.fake.add("av", "14.0", headers=["License-Expression: BSD-3-Clause"],
                      files=["av/__init__.py", "av/_core.cpython-311-x86_64-linux-gnu.so",
                             "av.libs/libavcodec-abc123.so.61.3.100"])

    def test_unlisted_bundled_library_fails(self):
        self.add_av()
        code, out = self.fake.run()
        self.assertEqual(code, 1)
        self.assertIn("FAIL library av.libs/libavcodec-abc123.so.61.3.100 14.0 ? no [[licences.library]] entry", out)

    def test_listed_library_passes_on_its_platform_only(self):
        self.add_av()
        self.fake.config(LIBRARY.format(licence="LGPL-2.1-or-later"))
        code, out = self.fake.run("linux-x86_64")
        self.assertEqual(code, 0, out)
        self.assertIn("PASS library av.libs/libavcodec-abc123.so.61.3.100 14.0 LGPL-2.1-or-later", out)
        code, out = self.fake.run("macos-arm64")
        self.assertEqual(code, 1)
        self.assertIn("FAIL library av.libs/libavcodec-abc123.so.61.3.100", out)

    def test_listed_library_with_an_unlisted_licence_fails(self):
        self.add_av()
        self.fake.config(LIBRARY.format(licence="GPL-2.0-or-later"))
        code, out = self.fake.run()
        self.assertEqual(code, 1)
        self.assertIn("FAIL library av.libs/libavcodec-abc123.so.61.3.100 14.0 GPL-2.0-or-later", out)

    def test_extension_modules_are_skipped(self):
        self.fake.add("zeta", headers=["License-Expression: MIT"],
                      files=["zeta/_a.cpython-311-x86_64-linux-gnu.so", "zeta/_b.cpython-311-darwin.so",
                             "zeta/_c.abi3.so"])
        code, out = self.fake.run()
        self.assertEqual(code, 0, out)
        self.assertNotIn("library", out.replace("[[licences.library]]", ""))

    def test_dylib_is_a_library(self):
        self.fake.add("zeta", headers=["License-Expression: MIT"], files=["zeta/.dylibs/libomp.dylib"])
        code, out = self.fake.run("macos-arm64")
        self.assertEqual(code, 1)
        self.assertIn("FAIL library zeta/.dylibs/libomp.dylib", out)

    def test_an_entry_matching_nothing_is_a_warning(self):
        self.fake.add("av", "14.0", headers=["License-Expression: BSD-3-Clause"])
        self.fake.config(LIBRARY.format(licence="LGPL-2.1-or-later"))
        code, out = self.fake.run()
        self.assertEqual(code, 0, out)
        self.assertIn("warning: [[licences.library]] av", out)

    def test_the_first_matching_entry_applies(self):
        self.fake.add("zeta", headers=["License-Expression: MIT"], files=["zeta/lib/libgomp.so.1"])
        self.fake.config(
            LIBRARY.replace('"av"', '"zeta"').replace("av.libs/libavcodec-*.so*", "zeta/lib/libgomp*")
            .format(licence="GPL-3.0-or-later WITH GCC-exception-3.1")
            + LIBRARY.replace('"av"', '"zeta"').replace("av.libs/libavcodec-*.so*", "zeta/lib/*")
            .format(licence="MIT"))
        code, out = self.fake.run()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS library zeta/lib/libgomp.so.1 1.0 GPL-3.0-or-later WITH GCC-exception-3.1", out)


TOOLCHAIN = Path(__file__).resolve().parents[2] / "toolchain.toml"


class TestRepositoryToolchain(unittest.TestCase):
    """The repository's own toolchain.toml: well-formed, and GPL only through the files it names (#122, 3a)."""

    def setUp(self):
        self.config = check_licences.load_config(TOOLCHAIN)

    def test_entries_are_complete_and_parse(self):
        for o in self.config["override"]:
            self.assertEqual(set(o), {"distribution", "version", "licence", "source"}, o)
            check_licences.parse_spdx(o["licence"])
        for e in self.config["library"]:
            self.assertEqual(set(e), {"distribution", "files", "library", "licence", "platforms", "source"}, e)
            self.assertTrue(set(e["platforms"]) <= set(check_licences.PLATFORMS), e)
            check_licences.parse_spdx(e["licence"])

    def test_gpl_passes_only_through_its_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeSite(tmp)
            fake.toolchain.write_text(TOOLCHAIN.read_text())
            fake.add("av", "18.1.0", headers=["License-Expression: BSD-3-Clause"],
                     files=["av.libs/libx264-d6533a8d.so.165", "av.libs/libfdk-aac-0123abcd.so.2"])
            code, out = fake.run()
        self.assertEqual(code, 1)
        self.assertIn("PASS library av.libs/libx264-d6533a8d.so.165 18.1.0 GPL-2.0-or-later", out)
        self.assertIn("FAIL library av.libs/libfdk-aac-0123abcd.so.2", out)


if __name__ == "__main__":
    unittest.main()
