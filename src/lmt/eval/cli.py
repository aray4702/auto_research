"""`lmt eval`. `--check-readme` is built (G1-14, design §6); `--set` comes with G1 stage 2."""

from pathlib import Path

from lmt.errors import CheckFailed, NotBuilt
from lmt.eval import readme

README = Path("README.md")


def cli_eval(args):
    if args.check_readme:
        results = Path(args.out) / "full.json"
        diff = readme.check(README, results)
        if diff is not None:
            print(diff, end="")
            raise CheckFailed(str(README), f"the results table differs from {results}; "
                                           "run lmt eval --set full --update-readme")
        return 0
    # Stage 2 builds --set, and with it --update-readme, which calls readme.update after the full run.
    raise NotBuilt("eval")
