"""The committed manifests and the fixture clips they describe (design/internal/g1-slice.md, S1, and
design/internal/g1-stage2.md, "The two-speaker clip's rule").

The manifests are read as TOML here, apart from `load_manifests`, so these checks don't rest on the loader's.
"""

import hashlib
import itertools
import re
import tomllib
import wave
from pathlib import Path, PurePosixPath

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
        assert manifests.fixtures["two_speakers"].rttm == "tests/fixtures/two_speakers.rttm"

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
                if key in dataset:
                    assert dataset[key] in ids, (dataset["name"], key)


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


class TestCommittedTwoSpeakers:
    """The two-speaker clip and its references (design/internal/g1-stage2.md, "The two-speaker clip's rule"), as
    committed. The `data` test in tests/test_datasets.py rebuilds them and checks the rule's choices."""

    RTTM_LINE = re.compile(r"SPEAKER two_speakers 1 (\d+\.\d{3}) (\d+\.\d{3}) <NA> <NA> (\d+) <NA> <NA>")

    @pytest.fixture
    def fixture(self):
        return next(f for f in SETS["fixture"] if f["name"] == "two_speakers")

    @pytest.fixture
    def turns(self, fixture):
        """(start, end, speaker) of each RTTM line, which must all match the format."""
        text = (ROOT / fixture["rttm"]).read_text(encoding="utf-8")
        assert text.endswith("\n")
        matches = [self.RTTM_LINE.fullmatch(line) for line in text.splitlines()]
        assert all(matches), text
        return [(float(m[1]), float(m[1]) + float(m[2]), m[3]) for m in matches]

    @staticmethod
    def duration(fixture):
        with wave.open(str(ROOT / fixture["path"]), "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
            return w.getnframes() / 16000

    def test_its_files_match_the_manifest(self, fixture):
        assert sha256(ROOT / fixture["path"]) == fixture["derived_sha256"]
        assert sha256(ROOT / fixture["reference"]) == fixture["reference_sha256"]
        assert sha256(ROOT / fixture["rttm"]) == fixture["rttm_sha256"]
        assert (fixture["path"], fixture["reference"], fixture["rttm"]) == (
            "tests/fixtures/two_speakers.wav", "tests/fixtures/two_speakers.txt", "tests/fixtures/two_speakers.rttm")

    def test_it_is_16_khz_mono_16_bit_and_at_least_55_5_s(self, fixture):
        assert self.duration(fixture) >= 55.5

    def test_the_rttm_has_a_line_per_piece_by_its_speaker_at_its_offset(self, fixture, turns):
        assert [speaker for _, _, speaker in turns] == [PurePosixPath(p["member"]).stem.split("-")[0]
                                                        for p in fixture["concat"]]
        assert turns[0][0] == 0.0
        for (_, end, _), (start, _, _) in itertools.pairwise(turns):
            assert start - end == pytest.approx(fixture["gap"], abs=0.0015)
        assert turns[-1][1] == pytest.approx(self.duration(fixture), abs=0.001)

    def test_four_alternating_turns_of_two_speakers_each_at_least_13_5_s(self, fixture, turns):
        runs = [list(group) for _, group in itertools.groupby(turns, key=lambda t: t[2])]
        assert len(runs) == 4
        assert runs[0][0][2] == runs[2][0][2] != runs[1][0][2] == runs[3][0][2]
        for run in runs:
            assert run[-1][1] - run[0][0] >= 13.5 - 0.001, run

    def test_the_reference_is_one_line_of_words_separated_by_single_spaces(self, fixture):
        text = (ROOT / fixture["reference"]).read_text(encoding="utf-8")
        assert re.fullmatch(r"[A-Z']+( [A-Z']+)*\n", text), text

    def test_its_new_sources_are_listed(self):
        (source,) = (s for s in SOURCES["source"] if s["id"] == "librispeech-test-clean")
        paths = {m["path"] for m in source["member"]}
        assert "LibriSpeech/SPEAKERS.TXT" in paths
        assert "LibriSpeech/test-clean/121/121726/121-121726.trans.txt" in paths
        assert "LibriSpeech/test-clean/61/70968/61-70968.trans.txt" in paths

    SETS_TEXT = (datasets.MANIFEST_DIR / "sets.toml").read_text(encoding="utf-8")

    @pytest.mark.parametrize("old, new, problem", [
        (re.compile(r'^rttm = .*\n', re.MULTILINE), "", "rttm and rttm_sha256 must be given together"),
        (re.compile(r'^rttm_sha256 = .*\n', re.MULTILINE), "", "rttm and rttm_sha256 must be given together"),
        (re.compile(r'^(rttm_sha256 = ")([0-9a-f]{64})', re.MULTILINE), lambda m: m[1] + m[2].upper(),
         "isn't a SHA-256 in lower-case hex"),
    ])
    def test_a_bad_entry_raises(self, tmp_path, old, new, problem):
        text = old.sub(new, self.SETS_TEXT)
        assert text != self.SETS_TEXT
        (tmp_path / "sets.toml").write_text(text, encoding="utf-8")
        (tmp_path / "sources.toml").write_bytes((datasets.MANIFEST_DIR / "sources.toml").read_bytes())
        with pytest.raises(ValueError, match=re.escape(problem)):
            datasets.load_manifests(tmp_path)


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
