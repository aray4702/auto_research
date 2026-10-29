"""The transcript JSON's schema. schema/transcript.schema.json at the repository root is its published
copy, kept identical by a test (design/architecture.md, layout)."""

import json
from pathlib import Path

from lmt.errors import InputError

SCHEMA_PATH = Path(__file__).with_name("transcript.schema.json")


def validate(doc) -> None:
    """InputError("transcript", …) with the first error's message and path, if `doc` fails the schema."""
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    error = next(jsonschema.Draft202012Validator(schema).iter_errors(doc), None)
    if error is not None:
        raise InputError("transcript", f"fails the schema at {error.json_path}: {error.message}")
