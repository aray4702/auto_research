"""`lmt eval --set ci`'s runs on fakes, with no model (design/internal/g1-stage2.md, P2a): the runner on
tests/fixtures/eval/fake_worker.py in place of the worker, and the worker in process on test_pipeline's fakes.

The fake items are #109's fixtures, so every expected figure is the hand-computed one in tests/test_score.py.
"""

import dataclasses
import json
import re
import tempfile
from pathlib import Path

import pytest
from test_pipeline import WORDS, Fakes, wav  # noqa: F401 -- wav is a fixture
from test_score import FIXTURES, ami_items, ls_items

from lmt import cli, diarize, engines
from lmt.errors import InputError
from lmt.eval import runner, worker
from lmt.eval.score import SYSTEMS

FAKE_WORKER = FIXTURES / "fake_worker.py"
LS_IDS, AMI_IDS = ["u1", "u2", "u3", "u4"], ["f1", "f2"]


def items():
    """LS-ci's four utterances and AMI-ci's two files, each audio path beside its fixture transcript."""
    return ([dataclasses.replace(i, audio=FIXTURES / "ls" / f"{i.id}.flac") for i in ls_items()]
            + [dataclasses.replace(i, audio=FIXTURES / "ami" / f"{i.id}.wav") for i in ami_items()])


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """The fake worker in place of the worker, the fake items as the ci set, and an empty temporary root."""
    log = tmp_path / "argv.jsonl"
    monkeypatch.setattr(runner, "WORKER", (str(FAKE_WORKER),))
    monkeypatch.setenv("FAKE_WORKER_LOG", str(log))
    monkeypatch.delenv("FAKE_WORKER_EXIT", raising=False)
    fetched = []

    def fetch(set_name):
        fetched.append(set_name)
        return items()

    monkeypatch.setattr(runner.datasets, "fetch", fetch)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))

    class Fake:
        def argvs(self):
            return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    f = Fake()
    f.fetched, f.tmp = fetched, tmp
    return f


def test_the_results_file_holds_the_hand_computed_figures(fake, tmp_path, capsys):
    out = tmp_path / "results"
    out.mkdir()
    results = runner.evaluate("ci", out_dir=out)
    assert fake.fetched == ["ci"]
    assert json.loads((out / "ci.json").read_text(encoding="utf-8")) == results
    assert sorted(p.name for p in out.iterdir()) == ["ci.json"]
    assert list(fake.tmp.iterdir()) == []  # the runs' directories are gone
    assert results["set"] == "ci"
    assert results["determinism"] is None
    # The first diarized run's models: f1's, which name diarization; the LibriSpeech runs' don't.
    assert results["models"]["diarization"]["name"] == "speaker-diarization-3.1"

    figures = {(f["dataset"], f["system"]): f for f in results["figures"]}
    assert list(figures) == [("LS-ci", "product"), ("LS-ci", "asr-alone"), ("AMI-ci", "product"),
                             ("AMI-ci", "asr-alone"), ("AMI-ci", "pyannote-alone")]
    # By hand, as in test_score: LS WER 5/19 for the product and for Whisper alone, whose words are the same;
    # AMI DER 2/17 for the product and for pyannote alone, whose turns are the same. The fake AMI items have
    # no reference words, so no WER there.
    assert (figures["LS-ci", "product"]["wer"], figures["LS-ci", "asr-alone"]["wer"]) == (0.263158, 0.263158)
    assert (figures["AMI-ci", "product"]["der"], figures["AMI-ci", "pyannote-alone"]["der"]) == (0.117647, 0.117647)
    for figure in figures.values():
        assert isinstance(figure["rtf"], float) and isinstance(figure["wall_seconds"], float), figure
        assert isinstance(figure["peak_rss_bytes"], int) == (figure["system"] == "product"), figure
        if figure["system"] != "product":
            assert figure["peak_rss_bytes"] is None
    assert figures["LS-ci", "product"]["peak_rss_bytes"] > 0
    # The alones' times are their stages' within the run: the fake's 0.25 s of ASR and 0.5 s of diarization.
    # Whisper alone on LS-ci: 4 × 0.25 = 1.0 s over 8.0 s, RTF 0.125. On AMI-ci: 2 × 0.25 = 0.5 s over 16.0 s,
    # RTF 0.03125, which rounds to even, 0.031. pyannote alone: 2 × 0.5 = 1.0 s over 16.0 s, 0.0625, to 0.062.
    assert (figures["LS-ci", "asr-alone"]["wall_seconds"], figures["LS-ci", "asr-alone"]["rtf"]) == (1.0, 0.125)
    assert (figures["AMI-ci", "asr-alone"]["wall_seconds"], figures["AMI-ci", "asr-alone"]["rtf"]) == (0.5, 0.031)
    assert (figures["AMI-ci", "pyannote-alone"]["wall_seconds"], figures["AMI-ci", "pyannote-alone"]["rtf"]) == (1.0, 0.062)

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 6
    for n, (line, item_id) in enumerate(zip(lines, LS_IDS + AMI_IDS, strict=True), start=1):
        dataset = "LS-ci" if item_id.startswith("u") else "AMI-ci"
        assert re.fullmatch(rf"lmt: eval: {dataset} {item_id} \({n}/6\) \d+\.\d s", line), line


def test_diarization_is_off_exactly_for_the_librispeech_items(fake, tmp_path):
    runner.evaluate("ci", out_dir=tmp_path)
    argvs = fake.argvs()
    assert [Path(a[2]).stem for a in argvs] == LS_IDS + AMI_IDS
    for argv, item in zip(argvs, items(), strict=True):
        assert argv[1:] == ["transcribe", str(item.audio)] + (["--no-diarize"] if item.ref_turns is None else [])
        assert Path(argv[0]).name == runner.STAGES


def test_each_run_gets_its_own_empty_directory(fake, tmp_path):
    runner.evaluate("ci", out_dir=tmp_path)
    dirs = [Path(a[0]).parent for a in fake.argvs()]
    assert len(set(dirs)) == 6


def test_hypotheses_take_each_system_s_timing():
    (item,) = [i for i in items() if i.id == "f1"]
    run = runner.Run(transcript=json.loads((FIXTURES / "ami" / "f1.json").read_text(encoding="utf-8")),
                     words=[[0.0, 1.0, "Shall"], [1.0, 2.0, "we"]], turns=[[0.0, 4.0, "SPEAKER_00"]],
                     wall_seconds=9.0, peak_rss_bytes=1234, asr_seconds=3.0, diarize_seconds=4.0)
    hyps = runner.hypotheses(item, run)
    assert sorted(hyps) == sorted((s, "AMI-ci", "f1") for s in SYSTEMS)
    product = hyps["product", "AMI-ci", "f1"]
    assert (product.text, product.wall_seconds, product.peak_rss_bytes) == (
        "Shall we get started? Yes, the agenda first. Right, item one.", 9.0, 1234)
    whisper = hyps["asr-alone", "AMI-ci", "f1"]
    assert (whisper.text, whisper.turns, whisper.wall_seconds, whisper.peak_rss_bytes) == ("Shall we", None, 3.0, None)
    pyannote = hyps["pyannote-alone", "AMI-ci", "f1"]
    assert (pyannote.text, pyannote.wall_seconds, pyannote.peak_rss_bytes) == (None, 4.0, None)
    assert [(t.start, t.end, t.speaker) for t in pyannote.turns] == [(0.0, 4.0, "SPEAKER_00")]
    # Without diarization there is no pyannote alone.
    undiarized = dataclasses.replace(run, turns=None, diarize_seconds=None)
    assert ("pyannote-alone", "AMI-ci", "f1") not in runner.hypotheses(item, undiarized)


def test_a_failed_run_raises_exit_3_naming_the_item(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER_EXIT", "4")
    with pytest.raises(InputError) as e:
        runner.evaluate("ci", out_dir=tmp_path)
    assert e.value.exit_code == 3
    assert e.value.subject == str(FIXTURES / "ls" / "u1.flac")
    assert e.value.problem == "lmt transcribe exited 4: lmt: error: u1.flac: fake exit 4"
    assert not (tmp_path / "ci.json").exists()
    assert list(fake.tmp.iterdir()) == []


@pytest.mark.criterion("G1-6")
def test_set_ci_and_out_write_the_results_file_and_print_its_path(fake, tmp_path, capsys):
    out = tmp_path / "new" / "results"  # --out is created when it's missing
    code = cli.main(["eval", "--set", "ci", "--out", str(out)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out == f"{out / 'ci.json'}\n"
    assert json.loads((out / "ci.json").read_text(encoding="utf-8"))["set"] == "ci"
    assert sorted(p.name for p in out.iterdir()) == ["ci.json"]


# --- The worker, in process --------------------------------------------------------------------------------


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    return Fakes(monkeypatch, tmp_path)


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    path = tmp_path / "run"
    path.mkdir()
    monkeypatch.chdir(path)
    return path


def test_the_worker_records_the_stages_and_exits_with_main_s_code(fakes, wav, run_dir, capsys):  # noqa: F811
    fake_asr_load, fake_diarize_load = engines.load, diarize.load
    stages = run_dir / "stages.json"
    code = worker.main([str(stages), "transcribe", str(wav)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out.splitlines() == [f"meeting.{fmt}" for fmt in ("txt", "srt", "vtt", "json")]
    doc = json.loads(stages.read_text(encoding="utf-8"))
    assert list(doc) == ["words", "turns", "asr_seconds", "diarize_seconds"]
    assert doc["words"] == [[w.start, w.end, w.text] for w in WORDS]
    assert doc["turns"] == [[0.0, 1.0, "SPEAKER_00"], [1.0, 1.9, "SPEAKER_01"]]
    assert doc["asr_seconds"] >= 0 and doc["diarize_seconds"] >= 0
    assert "load asr" in fakes.calls and "load diarize" in fakes.calls
    # The wrappers were for the call only.
    assert (engines.load, diarize.load) == (fake_asr_load, fake_diarize_load)


def test_the_worker_without_diarization(fakes, wav, run_dir):  # noqa: F811
    stages = run_dir / "stages.json"
    assert worker.main([str(stages), "transcribe", str(wav), "--no-diarize"]) == 0
    doc = json.loads(stages.read_text(encoding="utf-8"))
    assert doc["words"] == [[w.start, w.end, w.text] for w in WORDS]
    assert (doc["turns"], doc["diarize_seconds"]) == (None, None)
    assert doc["asr_seconds"] >= 0


def test_the_worker_with_no_words_exits_4_and_writes_no_stages(fakes, wav, run_dir, capsys):  # noqa: F811
    fakes.words = []
    stages = run_dir / "stages.json"
    assert worker.main([str(stages), "transcribe", str(wav)]) == 4
    assert capsys.readouterr().err == "lmt: error: meeting.wav: no speech found\n"
    assert list(run_dir.iterdir()) == []
