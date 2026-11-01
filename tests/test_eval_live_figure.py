"""`figure` on the fixture logs and transcripts, against the hand-computed figures, and the live timing fields in
`report.py` (design/internal/g2-stage1.md §5, item 5c). No model."""

import copy
import json
from pathlib import Path

from lmt import transcript
from lmt.eval import live, report, score
from lmt.eval.score import Item, Turn

FIXTURES = Path(__file__).parent / "fixtures" / "eval" / "live"
REF_TEXT = "okay so let's start the meeting"
ITEM = Item("AMI-ci", "ES2004a", Path("ES2004a.wav"), 4.0, ref_text=REF_TEXT, ref_turns=(Turn(0.5, 3.1, "A"),))
WALL_SECONDS = {1: 5.2, 2: 4.4, 3: 4.1}
KEYS = ["dataset", "system", "wer", "der", "audio_seconds", "wall_seconds", "rtf", "peak_rss_bytes",
        "p50_delay_seconds", "p95_delay_seconds", "max_delay_seconds",
        "p50_label_delay_seconds", "p95_label_delay_seconds", "max_label_delay_seconds", "matched_words", "runs"]
LEAD = 0.5  # s: in the 1.1 logs, each word's text events come this long before its words event


def ref_words():
    return json.loads((FIXTURES / "ref_words.json").read_text())


def text_events(arrival, event):
    """The words event's words as text events, LEAD earlier in arrival and `t`: words longer than 3 characters
    in two pieces, split at the 3rd character and at the middle of the word's time."""
    lines = []
    for w in event["words"]:
        cut = round((w["start"] + w["end"]) / 2, 3)
        parts = [(" " + w["text"][:3], w["start"], cut, True), (w["text"][3:], cut, w["end"], False)]
        if len(w["text"]) <= 3:
            parts = [(" " + w["text"], w["start"], w["end"], True)]
        for text, start, end, starts_word in parts:
            lines.append((round(arrival - LEAD, 3), {"type": "text", "t": round(event["t"] - LEAD, 3), "text": text,
                                                     "start": start, "end": end, "starts_word": starts_word}))
    return lines


def log(run):
    """Run `run`'s fixture log as live events 1.1: its words events, each after its text events."""
    lines = []
    for line in (FIXTURES / f"run{run}.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record["event"]["type"] == "words":
            lines += text_events(record["arrival"], record["event"])
        lines.append((record["arrival"], record["event"]))
    doc = transcript.to_dict(transcript.load(FIXTURES / f"run{run}.json"))  # valid against the schema
    return live.RunLog(lines=lines, wall_seconds=WALL_SECONDS[run], transcript=doc)


def logs():
    return [log(run) for run in (1, 2, 3)]


def results(figure):
    return report.build("live", [figure], logs()[0].transcript["models"], out_dir="results", commit="0" * 40,
                        dirty=False, date="2026-10-29T22:00:00-07:00", machine={"arch": "arm64"})


def test_the_fixture_transcripts_hold_each_runs_committed_words():
    for run in (1, 2, 3):
        words, _ = live.read_events(log(run).lines)
        doc = log(run).transcript
        assert doc["source"] == {"file": f"run{run}", "duration": 4.0, "live": True}
        [segment] = doc["segments"]
        assert (segment["start"], segment["end"], segment["speaker"]) == (0.5, 3.1, "S1")
        assert [w["text"] for w in segment["words"]] == [w.text for w, _ in words]
        assert segment["text"] == " ".join(w.text for w, _ in words)


def test_figure_gives_the_hand_computed_figures_in_key_order():
    hyps = [score.hypothesis_from_transcript(log(run).transcript).text for run in (1, 2, 3)]
    expected_wer = round(score.wer([(REF_TEXT, h) for h in hyps]), 6)

    figure = live.figure(ITEM, ref_words(), logs())

    assert list(figure) == KEYS
    assert figure == {
        "dataset": "AMI-ci",
        "system": "product-live",
        "wer": expected_wer,
        "der": 0.0,
        "audio_seconds": 12.0,
        "wall_seconds": 13.7,
        "rtf": 1.142,
        "peak_rss_bytes": None,
        "p50_delay_seconds": 0.8,
        "p95_delay_seconds": 1.3,
        "max_delay_seconds": 1.3,
        "p50_label_delay_seconds": 1.3,
        "p95_label_delay_seconds": 1.8,
        "max_label_delay_seconds": 1.8,
        "matched_words": 17,
        "runs": [
            {"p95_delay_seconds": 1.3, "p95_label_delay_seconds": 1.8, "behind_seconds": 1.1},
            {"p95_delay_seconds": 1.0, "p95_label_delay_seconds": 1.5, "behind_seconds": 0.3},
            {"p95_delay_seconds": 1.1, "p95_label_delay_seconds": 1.6, "behind_seconds": 0.0},
        ],
    }
    assert expected_wer > 0  # run 1 says `a` for `the`, and run 3 repeats `meeting`
    assert list(figure["runs"][0]) == ["p95_delay_seconds", "p95_label_delay_seconds", "behind_seconds"]


def test_the_1_1_logs_text_events_carry_the_words_events_words():
    for run in (1, 2, 3):
        labelled, _ = live.read_events(log(run).lines)
        text = live.read_text(log(run).lines)
        assert [(w.text, w.start, w.end) for w, _ in text] == [(w.text, w.start, w.end) for w, _ in labelled]
        assert [round(a, 3) for _, a in text] == [round(a - LEAD, 3) for _, a in labelled]


def test_a_1_0_log_has_no_word_delay_and_its_label_delays_as_before():
    one_0 = [live.RunLog([(a, e) for a, e in log(run).lines if e["type"] != "text"], WALL_SECONDS[run],
                         log(run).transcript) for run in (1, 2, 3)]
    figure = live.figure(ITEM, ref_words(), one_0)
    assert (figure["p95_delay_seconds"], figure["matched_words"]) == (None, 0)
    assert (figure["p50_label_delay_seconds"], figure["p95_label_delay_seconds"]) == (1.3, 1.8)


def test_strip_timing_removes_the_delays_and_each_runs_timing_keeping_the_runs():
    stripped = report.strip_timing(results(live.figure(ITEM, ref_words(), logs())))

    [figure] = stripped["figures"]
    assert list(figure) == ["dataset", "system", "wer", "der", "audio_seconds", "matched_words", "runs"]
    assert figure["runs"] == [{}, {}, {}]
    assert "date" not in stripped


def test_same_accuracy_ignores_the_live_timing_fields_but_not_wer():
    a = results(live.figure(ITEM, ref_words(), logs()))
    slower = copy.deepcopy(a)
    figure = slower["figures"][0]
    figure.update(p50_delay_seconds=2.0, p95_delay_seconds=3.0, max_delay_seconds=4.0, wall_seconds=20.0,
                  rtf=1.667, p50_label_delay_seconds=2.5, p95_label_delay_seconds=3.5, max_label_delay_seconds=4.5)
    figure["runs"] = [{"p95_delay_seconds": 3.0, "p95_label_delay_seconds": 3.5, "behind_seconds": 2.5}
                      for _ in figure["runs"]]
    assert report.same_accuracy(a, slower)

    worse = copy.deepcopy(a)
    worse["figures"][0]["wer"] = 0.5
    assert not report.same_accuracy(a, worse)


def test_a_figure_without_runs_strips_as_before():
    figure = {"dataset": "LS-ci", "system": "product", "wer": 0.05, "der": None, "audio_seconds": 10.0,
              "wall_seconds": 3.0, "rtf": 0.3, "peak_rss_bytes": 1000}
    [stripped] = report.strip_timing(results(figure))["figures"]
    assert stripped == {"dataset": "LS-ci", "system": "product", "wer": 0.05, "der": None, "audio_seconds": 10.0}
