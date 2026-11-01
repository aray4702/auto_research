"""Alignment: words and turns into the transcript (design/architecture.md, "Pipeline";
design/internal/g1-slice.md, S4; turns-first, design/internal/g1-stage2.md, P12). G2's live join
reuses the words-first rules."""

from lmt.transcript import SCHEMA_VERSION, Segment, Speaker, Transcript, Turn, Word

PAUSE = 1.0  # s: a gap this long or longer starts a segment
MAX_SEGMENT = 30.0  # s: no segment passes this length
NEAREST = 1.0  # s: a word in no turn takes the nearest one this close
CLOSERS = "\"')]"  # removed from a word's end before looking for its sentence end


def _ms(t: float) -> int:
    return round(t * 1000)


def assign_speaker(word: Word, turns, previous: str | None) -> str | None:
    """The turn overlapping the word most; else the nearest within NEAREST; else `previous`.
    Ties go to the earlier turn in `turns`' order."""
    best, most = None, 0.0
    for turn in turns:
        overlap = min(word.end, turn.end) - max(word.start, turn.start)
        if overlap > most:
            best, most = turn, overlap
    if best is not None:
        return best.speaker
    nearest = None  # (gap, turn); a zero-length word inside a turn is at gap 0 from it
    for turn in turns:
        gap = max(turn.start - word.end, word.start - turn.end, 0.0)
        if gap <= NEAREST and (nearest is None or gap < nearest[0]):
            nearest = (gap, turn)
    return nearest[1].speaker if nearest is not None else previous


def ends_sentence(word: Word) -> bool:
    return word.text.rstrip(CLOSERS).endswith((".", "?", "!"))


def breaks(previous_word: Word, word: Word, segment_start: float) -> bool:
    """Whether `word` starts a new segment, its speaker aside. Compared in whole milliseconds, the
    transcript's own unit, so that a pause of exactly 1.000 s breaks whatever its float error."""
    return (_ms(word.start) - _ms(previous_word.end) >= _ms(PAUSE)
            or ends_sentence(previous_word)
            or _ms(word.end) - _ms(segment_start) > _ms(MAX_SEGMENT))


def segment_text(words) -> str:
    return " ".join(w.text for w in words)


def _rounded(words, d: int) -> list[Word]:
    """Times in whole milliseconds, once: 0 <= start < end <= d, starts never decreasing."""
    out, previous = [], 0
    for w in words:
        start = min(max(_ms(w.start), previous, 0), d - 1)
        end = min(max(_ms(w.end), start + 1), d)
        out.append(Word(start / 1000, end / 1000, w.text, round(w.probability, 3)))
        previous = start
    return out


def _clipped(turns, d: int) -> list[list]:
    """P12 step 1: turns in whole milliseconds, clipped to [0, d], the empty ones dropped, ordered by
    (start, end, index in the input). Each is [start, end, speaker]."""
    out = []
    for i, t in enumerate(turns or ()):
        start, end = min(max(_ms(t.start), 0), d), min(max(_ms(t.end), 0), d)
        if end > start:
            out.append((start, end, i, t.speaker))
    return [[start, end, speaker] for start, end, _, speaker in sorted(out)]


def _place(words, turns) -> list[list[Word]]:
    """P12 steps 2 and 3: each word to the turn holding its midpoint, overlapping it most; a stray word
    to the nearest turn, which stretches to cover it. Ties go to the earlier turn."""
    held = [[] for _ in turns]
    strays = []
    for w in words:
        ws, we = _ms(w.start), _ms(w.end)
        best = None  # (overlap, index)
        for k, (start, end, _) in enumerate(turns):
            if 2 * start <= ws + we < 2 * end and (best is None or min(we, end) - max(ws, start) > best[0]):
                best = (min(we, end) - max(ws, start), k)
        if best is None:
            k = min((max(start - we, ws - end, 0), k) for k, (start, end, _) in enumerate(turns))[1]
            strays.append((k, ws, we))
        else:
            k = best[1]
        held[k].append(w)
    for k, ws, we in strays:
        turns[k][0], turns[k][1] = min(turns[k][0], ws), max(turns[k][1], we)
    return held


def _pieces(start: int, end: int, words) -> list:
    """P12 step 6: a turn cut greedily before a word, at a word boundary, once a piece would pass
    MAX_SEGMENT. Every piece holds a word. Each is (start, end, [words]), in milliseconds."""
    pieces, held, reach = [], [], 0  # reach: the end of every word in the piece so far
    for w in words:
        ws, we = _ms(w.start), _ms(w.end)
        if held and we - start > _ms(MAX_SEGMENT) and ws >= reach and ws > start:
            pieces.append((start, ws, held))
            start, held, reach = ws, [], 0
        held.append(w)
        reach = max(reach, we)
    return pieces + [(start, end, held)]


def _turns_first(words, turns) -> list:
    """With turns (P12): each turn holding words is a segment, with its span and speaker."""
    out = []
    for (start, end, speaker), ws in zip(turns, _place(words, turns)):
        if ws:
            out += [(s / 1000, e / 1000, speaker, piece) for s, e, piece in _pieces(start, end, ws)]
    return out


def _words_first(words) -> list:
    """Without turns: runs of words, broken by `breaks`, all one speaker."""
    runs = []
    for w in words:
        if not runs or breaks(runs[-1][-1], w, runs[-1][0].start):
            runs.append([])
        runs[-1].append(w)
    return [(ws[0].start, max(w.end for w in ws), None, ws) for ws in runs]


def build(words, turns: list[Turn] | None, *, duration_samples: int, source_file: str, models: dict,
          lmt_version: str) -> Transcript:
    d = duration_samples // 16  # ms at 16 kHz, rounded down so no time passes the audio's end
    words = _rounded(words, d)
    turns = _clipped(turns, d)
    runs = sorted(_turns_first(words, turns) if turns else _words_first(words), key=lambda r: r[0])
    ids = {}  # speakers numbered by first appearance
    segments = tuple(Segment(start, end, ids.setdefault(raw, f"S{len(ids) + 1}"), segment_text(ws), tuple(ws))
                     for start, end, raw, ws in runs)
    speakers = tuple(Speaker(f"S{n}", f"Speaker {n}") for n in range(1, len(ids) + 1))
    return Transcript(SCHEMA_VERSION, lmt_version, {"file": source_file, "duration": d / 1000}, models,
                      speakers, segments)
