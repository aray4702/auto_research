"""G1-11's bad inputs (spec/acceptance.md; design/internal/g1-stage2.md, P4): the reference clip's mutations,
the end-to-end set, and `lmt` run with a time limit. G2's tests call these too (g2-stage1.md)."""

import subprocess
import sys
import wave
import zipfile
from pathlib import Path

import numpy

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

SOURCES = ("wav", "flac", "mp3", "m4a", "mp4", "mov")  # tests/fixtures/clip.<suffix>
OPS = ("flip", "truncate", "splice")
E2E_MUTATIONS = (0, 7, 14, 21, 28)  # a flipped WAV, a truncated FLAC, a spliced MP3, a flipped M4A, a truncated MP4

RATE = 16_000
# The end-to-end inputs that decode refuses, so each exits 3 with no model.
DECODE_CASES = ("empty.wav", "zero.wav", "truncated.wav", "random.wav", "random.mp3", "random.m4a", "text.mp4",
                "video-only.mp4", "notes.txt", "notes.docx")
SPEECHLESS = ("silence.wav", "noise.wav")  # each decodes, and exits 4


def mutation_name(i: int) -> str:
    return f"mutation-{i}.{SOURCES[i % len(SOURCES)]}"


MUTATION_CASES = tuple(mutation_name(i) for i in E2E_MUTATIONS)


def run_bounded(argv, *, cwd, timeout=120) -> subprocess.CompletedProcess:
    """`python -m lmt <argv>`, as conftest.run_lmt, within `timeout` seconds. TimeoutExpired propagates."""
    return subprocess.run([sys.executable, "-m", "lmt", *argv], cwd=cwd, capture_output=True, text=True,
                          check=False, timeout=timeout)


def mutation(i: int) -> tuple[str, bytes]:
    """Mutation `i` of the clip's encodings: (suffix, bytes), from default_rng(i)."""
    suffix = SOURCES[i % len(SOURCES)]
    op = OPS[(i // len(SOURCES)) % len(OPS)]
    data = bytearray((FIXTURES / f"clip.{suffix}").read_bytes())
    rng = numpy.random.default_rng(i)
    if op == "flip":
        n = int(rng.integers(1, 33))
        for position in rng.integers(0, len(data), n):
            data[position] ^= int(rng.integers(1, 256))
    elif op == "truncate":
        data = data[:int(rng.integers(0, len(data)))]
    else:  # splice
        n = int(rng.integers(1, 4097))
        p, q = (int(x) for x in rng.integers(0, len(data) - n, 2))
        data[p:p + n] = data[q:q + n]
    return suffix, bytes(data)


def _write_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(numpy.asarray(samples, dtype="<i2").tobytes())


def _video_only(path):
    import av

    with av.open(str(path), "w") as out:
        stream = out.add_stream("mpeg4", rate=25)
        stream.width = stream.height = 64
        stream.pix_fmt = "yuv420p"
        for _ in range(3):
            frame = av.VideoFrame.from_ndarray(numpy.zeros((64, 64, 3), numpy.uint8), format="rgb24")
            out.mux(stream.encode(frame))
        out.mux(stream.encode())


def _docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Override PartName="/word/document.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   '<w:body><w:p><w:r><w:t>Meeting notes.</w:t></w:r></w:p></w:body></w:document>')


def _noise():
    """60 s of white noise at an RMS of -60 dBFS, as 16-bit samples."""
    x = numpy.random.default_rng(4).standard_normal(60 * RATE)
    x *= 10 ** (-60 / 20) / numpy.sqrt(numpy.mean(x ** 2))
    return numpy.clip(numpy.round(x * 32767), -32768, 32767).astype(numpy.int16)


def build(name: str, directory: Path) -> Path:
    """One end-to-end input, by its name in DECODE_CASES, SPEECHLESS or MUTATION_CASES, written to `directory`."""
    path = Path(directory) / name
    if name == "empty.wav":
        path.write_bytes(b"")
    elif name == "zero.wav":
        _write_wav(path, [])
    elif name == "truncated.wav":  # its header declares the clip's 56.565 s; it holds 1 s
        path.write_bytes((FIXTURES / "clip.wav").read_bytes()[:44 + 32_000])
    elif name in ("random.wav", "random.mp3", "random.m4a"):
        seed = ("random.wav", "random.mp3", "random.m4a").index(name) + 1
        path.write_bytes(numpy.random.default_rng(seed).bytes(65_536))
    elif name == "text.mp4":
        path.write_text("This is a line of English text, not a video.\n", encoding="utf-8")
    elif name == "video-only.mp4":
        _video_only(path)
    elif name == "notes.txt":
        path.write_text("Meeting notes: the budget is agreed.\n", encoding="utf-8")
    elif name == "notes.docx":
        _docx(path)
    elif name == "silence.wav":
        _write_wav(path, numpy.zeros(60 * RATE, numpy.int16))
    elif name == "noise.wav":
        _write_wav(path, _noise())
    elif name in MUTATION_CASES:
        i = E2E_MUTATIONS[MUTATION_CASES.index(name)]
        path.write_bytes(mutation(i)[1])
    else:
        raise ValueError(f"no bad input named {name!r}")
    return path
