"""`lmt live`'s parser and its usage checks, before the command is built (design/internal/g2-stage1.md §3b, 3b1)."""

import contextlib
import io
import sys
import unittest
from unittest import mock

import pytest
from fake_sounddevice import FakeSoundDevice, device

from lmt import cli


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def parsed(*argv):
    args = cli.build_parser().parse_args(["live", *argv])
    cli.validate(args)
    return args


@pytest.mark.criterion("G2-7")
class LiveHelpTest(unittest.TestCase):
    def test_help_exits_0(self):
        code, out, err = run("live", "--help")
        self.assertEqual(code, 0, err)
        self.assertIn("usage: lmt live", out)


@pytest.mark.criterion("G2-7")
class LiveUsageErrorTest(unittest.TestCase):
    def assertUsageError(self, argv, subject):
        code, out, err = run("live", *argv)
        self.assertEqual(code, 2, err)
        self.assertEqual(out, "")
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertTrue(err.startswith(f"lmt: error: {subject}: "), err)

    def test_formats(self):
        for value in ("txt,docx", "txt,", "srt,txt,srt"):
            with self.subTest(value=value):
                self.assertUsageError(["-f", value], "--formats")

    def test_max_speakers_out_of_range(self):
        for value in ("0", "21", "-1"):
            with self.subTest(value=value):
                self.assertUsageError(["--max-speakers", value], "--max-speakers")

    def test_max_speakers_not_an_integer(self):
        self.assertUsageError(["--max-speakers", "two"], "--max-speakers")

    def test_no_diarize_with_max_speakers(self):
        self.assertUsageError(["--no-diarize", "--max-speakers", "2"], "--max-speakers")

    def test_device_with_from_file(self):
        self.assertUsageError(["--device", "1", "--from-file", "a.wav"], "--device")

    def test_duration_not_above_0(self):
        for value in ("0", "-3", "nan", "inf", "four"):
            with self.subTest(value=value):
                self.assertUsageError(["--duration", value], "--duration")

    def test_name_not_a_plain_file_name(self):
        for value in ("", "a/b", "/abs", ".hidden", ".."):
            with self.subTest(value=value):
                self.assertUsageError(["--name", value], "--name")

    def test_engine_not_one_of_the_engines(self):
        for value in ("whisper", "Kyutai", ""):
            with self.subTest(value=value):
                self.assertUsageError(["--engine", value], "--engine")

    def test_the_old_model_option_is_unknown(self):
        self.assertUsageError(["--model", "distil-large-v3"], "--model")

    def test_unknown_option(self):
        self.assertUsageError(["--num-speakers", "2"], "--num-speakers")


@pytest.mark.criterion("G2-7")
class LiveValidTest(unittest.TestCase):
    def test_defaults(self):
        args = parsed()
        self.assertEqual(vars(args), {
            "command": "live", "handler": "lmt.live.command:cli_live",
            "output_dir": ".", "name": None, "formats": ["txt", "srt", "vtt", "json"], "device": None,
            "list_devices": False, "from_file": None, "duration": None, "max_speakers": None,
            "no_diarize": False, "engine": "kyutai", "save_audio": False, "jsonl": False, "offline": False,
            "overwrite": False, "verbose": False,
        })

    def test_every_option_valid(self):
        args = parsed("-o", "out", "-n", "meeting", "-f", "json,txt", "--from-file", "a.wav", "--duration", "4.5",
                      "--max-speakers", "20", "--engine", "apple", "--save-audio", "--jsonl",
                      "--offline", "--overwrite", "-v")
        self.assertEqual((args.output_dir, args.name, args.formats, args.from_file, args.duration),
                         ("out", "meeting", ["json", "txt"], "a.wav", 4.5))
        self.assertEqual((args.max_speakers, args.engine), (20, "apple"))
        self.assertTrue(all((args.save_audio, args.jsonl, args.offline, args.overwrite, args.verbose)))

    def test_device_and_no_diarize(self):
        args = parsed("--device", "MacBook Pro Microphone", "--no-diarize")
        self.assertEqual((args.device, args.no_diarize), ("MacBook Pro Microphone", True))

    def test_list_devices_skips_the_checks(self):
        args = parsed("--list-devices", "-f", "docx", "--max-speakers", "0", "--no-diarize", "--device", "1",
                      "--from-file", "a.wav", "--duration", "0", "--name", "a/b")
        self.assertTrue(args.list_devices)

    def test_list_devices_reaches_the_handler(self):
        # validate doesn't reject it: the handler lists the stand-in's devices (0), not a UsageError (2).
        with mock.patch.dict(sys.modules, {"sounddevice": FakeSoundDevice([device("Microphone", 1)])}):
            code, out, err = run("live", "--list-devices", "--no-diarize", "--max-speakers", "3")
        self.assertEqual((code, out), (0, "0*\tMicrophone\t1 ch\t48000 Hz\n"), err)


if __name__ == "__main__":
    unittest.main()
