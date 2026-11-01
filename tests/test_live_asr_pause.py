"""A pause completes a word (spec G4 "Terms"; design/internal/g4-stage1.md §1): with Kyutai's gap, 3 empty steps of
80 ms after a word's last piece commit it, without waiting for the next word. Apple's gap is 0, so a word is
complete as soon as it is out.
"""

from fake_engine import FakeEngine

from lmt.engines import Piece
from lmt.engines.kyutai import WORD_GAP
from lmt.live.asr import StreamingASR
from lmt.transcript import Word


def test_kyutai_s_gap_is_three_steps():
    assert round(WORD_GAP, 3) == 0.24


def test_a_pause_of_the_gap_commits_the_last_word_before_the_source_stops():
    engine = FakeEngine([Piece(" hello", 1.0, 1.08, True, 0.8)])
    stage = StreamingASR(engine.stream(), WORD_GAP)
    assert stage.feed([0.0] * round(1.2 * 16000)).words == []  # 1.2 s: 0.12 s after its end
    assert stage.feed([0.0] * round(0.12 * 16000)).words == [Word(1.0, 1.08, "hello", 0.8)]  # 1.32 s


def test_with_no_gap_a_word_is_complete_once_out():
    engine = FakeEngine([Piece(" hello", 1.0, 1.08, True, 0.8)], name="apple", word_gap=0.0)
    stage = StreamingASR(engine.stream(), engine.word_gap)
    assert stage.feed([0.0] * round(1.08 * 16000)).words == [Word(1.0, 1.08, "hello", 0.8)]
