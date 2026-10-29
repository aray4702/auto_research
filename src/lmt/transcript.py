"""The transcript types, and the JSON's document (design/public-api.md, "Transcript JSON, version 1.1")."""

import copy
import json
from dataclasses import dataclass
from pathlib import Path

from lmt import schema
from lmt.errors import InputError

SCHEMA_VERSION = "1.1"


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str
    probability: float


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class Speaker:
    id: str
    label: str


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    speaker: str
    text: str
    words: tuple[Word, ...]


@dataclass(frozen=True)
class Transcript:
    schema_version: str
    lmt_version: str
    source: dict
    models: dict
    speakers: tuple[Speaker, ...]
    segments: tuple[Segment, ...]


def to_dict(t: Transcript) -> dict:
    """The JSON's keys, in public-api.md's order."""
    return {
        "schema_version": t.schema_version,
        "lmt_version": t.lmt_version,
        "source": copy.deepcopy(t.source),
        "models": copy.deepcopy(t.models),
        "speakers": [{"id": s.id, "label": s.label} for s in t.speakers],
        "segments": [
            {
                "start": s.start, "end": s.end, "speaker": s.speaker, "text": s.text,
                "words": [{"start": w.start, "end": w.end, "text": w.text, "probability": w.probability}
                          for w in s.words],
            }
            for s in t.segments
        ],
    }


def from_dict(doc: dict) -> Transcript:
    """The transcript a document holds. `words` is optional, as the schema says."""
    return Transcript(
        schema_version=doc["schema_version"],
        lmt_version=doc["lmt_version"],
        source=copy.deepcopy(doc["source"]),
        models=copy.deepcopy(doc["models"]),
        speakers=tuple(Speaker(s["id"], s["label"]) for s in doc["speakers"]),
        segments=tuple(
            Segment(s["start"], s["end"], s["speaker"], s["text"],
                    tuple(Word(w["start"], w["end"], w["text"], w["probability"]) for w in s.get("words", ())))
            for s in doc["segments"]
        ),
    )


def load(path) -> Transcript:
    """The transcript a JSON file holds, validated against the schema (design/internal/g1-stage2.md, P9).
    Every failure is InputError(str(path), …)."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise InputError(str(path), f"can't read: {e.strerror}") from None
    try:
        doc = json.loads(data.decode("utf-8"))
    except ValueError as e:  # UnicodeDecodeError and json.JSONDecodeError
        raise InputError(str(path), f"not JSON: {e}") from None
    if not isinstance(doc, dict):
        raise InputError(str(path), "not a transcript")
    v = doc.get("schema_version")
    if isinstance(v, str) and v.split(".", 1)[0] != "1":
        raise InputError(str(path), f"schema version {v} isn't supported; this lmt reads 1.x")
    try:
        schema.validate(doc)
    except InputError as e:
        raise InputError(str(path), e.problem) from None
    return from_dict(doc)
