"""G3-1's last sentence on a real product run (design/internal/g3-stage1.md, 1c): `lmt rename` with the rename
names, then the rename back, gives every file of `two_speakers_run` back, byte for byte. Both runs are on one
platform, so this holds on the Mac and on Linux alike."""

import json
import shutil

import pytest
from test_rename import FORMATS, NAME1, NAME2, outputs, rename


@pytest.mark.models
@pytest.mark.criterion("G3-1")
def test_renaming_a_product_run_and_back_gives_its_files_back(two_speakers_run, tmp_path, capsys):
    assert two_speakers_run.returncode == 0, two_speakers_run.stderr
    originals = outputs(two_speakers_run.out_dir)
    for path in originals:
        assert path.is_file(), two_speakers_run.stdout
        shutil.copyfile(path, tmp_path / path.name)
    work = tmp_path / "two_speakers.json"
    labels = {s["id"]: s["label"] for s in json.loads(work.read_text(encoding="utf-8"))["speakers"]}
    assert sorted(labels) == ["S1", "S2"], labels

    code, _, err = rename(capsys, work, f"S1={NAME1}", f"S2={NAME2}")
    assert (code, err) == (0, ""), err
    assert all(path.read_bytes() != original.read_bytes()
               for path, original in zip(outputs(tmp_path), originals)), "the rename changed no file"

    code, _, err = rename(capsys, work, f"{NAME1}={labels['S1']}", f"S2={labels['S2']}")  # by label, by id
    assert (code, err) == (0, ""), err
    for path, original, fmt in zip(outputs(tmp_path), originals, FORMATS):
        assert path.read_bytes() == original.read_bytes(), fmt
