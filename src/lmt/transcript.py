"""The transcript's types (design/internal/g1-slice.md, S4). S2 needs only `Word`; S4 adds the rest."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    start: float  # seconds from the start of the audio, unrounded
    end: float
    text: str
    probability: float
