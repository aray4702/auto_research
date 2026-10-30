"""The markup fixture (design/internal/g3-stage1.md, "The markup fixture"): the transcript fixture with markup as
its first two segments' text. Built from the fixture at test time, so it can't fall out of step with it."""

import copy

IMG = '<img src=x onerror="window.__lmtXss=1">'
SCRIPT = "<script>window.__lmtXss=1</script>"


def markup_transcript(doc: dict) -> dict:
    """A deep copy of `doc` whose segment 0 text is IMG and segment 1 text is SCRIPT."""
    out = copy.deepcopy(doc)
    out["segments"][0]["text"] = IMG
    out["segments"][1]["text"] = SCRIPT
    return out
