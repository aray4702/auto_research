"""The errors the product means to raise, each with its exit code (design/public-api.md, "Exit codes").

`str(e)` is "<subject>: <problem>", where the subject is the file or option at fault, so the
CLI prints one line: `lmt: error: <subject>: <problem>`.
"""


class LmtError(Exception):
    """The base. Not raised directly."""

    exit_code = 1

    def __init__(self, subject: str, problem: str):
        super().__init__(f"{subject}: {problem}")
        self.subject = subject
        self.problem = problem


class NotBuilt(LmtError):
    """A command whose item hasn't landed yet. It goes when the stage it names closes."""

    def __init__(self, command: str, stage: str = "G1 stage 2"):
        super().__init__(command, f"not built yet ({stage})")


class UsageError(LmtError):
    """An unknown option, a bad value, conflicting options, or an existing output without --overwrite."""

    exit_code = 2


class InputError(LmtError):
    """A missing, unreadable, empty, corrupt or unsupported input, or an unwritable output directory."""

    exit_code = 3


class NoSpeechError(LmtError):
    """No speech found in the input."""

    exit_code = 4


class ModelError(LmtError):
    """A model missing with --offline, a failed download, or a checksum mismatch."""

    exit_code = 5


class CheckFailed(LmtError):
    """`lmt eval --check-readme` found that the README's results table differs."""

    exit_code = 6
