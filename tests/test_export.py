"""The JSON export, the schema and writing outputs (design/internal/g1-slice.md, S4)."""

import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lmt import export, schema
from lmt.errors import InputError, NotBuilt, UsageError
from lmt.transcript import SCHEMA_VERSION, Segment, Speaker, Transcript, Word, from_dict, to_dict

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "export" / "transcript.json"
FAKES = {"txt": lambda t: "text\n", "srt": lambda t: "srt\n", "vtt": lambda t: "WEBVTT\n"}


def small():
    """Two speakers, two segments, text that isn't ASCII."""
    return Transcript(
        SCHEMA_VERSION, "0.1.0", {"file": "meeting.wav", "duration": 4.0},
        {"asr": {"name": "distil-large-v3", "repository": "Systran/faster-distil-whisper-large-v3",
                 "revision": "0123abc"},
         "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1",
                         "revision": "4567def"}},
        (Speaker("S1", "Speaker 1"), Speaker("S2", "Speaker 2")),
        (Segment(0.48, 1.2, "S1", "Shall we?", (Word(0.48, 0.71, "Shall", 0.97), Word(0.75, 1.2, "we?", 0.9))),
         Segment(2.5, 3.25, "S2", "Sí, José.", (Word(2.5, 2.8, "Sí,", 0.812), Word(2.85, 3.25, "José.", 0.66)))),
    )


def without(doc, path):
    doc = copy.deepcopy(doc)
    *parents, key = path
    target = doc
    for p in parents:
        target = target[p]
    del target[key]
    return doc


def with_value(doc, path, value):
    doc = copy.deepcopy(doc)
    *parents, key = path
    target = doc
    for p in parents:
        target = target[p]
    target[key] = value
    return doc


class JsonExportTest(unittest.TestCase):
    def test_golden(self):
        self.assertEqual(export.render(small(), "json").encode("utf-8"), GOLDEN.read_bytes())

    def test_golden_validates_and_reads_back(self):
        doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
        schema.validate(doc)
        self.assertEqual(from_dict(doc), small())

    def test_unregistered_format_is_not_built(self):
        with mock.patch.dict(export.RENDERERS, clear=True), self.assertRaises(NotBuilt) as caught:
            export.render(small(), "json")
        self.assertEqual(caught.exception.subject, "--formats")

    def test_formats(self):
        self.assertEqual(export.FORMATS, ("txt", "srt", "vtt", "json"))


class SchemaTest(unittest.TestCase):
    REQUIRED = (
        ("schema_version",), ("source",), ("models",), ("speakers",), ("segments",),
        ("source", "file"), ("source", "duration"), ("models", "asr"), ("models", "diarization"),
        ("models", "asr", "name"), ("models", "asr", "repository"), ("models", "asr", "revision"),
        ("models", "diarization", "revision"), ("speakers", 0, "id"), ("speakers", 0, "label"),
        ("segments", 0, "start"), ("segments", 0, "end"), ("segments", 0, "speaker"), ("segments", 0, "text"),
        ("segments", 0, "words", 0, "start"), ("segments", 0, "words", 0, "end"),
        ("segments", 0, "words", 0, "text"), ("segments", 0, "words", 0, "probability"),
    )

    def setUp(self):
        self.doc = to_dict(small())

    def assertInvalid(self, doc):
        with self.assertRaises(InputError) as caught:
            schema.validate(doc)
        self.assertEqual((caught.exception.subject, caught.exception.exit_code), ("transcript", 3))
        return caught.exception.problem

    def test_valid(self):
        schema.validate(self.doc)

    def test_missing_required_field(self):
        for path in self.REQUIRED:
            with self.subTest(path=path):
                self.assertInvalid(without(self.doc, path))

    def test_error_names_the_path(self):
        problem = self.assertInvalid(without(self.doc, ("speakers", 1, "label")))
        self.assertEqual(problem, "fails the schema at $.speakers[1]: 'label' is a required property")

    def test_optional_fields(self):
        schema.validate(without(self.doc, ("lmt_version",)))
        schema.validate(without(self.doc, ("segments", 0, "words")))
        schema.validate(with_value(self.doc, ("models", "diarization"), None))
        schema.validate(with_value(self.doc, ("source", "live"), True))
        schema.validate(with_value(self.doc, ("source", "device"), "MacBook Pro Microphone"))
        schema.validate(with_value(self.doc, ("unknown",), {"readers": "ignore it"}))
        schema.validate(with_value(self.doc, ("segments", 0, "unknown"), 1))

    def test_bad_values(self):
        for path, value in [(("schema_version",), "2.0"), (("schema_version",), "1"), (("schema_version",), 1.1),
                            (("schema_version",), "1.0\n"),
                            (("lmt_version",), None), (("source", "live"), False), (("source", "device"), 3),
                            (("source", "duration"), -1), (("source", "file"), None),
                            (("segments", 0, "start"), -0.001), (("segments", 0, "end"), "1.2"),
                            (("models", "asr"), None), (("segments", 0, "text"), None)]:
            with self.subTest(path=path, value=value):
                self.assertInvalid(with_value(self.doc, path, value))

    def test_bad_speaker_id(self):
        for value in ("S0", "s1", "S01", "S", "1", "Speaker 1", "S1 ", "S1\n"):
            for path in (("speakers", 0, "id"), ("segments", 0, "speaker")):
                with self.subTest(value=value, path=path):
                    self.assertInvalid(with_value(self.doc, path, value))

    def test_bad_label(self):
        for value in ("", " Ana", "Ana ", " Ana", "A<b", "a>b", "{x}", "a=b", "x" * 33, "tab\there",
                      "\x00", "bell\x07", "del\x7f", "nel\x85x", "Ana\n", None):
            with self.subTest(value=value):
                self.assertInvalid(with_value(self.doc, ("speakers", 0, "label"), value))

    def test_good_label(self):
        for value in ("Speaker 1", "José & Ana", "A", "x" * 32, "Dr. Ng (PM)", "Ana  Ng", "李"):
            with self.subTest(value=value):
                schema.validate(with_value(self.doc, ("speakers", 0, "label"), value))

    def test_no_pattern_ends_with_dollar(self):
        """Python's `$` also matches before a final newline, which ECMA-262's doesn't (#286)."""
        def patterns(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "pattern":
                        yield value
                    else:
                        yield from patterns(value)
            elif isinstance(node, list):
                for value in node:
                    yield from patterns(value)

        found = list(patterns(json.loads(schema.SCHEMA_PATH.read_text(encoding="utf-8"))))
        self.assertEqual(len(found), 3)
        for pattern in found:
            with self.subTest(pattern=pattern):
                self.assertFalse(pattern.endswith("$"))
                self.assertTrue(pattern.endswith(r"(?![\s\S])"))

    def test_copies_identical(self):
        self.assertEqual((ROOT / "schema" / "transcript.schema.json").read_bytes(), schema.SCHEMA_PATH.read_bytes())
        self.assertEqual(json.loads(schema.SCHEMA_PATH.read_text(encoding="utf-8"))["$schema"],
                         "https://json-schema.org/draft/2020-12/schema")


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(export.RENDERERS, FAKES))

    def test_four_paths_in_formats_order(self):
        formats = ["vtt", "json", "txt", "srt"]
        paths = export.write(small(), self.dir, "m", formats, overwrite=False)
        self.assertEqual(paths, [self.dir / f"m.{fmt}" for fmt in formats])
        self.assertEqual(paths[1].read_bytes(), GOLDEN.read_bytes())
        self.assertEqual(paths[0].read_text(encoding="utf-8"), "WEBVTT\n")
        self.assertEqual(sorted(os.listdir(self.dir)), ["m.json", "m.srt", "m.txt", "m.vtt"])  # no temporary file

    def test_check_returns_the_paths_and_writes_nothing(self):
        paths = export.check(self.dir, "m", ["json", "txt"], overwrite=False)
        self.assertEqual(paths, [self.dir / "m.json", self.dir / "m.txt"])
        self.assertEqual(os.listdir(self.dir), [])

    def test_existing_output_without_overwrite(self):
        (self.dir / "m.srt").write_text("old")
        with self.assertRaises(UsageError) as caught:
            export.write(small(), self.dir, "m", ["txt", "srt", "json"], overwrite=False)
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertEqual(str(caught.exception), f"{self.dir / 'm.srt'}: exists; use --overwrite to replace it")
        self.assertEqual(os.listdir(self.dir), ["m.srt"])
        self.assertEqual((self.dir / "m.srt").read_text(), "old")

    def test_existing_output_with_overwrite(self):
        (self.dir / "m.srt").write_text("old")
        export.write(small(), self.dir, "m", ["srt"], overwrite=True)
        self.assertEqual((self.dir / "m.srt").read_text(), "srt\n")

    def test_missing_directory(self):
        missing = self.dir / "missing"
        with self.assertRaises(InputError) as caught:
            export.write(small(), missing, "m", ["json"], overwrite=False)
        self.assertEqual((caught.exception.exit_code, caught.exception.subject), (3, str(missing)))
        self.assertFalse(missing.exists())

    def test_file_as_directory(self):
        (self.dir / "f").write_text("")
        with self.assertRaises(InputError) as caught:
            export.write(small(), self.dir / "f", "m", ["json"], overwrite=False)
        self.assertEqual(caught.exception.exit_code, 3)

    @unittest.skipIf(os.geteuid() == 0, "root can write to any directory")
    def test_unwritable_directory(self):
        locked = self.dir / "locked"
        locked.mkdir(mode=0o500)
        self.addCleanup(locked.chmod, 0o700)
        with self.assertRaises(InputError) as caught:
            export.write(small(), locked, "m", ["json"], overwrite=False)
        self.assertEqual((caught.exception.exit_code, caught.exception.subject), (3, str(locked)))

    def test_failed_renderer_leaves_no_file(self):
        for error in (RuntimeError("boom"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                def fail(t, error=error):
                    raise error
                with mock.patch.dict(export.RENDERERS, {"srt": fail}), self.assertRaises(type(error)):
                    export.write(small(), self.dir, "m", ["json", "txt", "srt", "vtt"], overwrite=False)
                self.assertEqual(os.listdir(self.dir), [])

    def umask(self, mask):
        """Sets the process's umask for this test, restoring the old one after."""
        self.addCleanup(os.umask, os.umask(mask))

    def test_mode_under_umask_022(self):
        """What `open(path, "w")` gives a new file, an overwritten 0o600 output included (#286)."""
        self.umask(0o022)
        (self.dir / "m.srt").write_text("old")
        (self.dir / "m.srt").chmod(0o600)
        paths = export.write(small(), self.dir, "m", list(export.FORMATS), overwrite=True)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_mode_under_umask_077(self):
        self.umask(0o077)
        paths = export.write(small(), self.dir, "m", list(export.FORMATS), overwrite=False)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
