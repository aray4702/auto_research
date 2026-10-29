"""The evaluation data: the manifests, the cache and its fetch, the fixture recipe (design/internal/g1-slice.md,
S1), and the datasets' recipes (design/internal/g1-stage1.md §5a).

Every file that's downloaded is listed in `manifests/sources.toml`, with its SHA-256, and only the archive members
listed there are extracted. A source without members is a single file, kept as it is. `manifests/sets.toml` says
what's built from them. The cache is `<LMT_DATA_DIR>/sources/<source id>/<file or member path>`, and what a
recipe makes is under `<LMT_DATA_DIR>/derived/<dataset name>/`. Nothing here imports the eval extra. The
fixture's encodings are made by ffmpeg, with the arguments in ENCODINGS (design/internal/g1-formats.md).

    python -m lmt.eval.datasets fetch ci
    python -m lmt.eval.datasets build-fixture clip [--write]
    python -m lmt.eval.datasets encode-fixture clip [--write]
"""

import argparse
import hashlib
import http.client
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import wave
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np
import soundfile

from lmt.errors import InputError, LmtError, UsageError
from lmt.eval.score import Item, Turn

MANIFEST_DIR = Path(__file__).parent / "manifests"
SCHEMA_VERSION = 1
SAMPLE_RATE = 16000
CHUNK = 1 << 20
TIMEOUT = 60  # seconds without data before a download fails
SAMPLE_MEETING = "AMI-ci"  # lmt demo --sample's meeting (design/internal/g3-stage1.md)
KINDS = ("librispeech", "ami")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UTTERANCE = re.compile(r"^\d+-\d+-\d+$")

# The arguments after `ffmpeg` for each encoding, in the order they're made (design/internal/g1-formats.md,
# "The recipe"). <wav> is the fixture's absolute path and <out> a temporary file beside it. They're never read
# from the manifest, so the tool can run only these.
_AUDIO_IN = ("-nostdin", "-v", "error", "-y", "-i", "<wav>", "-map", "0:a:0", "-map_metadata", "-1",
             "-fflags", "+bitexact", "-flags:a", "+bitexact", "-threads", "1")
_VIDEO_IN = ("-nostdin", "-v", "error", "-y", "-i", "<wav>", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=1",
             "-map", "0:a:0", "-map", "1:v:0", "-shortest", "-map_metadata", "-1", "-fflags", "+bitexact",
             "-flags:a", "+bitexact", "-flags:v", "+bitexact", "-threads", "1")
_AAC = ("-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "1")
_STILL = ("-c:v", "mpeg4", "-pix_fmt", "yuv420p")
ENCODINGS: dict[str, tuple[str, ...]] = {
    "flac": (*_AUDIO_IN, "-c:a", "flac", "-compression_level", "8", "-sample_fmt", "s16", "-ar", "16000", "-ac", "1",
             "-f", "flac", "<out>"),
    "mp3": (*_AUDIO_IN, "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-ac", "1", "-f", "mp3", "<out>"),
    "m4a": (*_AUDIO_IN, *_AAC, "-f", "ipod", "<out>"),
    "mp4": (*_VIDEO_IN, *_AAC, *_STILL, "-f", "mp4", "<out>"),
    "mov": (*_VIDEO_IN, *_AAC, *_STILL, "-f", "mov", "<out>"),
}


@dataclass(frozen=True)
class Member:
    path: str  # inside the archive, and under the source's cache directory
    sha256: str


@dataclass(frozen=True)
class Source:
    id: str
    urls: tuple[str, ...]
    sha256: str
    size: int
    licence: str
    keep: bool
    members: tuple[Member, ...]


@dataclass(frozen=True)
class Piece:
    source: str
    member: str


@dataclass(frozen=True)
class Encoding:
    format: str  # a key of ENCODINGS; the file is the fixture's path with this as its suffix
    sha256: str


@dataclass(frozen=True)
class Fixture:
    name: str
    path: str  # from the repository root
    reference: str
    gap: float  # seconds of zero samples between pieces
    derived_sha256: str
    reference_sha256: str
    concat: tuple[Piece, ...]
    ffmpeg: str | None = None  # the version that made the encodings
    encodings: tuple[Encoding, ...] = ()


@dataclass(frozen=True)
class Dataset:
    name: str
    set: str  # "ci"; 5b adds "full"
    kind: str  # one of KINDS
    source: str | None = None  # librispeech: the archive's source id
    utterances: tuple[str, ...] = ()
    meetings: tuple[str, ...] = ()  # ami: one meeting, and its three sources' ids
    audio: str | None = None
    words: str | None = None
    rttm: str | None = None
    crop: float | None = None  # ami: seconds kept from the start
    derived_sha256: str | None = None  # of the cropped WAV


@dataclass(frozen=True)
class Manifests:
    sources: dict[str, Source]
    fixtures: dict[str, Fixture]
    datasets: dict[str, Dataset]


def data_dir() -> Path:
    """LMT_DATA_DIR, else ~/.cache/lmt/data."""
    value = os.environ.get("LMT_DATA_DIR")
    return Path(value).expanduser() if value else Path.home() / ".cache" / "lmt" / "data"


# --- The manifests ---------------------------------------------------------------------------------------------


def load_manifests(dir: Path = MANIFEST_DIR) -> Manifests:
    """Both files, with their format checked and every id resolved. A bad manifest raises ValueError."""
    sources_file, sets_file = Path(dir) / "sources.toml", Path(dir) / "sets.toml"
    sources = {}
    for i, entry in enumerate(_read(sources_file).get("source", [])):
        where = f"{sources_file}: source {i + 1}"
        source = Source(
            id=_get(entry, "id", str, where),
            urls=tuple(_get(entry, "urls", list, where)),
            sha256=_hex64(_get(entry, "sha256", str, where), where),
            size=_get(entry, "size", int, where),
            licence=_get(entry, "licence", str, where),
            keep=_get(entry, "keep", bool, where),
            members=tuple(_member(m, f"{where}, member {j + 1}") for j, m in enumerate(entry.get("member", []))),
        )
        if source.id in sources:
            raise ValueError(f"{where}: id {source.id!r} is repeated")
        if not source.urls or not all(isinstance(u, str) and u.startswith("https://") for u in source.urls):
            raise ValueError(f"{where}: urls must be one or more https:// URLs")
        if source.size <= 0:
            raise ValueError(f"{where}: size must be positive")
        if not source.members and not source.keep:
            raise ValueError(f"{where}: a source without members is a single file, so keep must be true")
        sources[source.id] = source

    sets = _read(sets_file)
    fixtures = {}
    for i, entry in enumerate(sets.get("fixture", [])):
        where = f"{sets_file}: fixture {i + 1}"
        fixture = Fixture(
            name=_get(entry, "name", str, where),
            path=_get(entry, "path", str, where),
            reference=_get(entry, "reference", str, where),
            gap=float(_get(entry, "gap", (int, float), where)),
            derived_sha256=_hex64(_get(entry, "derived_sha256", str, where), where),
            reference_sha256=_hex64(_get(entry, "reference_sha256", str, where), where),
            concat=tuple(Piece(source=_get(p, "source", str, where), member=_get(p, "member", str, where))
                         for p in _get(entry, "concat", list, where)),
            **_encodings(entry, where),
        )
        if fixture.name in fixtures:
            raise ValueError(f"{where}: name {fixture.name!r} is repeated")
        if fixture.gap < 0 or not fixture.concat:
            raise ValueError(f"{where}: gap must be at least 0, and concat must have a piece")
        for piece in fixture.concat:
            source = sources.get(piece.source)
            if source is None:
                raise ValueError(f"{where}: source {piece.source!r} isn't in {sources_file.name}")
            if piece.member not in {m.path for m in source.members}:
                raise ValueError(f"{where}: {piece.member} isn't a member of {piece.source}")
        fixtures[fixture.name] = fixture

    datasets = {}
    for i, entry in enumerate(sets.get("dataset", [])):
        where = f"{sets_file}: dataset {i + 1}"
        dataset = _dataset(entry, where)
        if dataset.name in datasets:
            raise ValueError(f"{where}: name {dataset.name!r} is repeated")
        _check_dataset(dataset, sources, where)
        datasets[dataset.name] = dataset
    return Manifests(sources=sources, fixtures=fixtures, datasets=datasets)


def _read(path):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: schema_version must be {SCHEMA_VERSION}")
    return data


def _get(entry, key, kind, where):
    value = entry.get(key)
    # bool is an int in Python, so a size or gap can't be `true`.
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
        raise ValueError(f"{where}: {key} is missing or has the wrong type")
    return value


def _hex64(value, where):
    if not _HEX64.match(value):
        raise ValueError(f"{where}: {value!r} isn't a SHA-256 in lower-case hex")
    return value


def _encodings(entry, where):
    """The fixture's `ffmpeg` and `encodings`, which come together or not at all."""
    if ("ffmpeg" in entry) != ("encodings" in entry):
        raise ValueError(f"{where}: ffmpeg and encodings must be given together")
    if "ffmpeg" not in entry:
        return {}
    encodings = []
    for j, e in enumerate(_get(entry, "encodings", list, where)):
        at = f"{where}, encoding {j + 1}"
        encoding = Encoding(format=_get(e, "format", str, at), sha256=_hex64(_get(e, "sha256", str, at), at))
        if encoding.format not in ENCODINGS:
            raise ValueError(f"{at}: format {encoding.format!r} isn't one of {', '.join(ENCODINGS)}")
        if encoding.format in {x.format for x in encodings}:
            raise ValueError(f"{at}: format {encoding.format!r} is repeated")
        encodings.append(encoding)
    return {"ffmpeg": _get(entry, "ffmpeg", str, where), "encodings": tuple(encodings)}


def _member(entry, where):
    path = _get(entry, "path", str, where)
    parts = PurePosixPath(path).parts
    # Members are extracted by these paths, so none may leave the source's cache directory.
    if not parts or path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"{where}: {path!r} isn't a relative path inside the archive")
    return Member(path=path, sha256=_hex64(_get(entry, "sha256", str, where), where))


def _dataset(entry, where):
    common = {"name": _get(entry, "name", str, where), "set": _get(entry, "set", str, where),
              "kind": _get(entry, "kind", str, where)}
    if common["kind"] == "librispeech":
        return Dataset(**common, source=_get(entry, "source", str, where),
                       utterances=_strings(entry, "utterances", where))
    if common["kind"] == "ami":
        crop = entry.get("crop")
        sha = entry.get("derived_sha256")
        return Dataset(**common, meetings=_strings(entry, "meetings", where), audio=_get(entry, "audio", str, where),
                       words=_get(entry, "words", str, where), rttm=_get(entry, "rttm", str, where),
                       crop=None if crop is None else float(_get(entry, "crop", (int, float), where)),
                       derived_sha256=None if sha is None else _hex64(_get(entry, "derived_sha256", str, where),
                                                                     where))
    raise ValueError(f"{where}: kind must be one of {', '.join(KINDS)}")


def _strings(entry, key, where):
    values = _get(entry, key, list, where)
    if not values or not all(isinstance(v, str) for v in values) or len(set(values)) != len(values):
        raise ValueError(f"{where}: {key} must be one or more different strings")
    return tuple(values)


def _check_dataset(dataset, sources, where):
    for source_id in _source_ids(dataset):
        if source_id not in sources:
            raise ValueError(f"{where}: source {source_id!r} isn't in sources.toml")
    if dataset.kind == "librispeech":
        source = sources[dataset.source]
        listed = {m.path for m in source.members}
        for uid in dataset.utterances:
            if not _UTTERANCE.match(uid):
                raise ValueError(f"{where}: {uid!r} isn't a LibriSpeech utterance id")
            flac = _utterance_member(source, uid)
            if flac is None:
                raise ValueError(f"{where}: {uid}.flac isn't a member of {source.id}")
            if str(_trans_member(flac)) not in listed:
                raise ValueError(f"{where}: {_trans_member(flac)} isn't a member of {source.id}")
        return
    (meeting, *more) = dataset.meetings
    if more:
        raise ValueError(f"{where}: an ami dataset has one meeting")
    for source_id in (dataset.audio, dataset.rttm):
        if sources[source_id].members:
            raise ValueError(f"{where}: {source_id} must be a single file, without members")
    if not _words_members(sources[dataset.words], meeting):
        raise ValueError(f"{where}: {dataset.words} has no {meeting}.<speaker>.words.xml member")
    if (dataset.crop is None) != (dataset.derived_sha256 is None):
        raise ValueError(f"{where}: crop and derived_sha256 go together")
    if dataset.crop is not None and dataset.crop <= 0:
        raise ValueError(f"{where}: crop must be positive")


def _source_ids(dataset):
    return (dataset.source,) if dataset.kind == "librispeech" else (dataset.audio, dataset.words, dataset.rttm)


def _utterance_member(source, uid):
    """The member path of the utterance's FLAC: <...>/<speaker>/<chapter>/<id>.flac, or None."""
    speaker, chapter, _ = uid.split("-")
    for member in source.members:
        path = PurePosixPath(member.path)
        if path.name == f"{uid}.flac" and path.parent.parts[-2:] == (speaker, chapter):
            return path
    return None


def _trans_member(flac):
    speaker, chapter, _ = flac.stem.split("-")
    return flac.parent / f"{speaker}-{chapter}.trans.txt"


def _words_members(source, meeting):
    """(speaker letter, member path) of each of the meeting's words.xml members, by letter."""
    pattern = re.compile(rf"{re.escape(meeting)}\.([A-Z])\.words\.xml")
    found = ((pattern.fullmatch(PurePosixPath(m.path).name), m.path) for m in source.members)
    return sorted((match[1], path) for match, path in found if match)


# --- The fetch -------------------------------------------------------------------------------------------------


def fetch_source(source: Source) -> Path:
    """The source's cache directory, with every listed member in it and verified.

    Members already in the cache are kept if their SHA-256 matches. Otherwise the archive is downloaded (or
    taken from the cache when `keep` is true), verified as a whole, the listed members are extracted, each
    checked against its own hash, and the archive is deleted unless `keep` is true. A source without members
    is a single file (`source_file`), downloaded unless it's in the cache and verified.
    """
    root = data_dir() / "sources" / source.id
    archive = source_file(source)
    missing = [m for m in source.members if not _verified(root / m.path, m.sha256)]
    if source.members and not missing:
        return root
    root.mkdir(parents=True, exist_ok=True)
    if not _verified(archive, source.sha256):
        _download(source, archive)
    try:
        if missing:
            _extract(archive, root, missing)
    finally:
        if not source.keep:
            archive.unlink(missing_ok=True)
    return root


def source_file(source: Source) -> Path:
    """Where the source's downloaded file is cached: its directory, under the first URL's file name."""
    return data_dir() / "sources" / source.id / PurePosixPath(source.urls[0]).name


def _verified(path, sha256):
    """True if the file is there and matches. A file that doesn't match is deleted."""
    if not path.is_file():
        return False
    if _sha256_file(path) == sha256:
        return True
    path.unlink()
    return False


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _download(source, archive):
    """Download to a temporary file beside the archive, check its size and SHA-256, and rename it into place."""
    failure = None
    for url in source.urls:
        fd, part = tempfile.mkstemp(dir=archive.parent, prefix=f".{archive.name}.", suffix=".part")
        part = Path(part)
        try:
            digest, size = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                while block := response.read(CHUNK):
                    out.write(block)
                    digest.update(block)
                    size += len(block)
        except (OSError, http.client.HTTPException) as e:  # URLError, HTTPError and timeouts are OSErrors
            part.unlink(missing_ok=True)
            failure = InputError(url, f"download failed: {getattr(e, 'reason', e)}")
            continue
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        if size != source.size:
            part.unlink()
            raise InputError(url, f"size {size} doesn't match the manifest's {source.size}")
        if digest.hexdigest() != source.sha256:
            part.unlink()
            raise InputError(url, "SHA-256 doesn't match the manifest")
        part.replace(archive)
        return
    raise failure


def _extract(archive, root, members):
    """Extract each listed member to its listed path, never to a path the archive gives, and check its hash.
    A .zip is read with zipfile, anything else with tarfile."""
    wanted = {m.path: m for m in members}
    try:
        for name, stream in _entries(archive):
            name = name.removeprefix("./")
            member = wanted.pop(name, None)
            if member is None:
                continue
            if stream is None:
                raise InputError(f"{archive}: {name}", "isn't a regular file in the archive")
            _write_member(stream, root / member.path, member.sha256)
            if not wanted:
                break
    except (tarfile.TarError, zipfile.BadZipFile) as e:
        raise InputError(str(archive), f"can't read the archive: {e}") from e
    if wanted:
        raise InputError(str(archive), f"has no member {min(wanted)}")


def _entries(archive):
    """(name, a stream of its bytes, or None if it isn't a regular file) for each entry, in the archive's order."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if info.is_dir():
                    yield info.filename, None
                    continue
                with z.open(info) as stream:
                    yield info.filename, stream
    else:
        with tarfile.open(archive, "r:*") as tar:
            for info in tar:
                yield info.name, tar.extractfile(info) if info.isfile() else None


def _write_member(stream, dest, sha256):
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, part = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    part = Path(part)
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as out:
            while block := stream.read(CHUNK):
                out.write(block)
                digest.update(block)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != sha256:
        part.unlink()
        raise InputError(str(dest), "SHA-256 doesn't match the manifest")
    part.replace(dest)


# --- The recipe ------------------------------------------------------------------------------------------------


def build_fixture(fixture: Fixture, manifests: Manifests) -> tuple[bytes, str]:
    """The fixture's WAV bytes and reference text, from the members in the cache.

    Each piece is read as 16-bit samples, which decodes FLAC exactly, and must be 16 kHz mono. The pieces are
    joined in order with round(gap × 16000) zero samples between them, and written as one-channel, 16-bit,
    16 kHz WAV by the `wave` module, so the bytes are the same on every platform. The reference is each
    utterance's line in its chapter's .trans.txt, without the id, joined by single spaces, with one final newline.
    """
    gap = np.zeros(round(fixture.gap * SAMPLE_RATE), dtype=np.int16)
    samples, texts = [], []
    for i, piece in enumerate(fixture.concat):
        path = data_dir() / "sources" / manifests.sources[piece.source].id / piece.member
        data, rate = soundfile.read(path, dtype="int16")
        if rate != SAMPLE_RATE or data.ndim != 1:
            channels = 1 if data.ndim == 1 else data.shape[1]
            raise ValueError(f"{path}: {rate} Hz, {channels} channels; the recipe takes 16 kHz mono")
        if i:
            samples.append(gap)
        samples.append(data)
        texts.append(_transcript(path))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.concatenate(samples).astype("<i2").tobytes())
    return buffer.getvalue(), " ".join(texts) + "\n"


def _transcript(flac):
    """The utterance's text: its line in <speaker>-<chapter>.trans.txt beside it, without the id."""
    utterance = flac.stem
    speaker, chapter, _ = utterance.split("-")
    with open(flac.parent / f"{speaker}-{chapter}.trans.txt", encoding="utf-8") as f:
        for line in f:
            uid, _, text = line.rstrip("\n").partition(" ")
            if uid == utterance:
                return text
    raise ValueError(f"{flac.parent}: no transcript line for {utterance}")


# --- The encodings ---------------------------------------------------------------------------------------------


def ffmpeg_version() -> str:
    """The token after "ffmpeg version " on the first line of `ffmpeg -version`."""
    try:
        r = subprocess.run(["ffmpeg", "-version"], check=False, capture_output=True, text=True)
    except FileNotFoundError:
        raise InputError("ffmpeg", "not found on PATH") from None
    first = r.stdout.splitlines()[0] if r.stdout else ""
    if r.returncode != 0 or not first.startswith("ffmpeg version "):
        raise InputError("ffmpeg", "`ffmpeg -version` didn't give its version")
    return first.removeprefix("ffmpeg version ").split()[0]


def encoding_path(fixture: Fixture, fmt: str) -> Path:
    """tests/fixtures/clip.<fmt>: the fixture's path with the format as its suffix, never from the manifest."""
    return Path(fixture.path).with_suffix(f".{fmt}")


def encode(fixture: Fixture, fmt: str, out: Path) -> None:
    """One encoding of the fixture's WAV to `out`, by ENCODINGS[fmt]. A failed ffmpeg raises InputError."""
    wav = str(Path(fixture.path).resolve())
    argv = ["ffmpeg", *(wav if a == "<wav>" else str(out) if a == "<out>" else a for a in ENCODINGS[fmt])]
    try:
        r = subprocess.run(argv, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        raise InputError("ffmpeg", "not found on PATH") from None
    if r.returncode != 0:
        last = (r.stderr.strip().splitlines() or [f"exit status {r.returncode}"])[-1]
        raise InputError(str(encoding_path(fixture, fmt)), f"ffmpeg failed: {last}")


# --- The datasets ----------------------------------------------------------------------------------------------


def fetch(set_name: str) -> list[Item]:
    """Every item of the set's datasets, in sets.toml's order, with its sources fetched and its recipe checked."""
    manifests = load_manifests(MANIFEST_DIR)
    chosen = [d for d in manifests.datasets.values() if d.set == set_name]
    if not chosen:
        names = sorted({d.set for d in manifests.datasets.values()})
        raise UsageError(set_name, f"not a set in sets.toml ({', '.join(names)})")
    for source_id in dict.fromkeys(s for d in chosen for s in _source_ids(d)):
        fetch_source(manifests.sources[source_id])
    items = []
    for dataset in chosen:
        items += _librispeech(dataset, manifests) if dataset.kind == "librispeech" else _ami(dataset, manifests)
    return items


def sample_meeting(*, offline: bool = False) -> Path:
    """AMI-ci's cropped WAV, verified against its derived_sha256. Only its audio source is downloaded.

    Under `offline`, a missing or unverified file is an InputError instead.
    """
    manifests = load_manifests(MANIFEST_DIR)
    dataset = manifests.datasets[SAMPLE_MEETING]
    path = _derived_wav(dataset)
    if _verified(path, dataset.derived_sha256):
        return path
    if offline:
        raise InputError("LMT_DATA_DIR", "the sample meeting isn't downloaded; run once without --offline")
    source = manifests.sources[dataset.audio]
    fetch_source(source)
    return _crop(source_file(source), dataset)


def _librispeech(dataset, manifests):
    """One item per utterance: its FLAC, its duration from the FLAC's header, and its line in .trans.txt."""
    source = manifests.sources[dataset.source]
    items = []
    for uid in dataset.utterances:
        path = data_dir() / "sources" / source.id / _utterance_member(source, uid)
        info = soundfile.info(str(path))
        items.append(Item(dataset.name, uid, path, info.frames / info.samplerate, ref_text=_transcript(path)))
    return items


def _ami(dataset, manifests):
    """The meeting's one item: the audio (cropped when `crop` is set), its words as ref_text and its RTTM turns."""
    (meeting,) = dataset.meetings
    words_root = data_dir() / "sources" / dataset.words
    words = ami_words([(letter, words_root / path) for letter, path in
                       _words_members(manifests.sources[dataset.words], meeting)])
    turns = rttm_turns(source_file(manifests.sources[dataset.rttm]), meeting)
    audio = source_file(manifests.sources[dataset.audio])
    if dataset.crop is not None:
        audio = _crop(audio, dataset)
        words, turns = crop_references(words, turns, dataset.crop)
    with wave.open(str(audio), "rb") as w:
        duration = w.getnframes() / w.getframerate()
    return [Item(dataset.name, meeting, audio, duration, ref_text=" ".join(text for _, _, text in words if text),
                 ref_turns=tuple(turns))]


def ami_words(files: list[tuple[str, Path]]) -> list[tuple[float, float, str]]:
    """(start, end, text) of every <w> in the speakers' words.xml files, given as (speaker letter, path).

    Words with punc="true" are skipped. A word without a starttime takes the previous word's in its file (0 for
    the first), and one without an endtime ends at its start. They're sorted by start time, then speaker letter,
    then position in the file.
    """
    keyed = []
    for letter, path in files:
        start = 0.0
        for position, w in enumerate(e for e in ET.parse(path).iter() if _local(e.tag) == "w"):
            if w.get("punc") == "true":
                continue
            start = float(w.get("starttime", start))
            end = float(w.get("endtime", start))
            keyed.append(((start, letter, position), (start, end, (w.text or "").strip())))
    return [word for _, word in sorted(keyed)]


def _local(tag):
    return tag.rpartition("}")[2]


def rttm_turns(path: Path, meeting: str) -> list[Turn]:
    """The meeting's SPEAKER lines, in the file's order: start, start + duration, and speaker."""
    turns = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            fields = line.split()
            if len(fields) >= 8 and fields[0] == "SPEAKER" and fields[1] == meeting:
                start, duration = float(fields[3]), float(fields[4])
                turns.append(Turn(start, start + duration, fields[7]))
    return turns


def crop_references(words, turns, crop):
    """The words whose midpoint is before `crop`, and the turns clipped to [0, crop], without those starting at
    or after it."""
    kept_words = [w for w in words if (w[0] + w[1]) / 2 < crop]
    kept_turns = [Turn(max(t.start, 0.0), min(t.end, crop), t.speaker) for t in turns if t.start < crop]
    return kept_words, kept_turns


def _derived_wav(dataset):
    return data_dir() / "derived" / dataset.name / f"{dataset.meetings[0]}.wav"


def _crop(audio, dataset):
    """The first crop × 16,000 frames of a 16 kHz, mono, 16-bit WAV, copied with the wave module into
    derived/<dataset>/<meeting>.wav, and checked against derived_sha256. A mismatch writes nothing."""
    path = _derived_wav(dataset)
    if _verified(path, dataset.derived_sha256):
        return path
    frames = round(dataset.crop * SAMPLE_RATE)
    with wave.open(str(audio), "rb") as r:
        if (r.getframerate(), r.getnchannels(), r.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{audio}: {r.getframerate()} Hz, {r.getnchannels()} channels, "
                             f"{8 * r.getsampwidth()}-bit; the crop takes 16 kHz mono 16-bit")
        if r.getnframes() < frames:
            raise ValueError(f"{audio}: {r.getnframes() / SAMPLE_RATE} s, shorter than the crop's {dataset.crop} s")
        data = r.readframes(frames)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(data)
    content = buffer.getvalue()
    if hashlib.sha256(content).hexdigest() != dataset.derived_sha256:
        raise InputError(str(path), "the cropped WAV's SHA-256 doesn't match sets.toml")
    _write_member(io.BytesIO(content), path, dataset.derived_sha256)
    return path


# --- The entry point -------------------------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lmt.eval.datasets",
                                     description="Fetch the evaluation data and build the fixtures.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    f = commands.add_parser("fetch", help="Fetch and verify a set's datasets, and build their recipes.")
    f.add_argument("set", help='The set\'s name in sets.toml ("ci").')
    b = commands.add_parser("build-fixture", help="Fetch a fixture's sources, build it and check its hashes.")
    b.add_argument("name", help="The fixture's name in sets.toml.")
    b.add_argument("--write", action="store_true", help="Write the fixture and its reference to their paths first.")
    e = commands.add_parser("encode-fixture", help="Encode a fixture with ffmpeg and check the files' hashes.")
    e.add_argument("name", help="The fixture's name in sets.toml.")
    e.add_argument("--write", action="store_true", help="Write the encodings beside the fixture first.")
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            return _fetch_command(args.set)
        command = _build_fixture_command if args.command == "build-fixture" else _encode_fixture_command
        return command(args.name, args.write)
    except LmtError as e:
        print(f"lmt: error: {e}", file=sys.stderr)
        return e.exit_code


def _fetch_command(set_name):
    """One line per dataset: its name, its items and their total seconds."""
    items = fetch(set_name)
    for name in dict.fromkeys(i.dataset for i in items):
        mine = [i for i in items if i.dataset == name]
        print(f"{name}: {len(mine)} items, {sum(i.duration for i in mine):.3f} s")
    return 0


def _fixture(name):
    manifests = load_manifests(MANIFEST_DIR)
    fixture = manifests.fixtures.get(name)
    if fixture is None:
        raise UsageError(name, f"not a fixture in sets.toml ({', '.join(sorted(manifests.fixtures))})")
    return manifests, fixture


def _encode_fixture_command(name, write):
    _, fixture = _fixture(name)
    if not fixture.encodings:
        raise UsageError(name, "has no encodings in sets.toml")
    version = ffmpeg_version()
    temps, hashes = {}, {}
    try:
        for fmt in ENCODINGS:
            fd, temp = tempfile.mkstemp(dir=Path(fixture.path).parent, suffix=f".{fmt}.tmp")
            os.close(fd)
            temps[fmt] = Path(temp)
            encode(fixture, fmt, temps[fmt])
            hashes[fmt] = _sha256_file(temps[fmt])
        if write:
            for fmt, temp in temps.items():
                temp.replace(encoding_path(fixture, fmt))
    finally:
        for temp in temps.values():
            temp.unlink(missing_ok=True)
    for fmt, digest in hashes.items():
        print(f"{encoding_path(fixture, fmt)} {digest}")
    print(f"ffmpeg {version}")
    if version != fixture.ffmpeg:
        raise InputError("ffmpeg", f"version {version} here, sets.toml records {fixture.ffmpeg}; "
                                   "the hashes can't be compared")
    expected = {e.format: e.sha256 for e in fixture.encodings}
    for fmt, digest in hashes.items():
        if digest != expected.get(fmt):
            raise InputError(str(encoding_path(fixture, fmt)), "the encoded file's SHA-256 doesn't match sets.toml")
    return 0


def _build_fixture_command(name, write):
    manifests, fixture = _fixture(name)
    for source_id in dict.fromkeys(p.source for p in fixture.concat):
        fetch_source(manifests.sources[source_id])
    audio, text = build_fixture(fixture, manifests)
    reference = text.encode("utf-8")
    if write:
        for path, content in ((fixture.path, audio), (fixture.reference, reference)):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(content)
    for path, content, expected in ((fixture.path, audio, fixture.derived_sha256),
                                    (fixture.reference, reference, fixture.reference_sha256)):
        if hashlib.sha256(content).hexdigest() != expected:
            raise InputError(path, "the rebuilt file's SHA-256 doesn't match sets.toml")
    print(f"{fixture.name}: {fixture.path} and {fixture.reference} match sets.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
