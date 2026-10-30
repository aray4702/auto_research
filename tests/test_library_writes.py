"""G1-13: the libraries a product run loads leave no file outside its outputs (design/internal/g1-stage2.md, P13),
with no model. Each case is a fresh process with fresh HOME, TMPDIR and XDG_CACHE_HOME, and an empty working
directory; nothing else is set, so neither MPLCONFIGDIR nor ORT_DISABLE_TELEMETRY comes from outside."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from isolation import snapshot

pytestmark = pytest.mark.criterion("G1-13")

# The product's paths for both libraries: diarize.load's matplotlib (through pyannote.audio's tasks) and P11's
# VAD path, which starts onnxruntime's environment and runs a session.
BOTH = """
import json
import numpy
from lmt import asr, diarize
diarize.scratch_matplotlib()
import pyannote.audio.pipelines
import matplotlib
print(json.dumps(str(matplotlib.get_cachedir())))
asr.quiet_onnxruntime()
from faster_whisper.vad import get_speech_timestamps
get_speech_timestamps(numpy.zeros(16_000, numpy.float32))
"""
SCRATCH = """
import json, os
from lmt import diarize
a, b = diarize.scratch_matplotlib(), diarize.scratch_matplotlib()
print(json.dumps([str(a), str(b), os.environ.get("MPLCONFIGDIR"), a.is_dir()]))
"""
SWITCH = """
import json, os
import lmt.asr
print(json.dumps(os.environ.get("ORT_DISABLE_TELEMETRY")))
"""


def run(code, tmp_path):
    """The child's exit and its last stdout line as JSON, and the four directories it was given."""
    dirs = home, tmp, xdg, work = [tmp_path / name for name in ("home", "tmp", "xdg", "work")]
    for directory in dirs:
        directory.mkdir()
    env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "HOME": str(home), "TMPDIR": str(tmp),
           "XDG_CACHE_HOME": str(xdg)}
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=work, capture_output=True, text=True,
                       check=False, timeout=600)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.splitlines()[-1]), dirs


def test_neither_library_leaves_a_file(tmp_path):
    cachedir, dirs = run(BOTH, tmp_path)
    left = snapshot(*dirs)
    assert left == {}, f"left after the run: {sorted(left)}"
    assert Path(cachedir).resolve().is_relative_to(dirs[1].resolve()), f"matplotlib's directory: {cachedir}"


def test_the_scratch_directory_is_the_processs_own_and_goes_at_exit(tmp_path):
    (a, b, variable, is_dir), (_home, tmp, _xdg, _work) = run(SCRATCH, tmp_path)
    assert a == b == variable
    assert is_dir
    assert Path(a).parent == tmp
    assert not Path(a).exists()
    assert os.listdir(tmp) == []


def test_onnxruntimes_telemetry_switch_is_set(tmp_path):
    value, _dirs = run(SWITCH, tmp_path)
    assert value == "1"
