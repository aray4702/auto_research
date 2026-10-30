"""`lmt eval --set live`'s measures on the fixture logs, against the hand-computed figures
(design/internal/g2-stage1.md §5, item 5a). No model."""

import json
from pathlib import Path

import pytest

from lmt.errors import InputError
from lmt.eval import live
from lmt.eval.live import RefWord
from lmt.transcript import Word

pytestmark = pytest.mark.criterion("G2-6c")

FIXTURES = Path(__file__).parent / "fixtures" / "eval" / "live"

# Per run, from the design's table: (committed (text, arrival) pairs, matched delays, run p95, behind).
RUNS = {
    1: (
        [("Okay,", 2.3), ("so", 2.3), ("let's", 3.2), ("start.", 3.2), ("a", 4.6), ("meeting", 4.6)],
        [1.5, 1.3, 1.8, 1.3, 1.5],
        1.8,
        1.1,
    ),
    2: (
        [("okay", 1.9), ("so", 1.9), ("let's", 2.9), ("start", 2.9), ("the", 4.1), ("meeting", 4.1)],
        [1.1, 0.9, 1.5, 1.0, 1.5, 1.0],
        1.5,
        0.3,
    ),
    3: (
        [("okay", 2.0), ("so", 2.6), ("let's", 2.6), ("start", 2.6), ("the", 3.9), ("meeting", 3.9),
         ("meeting", 3.9)],
        [1.2, 1.6, 1.2, 0.7, 1.3, 0.8],
        1.6,
        0.0,
    ),
}


def ref_words():
    return [RefWord(*w) for w in json.loads((FIXTURES / "ref_words.json").read_text())]


def log_lines(run):
    lines = []
    for line in (FIXTURES / f"run{run}.jsonl").read_text().splitlines():
        record = json.loads(line)
        lines.append((record["arrival"], record["event"]))
    return lines


def rounded(values):
    return [round(v, 3) for v in values]


def test_key():
    assert live.key("Okay,") == "okay"
    assert live.key("start.") == "start"
    assert live.key("let's") == "lets"
    assert live.key("…") == ""
    assert live.key(" ¿Qué? ") == "qué"


def test_ref_words_fixture():
    assert ref_words() == [
        RefWord(0.5, 0.8, "okay"), RefWord(0.9, 1.0, "so"), RefWord(1.1, 1.4, "let's"),
        RefWord(1.4, 1.9, "start"), RefWord(2.5, 2.6, "the"), RefWord(2.6, 3.1, "meeting"),
    ]


@pytest.mark.parametrize("run", [1, 2, 3])
def test_read_events(run):
    committed, _, _, behind = RUNS[run]
    words, behind_seconds = live.read_events(log_lines(run))
    assert all(isinstance(w, Word) for w, _ in words)
    assert [(w.text, t) for w, t in words] == committed
    assert round(behind_seconds, 3) == behind


@pytest.mark.parametrize("run", [1, 2, 3])
def test_match_words_and_delays_per_run(run):
    _, expected, p95, _ = RUNS[run]
    words, _ = live.read_events(log_lines(run))
    matches = live.match_words(ref_words(), words)
    refs = ref_words()
    assert [refs.index(ref) for ref, _ in matches] == sorted(refs.index(ref) for ref, _ in matches)
    assert rounded(live.delays(matches)) == expected
    assert round(live.percentile(live.delays(matches), 0.95), 3) == p95


def test_run_1_a_against_the_doesnt_match():
    words, _ = live.read_events(log_lines(1))
    matched = [ref.text for ref, _ in live.match_words(ref_words(), words)]
    assert matched == ["okay", "so", "let's", "start", "meeting"]


def test_run_3_one_meeting_is_an_insertion():
    words, _ = live.read_events(log_lines(3))
    matches = live.match_words(ref_words(), words)
    assert [ref.text for ref, _ in matches] == ["okay", "so", "let's", "start", "the", "meeting"]


def test_pooled_figures():
    pooled = []
    for run in (1, 2, 3):
        words, _ = live.read_events(log_lines(run))
        pooled += live.delays(live.match_words(ref_words(), words))
    assert len(pooled) == 17
    assert sorted(rounded(pooled)) == [
        0.7, 0.8, 0.9, 1.0, 1.0, 1.1, 1.2, 1.2, 1.3, 1.3, 1.3, 1.5, 1.5, 1.5, 1.5, 1.6, 1.8,
    ]
    assert round(live.percentile(pooled, 0.5), 3) == 1.3
    assert round(live.percentile(pooled, 0.95), 3) == 1.8  # linear interpolation would give 1.64
    assert round(max(pooled), 3) == 1.8


def test_match_words_drops_empty_keys():
    hyp = [(Word(0.0, 0.1, "—", 0.9), 1.0), (Word(0.5, 0.8, "Okay!", 0.9), 1.0)]
    assert live.match_words([RefWord(0.5, 0.8, "okay"), RefWord(0.8, 0.9, "...")], hyp) == [
        (RefWord(0.5, 0.8, "okay"), 1.0),
    ]
    assert live.match_words([RefWord(0.5, 0.8, "okay")], []) == []


def test_percentile_nearest_rank():
    assert live.percentile([2.5], 0.5) == 2.5
    assert live.percentile([2.5], 0.95) == 2.5
    values = [float(v) for v in range(20, 0, -1)]  # 1 to 20, unsorted
    assert live.percentile(values, 0.95) == 19.0
    assert live.percentile([float(v) for v in range(1, 101)], 0.07) == 7.0
    with pytest.raises(ValueError):
        live.percentile([], 0.5)


def test_run_log():
    log = live.RunLog(lines=log_lines(2), wall_seconds=4.4, transcript={})
    assert live.read_events(log.lines)[0][0][0].text == "okay"
    with pytest.raises(AttributeError):
        log.wall_seconds = 1.0


@pytest.mark.parametrize("change, problem", [
    (lambda lines: lines[1:], "start"),
    (lambda lines: lines[:-1], "stop"),
    (lambda lines: [], "start"),
    (lambda lines: lines[:1] + [lines[2], lines[1]] + lines[3:], "falls"),
])
def test_read_events_rejects(change, problem):
    with pytest.raises(InputError) as caught:
        live.read_events(change(log_lines(1)))
    assert caught.value.exit_code == 3
    assert caught.value.subject == "lmt live"
    assert problem in caught.value.problem
