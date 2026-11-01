"""Streaming ASR on the engine's stream, and `engines.Words` (design/internal/g4-stage1.md §1 "Words", §2 "Live
ASR"): pieces join into words, a word is complete when the next starts or at `finish`, and `done_until` is the open
word's start, or the stream's own.
"""

from fake_engine import FakeEngine

from lmt import engines
from lmt.engines import Piece
from lmt.live.asr import AsrUpdate, StreamingASR
from lmt.transcript import Word

RATE = 16000


def piece(text, start, starts_word=None, probability=0.9):
    return Piece(text, start, round(start + 0.08, 3), text.startswith(" ") if starts_word is None else starts_word,
                 probability)


def test_pieces_join_into_words_with_the_lowest_probability():
    pieces = [piece(" Hel", 0.0, probability=0.9), piece("lo", 0.08, probability=0.5), piece(" world", 0.4),
              piece(".", 0.48)]
    assert engines.words(pieces) == [Word(0.0, 0.16, "Hello", 0.5), Word(0.4, 0.56, "world.", 0.9)]


def test_a_first_piece_without_a_space_starts_a_word():
    assert engines.words([piece("so", 0.0), piece(" yes", 0.2)]) == [Word(0.0, 0.08, "so", 0.9),
                                                                    Word(0.2, 0.28, "yes", 0.9)]


def test_a_word_of_white_space_alone_is_dropped():
    assert engines.words([piece(" ", 0.0, starts_word=True), piece(" a", 0.1)]) == [Word(0.1, 0.18, "a", 0.9)]


def test_a_word_completes_when_the_next_starts():
    words = engines.Words(0.24)
    assert words.add([piece(" one", 0.0), piece("s", 0.08)], done_until=0.16) == []
    assert words.open_start == 0.0
    assert words.add([piece(" two", 0.16)], done_until=0.24) == [Word(0.0, 0.16, "ones", 0.9)]
    assert words.open_start == 0.16
    assert words.finish() == [Word(0.16, 0.24, "two", 0.9)]
    assert words.open_start is None and words.finish() == []


def test_a_word_completes_once_done_until_passes_its_end_by_the_gap():
    words = engines.Words(0.24)
    assert words.add([piece(" one", 0.0)], done_until=0.08) == []
    assert words.add([], done_until=0.319) == []
    assert words.add([], done_until=0.32) == [Word(0.0, 0.08, "one", 0.9)]


def test_streaming_asr_returns_completed_words_and_the_open_word_s_start():
    engine = FakeEngine([piece(" a", 0.0), piece(" b", 0.5), piece("c", 0.58)])
    stage = StreamingASR(engine.stream(), engine.word_gap)
    assert stage.feed([0.0] * 1600) == AsrUpdate([], 0)  # 0.1 s: "a" is out, still open
    assert stage.feed([0.0] * 6400) == AsrUpdate([Word(0.0, 0.08, "a", 0.9)], 8000)  # 0.5 s: "a" is complete
    assert stage.feed([0.0] * 3200) == AsrUpdate([], 8000)  # 0.7 s: "bc" is open
    assert stage.flush() == AsrUpdate([Word(0.5, 0.66, "bc", 0.9)], round(0.7 * RATE))
    assert engine.streams[0].closed == 1 and engine.finished == 1


def test_done_until_is_the_stream_s_when_no_word_is_open():
    engine = FakeEngine([])
    stage = StreamingASR(engine.stream(), engine.word_gap)
    assert stage.feed([0.0] * 4000) == AsrUpdate([], 4000)
