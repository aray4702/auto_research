"""`lmt eval`. `--check-readme` is built (G1-14, design §6), and `--set ci` (design/internal/g1-stage2.md,
P2a); `--set full` comes with P8."""

from pathlib import Path

from lmt.errors import CheckFailed, NotBuilt
from lmt.eval import readme, runner

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
    if args.set == "ci":
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        runner.evaluate("ci", out_dir=out_dir)
        print(out_dir / "ci.json")
        return 0
    # P8 builds --set full, and with it --update-readme, which calls readme.update after the full run.
    raise NotBuilt("eval")
