"""`lmt rename` through the command, on the transcript fixture: G3-1, G3-3 and G1-6 (design/internal/g3-stage1.md, 1b).

Each test works on a copy of the fixture named `two_speakers.json`, so the outputs are two_speakers.{txt,srt,vtt,json}.
No model. 1c imports the rename names from here.
"""

import hashlib
import json
import os
import shutil
import unicodedata
from pathlib import Path

import pytest

from lmt import cli, export
from lmt.rename import relabel
from lmt.transcript import load

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "two_speakers.transcript.json"
FORMATS = ("txt", "srt", "vtt", "json")
# The rename names (spec, G3 "Terms"), with precomposed letters.
NAME1 = "José & Ana"
NAME2 = "Bartholomew J. Fernández-Rodrigo"


@pytest.fixture
def work(tmp_path):
    path = tmp_path / "two_speakers.json"
    shutil.copyfile(FIXTURE, path)
    return path


def outputs(directory, formats=FORMATS):
    return [Path(directory) / f"two_speakers.{fmt}" for fmt in formats]


def rename(capsys, *argv):
    code = cli.main(["rename", *map(str, argv)])
    out, err = capsys.readouterr()
    return code, out, err


def digests(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(directory).iterdir())
            if p.is_file()}


def rendered(transcript, fmt):
    return export.render(transcript, fmt).encode("utf-8")


def test_the_rename_names():
    assert NAME1 == unicodedata.normalize("NFC", NAME1)
    assert NAME2 == unicodedata.normalize("NFC", NAME2)
    assert len(NAME2) == 32 and " " in NAME2


# G3-1

@pytest.mark.criterion("G3-1")
def test_rename_writes_every_format_with_the_names(work, capsys):
    code, out, err = rename(capsys, work, f"S1={NAME1}", f"S2={NAME2}")
    assert (code, err) == (0, "")
    assert out.splitlines() == [str(p) for p in outputs(work.parent)]
    expected = relabel(load(FIXTURE), {"S1": NAME1, "S2": NAME2})
    for path, fmt in zip(outputs(work.parent), FORMATS):
        assert path.read_bytes() == rendered(expected, fmt), fmt


@pytest.mark.criterion("G3-1")
def test_each_format_shows_each_name_where_it_shows_a_label(work, capsys):
    assert rename(capsys, work, f"S1={NAME1}", f"S2={NAME2}")[0] == 0
    txt, srt, vtt, doc = (p.read_text(encoding="utf-8") for p in outputs(work.parent))
    for name in (NAME1, NAME2):
        assert f"] {name}: " in txt
        assert f"\n{name}: " in srt
        escaped = name.replace("&", "&amp;")
        assert f"<v {escaped}>{escaped}: " in vtt
    assert "<v José &amp; Ana>José &amp; Ana: " in vtt
    assert "Speaker 1" not in txt + srt + vtt and "Speaker 2" not in txt + srt + vtt
    assert json.loads(doc)["speakers"] == [{"id": "S1", "label": NAME1}, {"id": "S2", "label": NAME2}]


@pytest.mark.criterion("G3-1")
def test_the_json_changes_only_in_the_labels(work, capsys):
    assert rename(capsys, work, f"S1={NAME1}", f"S2={NAME2}")[0] == 0
    before = json.loads(FIXTURE.read_text(encoding="utf-8"))
    after = json.loads(work.read_text(encoding="utf-8"))
    assert [s["id"] for s in after["speakers"]] == [s["id"] for s in before["speakers"]]
    del before["speakers"], after["speakers"]
    assert after == before


@pytest.mark.criterion("G3-1")
def test_renaming_back_gives_the_fixtures_own_files(work, capsys):
    assert rename(capsys, work, f"S1={NAME1}", f"S2={NAME2}")[0] == 0
    code, _, err = rename(capsys, work, f"{NAME1}=Speaker 1", "S2=Speaker 2")  # one key by label, one by id
    assert (code, err) == (0, "")
    fixture = load(FIXTURE)
    for path, fmt in zip(outputs(work.parent), FORMATS):
        assert path.read_bytes() == rendered(fixture, fmt), fmt
    assert work.read_bytes() == FIXTURE.read_bytes()


@pytest.mark.criterion("G3-1")
def test_the_name_is_trimmed(tmp_path, capsys):
    results = []
    for name in (f" {NAME1} ", NAME1):
        d = tmp_path / str(len(results))
        d.mkdir()
        shutil.copyfile(FIXTURE, d / "two_speakers.json")
        assert rename(capsys, d / "two_speakers.json", f"S1={name}")[0] == 0
        results.append([p.read_bytes() for p in outputs(d)])
    assert results[0] == results[1]


@pytest.mark.criterion("G3-1")
def test_the_same_name_twice(work, capsys):
    code, _, err = rename(capsys, work, "S1=Alex", "S2=Alex")
    assert (code, err) == (0, "")
    assert json.loads(work.read_text(encoding="utf-8"))["speakers"] == [
        {"id": "S1", "label": "Alex"}, {"id": "S2", "label": "Alex"}]
    txt = outputs(work.parent, ("txt",))[0].read_text(encoding="utf-8")
    assert "] Alex: " in txt and "Speaker" not in txt


@pytest.mark.criterion("G3-1")
def test_no_pairs_prints_the_table_and_writes_nothing(work, capsys):
    before = digests(work.parent)
    code, out, err = rename(capsys, work)
    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[0] == "ID\tLABEL\tSEGMENTS\tSECONDS\tFIRST WORDS"
    assert [line.split("\t")[:2] for line in lines[1:]] == [["S1", "Speaker 1"], ["S2", "Speaker 2"]]
    assert digests(work.parent) == before


# G3-3: each bad input exits with its code and one line, and changes no file.

def assert_refused(capsys, work, argv, code, subject):
    before = digests(work.parent)
    got, out, err = rename(capsys, *argv)
    assert got == code, err
    assert out == ""
    assert err.endswith("\n") and "\n" not in err[:-1], err
    assert err.startswith(f"lmt: error: {subject}: "), err
    assert digests(work.parent) == before


USAGE_CASES = {
    "unknown key": (["S9=Bob"], "S9"),
    "no equals sign": (["Bob"], "Bob"),
    "empty name": (["S1="], "S1"),
    "blank name": (["S1=   "], "S1"),
    "33 code points": (["S1=" + "a" * 33], "S1"),
    "forbidden character": (["S1=a<b"], "S1"),
    "newline": (["S1=a\nb"], "S1"),
    "same speaker twice": (["S1=Ana", "Speaker 1=Bob"], "Speaker 1"),
}


@pytest.mark.criterion("G3-3")
@pytest.mark.parametrize("case", USAGE_CASES)
def test_usage_error(work, capsys, case):
    pairs, subject = USAGE_CASES[case]
    assert_refused(capsys, work, [work, *pairs], 2, subject)


@pytest.mark.criterion("G3-3")
def test_ambiguous_label(work, capsys):
    assert rename(capsys, work, "S1=Alex", "S2=Alex")[0] == 0
    assert_refused(capsys, work, [work, "Alex=Bob"], 2, "Alex")
    assert rename(capsys, work, "Alex=Bob")[2] == "lmt: error: Alex: is the label of S1 and S2; use an id\n"


@pytest.mark.criterion("G3-3")
def test_missing_transcript(work, capsys):
    missing = work.parent / "missing.json"
    assert_refused(capsys, work, [missing, "S1=Ana"], 3, missing)


def broken(work, edit):
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    edit(doc)
    work.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.criterion("G3-3")
@pytest.mark.parametrize("case", ["not JSON", "segment without start", "schema version 2.0"])
def test_bad_transcript(work, capsys, case):
    if case == "not JSON":
        work.write_text("{", encoding="utf-8")
    elif case == "segment without start":
        broken(work, lambda doc: doc["segments"][0].pop("start"))
    else:
        broken(work, lambda doc: doc.update(schema_version="2.0"))
    assert_refused(capsys, work, [work, "S1=Ana"], 3, work)


@pytest.mark.criterion("G3-3")
def test_read_only_output_directory(work, capsys):
    if os.geteuid() == 0:
        pytest.skip("running as root, who can write anywhere, so no directory is unwritable")
    locked = work.parent / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert_refused(capsys, work, [work, "S1=Ana", "-o", locked], 3, locked)
        assert list(locked.iterdir()) == []
    finally:
        locked.chmod(0o700)


# G1-6: one test per option.

@pytest.mark.criterion("G1-6", "G3-3")
@pytest.mark.parametrize("flag", ["-o", "--output-dir"])
def test_output_dir(work, tmp_path, capsys, flag):
    other = tmp_path / "other"
    other.mkdir()
    before = digests(work.parent)
    code, out, err = rename(capsys, work, f"S1={NAME1}", flag, other)
    assert (code, err) == (0, "")
    assert out.splitlines() == [str(p) for p in outputs(other)]
    assert sorted(other.iterdir()) == sorted(outputs(other))
    assert digests(work.parent) == before
    assert json.loads(outputs(other, ("json",))[0].read_text(encoding="utf-8"))["speakers"][0]["label"] == NAME1


@pytest.mark.criterion("G1-6", "G3-3")
@pytest.mark.parametrize("flag", ["-f", "--formats"])
def test_formats(work, capsys, flag):
    code, out, err = rename(capsys, work, f"S1={NAME1}", flag, "txt,json")
    assert (code, err) == (0, "")
    assert out.splitlines() == [str(p) for p in outputs(work.parent, ("txt", "json"))]
    assert sorted(work.parent.iterdir()) == sorted(outputs(work.parent, ("txt", "json")))
