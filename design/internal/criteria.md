# Internal design: the per-criterion record

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-28 | architect, #239 | First version: the `criterion` marker, the registry, each job's record, the merge into per-criterion states, and the Owner's readings |

The Owner measures progress as features (F1–F9) and requirements (Q1–Q10) met, not as items closed (#239). A feature or requirement is met when every criterion in `spec/acceptance.md` that names it is met on `main`. This file says how each criterion's check carries its ID, what CI writes on each run, and how the state of each criterion comes out of that. It covers every goal's criteria (S1, G1, G2, G3) and any later ones. #240 (the builder) reports the features and requirements met from it.

It works inside `spec/acceptance.md` and `design/architecture.md` ("CI"), and changes neither. Where this file and those disagree, they win, and this file is the one to fix. The Reviewer reviews this file. The Architect changes it.

## Overview

1. **Tests carry the IDs.** A test that checks part of a criterion carries the pytest marker `criterion("G1-6")`. A check script that isn't pytest writes the same record itself.
2. **Each job writes its record.** Each product job that runs checks uploads `criteria-<job>.json`: every tagged check it ran, with its outcome. No new job is added.
3. **A registry says what the tests can't.** `tests/criteria.toml` marks which criteria's checks are complete, and which need the Owner's reading.
4. **A merge gives each criterion's state.** `.github/scripts/criteria.py` reads the spec, the registry and the jobs' records from `main`, and writes each criterion's state. #240 runs it.
5. **The Owner's readings** are lines in the Owner's comments on the goal's release item, in a fixed form.

**Why the merge isn't a CI job.** A job that merged the records would cost about one billed minute on every gate run, about 30–60 minutes a day against the 3,000-minute signal (`design/architecture.md`, "CI"). It still couldn't give the state at `main`'s head on its own: the tier filter skips the jobs a change can't break, so a run on `main` often holds only some jobs' records. The state comes from each job's latest record on `main`, which only a reader of several runs has. Uploading a small file from the jobs that already run costs seconds.

## 1. The `criterion` marker

```python
@pytest.mark.criterion("G1-6")
class OptionTableTest(unittest.TestCase): ...

pytestmark = pytest.mark.criterion("G1-14")      # every test in the module

@pytest.mark.criterion("G1-11", "G2-8")          # one test, two criteria
def test_empty_file_exits_3(): ...
```

- **What gets tagged.** A test is tagged with a criterion when it checks part of that criterion's "How it's checked" column. Helpers' own tests (a parser's unit tests, a fixture's builder) aren't tagged. A test may carry several IDs, and a criterion may have many tests, in any job.
- **IDs** match `^S\d+-\d+$|^G\d+-\d+[mc]?$` and must be IDs in the spec's done-check tables. An unknown ID stops pytest at collection with a `pytest.UsageError` naming the test and the ID, so a tag can't silently count for nothing.
- **The marker** is registered in `pyproject.toml`'s `markers`, beside `models` and `data`: `"criterion(*ids): the acceptance criteria this test checks (design/internal/criteria.md)"`.
- **From now on,** every item that adds a check for a criterion tags it in the same PR, and the Reviewer checks the tags against the spec's row. G2's and G3's tests are tagged as their items land.

## 2. The registry, `tests/criteria.toml`

What the spec doesn't say in a form a program can read. The spec stays the source of each criterion's ID, goal and requirements; the registry never repeats them.

```toml
# Per criterion: whether its CI checks are complete, and whether it needs the Owner's reading
# (design/internal/criteria.md §2). A criterion not listed is checked in CI and not yet complete.

[S1-1]
complete = true

[G1-4m]
ci = false
owner = "The two-speaker clip's SRT and WebVTT show their cues at the right times, with the speaker's label, in QuickTime Player and in VLC"

[G1-13]
owner = "The two-speaker clip transcribes with Wi-Fi off"
```

| Key | Default | Meaning |
| --- | --- | --- |
| `ci` | `true` | The criterion has a part that a check in CI decides. |
| `complete` | `false` | The tagged checks cover the whole of the criterion's CI part, as its "How it's checked" column gives it, including any part the Reviewer reads on the PR (S1-2's wording, G1-7's reading of `public-api.md`, G3-9's image). Only meaningful when `ci` is true. |
| `owner` | none | The criterion has a part the Owner reads by eye or by ear, recorded on the goal's release item (§5). The text says what the Owner checks, in a line. |

- **`complete` is set** by the PR that finishes a criterion's CI checks, and the Reviewer checks it against the spec's row. It goes back to `false` if the spec adds to the criterion. Until it is set, passing tests give `partial`, not `pass`: G1-6 has its help and option-table tests today, but not yet a test of each option's effect, so it isn't met.
- **`ci = false` needs `owner`.** A criterion with neither a CI part nor an Owner part can't be met, so the registry test rejects it.
- **The Mac's figures are checked in CI, from the committed results file.** G1-8, G1-9, G1-10's memory, G1-12's Mac half, G1-14's figures and G2-6 are measured on the Owner's Mac, and the spec puts their results files in `results/` by PR. A test in the `test` job reads the committed file against the criterion's bound, so these criteria have `ci = true`. The measurement is still the Mac's; CI only reads it. While the file holds no figures (the placeholder, design `g1-stage1.md` §6), the test skips with that reason, and the criterion is `waiting` (§4). The items that write those files add these tests: G1's stage 3 and G2's `--set live`.

## 3. Each job's record

Each product job that runs tagged checks uploads one record per run, passed or failed:

```yaml
- if: always()
  uses: actions/upload-artifact@<pinned SHA>
  with:
    name: criteria-<job>
    path: criteria-<job>.json
    retention-days: 90
    if-no-files-found: error
```

The upload step goes last in its job. `readme` and `test` gain it now. `test-models` and `eval-ci` have no tagged checks yet, so the items that add their pytest steps (#108, #110) add the step with them, as do `browser` and `install-full` (G3) when they land. A job skipped by its tier uploads nothing, and the merge then uses its latest earlier record (§4).

**The format,** `criteria-<job>.json`:

```json
{
  "format": 1,
  "job": "test",
  "commit": "<GITHUB_SHA>",
  "ref": "<GITHUB_REF>",
  "event": "<GITHUB_EVENT_NAME>",
  "run_id": 123456789,
  "run_attempt": 1,
  "tier": 2,
  "checks": [
    {"check": "tests/test_cli.py::OptionTableTest::test_parser_matches_public_api", "criteria": ["G1-6"], "outcome": "passed"},
    {"check": "tests/test_results.py::test_full_set_bounds", "criteria": ["G1-8"], "outcome": "skipped",
     "reason": "results/full.json has no figures yet (the Owner's run of lmt eval --set full)"}
  ]
}
```

- **`checks`** lists every tagged check the job ran, and only those. `outcome` is `passed`, `failed`, `skipped` (with `reason`) or `not-run` (with `reason`).
- **The run's fields** come from the environment: `GITHUB_SHA`, `GITHUB_REF`, `GITHUB_EVENT_NAME`, `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`, and the tier from `LMT_TIER`, which the step sets from `needs.changes.outputs.tier` (`env: LMT_TIER: ${{ needs.changes.outputs.tier }}`). Outside CI they are `null`.

**pytest** writes it through a plugin in `tests/conftest.py`, with the option `--criteria-out PATH`. Each job's pytest step gains `--criteria-out criteria-<job>.json`.

- A test's outcome is `failed` if any phase (setup, call or teardown) failed or errored, `skipped` if it was skipped (its reason is pytest's skip reason), and otherwise `passed`. `xfail` and `xpass` count as `failed`: a criterion is met only by tests that pass outright.
- Deselected tests aren't listed. The `test` job deselects the `models` and `data` tests, and the jobs that run them list them.
- The plugin reads the spec's IDs with `criteria.py`'s parser (§4), loaded from `.github/scripts/criteria.py` by path, so there is one parser.

**A check that isn't pytest** writes the same format itself, with the same `--criteria-out PATH` option:

- **`check_readme.py`** writes one check per row of its `CHECKS` table (`check_readme.py::S1-1 title`, …), each with its criterion. With `--no-install` (tier 0), S1-3 and S1-4 are `not-run`, with the reason "tier 0 runs no install". Both of the `readme` job's steps that run it pass the option; only one of them runs in a given run.
- **Later scripts** (the licence check, G1-15; `check_release.py`, G3-10) do the same, or are wrapped in a tagged pytest test. A job whose checks span several programs writes one record per program, `criteria-<job>-<n>.json`, uploaded under one artifact name with a glob, and the merge treats them as one.
- **Job steps that check a criterion** without a program of their own, such as `eval-ci`'s double run (G1-5) and its bounds (G1-8c), become tagged `data` tests that read what the steps wrote. The stage-2 item that adds them designs them so.

## 4. The merge: each criterion's state

`.github/scripts/criteria.py`, standard library only (`tomllib`, `json`, `re`), Python 3.11 or later:

```
python3 -I .github/scripts/criteria.py --spec spec/acceptance.md --registry tests/criteria.toml [--ref REF] [--out FILE] RECORD...
```

It reads the spec and the registry from the checkout it's given, and any number of job records from `main`'s gate runs. It writes `criteria.json` (below) to `--out`, or to stdout. It exits 0 when it wrote the state, and 3 with one `criteria.py: error:` line on stderr when an input is missing or malformed: a record of another `format`, an ID in a record or the registry that isn't in the spec, or a registry key it doesn't know.

**The spec's criteria.** Each goal's section is a level-2 heading, `## <goal>: …`. Under its `### Done-check`, each table row whose first cell is an ID is a criterion. Its requirements are the "Requirement" column's cell split on `, ` (`"§7, Q9"` gives `["§7", "Q9"]`). A table with no "Requirement" column (S1's) gives the goal as the requirement (`["S1"]`).

**Which records count.** Only records whose `ref` is `--ref`'s, `refs/heads/main` by default; a PR's own records are read with `--ref refs/pull/<n>/merge`. Records are grouped by `job` and ordered by `(run_id, run_attempt)`.

- **The job's current checks** are those in its latest record. A check missing from it no longer counts, so a deleted or renamed test drops out.
- **A `not-run` check** takes its outcome from the latest earlier record of the same job in which it ran. If there is none, it stays `not-run`.

Taking each job's latest record is sound because of the tier filter: a change runs every check it could break (`design/architecture.md`, "CI"), so a job that a later commit skipped would have given the same result at that commit. A failure on `main` fails the gate, so it shows in the next run's record too.

**The state of a criterion's CI part** (`ci`), from its tagged checks across the jobs, taking the first rule that applies:

| State | When |
| --- | --- |
| `not-in-ci` | The registry gives `ci = false`. |
| `no-check` | No current check carries the ID. |
| `fail` | A check carrying it `failed`. |
| `not-run` | A check carrying it has never run on `main` (it is `not-run` with no earlier outcome). |
| `waiting` | A check carrying it was `skipped`: it ran, but what it checks isn't there yet, such as the Mac's figures. The reasons are listed. |
| `partial` | Every check carrying it passed, and the registry doesn't give `complete = true`. |
| `pass` | Every check carrying it passed, and the registry gives `complete = true`. |

**The output,** `criteria.json`:

```json
{
  "format": 1,
  "criteria": [
    {"id": "G1-6", "goal": "G1", "requirements": ["F5"], "ci": "partial",
     "checks": {"passed": 31, "failed": 0, "skipped": 0, "not-run": 0},
     "reasons": [], "commits": ["<sha>"], "owner": null},
    {"id": "G1-4m", "goal": "G1", "requirements": ["F3"], "ci": "not-in-ci",
     "checks": {"passed": 0, "failed": 0, "skipped": 0, "not-run": 0},
     "reasons": [], "commits": [],
     "owner": "The two-speaker clip's SRT and WebVTT show their cues at the right times, with the speaker's label, in QuickTime Player and in VLC"}
  ]
}
```

Criteria come in the spec's order. `commits` are the commits of the records the criterion's outcomes came from, so a reader can say when each was measured. `owner` is the registry's text, or `null`.

**Met.** A criterion is met when its `ci` is `pass` or `not-in-ci`, and, if it has an `owner` part, the Owner's latest reading of it is `pass` (§5). A feature or requirement is met when every criterion naming it is met. The merge reports the CI part only; #240 adds the readings and counts the features and requirements.

## 5. The Owner's readings

The spec has the Owner record each reading by eye or by ear on the goal's release item (G1-4m, G1-13's Wi-Fi run, G2-5m, G3-5m, G3-8m). So that a program can read it, a reading is a line of its own in a comment by the Owner on that item:

```
G1-4m: pass
G1-13: fail: VLC showed no labels
```

- The ID, a colon, then `pass` or `fail`, in any case. After `fail`, a colon and what was wrong.
- Only the Owner's own posts count: by `aray4702`, without a role marker (`system/rules.md`, "Authority"). For each ID, the latest such line counts, so the Owner corrects a reading by posting it again.
- A reading made before the release item exists waits for it, since the spec names the release item. The Coordinator, opening a goal's release item, lists the readings it needs in this form, so the Owner can copy each line.

## Items

| Item | What | Files |
| --- | --- | --- |
| **Developer** (the Coordinator opens it after this merges, in G1.1) | The marker and plugin; the registry; the tags on the existing tests; `check_readme.py --criteria-out`; `criteria.py` and its tests; the upload steps in `product.yml` | `pyproject.toml` (`markers`), `tests/conftest.py`, `tests/criteria.toml`, `tests/test_criteria.py`, `tests/test_cli.py`, `tests/test_readme_table.py`, `.github/scripts/criteria.py`, `.github/scripts/test_criteria.py`, `.github/scripts/check_readme.py`, `.github/scripts/test_check_readme.py`, `.github/workflows/product.yml` (`readme` and `test` jobs) |
| **#240** (builder) | Report the features and requirements met: download `main`'s `criteria-*` artifacts, run `criteria.py`, read the Owner's readings, and show the result in the product audit, the goal issues and `arsys status` | system files only |

**The Developer item's first registry.** Every `owner` part above. `complete = true` for S1-1, S1-2, S1-3 and S1-5, whose checks `check_readme.py` makes in full and whose README the Reviewer read when S1 closed. S1-4 isn't complete: the README doesn't yet name the evaluation command, which stage 2 adds (`g1-stage1.md` §1a). The tags: `tests/test_cli.py`'s help, option-table and usage-error tests carry `G1-6`; `tests/test_readme_table.py` carries `G1-14`. G1-6 and G1-14 aren't complete: G1-6 lacks the options' effects, and G1-14 the Mac's figures.

**Tests of the Developer item:**
- `tests/test_criteria.py`: every registry key is a spec ID, and every entry's keys are known; `ci = false` comes with `owner`; `complete` isn't set where `ci` is false. A tag with an unknown ID, in a temporary test module run through `pytest.main`, fails collection naming it. The plugin's record for a temporary module with a passing, a failing, an erroring, a skipped and an `xfail` test gives `passed`, `failed`, `failed`, `skipped` with its reason, and `failed`.
- `.github/scripts/test_criteria.py`: the parser on the real spec finds 44 criteria (S1 5, G1 17, G2 10, G3 12), with G1-15's requirements `["§7", "Q9"]` and S1-1's `["S1"]`; on a small spec, each state in §4's table from records built for it, rule by rule; a `not-run` check taking its earlier outcome; a record from a branch other than `main` ignored; a check dropped from the latest record dropping out; exit 3 for each malformed input.
- `test_check_readme.py`: the record from `check_readme.py`, with and without `--no-install`.
- On the PR: the `readme` and `test` jobs upload `criteria-readme` and `criteria-test`, and `criteria.py --ref refs/pull/<n>/merge` on those two records gives `partial` for G1-6 and G1-14, `pass` for S1-1, S1-2, S1-3 and S1-5, `partial` for S1-4, and `no-check` for criteria without tests. The PR shows that output.
