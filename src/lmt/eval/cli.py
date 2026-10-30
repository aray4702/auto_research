"""`lmt eval`: `--check-readme` (G1-14, design §6), `--set ci` and `--out` (design/internal/g1-stage2.md,
P2a), `--set full` and `--update-readme` (P8), and `--set live` (design/internal/g2-stage1.md §5, item 5d)."""

from pathlib import Path

from lmt.errors import CheckFailed
from lmt.eval import live, readme, runner

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
    if args.set == "live":
        return live.cli_eval_live(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.set == "ci":
        runner.evaluate("ci", out_dir=out_dir)
    else:
        runner.evaluate_full(out_dir, update_readme=args.update_readme)
    print(out_dir / f"{args.set}.json")
    return 0
