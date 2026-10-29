"""The evaluation data: the manifests, the cache and its fetch, and the fixture recipe (design/internal/g1-slice.md, S1).

Every file that's downloaded is listed in `manifests/sources.toml`, with its SHA-256, and only the archive members
listed there are extracted. `manifests/sets.toml` says what's built from them. The cache is
`<LMT_DATA_DIR>/sources/<source id>/<member path>`. Nothing here imports the eval extra. The fixture's encodings
are made by ffmpeg, with the arguments in ENCODINGS (design/internal/g1-formats.md).

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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np
import soundfile

from lmt.errors import InputError, LmtError, UsageError

MANIFEST_DIR = Path(__file__).parent / "manifests"
SCHEMA_VERSION = 1
SAMPLE_RATE = 16000
CHUNK = 1 << 20
TIMEOUT = 60  # seconds without data before a download fails

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

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
class Manifests:
    sources: dict[str, Source]
    fixtures: dict[str, Fixture]


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
        sources[source.id] = source

    fixtures = {}
    for i, entry in enumerate(_read(sets_file).get("fixture", [])):
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
    return Manifests(sources=sources, fixtures=fixtures)


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


# --- The fetch -------------------------------------------------------------------------------------------------


def fetch_source(source: Source) -> Path:
    """The source's cache directory, with every listed member in it and verified.

    Members already in the cache are kept if their SHA-256 matches. Otherwise the archive is downloaded (or
    taken from the cache when `keep` is true), verified as a whole, the listed members are extracted, each
    checked against its own hash, and the archive is deleted unless `keep` is true.
    """
    root = data_dir() / "sources" / source.id
    archive = root / PurePosixPath(source.urls[0]).name
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
    """Extract each listed member to its listed path, never to a path the archive gives, and check its hash."""
    wanted = {m.path: m for m in members}
    try:
        with tarfile.open(archive, "r:*") as tar:
            for info in tar:
                name = info.name.removeprefix("./")
                member = wanted.pop(name, None)
                if member is None:
                    continue
                if not info.isfile():
                    raise InputError(f"{archive}: {name}", "isn't a regular file in the archive")
                _write_member(tar.extractfile(info), root / member.path, member.sha256)
                if not wanted:
                    break
    except tarfile.TarError as e:
        raise InputError(str(archive), f"can't read the archive: {e}") from e
    if wanted:
        raise InputError(str(archive), f"has no member {min(wanted)}")


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


# --- The entry point -------------------------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lmt.eval.datasets",
                                     description="Fetch the evaluation data and build the fixtures.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    b = commands.add_parser("build-fixture", help="Fetch a fixture's sources, build it and check its hashes.")
    b.add_argument("name", help="The fixture's name in sets.toml.")
    b.add_argument("--write", action="store_true", help="Write the fixture and its reference to their paths first.")
    e = commands.add_parser("encode-fixture", help="Encode a fixture with ffmpeg and check the files' hashes.")
    e.add_argument("name", help="The fixture's name in sets.toml.")
    e.add_argument("--write", action="store_true", help="Write the encodings beside the fixture first.")
    args = parser.parse_args(argv)
    command = _build_fixture_command if args.command == "build-fixture" else _encode_fixture_command
    try:
        return command(args.name, args.write)
    except LmtError as e:
        print(f"lmt: error: {e}", file=sys.stderr)
        return e.exit_code


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
