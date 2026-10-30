"""`lmt rename`: gives speakers names, then exports again (design/public-api.md; g3-stage1.md §1).

Item 1a builds its parts; `cli_rename` stays a stub until item 1b.
"""

import dataclasses
import re

from lmt.errors import NotBuilt, UsageError
from lmt.transcript import Transcript

MAX_NAME = 32
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")
_FORBIDDEN = "<>{}="


def _echo(text: str) -> str:
    """Text from the user, with its control characters written as \\uXXXX, so it can't split an error line."""
    return _CONTROL.sub(lambda m: f"\\u{ord(m.group()):04X}", text)


def parse_pair(arg: str) -> tuple[str, str]:
    """KEY=NAME, split at the first "="."""
    key, sep, name = arg.partition("=")
    if not sep:
        raise UsageError(_echo(arg), "expected KEY=NAME")
    return key, name


def check_name(key: str, name: str) -> str:
    """The trimmed name, or UsageError(key, problem): exactly the schema's label rule."""
    name = name.strip()
    if not name:
        problem = "the name is empty"
    elif len(name) > MAX_NAME:
        problem = f"the name is {len(name)} characters; at most {MAX_NAME}"
    elif _CONTROL.search(name):
        problem = "the name has a control character"
    elif bad := next((c for c in name if c in _FORBIDDEN), None):
        problem = f"the name has '{bad}'"
    else:
        return name
    raise UsageError(_echo(key), problem)


def _and(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " and " + ids[-1]


def resolve(transcript: Transcript, pairs) -> dict[str, str]:
    """Speaker id -> new label, for each (key, name) pair, against the transcript as read: a key is a
    speaker's id, else exactly one speaker's label."""
    ids = {s.id for s in transcript.speakers}
    labels = {}
    for key, name in pairs:
        label = check_name(key, name)
        if key in ids:
            sid = key
        else:
            matches = [s.id for s in transcript.speakers if s.label == key]
            if not matches:
                raise UsageError(_echo(key), "no speaker has this id or label")
            if len(matches) > 1:
                raise UsageError(_echo(key), f"is the label of {_and(matches)}; use an id")
            sid = matches[0]
        if sid in labels:
            raise UsageError(_echo(key), f"names {sid} a second time")
        labels[sid] = label
    return labels


def relabel(transcript: Transcript, labels: dict[str, str]) -> Transcript:
    """The transcript with the speakers in `labels` renamed; nothing else changes."""
    speakers = tuple(dataclasses.replace(s, label=labels.get(s.id, s.label)) for s in transcript.speakers)
    return dataclasses.replace(transcript, speakers=speakers)


def speaker_table(transcript: Transcript) -> str:
    """One tab-separated line per speaker, after a header, in `speakers` order."""
    lines = ["ID\tLABEL\tSEGMENTS\tSECONDS\tFIRST WORDS"]
    for s in transcript.speakers:
        segments = [g for g in transcript.segments if g.speaker == s.id]
        seconds = sum(g.end - g.start for g in segments)
        words = segments[0].text.split() if segments else []
        first = " ".join(words[:6]) + ("…" if len(words) > 6 else "")
        row = [s.id, s.label, str(len(segments)), f"{seconds:.1f}", first]
        lines.append("\t".join(cell.replace("\t", " ") for cell in row))
    return "\n".join(lines)


def cli_rename(args):
    raise NotBuilt("rename", "G3 stage 1")
