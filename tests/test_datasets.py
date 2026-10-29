"""The fetch of one source and the fixture recipe (design/internal/g1-slice.md, S1), and the CI subset's sources,
recipes and `fetch` (design/internal/g1-stage1.md §5a), with no network.

`urllib.request.urlopen` is replaced by one that serves a small .tar.gz built here, of FLAC files written
with soundfile and their chapter's .trans.txt, and for the AMI recipes a small WAV, a .zip of two speakers'
words.xml files and an RTTM. The tests marked `data`, at the end, read the real CI subset instead.
"""

import gzip
import hashlib
import io
import re
import subprocess
import sys
import tarfile
import urllib.error
import wave
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile

from lmt.eval import datasets

URLS = ["https://www.openslr.org/resources/12/test-clean.tar.gz",
        "https://us.openslr.org/resources/12/test-clean.tar.gz"]
CHAPTER = "LibriSpeech/test-clean/61/70968"
IDS = ["61-70968-0000", "61-70968-0001", "61-70968-0002"]
TEXTS = ["HE BEGAN A CONFUSED COMPLAINT", "AGAINST THE WIZARD", "WHO HAD VANISHED BEHIND THE CURTAIN"]
TRANS = f"{CHAPTER}/61-70968.trans.txt"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def flac(samples, rate=16000):
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, rate, format="FLAC", subtype="PCM_16")
    return buffer.getvalue()


def pieces():
    """Three short utterances of distinct samples, so their order shows in the output."""
    return [np.arange(1, 801, dtype=np.int16) * (i + 1) for i in range(3)]


def tar_gz(files):
    """The same bytes for the same files: the tar's and the gzip header's times are 0."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), mtime=0)


def members():
    files = {f"{CHAPTER}/{uid}.flac": flac(samples) for uid, samples in zip(IDS, pieces(), strict=True)}
    # A line for an utterance the clip doesn't use, so the reference takes only its own lines.
    files[TRANS] = "".join(f"{uid} {text}\n" for uid, text in zip([*IDS, "61-70968-0003"], [*TEXTS, "NOT USED"],
                                                                  strict=True)).encode()
    return files


def write_manifests(dir, archive, files, *, member_sha=None, gap=0.0, derived=None, reference=None):
    dir.mkdir(parents=True, exist_ok=True)
    listed = "".join(f'\n[[source.member]]\npath = "{name}"\nsha256 = "{(member_sha or {}).get(name, sha256(data))}"\n'
                     for name, data in files.items())
    (dir / "sources.toml").write_text(
        f'schema_version = 1\n\n[[source]]\nid = "librispeech-test-clean"\nurls = {URLS!r}\n'
        f'sha256 = "{sha256(archive)}"\nsize = {len(archive)}\nlicence = "CC-BY-4.0"\nkeep = false\n{listed}')
    concat = ",\n".join(f'  {{source = "librispeech-test-clean", member = "{CHAPTER}/{uid}.flac"}}' for uid in IDS)
    (dir / "sets.toml").write_text(
        f'schema_version = 1\n\n[[fixture]]\nname = "clip"\npath = "tests/fixtures/clip.wav"\n'
        f'reference = "tests/fixtures/clip.txt"\ngap = {gap}\nderived_sha256 = "{derived or "0" * 64}"\n'
        f'reference_sha256 = "{reference or "0" * 64}"\nconcat = [\n{concat},\n]\n')


class Server:
    """Stands in for urllib.request.urlopen: serves `files` by URL; any other URL fails as a network error."""

    def __init__(self, files):
        self.files = files
        self.requests = []

    def __call__(self, url, timeout=None):
        self.requests.append(url)
        if url not in self.files:
            raise urllib.error.URLError("connection refused")
        return io.BytesIO(self.files[url])


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LMT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    return tmp_path / "data"


def serve(monkeypatch, files):
    server = Server(files)
    monkeypatch.setattr(datasets.urllib.request, "urlopen", server)
    return server


def setup(tmp_path, monkeypatch, *, archive_files=None, served=None, **manifest):
    """Manifests listing members() as an archive of `archive_files` (members() by default), served at the
    first URL as is, or as `served` instead."""
    files = members()
    archive = tar_gz(files if archive_files is None else archive_files)
    write_manifests(tmp_path / "manifests", archive, files, **manifest)
    monkeypatch.setattr(datasets, "MANIFEST_DIR", tmp_path / "manifests")
    server = serve(monkeypatch, {URLS[0]: archive if served is None else served})
    return datasets.load_manifests(tmp_path / "manifests"), files, server


def cached_files(cache):
    return sorted(str(p.relative_to(cache)) for p in cache.rglob("*") if p.is_file()) if cache.exists() else []


def test_imports_nothing_from_the_eval_extra():
    code = "import sys, lmt.eval.datasets; print(sorted({'jiwer', 'pyannote.metrics'} & set(sys.modules)))"
    out = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True).stdout
    assert out == "[]\n"


def test_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LMT_DATA_DIR", str(tmp_path))
    assert datasets.data_dir() == tmp_path
    monkeypatch.delenv("LMT_DATA_DIR")
    assert datasets.data_dir() == datasets.Path.home() / ".cache" / "lmt" / "data"


class TestFetch:
    def test_good_archive_extracts_and_verifies_members_and_deletes_it(self, cache, tmp_path, monkeypatch):
        manifests, files, server = setup(tmp_path, monkeypatch)
        root = datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        assert root == cache / "sources" / "librispeech-test-clean"
        assert cached_files(cache) == sorted(f"sources/librispeech-test-clean/{name}" for name in files)
        for name, data in files.items():
            assert (root / name).read_bytes() == data
        assert server.requests == [URLS[0]]

        # Verified members in the cache aren't downloaded again.
        datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        assert server.requests == [URLS[0]]

    def test_a_changed_member_in_the_cache_is_fetched_again(self, cache, tmp_path, monkeypatch):
        manifests, files, server = setup(tmp_path, monkeypatch)
        root = datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        (root / TRANS).write_text("changed\n")
        datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        assert (root / TRANS).read_bytes() == files[TRANS]
        assert server.requests == [URLS[0], URLS[0]]

    def test_bad_archive_exits_3_and_leaves_nothing_in_the_cache(self, cache, tmp_path, monkeypatch, capsys):
        setup(tmp_path, monkeypatch, served=b"not the archive")
        assert datasets.main(["build-fixture", "clip"]) == 3
        size = len(tar_gz(members()))
        assert capsys.readouterr().err.splitlines() == [
            f"lmt: error: {URLS[0]}: size 15 doesn't match the manifest's {size}"]
        assert cached_files(cache) == []

    def test_archive_of_the_right_size_but_wrong_hash_exits_3(self, cache, tmp_path, monkeypatch, capsys):
        good = tar_gz(members())
        setup(tmp_path, monkeypatch, served=bytes(len(good)))
        assert datasets.main(["build-fixture", "clip"]) == 3
        assert capsys.readouterr().err.splitlines() == [
            f"lmt: error: {URLS[0]}: SHA-256 doesn't match the manifest"]
        assert cached_files(cache) == []

    def test_the_second_url_is_used_when_the_first_fails(self, cache, tmp_path, monkeypatch):
        manifests, files, _ = setup(tmp_path, monkeypatch)
        server = serve(monkeypatch, {URLS[1]: tar_gz(files)})
        datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        assert server.requests == URLS
        assert len(cached_files(cache)) == len(files)

    def test_every_url_failing_is_an_input_error(self, cache, tmp_path, monkeypatch):
        manifests, _, _ = setup(tmp_path, monkeypatch)
        server = serve(monkeypatch, {})
        with pytest.raises(datasets.InputError, match="download failed: connection refused") as e:
            datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        assert e.value.subject == URLS[1] and e.value.exit_code == 3
        assert server.requests == URLS
        assert cached_files(cache) == []

    def test_a_member_whose_hash_doesnt_match_is_deleted_with_exit_3(self, cache, tmp_path, monkeypatch, capsys):
        setup(tmp_path, monkeypatch, member_sha={TRANS: "f" * 64})
        assert datasets.main(["build-fixture", "clip"]) == 3
        root = cache / "sources" / "librispeech-test-clean"
        assert capsys.readouterr().err.splitlines() == [
            f"lmt: error: {root / TRANS}: SHA-256 doesn't match the manifest"]
        assert f"sources/librispeech-test-clean/{TRANS}" not in cached_files(cache)
        assert not any(name.endswith((".part", ".tar.gz")) for name in cached_files(cache))

    def test_only_listed_members_are_extracted_by_their_listed_paths(self, cache, tmp_path, monkeypatch):
        extra = {"../outside.txt": b"x", "/etc/absolute.txt": b"x", f"{CHAPTER}/other.flac": b"x"}
        manifests, files, _ = setup(tmp_path, monkeypatch, archive_files=members() | extra)
        datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        assert cached_files(cache) == sorted(f"sources/librispeech-test-clean/{name}" for name in files)
        assert not (cache / "sources" / "outside.txt").exists() and not (tmp_path / "outside.txt").exists()

    def test_a_listed_member_missing_from_the_archive_is_an_input_error(self, cache, tmp_path, monkeypatch):
        short = {name: data for name, data in members().items() if name != TRANS}
        manifests, _, _ = setup(tmp_path, monkeypatch, archive_files=short)
        with pytest.raises(datasets.InputError, match=f"has no member {TRANS}"):
            datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        assert not any(name.endswith(".tar.gz") for name in cached_files(cache))


class TestManifests:
    def test_a_member_path_outside_the_cache_is_refused(self, tmp_path):
        files = members()
        archive = tar_gz(files)
        write_manifests(tmp_path, archive, files | {"../outside.txt": b"x"})
        with pytest.raises(ValueError, match="'../outside.txt' isn't a relative path"):
            datasets.load_manifests(tmp_path)

    def test_a_piece_must_be_a_listed_member(self, tmp_path):
        files = members()
        listed = {name: data for name, data in files.items() if not name.endswith(f"{IDS[1]}.flac")}
        write_manifests(tmp_path, tar_gz(files), listed)
        with pytest.raises(ValueError, match=f"{IDS[1]}.flac isn't a member of librispeech-test-clean"):
            datasets.load_manifests(tmp_path)

    def test_a_bad_hash_is_refused(self, tmp_path):
        files = members()
        write_manifests(tmp_path, tar_gz(files), files, member_sha={TRANS: "ABC"})
        with pytest.raises(ValueError, match="isn't a SHA-256"):
            datasets.load_manifests(tmp_path)


class TestRecipe:
    def build(self, tmp_path, monkeypatch, gap=0.0):
        manifests, _, _ = setup(tmp_path, monkeypatch, gap=gap)
        datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        return datasets.build_fixture(manifests.fixtures["clip"], manifests)

    @staticmethod
    def frames(audio):
        with wave.open(io.BytesIO(audio)) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
            return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")

    def test_samples_come_out_in_order(self, cache, tmp_path, monkeypatch):
        audio, _ = self.build(tmp_path, monkeypatch)
        assert np.array_equal(self.frames(audio), np.concatenate(pieces()))

    def test_a_gap_of_a_quarter_second_inserts_4000_zero_samples(self, cache, tmp_path, monkeypatch):
        audio, _ = self.build(tmp_path, monkeypatch, gap=0.25)
        silence = np.zeros(4000, dtype=np.int16)
        a, b, c = pieces()
        assert np.array_equal(self.frames(audio), np.concatenate([a, silence, b, silence, c]))

    def test_the_reference_is_the_lines_joined_by_single_spaces(self, cache, tmp_path, monkeypatch):
        _, text = self.build(tmp_path, monkeypatch)
        assert text == " ".join(TEXTS) + "\n"

    @pytest.mark.parametrize("samples, rate, problem", [
        (np.zeros(800, dtype=np.int16), 44100, "44100 Hz, 1 channels"),
        (np.zeros((800, 2), dtype=np.int16), 16000, "16000 Hz, 2 channels"),
    ])
    def test_a_piece_that_isnt_16_khz_mono_raises(self, cache, tmp_path, monkeypatch, samples, rate, problem):
        manifests, _, _ = setup(tmp_path, monkeypatch)
        root = datasets.fetch_source(manifests.sources["librispeech-test-clean"])
        bad = root / CHAPTER / f"{IDS[1]}.flac"
        bad.write_bytes(flac(samples, rate))
        with pytest.raises(ValueError, match=f"{IDS[1]}.flac: {problem}"):
            datasets.build_fixture(manifests.fixtures["clip"], manifests)

    def test_build_fixture_write_then_check(self, cache, tmp_path, monkeypatch, capsys):
        audio, text = self.build(tmp_path, monkeypatch)
        setup(tmp_path, monkeypatch, derived=sha256(audio), reference=sha256(text.encode()))
        assert datasets.main(["build-fixture", "clip", "--write"]) == 0
        assert (tmp_path / "tests/fixtures/clip.wav").read_bytes() == audio
        assert (tmp_path / "tests/fixtures/clip.txt").read_text() == text
        assert datasets.main(["build-fixture", "clip"]) == 0
        assert capsys.readouterr().err == ""

    def test_build_fixture_exits_3_naming_the_file_that_differs(self, cache, tmp_path, monkeypatch, capsys):
        audio, _ = self.build(tmp_path, monkeypatch)
        setup(tmp_path, monkeypatch, derived=sha256(audio), reference="e" * 64)
        assert datasets.main(["build-fixture", "clip"]) == 3
        assert capsys.readouterr().err.splitlines() == [
            "lmt: error: tests/fixtures/clip.txt: the rebuilt file's SHA-256 doesn't match sets.toml"]


# --- The CI subset (design/internal/g1-stage1.md §5a) ----------------------------------------------------------

AMI_WAV_URL = "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2004a/audio/ES2004a.Mix-Headset.wav"
AMI_ZIP_URL = "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip"
RTTM_URL = ("https://raw.githubusercontent.com/BUTSpeechFIT/AMI-diarization-setup/" + "0" * 40
            + "/only_words/rttms/test/ES2004a.rttm")
CROP = 0.5  # seconds: 8,000 of the meeting's 16,000 frames

# Two speakers. B's first word has no starttime (0); A's "so" takes "Okay"'s. "Okay", "so" and "well" start
# together, so they sort by letter, then position. The "." is punctuation. "crossing"'s midpoint, 0.55 s, is
# past the crop, but "yes"'s, 0.485 s, is before it.
WORDS = {
    "words/ES2004a.A.words.xml": """<?xml version="1.0" encoding="ISO-8859-1" standalone="yes"?>
<nite:root nite:id="ES2004a.A.words" xmlns:nite="http://nite.sourceforge.net/">
   <w nite:id="ES2004a.A.words0" starttime="0.10" endtime="0.20">Okay</w>
   <w nite:id="ES2004a.A.words1" starttime="0.20" endtime="0.20" punc="true">.</w>
   <vocalsound nite:id="ES2004a.A.words2" starttime="0.21" endtime="0.25" type="laugh"/>
   <w nite:id="ES2004a.A.words3" endtime="0.35">so</w>
   <w nite:id="ES2004a.A.words4" starttime="0.40" endtime="0.70">crossing</w>
</nite:root>
""",
    "words/ES2004a.B.words.xml": """<?xml version="1.0" encoding="ISO-8859-1" standalone="yes"?>
<nite:root nite:id="ES2004a.B.words" xmlns:nite="http://nite.sourceforge.net/">
   <w nite:id="ES2004a.B.words0" endtime="0.05">um</w>
   <w nite:id="ES2004a.B.words1" starttime="0.10" endtime="0.15">well</w>
   <w nite:id="ES2004a.B.words2" starttime="0.30" endtime="0.40">right</w>
   <w nite:id="ES2004a.B.words3" starttime="0.45" endtime="0.52">yes</w>
</nite:root>
""",
}
REF_WORDS = "um Okay so well right yes"  # without "crossing"
RTTM = """SPEAKER ES2004a 1 0.000 0.300 <NA> <NA> MEE006 <NA> <NA>
SPEAKER ES2004a 1 0.400 0.300 <NA> <NA> FEE005 <NA> <NA>
SPEAKER ES2004a 1 0.500 0.200 <NA> <NA> MEE006 <NA> <NA>
SPEAKER ES2004b 1 0.100 0.100 <NA> <NA> MEE007 <NA> <NA>
"""


def meeting_samples():
    return (np.arange(16000) % 1000).astype(np.int16)


def wav(samples):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


def zip_of(files):
    """A directory entry, the files, and one file the manifest doesn't list."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(zipfile.ZipInfo("words/", date_time=(1980, 1, 1, 0, 0, 0)), b"")
        for name, data in {**files, "words/ES2004b.A.words.xml": "<nite:root/>"}.items():
            z.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data)
    return buffer.getvalue()


def source_toml(id, url, data, *, members=None, keep=True, sha=None):
    listed = "".join(f'\n[[source.member]]\npath = "{name}"\nsha256 = "{member_sha}"\n'
                     for name, member_sha in (members or {}).items())
    return (f'\n[[source]]\nid = "{id}"\nurls = ["{url}"]\nsha256 = "{sha or sha256(data)}"\nsize = {len(data)}\n'
            f'licence = "CC-BY-4.0"\nkeep = {str(keep).lower()}\n{listed}')


LS_CI = f"""
[[dataset]]
name = "LS-ci"
set = "ci"
kind = "librispeech"
source = "librispeech-test-clean"
utterances = {IDS[:2]!r}
"""


def ami_ci(derived, *, meetings='["ES2004a"]', crop=f"crop = {CROP}\n"):
    return (f'\n[[dataset]]\nname = "AMI-ci"\nset = "ci"\nkind = "ami"\nmeetings = {meetings}\n'
            f'audio = "ami-ES2004a"\nwords = "ami-annotations-1.6.2"\nrttm = "ami-rttm-ES2004a"\n{crop}'
            f'derived_sha256 = "{derived}"\n')


class CI:
    """Manifests of the CI subset in small: LS-ci with two of members()'s utterances, and AMI-ci cropped."""

    def __init__(self, tmp_path, monkeypatch, *, wav_sha=None, derived=None, datasets_toml=None, words_sha=None,
                 meeting_wav=None):
        self.ls = members()
        self.archive = tar_gz(self.ls)
        self.wav = wav(meeting_samples()) if meeting_wav is None else meeting_wav
        self.zip = zip_of(WORDS)
        self.rttm = RTTM.encode()
        self.cropped = wav(meeting_samples()[:8000])
        word_members = {name: sha256(data.encode()) for name, data in WORDS.items()} | (words_sha or {})
        dir = tmp_path / "manifests"
        dir.mkdir(parents=True, exist_ok=True)
        (dir / "sources.toml").write_text(
            "schema_version = 1\n"
            + source_toml("librispeech-test-clean", URLS[0], self.archive, keep=False,
                          members={name: sha256(data) for name, data in self.ls.items()})
            + source_toml("ami-ES2004a", AMI_WAV_URL, self.wav, sha=wav_sha)
            + source_toml("ami-annotations-1.6.2", AMI_ZIP_URL, self.zip, keep=False, members=word_members)
            + source_toml("ami-rttm-ES2004a", RTTM_URL, self.rttm))
        (dir / "sets.toml").write_text(
            "schema_version = 1\n" + (datasets_toml if datasets_toml is not None
                                      else LS_CI + ami_ci(derived or sha256(self.cropped))))
        monkeypatch.setattr(datasets, "MANIFEST_DIR", dir)
        self.server = serve(monkeypatch, {URLS[0]: self.archive, AMI_WAV_URL: self.wav, AMI_ZIP_URL: self.zip,
                                          RTTM_URL: self.rttm})
        self.dir = dir


class TestFetchSet:
    def test_fetch_ci_builds_ls_ci_and_ami_ci(self, cache, tmp_path, monkeypatch):
        ci = CI(tmp_path, monkeypatch)
        ls, ls_2, ami = datasets.fetch("ci")
        ls_root = cache / "sources" / "librispeech-test-clean"
        assert (ls.dataset, ls.id, ls.audio, ls.duration, ls.ref_text) == (
            "LS-ci", IDS[0], ls_root / CHAPTER / f"{IDS[0]}.flac", 800 / 16000, TEXTS[0])
        assert (ls_2.id, ls_2.ref_text, ls_2.ref_turns) == (IDS[1], TEXTS[1], None)

        assert (ami.dataset, ami.id, ami.duration) == ("AMI-ci", "ES2004a", CROP)
        assert ami.audio == cache / "derived" / "AMI-ci" / "ES2004a.wav"
        assert ami.audio.read_bytes() == ci.cropped
        assert ami.ref_text == REF_WORDS
        assert ami.ref_turns == (datasets.Turn(0.0, 0.3, "MEE006"), datasets.Turn(0.4, CROP, "FEE005"))

        # Every source once, single files kept, archives deleted after their members are extracted.
        assert sorted(ci.server.requests) == sorted([URLS[0], AMI_WAV_URL, AMI_ZIP_URL, RTTM_URL])
        assert cached_files(cache) == sorted(
            [f"sources/librispeech-test-clean/{name}" for name in ci.ls]
            + [f"sources/ami-annotations-1.6.2/{name}" for name in WORDS]
            + ["sources/ami-ES2004a/ES2004a.Mix-Headset.wav", "sources/ami-rttm-ES2004a/ES2004a.rttm",
               "derived/AMI-ci/ES2004a.wav"])

        # A second fetch verifies the cache and downloads nothing.
        assert datasets.fetch("ci") == [ls, ls_2, ami]
        assert len(ci.server.requests) == 4

    def test_the_fetch_command_prints_one_line_per_dataset(self, cache, tmp_path, monkeypatch, capsys):
        CI(tmp_path, monkeypatch)
        assert datasets.main(["fetch", "ci"]) == 0
        assert capsys.readouterr().out.splitlines() == ["LS-ci: 2 items, 0.100 s", "AMI-ci: 1 items, 0.500 s"]

    def test_an_unknown_set_exits_2(self, cache, tmp_path, monkeypatch, capsys):
        CI(tmp_path, monkeypatch)
        assert datasets.main(["fetch", "full"]) == 2
        assert capsys.readouterr().err.splitlines() == ["lmt: error: full: not a set in sets.toml (ci)"]

    def test_a_bad_single_file_exits_3_and_leaves_nothing_in_the_cache(self, cache, tmp_path, monkeypatch, capsys):
        CI(tmp_path, monkeypatch, wav_sha="a" * 64, datasets_toml=ami_ci("b" * 64))
        assert datasets.main(["fetch", "ci"]) == 3
        assert f"lmt: error: {AMI_WAV_URL}: SHA-256 doesn't match the manifest" in capsys.readouterr().err
        assert not any(name.startswith("sources/ami-ES2004a/") for name in cached_files(cache))

    def test_a_changed_single_file_in_the_cache_is_fetched_again(self, cache, tmp_path, monkeypatch):
        ci = CI(tmp_path, monkeypatch)
        datasets.fetch("ci")
        (cache / "sources" / "ami-rttm-ES2004a" / "ES2004a.rttm").write_text("changed\n")
        datasets.fetch("ci")
        assert (cache / "sources" / "ami-rttm-ES2004a" / "ES2004a.rttm").read_bytes() == ci.rttm
        assert ci.server.requests.count(RTTM_URL) == 2

    def test_a_zip_member_whose_hash_doesnt_match_exits_3(self, cache, tmp_path, monkeypatch, capsys):
        name = "words/ES2004a.B.words.xml"
        CI(tmp_path, monkeypatch, words_sha={name: "f" * 64})
        assert datasets.main(["fetch", "ci"]) == 3
        dest = cache / "sources" / "ami-annotations-1.6.2" / name
        assert f"lmt: error: {dest}: SHA-256 doesn't match the manifest" in capsys.readouterr().err
        assert not dest.exists()
        assert not any(name.endswith((".part", ".zip")) for name in cached_files(cache))

    def test_a_cropped_wav_that_doesnt_match_exits_3_and_writes_nothing(self, cache, tmp_path, monkeypatch, capsys):
        CI(tmp_path, monkeypatch, derived="c" * 64)
        assert datasets.main(["fetch", "ci"]) == 3
        path = cache / "derived" / "AMI-ci" / "ES2004a.wav"
        assert capsys.readouterr().err.splitlines() == [
            f"lmt: error: {path}: the cropped WAV's SHA-256 doesn't match sets.toml"]
        assert not any(name.startswith("derived/") for name in cached_files(cache))

    def test_a_changed_cropped_wav_is_rebuilt(self, cache, tmp_path, monkeypatch):
        ci = CI(tmp_path, monkeypatch)
        (_, _, ami) = datasets.fetch("ci")
        ami.audio.write_bytes(b"changed")
        datasets.fetch("ci")
        assert ami.audio.read_bytes() == ci.cropped


class TestSampleMeeting:
    def test_it_downloads_only_the_audio_and_returns_the_cropped_wav(self, cache, tmp_path, monkeypatch):
        ci = CI(tmp_path, monkeypatch)
        path = datasets.sample_meeting()
        assert path == cache / "derived" / "AMI-ci" / "ES2004a.wav"
        assert path.read_bytes() == ci.cropped
        assert ci.server.requests == [AMI_WAV_URL]
        assert datasets.sample_meeting(offline=True) == path
        assert ci.server.requests == [AMI_WAV_URL]

    def test_offline_without_it_is_an_input_error(self, cache, tmp_path, monkeypatch):
        ci = CI(tmp_path, monkeypatch)
        with pytest.raises(datasets.InputError) as e:
            datasets.sample_meeting(offline=True)
        assert str(e.value) == "LMT_DATA_DIR: the sample meeting isn't downloaded; run once without --offline"
        assert ci.server.requests == []

    def test_offline_with_a_changed_file_is_an_input_error(self, cache, tmp_path, monkeypatch):
        CI(tmp_path, monkeypatch)
        datasets.sample_meeting().write_bytes(b"changed")
        with pytest.raises(datasets.InputError, match="the sample meeting isn't downloaded"):
            datasets.sample_meeting(offline=True)


class TestAmiRecipes:
    @pytest.fixture
    def words(self, tmp_path):
        paths = []
        for name, data in WORDS.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="iso-8859-1")
            paths.append((name.split(".")[1], path))
        return datasets.ami_words(paths)

    def test_words_sorted_by_start_then_letter_then_position(self, words):
        assert [text for _, _, text in words] == ["um", "Okay", "so", "well", "right", "crossing", "yes"]

    def test_a_missing_start_takes_the_previous_words_or_0(self, words):
        assert words[0] == (0.0, 0.05, "um")
        assert words[2] == (0.10, 0.35, "so")

    def test_punctuation_is_skipped(self, words):
        assert "." not in [text for _, _, text in words]

    def test_the_crop_keeps_words_by_midpoint_and_clips_turns(self, words, tmp_path):
        rttm = tmp_path / "ES2004a.rttm"
        rttm.write_text(RTTM)
        turns = datasets.rttm_turns(rttm, "ES2004a")
        assert [t.speaker for t in turns] == ["MEE006", "FEE005", "MEE006"]
        kept_words, kept_turns = datasets.crop_references(words, turns, CROP)
        assert " ".join(text for _, _, text in kept_words) == REF_WORDS
        assert kept_turns == [datasets.Turn(0.0, 0.3, "MEE006"), datasets.Turn(0.4, CROP, "FEE005")]

    def test_a_wav_that_isnt_16_khz_mono_raises(self, cache, tmp_path, monkeypatch):
        stereo = io.BytesIO()
        with wave.open(stereo, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(bytes(4 * 16000))
        CI(tmp_path, monkeypatch, meeting_wav=stereo.getvalue())
        with pytest.raises(ValueError, match="16000 Hz, 2 channels, 16-bit; the crop takes 16 kHz mono"):
            datasets.sample_meeting()


class TestDatasetManifests:
    def load(self, tmp_path, monkeypatch, datasets_toml):
        ci = CI(tmp_path, monkeypatch, datasets_toml=datasets_toml)
        return datasets.load_manifests(ci.dir)

    def test_the_ci_manifests_load(self, tmp_path, monkeypatch):
        manifests = self.load(tmp_path, monkeypatch, LS_CI + ami_ci("0" * 64))
        assert list(manifests.datasets) == ["LS-ci", "AMI-ci"]
        assert manifests.datasets["AMI-ci"].crop == CROP

    @pytest.mark.parametrize("datasets_toml, problem", [
        (LS_CI.replace(IDS[1], "61-70968-0009"), "61-70968-0009.flac isn't a member of librispeech-test-clean"),
        (LS_CI.replace(IDS[1], "61_70968"), "'61_70968' isn't a LibriSpeech utterance id"),
        (LS_CI.replace('"librispeech-test-clean"', '"elsewhere"'), "source 'elsewhere' isn't in sources.toml"),
        (LS_CI.replace('"librispeech"', '"other"'), "kind must be one of librispeech, ami"),
        (LS_CI + LS_CI, "name 'LS-ci' is repeated"),
        (ami_ci("0" * 64, meetings='["ES2004a", "ES2004b"]'), "an ami dataset has one meeting"),
        (ami_ci("0" * 64, crop=""), "crop and derived_sha256 go together"),
        (ami_ci("0" * 64, meetings='["ES2004c"]'), "has no ES2004c.<speaker>.words.xml member"),
        (ami_ci("0" * 64).replace('audio = "ami-ES2004a"', 'audio = "ami-annotations-1.6.2"'),
         "ami-annotations-1.6.2 must be a single file"),
    ])
    def test_a_bad_dataset_is_refused(self, tmp_path, monkeypatch, datasets_toml, problem):
        with pytest.raises(ValueError, match=re.escape(problem)):
            self.load(tmp_path, monkeypatch, datasets_toml)

    def test_a_dataset_needs_its_chapters_transcript(self, tmp_path, monkeypatch):
        ci = CI(tmp_path, monkeypatch, datasets_toml=LS_CI)
        sources = (ci.dir / "sources.toml").read_text()
        (ci.dir / "sources.toml").write_text(
            sources.replace(f'\n[[source.member]]\npath = "{TRANS}"\nsha256 = "{sha256(ci.ls[TRANS])}"\n', ""))
        with pytest.raises(ValueError, match=re.escape(f"{TRANS} isn't a member of librispeech-test-clean")):
            datasets.load_manifests(ci.dir)

    def test_a_single_file_source_must_be_kept(self, tmp_path):
        (tmp_path / "sources.toml").write_text(
            "schema_version = 1\n" + source_toml("ami-ES2004a", AMI_WAV_URL, b"x", keep=False))
        (tmp_path / "sets.toml").write_text("schema_version = 1\n")
        with pytest.raises(ValueError, match="a source without members is a single file, so keep must be true"):
            datasets.load_manifests(tmp_path)


# --- The concatenation's slices, cut and references (design/internal/g1-stage1.md §5b) -------------------------


def slices_fixture(tmp_path, monkeypatch, concat, *, gap=0.0625, cut=None, sources=None):
    """Manifests with the meeting WAV as a single-file source, and a fixture of `concat`'s slices of it."""
    dir = tmp_path / "manifests"
    dir.mkdir(parents=True, exist_ok=True)
    audio = wav(meeting_samples())
    (dir / "sources.toml").write_text(
        "schema_version = 1\n" + (sources if sources is not None else source_toml("ami-ES2004a", AMI_WAV_URL, audio)))
    pieces = ",\n".join(f"  {{source = \"ami-ES2004a\", {piece}}}" for piece in concat)
    (dir / "sets.toml").write_text(
        f'schema_version = 1\n\n[[fixture]]\nname = "hour"\npath = "hour.wav"\nreference = "hour.txt"\ngap = {gap}\n'
        f'{"" if cut is None else f"cut = {cut}{chr(10)}"}derived_sha256 = "{"0" * 64}"\n'
        f'reference_sha256 = "{"0" * 64}"\nconcat = [\n{pieces},\n]\n')
    monkeypatch.setattr(datasets, "MANIFEST_DIR", dir)
    serve(monkeypatch, {AMI_WAV_URL: audio})
    return dir


class TestConcat:
    def test_offsets_count_the_pieces_and_the_gaps(self):
        a, b, c = pieces()  # 800 samples, 0.05 s, each
        samples, offsets = datasets.concat([a, b, c], gap=0.25)
        assert offsets == [0.0, 0.3, 0.6]
        silence = np.zeros(4000, dtype=np.int16)
        assert np.array_equal(samples, np.concatenate([a, silence, b, silence, c]))

    def test_the_cut_keeps_the_first_cut_seconds_and_the_offsets_stay(self):
        a, b, c = pieces()
        samples, offsets = datasets.concat([a, b, c], gap=0.25, cut=0.33)
        assert np.array_equal(samples, np.concatenate([a, np.zeros(4000, dtype=np.int16), b[:480]]))
        assert offsets == [0.0, 0.3, 0.6]

    def test_a_fixture_of_slices_with_a_gap_and_a_cut(self, cache, tmp_path, monkeypatch):
        dir = slices_fixture(tmp_path, monkeypatch, ["start = 0.25, end = 0.5", "start = 0, end = 0.125"], cut=0.3)
        manifests = datasets.load_manifests(dir)
        fixture = manifests.fixtures["hour"]
        assert fixture.concat[0] == datasets.Piece("ami-ES2004a", start=0.25, end=0.5)
        assert fixture.cut == 0.3
        datasets.fetch_source(manifests.sources["ami-ES2004a"])
        audio, text = datasets.build_fixture(fixture, manifests)
        meeting = meeting_samples()
        expected = np.concatenate([meeting[4000:8000], np.zeros(1000, dtype=np.int16), meeting[:2000]])[:4800]
        assert np.array_equal(TestRecipe.frames(audio), expected)
        assert text == "\n"  # a slice's references are its dataset's, not a .trans.txt line

    def test_a_slice_past_the_end_of_its_source_raises(self, cache, tmp_path, monkeypatch):
        dir = slices_fixture(tmp_path, monkeypatch, ["start = 0.5, end = 1.5"])
        manifests = datasets.load_manifests(dir)
        datasets.fetch_source(manifests.sources["ami-ES2004a"])
        with pytest.raises(ValueError, match=r"1.0 s, shorter than the slice's end, 1.5 s"):
            datasets.build_fixture(manifests.fixtures["hour"], manifests)

    def test_a_slice_of_a_wav_that_isnt_16_khz_mono_16_bit_raises(self, tmp_path):
        path = tmp_path / "stereo.wav"
        soundfile.write(path, np.zeros((1600, 2), dtype=np.int16), 16000, subtype="PCM_16")
        with pytest.raises(ValueError, match="16000 Hz, 2 channels, 16-bit; the recipe takes 16 kHz mono 16-bit"):
            datasets.slice_wav(path, 0.0, 0.05)

    @pytest.mark.parametrize("concat, cut, problem", [
        (["start = 0.5, end = 0.5"], None, "a slice needs 0 <= start < end"),
        (["start = -0.1, end = 0.5"], None, "a slice needs 0 <= start < end"),
        (["start = 0, end = 0.5"], 0, "cut must be positive"),
        (['member = "words/x.xml", start = 0, end = 0.5'], None, "a piece is a member or a slice, not both"),
        (["start = 0"], None, "end is missing or has the wrong type"),
    ])
    def test_a_bad_slice_or_cut_is_refused(self, tmp_path, monkeypatch, concat, cut, problem):
        dir = slices_fixture(tmp_path, monkeypatch, concat, cut=cut)
        with pytest.raises(ValueError, match=problem):
            datasets.load_manifests(dir)

    def test_a_slice_of_an_archive_is_refused(self, tmp_path, monkeypatch):
        archive = zip_of(WORDS)
        sources = source_toml("ami-ES2004a", AMI_ZIP_URL, archive, keep=False,
                              members={name: sha256(data.encode()) for name, data in WORDS.items()})
        dir = slices_fixture(tmp_path, monkeypatch, ["start = 0, end = 0.5"], sources=sources)
        with pytest.raises(ValueError, match="a slice's source ami-ES2004a must be a single file, without members"):
            datasets.load_manifests(dir)


class TestPlaceReferences:
    WORDS = ((0.9, 1.3, "before"), (1.1, 1.3, "first"), (2.0, 2.4, "inside"), (2.8, 3.4, "last"),
             (2.9, 3.5, "after"))
    TURNS = (datasets.Turn(0.0, 0.5, "A"), datasets.Turn(0.8, 1.6, "A"), datasets.Turn(1.8, 2.6, "B"),
             datasets.Turn(2.9, 4.0, "B"), datasets.Turn(3.2, 3.6, "A"))

    def test_words_by_midpoint_inside_the_slice_are_shifted_to_the_offset(self):
        words, _ = datasets.place_references(self.WORDS, [], start=1.0, end=3.2, offset=10.0)
        # Midpoints 1.1, 1.2, 2.2, 3.1 and 3.2: "before"'s is inside [1.0, 3.2), "after"'s isn't.
        assert [(round(s, 6), round(e, 6), t) for s, e, t in words] == [
            (9.9, 10.3, "before"), (10.1, 10.3, "first"), (11.0, 11.4, "inside"), (11.8, 12.4, "last")]

    def test_turns_overlapping_the_slice_are_clipped_to_it_and_shifted(self):
        _, turns = datasets.place_references([], self.TURNS, start=1.0, end=3.2, offset=10.0)
        assert [(round(t.start, 6), round(t.end, 6), t.speaker) for t in turns] == [
            (10.0, 10.6, "A"), (10.8, 11.6, "B"), (11.9, 12.2, "B")]

    def test_then_the_cut_crops_them_as_ami_cis_crop_does(self):
        words, turns = datasets.place_references(self.WORDS, self.TURNS, start=1.0, end=3.2, offset=10.0)
        words, turns = datasets.crop_references(words, turns, 11.5)
        assert [t for _, _, t in words] == ["before", "first", "inside"]
        assert [(round(t.start, 6), round(t.end, 6), t.speaker) for t in turns] == [
            (10.0, 10.6, "A"), (10.8, 11.5, "B")]


class TestPin:
    def test_an_archive_prints_its_hash_then_the_matching_members_with_their_audio_facts(
            self, cache, monkeypatch, capsys):
        files = members()
        archive = tar_gz(files)
        serve(monkeypatch, {URLS[0]: archive})
        assert datasets.main(["pin", URLS[0], "--member", "*.flac", "--member", "*/61-70968.trans.txt"]) == 0
        assert capsys.readouterr().out.splitlines() == [
            f"{URLS[0]} {len(archive)} {sha256(archive)}",
            *(f"{CHAPTER}/{uid}.flac {sha256(files[f'{CHAPTER}/{uid}.flac'])} 800 16000 1" for uid in IDS),
            f"{TRANS} {sha256(files[TRANS])}",
        ]
        assert cached_files(cache) == []

    def test_a_zip_prints_only_the_regular_members_that_match(self, cache, monkeypatch):
        archive = zip_of(WORDS)
        serve(monkeypatch, {AMI_ZIP_URL: archive})
        assert datasets.pin(AMI_ZIP_URL, ("words/ES2004a.*",)) == [
            f"{AMI_ZIP_URL} {len(archive)} {sha256(archive)}",
            *(f"{name} {sha256(data.encode())}" for name, data in WORDS.items()),
        ]

    def test_a_wav_prints_its_frames_rate_and_channels(self, cache, monkeypatch):
        audio = wav(meeting_samples())
        serve(monkeypatch, {AMI_WAV_URL: audio})
        assert datasets.pin(AMI_WAV_URL) == [f"{AMI_WAV_URL} {len(audio)} {sha256(audio)} 16000 16000 1"]
        assert cached_files(cache) == []

    @pytest.mark.parametrize("url", [
        "https://example.com/resources/12/test-clean.tar.gz",
        "http://www.openslr.org/resources/12/test-clean.tar.gz",
        "https://www.openslr.org/resources/13/test-clean.tar.gz",
        "https://raw.githubusercontent.com/BUTSpeechFIT/AMI-diarization-setup/main/only_words/rttms/test/x.rttm",
    ])
    def test_a_url_outside_the_pinned_prefixes_exits_2_without_a_request(self, cache, monkeypatch, capsys, url):
        server = serve(monkeypatch, {})
        assert datasets.main(["pin", url]) == 2
        assert server.requests == []
        assert capsys.readouterr().err.splitlines() == [
            f"lmt: error: {url}: isn't under a pinned LibriSpeech or AMI prefix"]

    def test_member_on_a_single_file_exits_2(self, cache, monkeypatch):
        server = serve(monkeypatch, {})
        assert datasets.main(["pin", AMI_WAV_URL, "--member", "*"]) == 2
        assert server.requests == []

    def test_a_failed_download_exits_3(self, cache, monkeypatch, capsys):
        serve(monkeypatch, {})
        assert datasets.main(["pin", AMI_WAV_URL]) == 3
        assert "download failed" in capsys.readouterr().err

    def test_the_prefixes_are_the_manifest_tests(self):
        assert datasets.URL_PREFIXES[-1] == ("https://raw.githubusercontent.com/BUTSpeechFIT/AMI-diarization-setup/"
                                             f"{datasets.BUT_COMMIT}/")


# --- The CI subset itself ---------------------------------------------------------------------------------------
# Marked `data`: they run in the eval-ci job, after `python -m lmt.eval.datasets fetch ci` has filled LMT_DATA_DIR.

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ci_items():
    return datasets.fetch("ci")


@pytest.mark.data
@pytest.mark.criterion("G1-5")
class TestFetchCI:
    """`fetch("ci")` gives the CI subset, every file verified against the manifests."""

    def test_ls_ci_has_20_items_with_references(self, ci_items):
        ls = [i for i in ci_items if i.dataset == "LS-ci"]
        assert [i.id for i in ls] == list(datasets.load_manifests().datasets["LS-ci"].utterances)
        for item in ls:
            assert item.audio.is_file(), item.id
            assert item.ref_text and item.ref_text == item.ref_text.strip(), item.id
            assert 4.0 <= item.duration <= 12.0, item.id

    def test_ami_ci_is_one_300_s_item_with_words_and_turns(self, ci_items):
        (ami,) = [i for i in ci_items if i.dataset == "AMI-ci"]
        assert ami.id == "ES2004a"
        assert ami.duration == 300.0
        with wave.open(str(ami.audio), "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert len(ami.ref_text.split()) > 100
        assert len({t.speaker for t in ami.ref_turns}) >= 2
        assert all(0.0 <= t.start < t.end <= 300.0 for t in ami.ref_turns)

    def test_the_sample_meeting_is_ami_ci_offline(self, ci_items):
        (ami,) = [i for i in ci_items if i.dataset == "AMI-ci"]
        assert datasets.sample_meeting(offline=True) == ami.audio


@pytest.mark.data
def test_the_clip_rebuilds_from_the_cache(ci_items):
    """S1's reference clip, rebuilt from the cache, is the committed one byte for byte."""
    manifests = datasets.load_manifests()
    fixture = manifests.fixtures["clip"]
    audio, text = datasets.build_fixture(fixture, manifests)
    assert audio == (ROOT / fixture.path).read_bytes()
    assert text.encode("utf-8") == (ROOT / fixture.reference).read_bytes()
