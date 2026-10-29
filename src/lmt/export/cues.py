"""Splitting a segment into subtitle cues, shared by SRT and WebVTT (design/internal/g1-slice.md, S5)."""

from dataclasses import dataclass

from lmt.transcript import Segment

LINE = 42  # code points a line, counting the visible prefix and not any tag
LINES = 2  # lines a cue


@dataclass(frozen=True)
class Cue:
    start: int  # whole milliseconds
    end: int
    lines: tuple[str, ...]  # the first starts with the visible prefix, `<label>: `


def ms(t: float) -> int:
    """A transcript time in whole milliseconds, as every format writes it."""
    return round(t * 1000)


def clock(t_ms: int, separator: str) -> str:
    """`HH:MM:SS<separator>mmm`."""
    seconds, millis = divmod(t_ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def pack(prefix: str, texts) -> list[list[list[int]]]:
    """The cues, each a list of lines, each a list of indices into `texts`. Greedy: a word joins the
    current line if the line stays within LINE, counting the prefix on a cue's first line. Else it goes
    to the next line, or, when that would be a third, to the next cue, which starts with the prefix. A
    word longer than LINE takes a line of its own, as does any word that doesn't fit after the prefix."""
    packed = [[[]]]
    for i in range(len(texts)):
        while True:
            cue = packed[-1]
            line = cue[-1]
            width = (len(prefix) if len(cue) == 1 else 0) + len(" ".join(texts[j] for j in [*line, i]))
            if width <= LINE or (not line and len(cue) > 1):
                line.append(i)
                break
            if len(cue) < LINES:
                cue.append([])
            else:
                packed.append([[]])
    return packed


def cues(segment: Segment, label: str) -> list[Cue]:
    """The segment's cues. A cue's start is its first word's and its end its last word's, except that the
    first cue starts at the segment's start and the last ends at the segment's end. A segment without
    words is split on its text's spaces, its span shared among its cues in proportion to their
    characters."""
    prefix = f"{label}: "
    texts = [w.text for w in segment.words] if segment.words else segment.text.split()
    if not texts:
        return [Cue(ms(segment.start), ms(segment.end), (prefix.rstrip(),))]
    packed = pack(prefix, texts)
    first = [(cue[0] or cue[1])[0] for cue in packed]
    last = [cue[-1][-1] for cue in packed]
    if segment.words:
        starts = [ms(segment.words[i].start) for i in first]
        ends = [ms(segment.words[i].end) for i in last]
    else:
        sizes = [len(" ".join(texts[a:b + 1])) for a, b in zip(first, last)]
        span, total = ms(segment.end) - ms(segment.start), sum(sizes)
        bounds = [ms(segment.start) + round(span * sum(sizes[:k]) / total) for k in range(len(sizes) + 1)]
        starts, ends = bounds[:-1], bounds[1:]
    starts[0], ends[-1] = ms(segment.start), ms(segment.end)
    out = []
    for start, end, cue in zip(starts, ends, packed):
        lines = [" ".join(texts[i] for i in line) for line in cue]
        lines[0] = (prefix + lines[0]).rstrip(" ")
        out.append(Cue(start, end, tuple(lines)))
    return out
