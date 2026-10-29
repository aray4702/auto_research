"""The SRT export: cues numbered from 1, `HH:MM:SS,mmm`, the speaker's label as visible text."""

from lmt.export.cues import clock, cues
from lmt.transcript import Transcript


def render(transcript: Transcript) -> str:
    labels = {s.id: s.label for s in transcript.speakers}
    out = []
    for segment in transcript.segments:
        for cue in cues(segment, labels[segment.speaker]):
            out.append(f"{len(out) + 1}\n{clock(cue.start, ',')} --> {clock(cue.end, ',')}\n"
                       + "".join(f"{line}\n" for line in cue.lines) + "\n")
    return "".join(out)
