"""The WebVTT export: `HH:MM:SS.mmm`, the speaker as both a voice tag and visible text, `<v José &amp;
Ana>José &amp; Ana: text`."""

from lmt.export.cues import clock, cues
from lmt.transcript import Transcript


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(transcript: Transcript) -> str:
    labels = {s.id: s.label for s in transcript.speakers}
    out = ["WEBVTT\n\n"]
    for segment in transcript.segments:
        label = labels[segment.speaker]
        voice = f"<v {label.replace('&', '&amp;')}>"  # a label can't hold `<` or `>`
        for cue in cues(segment, label):
            lines = [escape(line) for line in cue.lines]
            lines[0] = voice + lines[0]
            out.append(f"{clock(cue.start, '.')} --> {clock(cue.end, '.')}\n"
                       + "".join(f"{line}\n" for line in lines) + "\n")
    return "".join(out)
