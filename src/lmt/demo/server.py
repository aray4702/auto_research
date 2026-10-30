"""`lmt demo`'s server (g3-stage1.md §2a).

A stub until G3 stage 1's items 2a2 to 2a4 build it.
"""

from lmt.demo import sample
from lmt.errors import NotBuilt


def cli_demo(args):
    if args.sample:
        sample.prepare(offline=args.offline)
    raise NotBuilt("demo", "G3 stage 1")
