"""The committed manifests and the reference clip they describe (design/internal/g1-slice.md, S1).

The manifests are read as TOML here, apart from `load_manifests`, so these checks don't rest on the loader's.
"""

import hashlib
import re
import tomllib
import wave
from pathlib import Path

import pytest

from lmt.eval import datasets

ROOT = Path(__file__).resolve().parents[1]
SOURCES = tomllib.loads((datasets.MANIFEST_DIR / "sources.toml").read_text(encoding="utf-8"))
SETS = tomllib.loads((datasets.MANIFEST_DIR / "sets.toml").read_text(encoding="utf-8"))
HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Only LibriSpeech and AMI sources (design/internal/g1-stage1.md, 5a). #110 adds the BUT RTTM prefix with its
# pinned commit when it lists that source.
URL_PREFIXES = (
    "https://www.openslr.org/resources/12/",
    "https://us.openslr.org/resources/12/",
    "https://groups.inf.ed.ac.uk/ami/",
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.criterion("G1-7")
class TestManifests:
    def test_the_loader_accepts_them(self):
        manifests = datasets.load_manifests()
        assert "librispeech-test-clean" in manifests.sources
        assert "clip" in manifests.fixtures

    def test_every_source_has_a_sha256_a_size_and_a_url(self):
        assert SOURCES["source"]
        for source in SOURCES["source"]:
            assert HEX64.match(source["sha256"]), source["id"]
            assert isinstance(source["size"], int) and source["size"] > 0, source["id"]
            assert source["urls"], source["id"]

    def test_every_member_has_a_sha256(self):
        for source in SOURCES["source"]:
            for member in source.get("member", []):
                assert HEX64.match(member["sha256"]), member["path"]

    def test_every_url_is_a_librispeech_or_ami_source(self):
        for source in SOURCES["source"]:
            for url in source["urls"]:
                assert url.startswith(URL_PREFIXES), url

    def test_every_fixture_piece_is_a_listed_member(self):
        members = {(s["id"], m["path"]) for s in SOURCES["source"] for m in s.get("member", [])}
        for fixture in SETS["fixture"]:
            assert fixture["concat"], fixture["name"]
            for piece in fixture["concat"]:
                assert (piece["source"], piece["member"]) in members, piece


class TestCommittedClip:
    """The fixture can't change without its manifest."""

    @pytest.fixture
    def clip(self):
        return next(f for f in SETS["fixture"] if f["name"] == "clip")

    def test_its_files_match_the_manifest(self, clip):
        assert sha256(ROOT / clip["path"]) == clip["derived_sha256"]
        assert sha256(ROOT / clip["reference"]) == clip["reference_sha256"]

    def test_it_is_16_khz_mono_16_bit_and_55_to_70_s(self, clip):
        with wave.open(str(ROOT / clip["path"]), "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
            assert 55.0 <= w.getnframes() / 16000 <= 70.0
