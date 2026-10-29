"""The text export: one line per segment, `[HH:MM:SS] <label>: <text>` (design/public-api.md, "Output
formats")."""

from lmt.export.cues import ms
from lmt.transcript import Segment, Transcript


def render_segment(segment: Segment, label: str) -> str:
    """The segment's line, its time the segment's start rounded down to the second. G2's growing
    `<stem>.txt` writes this too, so the two can't differ."""
    seconds = ms(segment.start) // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"[{hours:02d}:{minutes:02d}:{seconds:02d}] {label}: {segment.text}\n"


def render(transcript: Transcript) -> str:
    labels = {s.id: s.label for s in transcript.speakers}
    return "".join(render_segment(s, labels[s.speaker]) for s in transcript.segments)
