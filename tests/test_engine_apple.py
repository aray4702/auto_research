"""Apple's engine's Python side, on the helper's replies recorded on the Mac (design/internal/g4-stage1.md §3):
G4-2's reasons, G4-7's command line, stdin and install request, the cache directory, and the pieces."""

import hashlib
import json
import stat
from pathlib import Path

import fake_lmt_speech as fake
import numpy as np
import pytest

from lmt.engines import apple
from lmt.errors import ModelError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = fake.FIXTURES
SOURCE = ROOT / "src" / "lmt" / "helper" / "lmt-speech" / "main.swift"
AUDIO = (np.sin(np.arange(16000 * 3) / 7) * 0.1).astype(np.float32)  # 3 s


def recorded(name):
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


@pytest.fixture
def helper(tmp_path, monkeypatch):
    """Points apple.py at the stand-in, replying with `replies`; gives the stand-in's directory."""
    monkeypatch.setenv("LMT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(apple, "macos_version", lambda: "26.6.2")
    directory = tmp_path / "fake"

    def use(**replies):
        binary = fake.install(directory, {verb.replace("_", "-"): path for verb, path in replies.items()})
        monkeypatch.setattr(apple, "build", lambda: binary)
        return directory

    return use


def write(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text("".join(line + "\n" for line in lines))
    return path


@pytest.mark.criterion("G4-2")
class TestWhereItCantRun:
    def test_ready_gives_the_version_record(self, helper):
        helper(status=FIXTURES / "status-ok.jsonl")
        engine = apple.load(offline=True)
        status = recorded("status-ok.jsonl")[0]
        assert engine.info == {"name": "SpeechTranscriber", "engine": "apple", "repository": "com.apple.Speech",
                               "revision": f"macOS {status['os']} ({status['build']}) {status['locale']}"}
        assert (engine.name, engine.word_gap) == ("apple", 0.0)

    @pytest.mark.parametrize("fixture, remedy", [
        ("status-unsupported.jsonl", "use --engine kyutai"),
        ("status-not-installed.jsonl", "run lmt models download --engine apple"),
    ])
    def test_unsupported_and_not_installed_exit_5_naming_the_remedy(self, helper, fixture, remedy):
        helper(status=FIXTURES / fixture)
        with pytest.raises(ModelError) as e:
            apple.load(offline=False)
        assert e.value.exit_code == 5
        assert str(e.value).startswith("--engine apple: ") and str(e.value).endswith(remedy)
        assert "\n" not in str(e.value)

    def test_the_reasons_differ(self, helper):
        reasons = set()
        for fixture in ("status-unsupported.jsonl", "status-not-installed.jsonl"):
            helper(status=FIXTURES / fixture)
            with pytest.raises(ModelError) as e:
                apple.load(offline=False)
            reasons.add(e.value.problem)
        assert len(reasons) == 2

    @pytest.mark.parametrize("version", ["15.6.1", ""])
    def test_older_macos_exits_5_before_any_build(self, monkeypatch, tmp_path, version):
        monkeypatch.setenv("LMT_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(apple, "macos_version", lambda: version)
        monkeypatch.setattr(apple, "XCODE_SELECT", "/nonexistent/xcode-select")
        with pytest.raises(ModelError, match=r"needs macOS 26 or later") as e:
            apple.load(offline=False)
        assert e.value.exit_code == 5
        assert list(tmp_path.iterdir()) == []

    def test_no_command_line_tools_names_xcode_select_and_never_calls_swiftc(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LMT_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setattr(apple, "macos_version", lambda: "26.0")
        monkeypatch.setattr(apple, "XCODE_SELECT", "/usr/bin/false")
        compiler = fake_compiler(tmp_path, "exit 0")
        monkeypatch.setattr(apple, "XCRUN", str(compiler))
        with pytest.raises(ModelError, match=r"run xcode-select --install$") as e:
            apple.load(offline=False)
        assert e.value.exit_code == 5
        assert not (tmp_path / "compiler.log").exists()
        assert not (tmp_path / "cache").exists()

    def test_denied_in_transcribe_exits_5(self, helper, tmp_path):
        helper(status=FIXTURES / "status-ok.jsonl", transcribe=write(tmp_path, "denied.jsonl", [
            (FIXTURES / "status-ok.jsonl").read_text().strip(),
            '{"type": "error", "code": "denied", "message": "turned off"}']))
        with pytest.raises(ModelError, match=r"denied in System Settings") as e:
            apple.load(offline=False).transcribe(AUDIO)
        assert e.value.exit_code == 5

    def test_denied_at_load_exits_5_before_any_audio(self, helper, tmp_path):
        directory = helper(status=write(tmp_path, "denied.jsonl", [
            (FIXTURES / "status-ok.jsonl").read_text().strip(),
            '{"type": "error", "code": "denied", "message": "turned off"}']))
        with pytest.raises(ModelError, match=r"denied in System Settings") as e:
            apple.load(offline=False)
        assert e.value.exit_code == 5
        assert [call["argv"][1] for call in fake.calls(directory)] == ["status"]
        row = apple.status()
        assert (row["ok"], row["status"]) == (False, apple.REASONS["denied"])

    def test_the_helper_s_status_verb_checks_authorisation(self):
        status = SOURCE.read_text().split('case "status":', 1)[1].split("case ", 1)[0]
        assert "checkAuthorized()" in status

    def test_models_list_status_matches(self, helper):
        helper(status=FIXTURES / "status-ok.jsonl")
        assert apple.status()["ok"] is True
        helper(status=FIXTURES / "status-not-installed.jsonl")
        row = apple.status()
        assert row["ok"] is False and row["name"] == "apple"
        assert row["status"] == apple.REASONS["not-installed"]


class TestInternalErrors:
    """`failed`, a line that isn't JSON, or an exit without done: exit 1, with the helper's stderr."""

    @pytest.mark.parametrize("lines, message", [
        (['{"type": "error", "code": "failed", "message": "the analyzer broke"}'], "the analyzer broke"),
        (["not json"], "isn't JSON"),
        ([], "without done"),
    ])
    def test_transcribe(self, helper, tmp_path, lines, message):
        status = (FIXTURES / "status-ok.jsonl").read_text().strip()
        helper(status=FIXTURES / "status-ok.jsonl", transcribe=write(tmp_path, "bad.jsonl", [status, *lines]))
        engine = apple.load(offline=False)
        with pytest.raises(apple.HelperError, match=message) as e:
            engine.transcribe(AUDIO)
        assert not isinstance(e.value, ModelError)
        assert "lmt-speech: a stand-in" in str(e.value)

    def test_status_without_a_status_line(self, helper, tmp_path):
        helper(status=write(tmp_path, "empty.jsonl", []))
        with pytest.raises(apple.HelperError, match="without a status line"):
            apple.load(offline=False)

    @pytest.mark.parametrize("lines, message", [
        ([], "HelperError: lmt-speech status exited 0 without a status line"),
        (["not json"], "HelperError: lmt-speech status: a line that isn't JSON"),
        (['{"type": "error", "code": "failed", "message": "the inventory broke"}'], "the inventory broke"),
    ])
    def test_models_list_reports_an_internal_error_and_never_raises(self, helper, tmp_path, lines, message):
        helper(status=write(tmp_path, "bad.jsonl", lines))
        row = apple.status()
        assert (row["name"], row["ok"], row["os"]) == ("apple", False, "26.6.2")
        assert row["status"].startswith("internal error: ") and message in row["status"]

    def test_models_list_reports_a_helper_that_wont_start(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LMT_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setattr(apple, "macos_version", lambda: "26.6.2")
        monkeypatch.setattr(apple, "build", lambda: tmp_path / "missing" / "lmt-speech")
        row = apple.status()
        assert row["ok"] is False and row["status"].startswith("internal error: FileNotFoundError: ")


@pytest.mark.criterion("G4-7")
class TestBoundaries:
    def test_argv_is_fixed_and_stdin_gets_the_samples(self, helper):
        directory = helper(status=FIXTURES / "status-ok.jsonl", transcribe=FIXTURES / "transcribe-clip.jsonl")
        engine = apple.load(offline=False)
        engine.transcribe(AUDIO)
        stream = engine.stream()
        stream.feed(AUDIO[:16000])
        stream.feed(AUDIO[16000:])
        stream.finish()
        stream.close()
        made = fake.calls(directory)
        binary = str(directory / "lmt-speech")
        assert [c["argv"] for c in made] == [[binary, "status", "--locale", "en-US"],
                                             [binary, "transcribe", "--locale", "en-US"],
                                             [binary, "transcribe", "--locale", "en-US"]]
        expected = AUDIO.astype("<f4").tobytes()
        assert Path(made[0]["stdin"]).read_bytes() == b""
        assert Path(made[1]["stdin"]).read_bytes() == expected
        assert Path(made[2]["stdin"]).read_bytes() == expected

    def test_only_download_sends_install(self, helper):
        directory = helper(status=FIXTURES / "status-ok.jsonl", install=FIXTURES / "status-ok.jsonl",
                           transcribe=FIXTURES / "transcribe-clip.jsonl")
        apple.status()
        apple.load(offline=False).transcribe(AUDIO)
        assert "install" not in {c["argv"][1] for c in fake.calls(directory)}
        apple.download()
        assert fake.calls(directory)[-1]["argv"][1:] == ["install", "--locale", "en-US"]

    def test_download_reports_assets_still_missing(self, helper):
        helper(install=FIXTURES / "status-not-installed.jsonl")
        with pytest.raises(ModelError, match="lmt models download --engine apple"):
            apple.download()

    def test_the_binary_is_under_the_source_s_sha256(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LMT_CACHE_DIR", str(tmp_path))
        digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        assert apple.binary_path() == tmp_path / "helper" / digest / "lmt-speech"
        assert apple.binary_path(b"changed") != apple.binary_path()

    def test_the_source_is_package_data(self):
        assert apple.source() == SOURCE.read_bytes()


def fake_compiler(tmp_path, body) -> Path:
    """A stand-in for xcrun: logs its argv and environment, then runs `body` (sh)."""
    path = tmp_path / "xcrun"
    log = tmp_path / "compiler.log"
    path.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\necho "TMPDIR=$TMPDIR CLANG=$CLANG_MODULE_CACHE_PATH" '
                    f'>> "{log}"\n{body}\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.mark.criterion("G4-7")
class TestBuild:
    @pytest.fixture
    def build_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LMT_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setattr(apple, "macos_version", lambda: "26.0")
        monkeypatch.setattr(apple, "XCODE_SELECT", "/usr/bin/true")

    def test_builds_into_the_cache_through_a_build_directory(self, build_env, monkeypatch, tmp_path):
        # The stand-in writes its -o argument (the 6th) as the binary.
        monkeypatch.setattr(apple, "XCRUN", str(fake_compiler(tmp_path, 'printf built > "$6"')))
        binary = apple.build()
        assert binary == apple.binary_path() and binary.read_text() == "built"
        argv, env = (tmp_path / "compiler.log").read_text().splitlines()
        work = binary.parent.parent / argv.split()[5].split("/")[-2]
        assert work.name.startswith(f"{binary.parent.name}.build-")
        assert argv.split()[:4] == ["swiftc", "-O", "-module-cache-path", str(work / "module-cache")]
        assert env == f"TMPDIR={work / 'tmp'} CLANG={work / 'module-cache'}"
        assert sorted(p.name for p in (tmp_path / "cache" / "helper").iterdir()) == [binary.parent.name]

        (tmp_path / "compiler.log").unlink()
        assert apple.build() == binary
        assert not (tmp_path / "compiler.log").exists()  # built once

    def test_a_failed_build_names_the_compiler_s_last_line_and_leaves_nothing(self, build_env, monkeypatch,
                                                                              tmp_path):
        monkeypatch.setattr(apple, "XCRUN", str(fake_compiler(
            tmp_path, 'echo "main.swift:1: warning: w" >&2\necho "main.swift:2: error: broken" >&2\nexit 1')))
        with pytest.raises(ModelError, match=r"didn't build: main.swift:2: error: broken$") as e:
            apple.build()
        assert e.value.exit_code == 5
        assert list((tmp_path / "cache" / "helper").iterdir()) == []


class TestPieces:
    """The pieces from the transcript recorded on the Mac, in batch and live mode."""

    def finals(self):
        return [w for line in recorded("transcribe-clip.jsonl") if line["type"] == "final" for w in line["words"]]

    def test_transcribe_gives_one_word_per_final_word(self, helper):
        helper(status=FIXTURES / "status-ok.jsonl", transcribe=FIXTURES / "transcribe-clip.jsonl")
        engine = apple.load(offline=False)
        words = engine.transcribe(AUDIO)
        assert [(w.text, w.start, w.end, w.probability) for w in words] == \
               [(w["text"], w["start"], w["end"], w["confidence"]) for w in self.finals()]
        assert words[0].text == "He" and len(words) > 100
        assert engine.peak_rss_bytes > 0

    def test_stream_gives_every_final_word_once_and_ignores_volatile_text(self, helper):
        helper(status=FIXTURES / "status-ok.jsonl", transcribe=FIXTURES / "transcribe-clip.jsonl")
        stream = apple.load(offline=False).stream()
        pieces = stream.feed(AUDIO)
        pieces += stream.finish()
        stream.close()
        stream.close()  # safe twice
        finals = self.finals()
        assert [p.text for p in pieces] == [" " + w["text"] for w in finals]
        assert all(p.starts_word for p in pieces)
        assert stream.done_until == max(line["end"] for line in recorded("transcribe-clip.jsonl")
                                        if line["type"] == "final")
        assert stream.volatile is None  # the recording ends on a final result
