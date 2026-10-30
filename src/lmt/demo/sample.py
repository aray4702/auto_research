"""`lmt demo --sample`'s cache: the sample meeting and its transcript (g3-stage1.md §2b).

A stub until G3 stage 1's item 2b1 builds it.
"""

from pathlib import Path

from lmt.errors import NotBuilt


def prepare(*, offline: bool) -> tuple[Path, Path]:
    """The sample's (transcript, audio) paths in `LMT_DATA_DIR`, made on the first run."""
    raise NotBuilt("demo --sample", "G3 stage 1")
