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

# Only LibriSpeech and AMI sources (design/internal/g1-stage1.md, 5a), by the list `pin` also refuses others by.
URL_PREFIXES = datasets.URL_PREFIXES


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

    def test_every_dataset_source_id_resolves(self):
        ids = {s["id"] for s in SOURCES["source"]}
        for dataset in SETS["dataset"]:
            for key in ("source", "audio", "words", "rttm"):
                values = dataset.get(key, [])
                for value in [values] if isinstance(values, str) else values:
                    assert value in ids, (dataset["name"], key)
            for piece in dataset.get("concat", []):
                assert piece["source"] in ids, (dataset["name"], piece)


class TestCISubset:
    @pytest.fixture
    def datasets(self):
        return {d["name"]: d for d in SETS["dataset"] if d["set"] == "ci"}

    def test_it_is_ls_ci_and_ami_ci(self, datasets):
        assert sorted(datasets) == ["AMI-ci", "LS-ci"]

    def test_ls_ci_has_20_utterances_from_20_speakers(self, datasets):
        utterances = datasets["LS-ci"]["utterances"]
        assert len(set(utterances)) == 20
        assert len({u.split("-")[0] for u in utterances}) == 20

    def test_ami_ci_is_es2004a_cropped_to_300_s(self, datasets):
        ami = datasets["AMI-ci"]
        assert ami["meetings"] == ["ES2004a"]
        assert ami["crop"] == 300.0
        assert HEX64.match(ami["derived_sha256"])


class TestLSFull:
    """LS full (design/internal/g1-stage1.md §5b, decision 3)."""

    @pytest.fixture
    def sources(self):
        return {s["id"]: s for s in SOURCES["source"]}

    def test_its_source_is_the_ci_archive_with_every_member(self, sources):
        full, ci = sources["librispeech-test-clean-full"], sources["librispeech-test-clean"]
        assert (full["urls"], full["sha256"], full["size"], full["keep"]) == (ci["urls"], ci["sha256"], ci["size"], False)
        paths = [m["path"] for m in full["member"]]
        assert len(set(paths)) == len(paths) == 2707
        assert sum(p.endswith(".flac") for p in paths) == 2620
        assert sum(p.endswith(".trans.txt") for p in paths) == 87
        # fetch ci extracts only its own members, not the full set's.
        assert len(ci["member"]) < 100

    def test_it_is_every_test_clean_utterance(self):
        (entry,) = [d for d in SETS["dataset"] if d["name"] == "LS full"]
        assert (entry["set"], entry["kind"], entry["source"]) == ("full", "librispeech", "librispeech-test-clean-full")
        assert "utterances" not in entry
        utterances = datasets.load_manifests().datasets["LS full"].utterances
        assert len(set(utterances)) == len(utterances) == 2620
        assert len({u.split("-")[0] for u in utterances}) == 40
        assert list(utterances) == sorted(utterances)


AMI_FULL = [f"{series}{part}" for series in ("EN2002", "ES2004", "IS1009", "TS3003") for part in "abcd"]


class TestAMIFull:
    """AMI full and the one-hour recording (design/internal/g1-stage1.md §5b, decisions 4 and 5)."""

    @pytest.fixture
    def entries(self):
        return {d["name"]: d for d in SETS["dataset"]}

    def test_it_is_the_specs_16_meetings_in_order_with_their_sources(self, entries):
        ami = entries["AMI full"]
        assert (ami["set"], ami["kind"], ami["words"]) == ("full", "ami", "ami-annotations-1.6.2")
        assert ami["meetings"] == AMI_FULL
        assert ami["audio"] == [f"ami-{m}" for m in AMI_FULL]
        assert ami["rttm"] == [f"ami-rttm-{m}" for m in AMI_FULL]
        assert "crop" not in ami
        sources = {s["id"]: s for s in SOURCES["source"]}
        for meeting in AMI_FULL:
            assert sources[f"ami-{meeting}"]["urls"] == [
                f"https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{meeting}/audio/{meeting}.Mix-Headset.wav"]
            assert sources[f"ami-rttm-{meeting}"]["urls"] == [
                f"{URL_PREFIXES[-1]}only_words/rttms/test/{meeting}.rttm"]

    def test_the_annotations_have_each_meetings_four_words_files(self):
        (annotations,) = [s for s in SOURCES["source"] if s["id"] == "ami-annotations-1.6.2"]
        assert sorted(m["path"] for m in annotations["member"]) == sorted(
            f"words/{meeting}.{speaker}.words.xml" for meeting in AMI_FULL for speaker in "ABCD")

    def test_ami_ci_uses_the_list_form(self, entries):
        assert (entries["AMI-ci"]["audio"], entries["AMI-ci"]["rttm"]) == (["ami-ES2004a"], ["ami-rttm-ES2004a"])


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


class TestEncodings:
    """The clip's five encodings in sets.toml (design/internal/g1-formats.md, "sets.toml")."""

    SETS_TEXT = (datasets.MANIFEST_DIR / "sets.toml").read_text(encoding="utf-8")

    def test_the_clip_has_the_five_encodings_and_an_ffmpeg_version(self):
        clip = next(f for f in SETS["fixture"] if f["name"] == "clip")
        assert [e["format"] for e in clip["encodings"]] == ["flac", "mp3", "m4a", "mp4", "mov"]
        assert all(HEX64.match(e["sha256"]) for e in clip["encodings"])
        assert isinstance(clip["ffmpeg"], str) and clip["ffmpeg"]

    def test_the_loader_reads_them(self):
        fixture = datasets.load_manifests().fixtures["clip"]
        assert [(e.format, e.sha256) for e in fixture.encodings] == [
            (e["format"], e["sha256"]) for e in next(f for f in SETS["fixture"] if f["name"] == "clip")["encodings"]]
        assert fixture.ffmpeg

    @pytest.mark.parametrize("old, new, problem", [
        (re.compile(r'^ffmpeg = .*\n', re.MULTILINE), "", "ffmpeg and encodings must be given together"),
        (re.compile(r'^encodings = \[.*?^\]\n', re.MULTILINE | re.DOTALL), "",
         "ffmpeg and encodings must be given together"),
        ('format = "mov"', 'format = "wav"', "format 'wav' isn't one of flac, mp3, m4a, mp4, mov"),
        ('format = "mov"', 'format = "mp4"', "format 'mp4' is repeated"),
        (re.compile(r'(format = "flac", sha256 = ")([0-9a-f]{64})'), lambda m: m[1] + m[2].upper(),
         "isn't a SHA-256 in lower-case hex"),
    ])
    def test_a_bad_entry_raises(self, tmp_path, old, new, problem):
        if isinstance(old, str):
            text = self.SETS_TEXT.replace(old, new)
        else:
            text = old.sub(new, self.SETS_TEXT)
        assert text != self.SETS_TEXT
        (tmp_path / "sets.toml").write_text(text, encoding="utf-8")
        (tmp_path / "sources.toml").write_bytes((datasets.MANIFEST_DIR / "sources.toml").read_bytes())
        with pytest.raises(ValueError, match=re.escape(problem)):
            datasets.load_manifests(tmp_path)
