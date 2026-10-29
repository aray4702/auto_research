"""The fetch of one source and the fixture recipe, with no network (design/internal/g1-slice.md, S1).

`urllib.request.urlopen` is replaced by one that serves a small .tar.gz built here, of FLAC files written
with soundfile and their chapter's .trans.txt.
"""

import gzip
import hashlib
import io
import subprocess
import sys
import tarfile
import urllib.error
import wave

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


FORMATS = ("flac", "mp3", "m4a", "mp4", "mov")


def recipe(wav, out):
    """The table in design/internal/g1-formats.md, "The recipe", word for word."""
    mp4 = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", wav, "-f", "lavfi", "-i", "color=c=black:s=320x240:r=1",
           "-map", "0:a:0", "-map", "1:v:0", "-shortest", "-map_metadata", "-1", "-fflags", "+bitexact",
           "-flags:a", "+bitexact", "-flags:v", "+bitexact", "-threads", "1", "-c:a", "aac", "-profile:a", "aac_low",
           "-b:a", "128k", "-ar", "48000", "-ac", "1", "-c:v", "mpeg4", "-pix_fmt", "yuv420p"]
    return {
        "flac": ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", wav, "-map", "0:a:0", "-map_metadata", "-1",
                 "-fflags", "+bitexact", "-flags:a", "+bitexact", "-threads", "1", "-c:a", "flac",
                 "-compression_level", "8", "-sample_fmt", "s16", "-ar", "16000", "-ac", "1", "-f", "flac", out],
        "mp3": ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", wav, "-map", "0:a:0", "-map_metadata", "-1",
                "-fflags", "+bitexact", "-flags:a", "+bitexact", "-threads", "1", "-c:a", "libmp3lame",
                "-b:a", "128k", "-ar", "44100", "-ac", "1", "-f", "mp3", out],
        "m4a": ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", wav, "-map", "0:a:0", "-map_metadata", "-1",
                "-fflags", "+bitexact", "-flags:a", "+bitexact", "-threads", "1", "-c:a", "aac",
                "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "1", "-f", "ipod", out],
        "mp4": [*mp4, "-f", "mp4", out],
        "mov": [*mp4, "-f", "mov", out],
    }


def encoded(fmt):
    return f"encoded as {fmt}\n".encode()


class FakeFfmpeg:
    """Stands in for subprocess.run: answers `ffmpeg -version`, and for each encoding records its argv and writes
    encoded(<format>) to the last argument, or fails on the format `fail`."""

    def __init__(self, version="8.0", fail=None):
        self.version, self.fail = version, fail
        self.calls = []

    def __call__(self, argv, check=None, capture_output=None, text=None):
        if argv == ["ffmpeg", "-version"]:
            return subprocess.CompletedProcess(argv, 0, f"ffmpeg version {self.version} Copyright (c)\n", "")
        fmt = FORMATS[len(self.calls)]
        self.calls.append(argv)
        if fmt == self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "[mp3 @ 0x1] warning\nUnknown encoder 'libmp3lame'\n")
        datasets.Path(argv[-1]).write_bytes(encoded(fmt))
        return subprocess.CompletedProcess(argv, 0, "", "")


class TestEncodeFixture:
    @pytest.fixture
    def fixtures(self, cache, tmp_path, monkeypatch):
        """A WAV in a temporary tests/fixtures, and manifests giving it encodings recorded by ffmpeg 8.0."""
        setup(tmp_path, monkeypatch)
        (tmp_path / "tests/fixtures").mkdir(parents=True)
        (tmp_path / "tests/fixtures/clip.wav").write_bytes(b"the clip")
        self.record(tmp_path)
        return tmp_path / "tests/fixtures"

    @staticmethod
    def record(tmp_path, version="8.0", hashes=None):
        hashes = {fmt: sha256(encoded(fmt)) for fmt in FORMATS} | (hashes or {})
        listed = "".join(f'  {{format = "{fmt}", sha256 = "{hashes[fmt]}"}},\n' for fmt in FORMATS)
        sets = tmp_path / "manifests" / "sets.toml"
        sets.write_text(sets.read_text() + f'ffmpeg = "{version}"\nencodings = [\n{listed}]\n')

    @staticmethod
    def fake(monkeypatch, **kwargs):
        ffmpeg = FakeFfmpeg(**kwargs)
        monkeypatch.setattr(datasets.subprocess, "run", ffmpeg)
        return ffmpeg

    def test_write_runs_the_recipe_in_order_writes_the_files_and_exits_0(self, fixtures, monkeypatch, capsys):
        ffmpeg = self.fake(monkeypatch)
        assert datasets.main(["encode-fixture", "clip", "--write"]) == 0
        wav = str((fixtures / "clip.wav").resolve())
        for fmt, argv in zip(FORMATS, ffmpeg.calls, strict=True):
            out = datasets.Path(argv[-1])
            assert out.parent == fixtures.resolve() and out.name.endswith(f".{fmt}.tmp")
            assert argv == recipe(wav, argv[-1])[fmt]
        assert sorted(p.name for p in fixtures.iterdir()) == sorted(["clip.wav", *(f"clip.{f}" for f in FORMATS)])
        for fmt in FORMATS:
            assert (fixtures / f"clip.{fmt}").read_bytes() == encoded(fmt)
        out, err = capsys.readouterr()
        assert out.splitlines() == [f"tests/fixtures/clip.{fmt} {sha256(encoded(fmt))}" for fmt in FORMATS] + [
            "ffmpeg 8.0"]
        assert err == ""

    def test_without_write_nothing_is_written(self, fixtures, monkeypatch):
        ffmpeg = self.fake(monkeypatch)
        assert datasets.main(["encode-fixture", "clip"]) == 0
        assert len(ffmpeg.calls) == 5
        assert [p.name for p in fixtures.iterdir()] == ["clip.wav"]

    def test_a_hash_that_doesnt_match_exits_3_naming_the_first(self, fixtures, tmp_path, monkeypatch, capsys):
        setup(tmp_path, monkeypatch)
        self.record(tmp_path, hashes={"m4a": "e" * 64, "mov": "e" * 64})
        self.fake(monkeypatch)
        assert datasets.main(["encode-fixture", "clip", "--write"]) == 3
        assert capsys.readouterr().err.splitlines() == [
            "lmt: error: tests/fixtures/clip.m4a: the encoded file's SHA-256 doesn't match sets.toml"]
        assert (fixtures / "clip.m4a").read_bytes() == encoded("m4a")  # --write writes before it compares

    def test_another_ffmpeg_version_exits_3(self, fixtures, monkeypatch, capsys):
        self.fake(monkeypatch, version="7.1")
        assert datasets.main(["encode-fixture", "clip"]) == 3
        assert capsys.readouterr().err.splitlines() == [
            "lmt: error: ffmpeg: version 7.1 here, sets.toml records 8.0; the hashes can't be compared"]

    def test_a_failed_encoding_exits_3_and_leaves_no_file(self, fixtures, monkeypatch, capsys):
        self.fake(monkeypatch, fail="mp3")
        assert datasets.main(["encode-fixture", "clip", "--write"]) == 3
        assert capsys.readouterr().err.splitlines() == [
            "lmt: error: tests/fixtures/clip.mp3: ffmpeg failed: Unknown encoder 'libmp3lame'"]
        assert [p.name for p in fixtures.iterdir()] == ["clip.wav"]

    def test_no_ffmpeg_on_path_is_one_error_line(self, fixtures, monkeypatch, capsys):
        def missing(argv, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", argv[0])

        monkeypatch.setattr(datasets.subprocess, "run", missing)
        assert datasets.main(["encode-fixture", "clip"]) == 3
        assert capsys.readouterr().err.splitlines() == ["lmt: error: ffmpeg: not found on PATH"]

    def test_a_fixture_without_encodings_is_a_usage_error(self, cache, tmp_path, monkeypatch, capsys):
        setup(tmp_path, monkeypatch)
        self.fake(monkeypatch)
        assert datasets.main(["encode-fixture", "clip"]) == 2
        assert capsys.readouterr().err.splitlines() == ["lmt: error: clip: has no encodings in sets.toml"]
