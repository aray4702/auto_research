"""The tuning entry point (design/internal/g2-stage1.md §3c, item 3c3): `lmt` with the live stages' settings
replaced, for stage 2's sweep. Internal, like `python -m lmt.eval.datasets`, and not in the public API.

    python -m lmt.live.tuning [--set diar.latency=24000] ... -- live --from-file X --jsonl ...

Each `--set` replaces one field of `DEFAULT_DIAR_CONFIG` with `dataclasses.replace`, its value parsed as the
field's type. The command reads it through its module when it builds the stages, so the replacements reach it. The
effective settings go to stderr, then `lmt.cli.main` runs the arguments after `--`. Streaming ASR has no settings
since G4: the engine's stream commits each piece as it comes (design/internal/g4-stage1.md §2).
"""

import argparse
import dataclasses
import sys

from lmt import cli
from lmt.live import diarize

# The prefix of a `--set` key, and the module whose default settings it replaces, by their attribute's name.
CONFIGS = {"diar": (diarize, "DEFAULT_DIAR_CONFIG")}


def _override(parser, setting: str) -> None:
    """Applies one `<stage>.<field>=<value>`, or exits 2 through `parser.error`."""
    key, sep, value = setting.partition("=")
    prefix, _, name = key.partition(".")
    if not sep or prefix not in CONFIGS:
        parser.error(f"--set {setting!r}: expected diar.<field>=<value>")
    module, attribute = CONFIGS[prefix]
    config = getattr(module, attribute)
    fields = {f.name for f in dataclasses.fields(config)}
    if name not in fields:
        parser.error(f"--set {setting!r}: unknown key {key!r}; {prefix} has {', '.join(sorted(fields))}")
    kind = type(getattr(config, name))  # every field has a default, so its value's type is the field's
    try:
        parsed = kind(value)
    except ValueError:
        parser.error(f"--set {setting!r}: {value!r} is not {'an' if kind is int else 'a'} {kind.__name__}")
    setattr(module, attribute, dataclasses.replace(config, **{name: parsed}))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(prog="python -m lmt.live.tuning",
                                     usage="%(prog)s [--set KEY=VALUE ...] -- LMT_ARGS ...",
                                     description="Run lmt with the live stages' settings replaced.")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="diar.<field>=<value>; may be repeated.")
    if "--" not in argv:
        parser.error("expected -- and the lmt arguments")
    split = argv.index("--")
    args = parser.parse_args(argv[:split])
    for setting in args.set:
        _override(parser, setting)
    print(f"lmt: tuning: {diarize.DEFAULT_DIAR_CONFIG}", file=sys.stderr)
    return cli.main(argv[split + 1:])


if __name__ == "__main__":
    sys.exit(main())
