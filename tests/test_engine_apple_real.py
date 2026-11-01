"""The helper for real, where it can build: macOS 26 or later with the Command Line Tools (CI's `macos-26`, and
the Mac). Its first-use build writes nothing outside LMT_CACHE_DIR (G4-7, Q8), and it answers `status`; on CI's
runner the recognizer is unsupported (A5), which is G4-2's real case."""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest
from isolation import snapshot

from lmt.engines import apple

ROOT = Path(__file__).resolve().parents[1]
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"
MODULE_CACHES = ("org.llvm.clang", "clang")  # the compiler's default module caches, by prefix


def can_build() -> bool:
    version = platform.mac_ver()[0]
    if not version or int(version.split(".")[0]) < 26:
        return False
    return subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, check=False).returncode == 0


pytestmark = pytest.mark.skipif(not can_build(), reason="needs macOS 26 or later with the Command Line Tools")


def darwin_cache_listing() -> dict[str, tuple]:
    """path -> (size, mtime) under the default module caches in `getconf DARWIN_USER_CACHE_DIR`, without hashing
    them: they can be large, and any new or changed entry is what counts."""
    top = Path(subprocess.run(["getconf", "DARWIN_USER_CACHE_DIR"], capture_output=True, text=True,
                              check=True).stdout.strip())
    listing = {}
    for entry in top.iterdir():
        if entry.name.startswith(MODULE_CACHES):
            listing[str(entry)] = ("top",)
            for root, dirnames, filenames in os.walk(entry):
                for name in dirnames + filenames:
                    path = os.path.join(root, name)
                    st = os.lstat(path)
                    listing[path] = (st.st_size, st.st_mtime_ns)
    return listing


@pytest.fixture(scope="module")
def first_use(tmp_path_factory):
    """`apple.build()` in a process of its own, with fresh HOME, TMPDIR, XDG_CACHE_HOME, working directory and
    LMT_CACHE_DIR, as G1-13's file check runs; then the built helper's `status`."""
    top = tmp_path_factory.mktemp("apple_build")
    home, tmp, xdg, work, cache = (top / name for name in ("home", "tmp", "xdg", "work", "cache"))
    for directory in (home, tmp, xdg, work, cache):
        directory.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(tmp), "XDG_CACHE_HOME": str(xdg),
           "LMT_CACHE_DIR": str(cache), "LANG": "C.UTF-8"}
    code = "from lmt.engines import apple; print(apple.build()); print(apple.binary_path())"
    before, darwin_before = snapshot(home, tmp, xdg, work, cache), darwin_cache_listing()
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=work, capture_output=True, text=True,
                       timeout=600, check=False)
    after, darwin_after = snapshot(home, tmp, xdg, work, cache), darwin_cache_listing()
    assert r.returncode == 0, r.stderr
    binary, expected = (Path(line) for line in r.stdout.splitlines())
    status = subprocess.run([str(binary), "status", "--locale", "en-US"], capture_output=True, text=True,
                            timeout=120, check=False)
    return {"binary": binary, "expected": expected, "cache": cache, "env": env,
            "new": sorted(set(after) - set(before)),
            "changed": sorted(p for p in set(before) & set(after) if before[p] != after[p]),
            "gone": sorted(set(before) - set(after)),
            "darwin_changed": sorted(p for p in set(darwin_before) | set(darwin_after)
                                     if darwin_before.get(p) != darwin_after.get(p)),
            "status": status}


@pytest.mark.criterion("G4-7")
def test_the_first_use_build_writes_only_the_binary_into_lmt_cache_dir(first_use):
    binary = first_use["binary"]
    assert binary == first_use["expected"]
    helper = first_use["cache"] / "helper"
    assert first_use["new"] == sorted(str(p) for p in (helper, binary.parent, binary))
    assert first_use["changed"] == [] and first_use["gone"] == []
    assert first_use["darwin_changed"] == []


@pytest.mark.criterion("G4-7")
def test_the_built_helper_answers_status(first_use):
    r = first_use["status"]
    assert r.returncode == 0, r.stderr
    (line,) = [json.loads(text) for text in r.stdout.splitlines()]
    assert line["type"] == "status" and line["locale"].startswith("en")
    assert isinstance(line["supported"], bool) and isinstance(line["installed"], bool)
    assert line["os"] and line["build"]


def test_where_en_us_is_installed_the_python_side_transcribes_the_clip(first_use, monkeypatch):
    (line,) = [json.loads(text) for text in first_use["status"].stdout.splitlines()]
    if not line["installed"]:
        pytest.skip("Apple's en-US assets aren't installed here")
    from lmt import audio

    monkeypatch.setenv("LMT_CACHE_DIR", str(first_use["cache"]))
    engine = apple.load(offline=True)
    words = engine.transcribe(audio.decode(ROOT / "tests" / "fixtures" / "clip.wav"))
    assert len(words) > 100 and words[0].text == "He"
    assert all(0 <= w.start <= w.end <= 57 for w in words)
    assert engine.peak_rss_bytes > 0


@pytest.mark.criterion("G4-2")
@pytest.mark.skipif(not ON_CI, reason="CI's macos-26 runner, where the recognizer is unsupported (A5)")
def test_on_ci_the_recognizer_is_unsupported_and_load_exits_5(first_use):
    (line,) = [json.loads(text) for text in first_use["status"].stdout.splitlines()]
    assert line["supported"] is False
    code = ("import sys\nfrom lmt.engines import apple\nfrom lmt.errors import ModelError\n"
            "try:\n    apple.load(offline=True)\nexcept ModelError as e:\n    print(e); sys.exit(e.exit_code)\n")
    r = subprocess.run([sys.executable, "-c", code], env=first_use["env"], capture_output=True, text=True,
                       timeout=120, check=False)
    assert (r.returncode, r.stdout.strip()) == (5, f"--engine apple: {apple.REASONS['unsupported']}")
