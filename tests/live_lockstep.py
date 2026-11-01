"""The one way to reach lockstep from a subprocess (design/internal/g2-stage1.md §3b, item 3b3): it sets
`lmt.live.command.LOCKSTEP`, then exits with `lmt.cli.main(<its arguments>)`. It isn't part of the package.

    python tests/live_lockstep.py live --from-file X --jsonl ...
"""

import sys

import lmt.live.command
from lmt import cli

if __name__ == "__main__":
    lmt.live.command.LOCKSTEP = True
    sys.exit(cli.main(sys.argv[1:]))
