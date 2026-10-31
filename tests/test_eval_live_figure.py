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
        "p50_delay_seconds", "p95_delay_seconds", "max_delay_seconds", "p50_label_delay_seconds",
        "p95_label_delay_seconds", "max_label_delay_seconds", "matched_words", "runs"]


def ref_words():
    return json.loads((FIXTURES / "ref_words.json").read_text())


def log(run):
    lines = []
    for line in (FIXTURES / f"run{run}.jsonl").read_text().splitlines():
        record = json.loads(line)
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
        "p50_delay_seconds": 1.3,
        "p95_delay_seconds": 1.8,
        "max_delay_seconds": 1.8,
        # The fixture streams are live events 1.0, with no `text` event, so words come with their labels.
        "p50_label_delay_seconds": 1.3,
        "p95_label_delay_seconds": 1.8,
        "max_label_delay_seconds": 1.8,
        "matched_words": 17,
        "runs": [
            {"p95_delay_seconds": 1.8, "p95_label_delay_seconds": 1.8, "behind_seconds": 1.1},
            {"p95_delay_seconds": 1.5, "p95_label_delay_seconds": 1.5, "behind_seconds": 0.3},
            {"p95_delay_seconds": 1.6, "p95_label_delay_seconds": 1.6, "behind_seconds": 0.0},
        ],
    }
    assert expected_wer > 0  # run 1 says `a` for `the`, and run 3 repeats `meeting`
    assert list(figure["runs"][0]) == ["p95_delay_seconds", "p95_label_delay_seconds", "behind_seconds"]


def text(t, piece, start, end, starts_word=True):
    return (t, {"type": "text", "t": t, "text": piece, "start": start, "end": end, "starts_word": starts_word})


def with_text_events():
    """Run 1 as live events 1.1 would carry it: each word's pieces in `text` events before its `words` event,
    `let's` and `meeting` in two pieces each."""
    lines = log(1).lines
    return [lines[0],
            text(1.0, " Okay,", 0.5, 0.8), text(1.3, " so", 0.9, 1.0), text(1.6, " let", 1.1, 1.3),
            text(1.8, "'s", 1.3, 1.4, False),
            lines[1],
            text(2.4, " start.", 1.4, 1.9), text(2.5, " a", 1.9, 2.0),
            lines[2],
            text(3.4, " meet", 2.6, 2.9), text(3.6, "ing", 2.9, 3.1, False),
            lines[3], lines[4]]


def test_read_text_joins_pieces_into_words_at_their_last_pieces_arrival():
    words = live.read_text(with_text_events())
    assert [(w.text, w.start, w.end, t) for w, t in words] == [
        ("Okay,", 0.5, 0.8, 1.0), ("so", 0.9, 1.0, 1.3), ("let's", 1.1, 1.4, 1.8), ("start.", 1.4, 1.9, 2.4),
        ("a", 1.9, 2.0, 2.5), ("meeting", 2.6, 3.1, 3.6)]
    assert live.read_text(log(1).lines) is None  # 1.0: no `text` event


def test_word_delay_at_the_text_event_and_label_delay_at_the_words_event():
    # spec, G4 "Terms". By hand, the five matched words (`a` isn't `the`), against their reference ends
    # 0.8, 1.0, 1.4, 1.9 and 3.1: words at 1.0, 1.3, 1.8, 2.4 and 3.6 give 0.2, 0.3, 0.4, 0.5 and 0.5 s; labels
    # at 2.3, 2.3, 3.2, 3.2 and 4.6 give 1.5, 1.3, 1.8, 1.3 and 1.5 s. Nearest rank of five: p50 the 3rd, p95 the
    # 5th.
    run = log(1)
    figure = live.figure(ITEM, ref_words(), [live.RunLog(with_text_events(), run.wall_seconds, run.transcript)])
    assert {k: figure[k] for k in KEYS[8:-1] + ["runs"]} == {
        "p50_delay_seconds": 0.4, "p95_delay_seconds": 0.5, "max_delay_seconds": 0.5,
        "p50_label_delay_seconds": 1.5, "p95_label_delay_seconds": 1.8, "max_label_delay_seconds": 1.8,
        "matched_words": 5,
        "runs": [{"p95_delay_seconds": 0.5, "p95_label_delay_seconds": 1.8, "behind_seconds": 1.1}]}


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
                  rtf=1.667)
    figure["runs"] = [{"p95_delay_seconds": 3.0, "behind_seconds": 2.5} for _ in figure["runs"]]
    assert report.same_accuracy(a, slower)

    worse = copy.deepcopy(a)
    worse["figures"][0]["wer"] = 0.5
    assert not report.same_accuracy(a, worse)


def test_a_figure_without_runs_strips_as_before():
    figure = {"dataset": "LS-ci", "system": "product", "wer": 0.05, "der": None, "audio_seconds": 10.0,
              "wall_seconds": 3.0, "rtf": 0.3, "peak_rss_bytes": 1000}
    [stripped] = report.strip_timing(results(figure))["figures"]
    assert stripped == {"dataset": "LS-ci", "system": "product", "wer": 0.05, "der": None, "audio_seconds": 10.0}
