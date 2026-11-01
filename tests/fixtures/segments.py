"""The segments G3-5's "first, middle and last" picks (design/internal/g3-stage1.md, 0c), worked out from the
transcript at test time, so the fixture taken again needs no change to the design."""

from collections.abc import Sequence

# G3-5 checks the marked segment 1 s after the click, from up to 0.05 s before its start (g3-stage1.md, 3b).
MIN_CURRENT = 1.6


def current_span(segments: Sequence, i: int) -> float:
    """How long segment `i` stays current: the last segment whose start <= currentTime is current, so it is the
    earliest start among the segments after `i`, minus `segments[i].start`. For the last segment, `end - start`."""
    later = segments[i + 1 :]
    if not later:
        return segments[i].end - segments[i].start
    return min(s.start for s in later) - segments[i].start


def play_targets(segments: Sequence) -> tuple[int, int, int]:
    """The first, middle and last segments' indices. The middle one is the first `i` with n // 2 <= i < n - 1 that
    stays current for at least MIN_CURRENT."""
    n = len(segments)
    for i in range(n // 2, n - 1):
        if round(current_span(segments, i), 3) >= MIN_CURRENT:
            return 0, i, n - 1
    raise LookupError(f"no segment from index {n // 2} (n // 2) to the last stays current for {MIN_CURRENT} s")
