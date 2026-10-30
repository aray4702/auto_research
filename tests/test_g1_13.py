"""G1-13 (spec/acceptance.md): the product run on the two-speaker clip, without the network and with a fresh home,
temporary and cache directory, opens no connection, writes only its four outputs, and gives the same bytes as the
run with the network up (design/internal/g1-stage2.md, P7c)."""

import os
from pathlib import Path

import pytest
from conftest import TWO_SPEAKERS
from isolation import run_isolated, snapshot
from test_g1_1 import report

NETGUARD = Path(__file__).resolve().parent / "netguard"
OUTPUTS = ("two_speakers.txt", "two_speakers.srt", "two_speakers.vtt", "two_speakers.json")  # the default --formats


@pytest.mark.models
@pytest.mark.criterion("G1-13")
def test_g1_13_the_offline_run_opens_no_connection_writes_only_its_outputs_and_matches(tmp_path, two_speakers_run):
    from lmt import models

    home, tmp, xdg, work = (tmp_path / name for name in ("home", "tmp", "xdg", "work"))
    for directory in (home, tmp, xdg, work):
        directory.mkdir()
    net_log = tmp_path / "net.log"  # beside the watched directories, outside every one
    cache = models.cache_dir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "XDG_CACHE_HOME": str(xdg),
        "LMT_CACHE_DIR": str(cache),
        "PYTHONPATH": str(NETGUARD),
        "LMT_NETGUARD_LOG": str(net_log),
        "LANG": "C.UTF-8",
    }

    before = snapshot(home, tmp, xdg, work, cache)
    r = run_isolated(["transcribe", str(TWO_SPEAKERS)], env=env, cwd=work)
    after = snapshot(home, tmp, xdg, work, cache)

    connections = net_log.read_text(encoding="utf-8").splitlines() if net_log.exists() else []
    new = sorted(set(after) - set(before))
    gone = sorted(set(before) - set(after))
    changed = sorted(path for path in set(before) & set(after) if before[path] != after[path])
    report(f"G1-13: the product run on tests/fixtures/two_speakers.wav: exit {r.returncode}, isolated {r.isolated}")
    report(f"G1-13: the guard's log: {len(connections)} connections {connections}")
    report(f"G1-13: {len(before)} entries watched (cache {cache}); new {new}, changed {changed}, gone {gone}")
    same = {name: (work / name).is_file() and (two_speakers_run.out_dir / name).is_file()
            and (work / name).read_bytes() == (two_speakers_run.out_dir / name).read_bytes() for name in OUTPUTS}
    report(f"G1-13: byte-identical to the run with the network up (exit {two_speakers_run.returncode}): {same}")

    assert r.returncode == 0, r.stderr
    if os.environ.get("CI"):
        assert r.isolated, "under CI, the run must be isolated (sudo -n unshare --net)"
    assert not connections, f"the run opened connections: {connections}"
    assert not changed and not gone, f"changed {changed}, gone {gone}"
    assert new == sorted(str(work / name) for name in OUTPUTS), f"new entries: {new}"

    assert two_speakers_run.returncode == 0, two_speakers_run.stderr
    assert all(same.values()), f"outputs that differ from two_speakers_run's: {same}"
