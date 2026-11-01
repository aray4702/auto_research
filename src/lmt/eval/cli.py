"""`lmt eval`: `--check-readme` (G1-14, design §6), `--set ci` and `--out` (design/internal/g1-stage2.md,
P2a), `--set full` and `--update-readme` (P8), `--set live` (design/internal/g2-stage1.md §5, item 5d), and
`--engine` (G4, public-api.md "G4's changes to the commands")."""

from pathlib import Path

from lmt.errors import CheckFailed
from lmt.eval import live, readme, report, runner

README = Path("README.md")


def cli_eval(args):
    if args.check_readme:
        diff = readme.check(README, Path(args.out))
        if diff is not None:
            print(diff, end="")
            raise CheckFailed(str(README), f"the results table differs from the results files in {args.out}; "
                                           "run lmt eval --set full --update-readme")
        return 0
    if args.set == "live":
        return live.cli_eval_live(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.set == "ci":
        runner.evaluate("ci", out_dir=out_dir, engine=args.engine)
    else:
        runner.evaluate_full(out_dir, update_readme=args.update_readme, engine=args.engine)
    print(out_dir / report.file_name(args.set, args.engine))
    return 0
