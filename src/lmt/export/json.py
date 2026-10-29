"""The JSON export: the transcript's document, valid against schema/transcript.schema.json."""

import json

from lmt.transcript import Transcript, to_dict


def render(transcript: Transcript) -> str:
    return json.dumps(to_dict(transcript), indent=2, ensure_ascii=False) + "\n"
