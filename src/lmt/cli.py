"""The `lmt` command line: the whole public CLI in design/public-api.md.

Each subcommand names its handler as "module:function". `main` imports the module only when the
command runs, so `lmt --help` loads no heavy library. Later items replace a stub module, never this file.
"""

import argparse
import importlib
import re
import sys

from lmt import __version__
from lmt.errors import LmtError, UsageError

FORMATS = ("txt", "srt", "vtt", "json")
SPEAKERS_MIN, SPEAKERS_MAX = 1, 20

_ARGUMENT = re.compile(r"^argument (\S+): (.*)$", re.DOTALL)
_REQUIRED = re.compile(r"^the following arguments are required: ([^,\s]+)")
_UNRECOGNIZED = re.compile(r"^unrecognized arguments: (\S+)")


class Parser(argparse.ArgumentParser):
    """An ArgumentParser whose errors raise UsageError, so every usage error is one line (G1-6)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        if m := _ARGUMENT.match(message):
            raise UsageError(m.group(1).split("/")[-1], m.group(2))
        if m := _REQUIRED.match(message):
            raise UsageError(m.group(1), "required")
        if m := _UNRECOGNIZED.match(message):
            token = m.group(1)
            raise UsageError(token, "unknown option" if token.startswith("-") else "unexpected argument")
        raise UsageError("lmt", message)


def build_parser():
    parser = Parser(
        prog="lmt",
        description="Offline, speaker-labelled meeting transcription that runs entirely on your Mac.",
    )
    parser.add_argument("--version", action="version", version=f"lmt {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    t = commands.add_parser("transcribe", help="Transcribe one recording and write its transcript.",
                            description="Transcribes one recording and writes its transcript.")
    t.add_argument("input", metavar="INPUT", help="The recording to transcribe.")
    t.add_argument("-o", "--output-dir", metavar="DIR", default=".",
                   help="Where the outputs go, as <DIR>/<input stem>.<ext> (default: the current directory).")
    t.add_argument("-f", "--formats", metavar="LIST", default=",".join(FORMATS),
                   help="A comma-separated subset of txt, srt, vtt and json (default: all four).")
    t.add_argument("--num-speakers", metavar="N", type=int,
                   help="The exact number of speakers, from 1 to 20.")
    t.add_argument("--min-speakers", metavar="N", type=int, help="The least number of speakers, from 1 to 20.")
    t.add_argument("--max-speakers", metavar="N", type=int, help="The greatest number of speakers, from 1 to 20.")
    t.add_argument("--no-diarize", action="store_true",
                   help="Skip diarization: every segment is speaker S1.")
    t.add_argument("--model", metavar="NAME", help="The ASR model, one of those `lmt models list` shows.")
    t.add_argument("--offline", action="store_true", help="Never download. Exits 5 if a model is missing.")
    t.add_argument("--overwrite", action="store_true", help="Replace existing outputs.")
    t.add_argument("-v", "--verbose", action="store_true", help="Print each stage's time to stderr.")
    t.set_defaults(handler="lmt.pipeline:cli_transcribe")

    m = commands.add_parser("models", help="Download, list and verify the models.",
                            description="Downloads, lists and verifies the models.")
    m.set_defaults(handler="lmt.models:cli_models")
    actions = m.add_subparsers(dest="action", metavar="ACTION", required=True)
    d = actions.add_parser("download", help="Download the default models and verify every file.",
                           description="Downloads the default models, or the named ASR model plus the "
                                       "diarization models, and verifies every file.")
    d.add_argument("--model", metavar="NAME", help="The ASR model to download instead of the default.")
    ls = actions.add_parser("list", help="List each model in the manifest.",
                            description="Lists each model in the manifest: its name, revision, whether it's "
                                        "the default, and whether it's downloaded and verified.")
    ls.add_argument("--bytes", action="store_true",
                    help="Add each model's size on disk and the default models' total.")
    actions.add_parser("verify", help="Re-check every downloaded file's SHA-256.",
                       description="Re-checks every downloaded file's SHA-256. A file that doesn't match "
                                   "is deleted, and the command exits 5.")

    e = commands.add_parser("eval", help="Score the product on the evaluation sets (needs lmt[eval]).",
                            description="Scores the product on the evaluation sets. Needs the eval extra.")
    e.add_argument("--set", choices=("ci", "full"), help="The set to score.")
    e.add_argument("--out", metavar="DIR", default="results", help="Where the results files go "
                   "(default: results/).")
    e.add_argument("--update-readme", action="store_true",
                   help="After --set full, regenerate README.md's results table.")
    e.add_argument("--check-readme", action="store_true",
                   help="Check README.md's results table against results/full.json. Exits 6 if it differs.")
    e.set_defaults(handler="lmt.eval.cli:cli_eval")
    return parser


def validate(args):
    """Raise UsageError for what argparse can't express."""
    if args.command == "transcribe":
        _validate_transcribe(args)
    elif args.command == "eval":
        _validate_eval(args)


def _validate_transcribe(args):
    formats = args.formats.split(",")
    for f in formats:
        if f not in FORMATS:
            raise UsageError("--formats", f"{f!r} isn't one of {', '.join(FORMATS)}")
    if len(set(formats)) != len(formats):
        raise UsageError("--formats", "a format is repeated")
    args.formats = formats

    for option in ("num_speakers", "min_speakers", "max_speakers"):
        value = getattr(args, option)
        if value is not None and not SPEAKERS_MIN <= value <= SPEAKERS_MAX:
            raise UsageError(_flag(option), f"must be from {SPEAKERS_MIN} to {SPEAKERS_MAX}, not {value}")
    if args.no_diarize:
        for option in ("num_speakers", "min_speakers", "max_speakers"):
            if getattr(args, option) is not None:
                raise UsageError(_flag(option), "can't be combined with --no-diarize")
    if args.num_speakers is not None:
        for option in ("min_speakers", "max_speakers"):
            if getattr(args, option) is not None:
                raise UsageError(_flag(option), "can't be combined with --num-speakers")
    if args.min_speakers is not None and args.max_speakers is not None and args.min_speakers > args.max_speakers:
        raise UsageError("--min-speakers", f"{args.min_speakers} is more than --max-speakers {args.max_speakers}")


def _validate_eval(args):
    if args.set is None and not args.check_readme:
        raise UsageError("--set", "give --set or --check-readme")
    if args.set is not None and args.check_readme:
        raise UsageError("--check-readme", "can't be combined with --set")
    if args.update_readme and args.set != "full":
        raise UsageError("--update-readme", "only with --set full")


def _flag(dest):
    return "--" + dest.replace("_", "-")


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
        validate(args)
        module, function = args.handler.split(":")
        code = getattr(importlib.import_module(module), function)(args)
        return 0 if code is None else code
    except SystemExit as e:  # --help and --version
        return e.code if isinstance(e.code, int) else 0
    except LmtError as e:
        print(f"lmt: error: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 -- any bug is one line, never a traceback (design §1a)
        print(f"lmt: error: internal: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


def run():
    sys.exit(main())
