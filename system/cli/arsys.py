#!/opt/homebrew/bin/python3 -I
"""arsys: the Owner's CLI and dashboard for this repository's development system (blueprint §7).

  arsys status                 everything waiting on you, PRs, work order, usage, health, merges
  arsys progress               features and requirements: status, start, due and cost (also the dashboard's Progress tab)
  arsys decisions              your pending questions in full
  arsys answer N TEXT          answer the question on #N, as you (unmarked): the asker is started
  arsys approve PR             open the PR's review page, for approving from your second account
  arsys merge PR               merge a PR (it must already be approved and green)
  arsys pause | resume         throw or clear the kill switch (AGENTS_PAUSED)
  arsys goal add ID GOAL OUTCOME [--deadline D]          propose a goal, by PR
  arsys req add ID TEXT [--priority P] [--use-case U] [--hint H]   propose a functional requirement, by PR
  arsys merges                 merges made from your account, to recognise
  arsys audit [product|system] run the audits now and post their reports (also daily at 07:30, and `audit` in Discord)
  arsys serve [--port 8765]    the dashboard at http://127.0.0.1:PORT (this Mac only, read-only)

Agents never run this: its actions are the Owner's (blueprint §5). Standard library only.
"""

import argparse
import http.server
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.path.realpath(__file__)).parents[2]
sys.path.insert(0, str(ROOT))
from system.cli import data, views  # noqa: E402
from system.dispatcher import killswitch, run  # noqa: E402
from system.tools import post, runner  # noqa: E402


def confirm(prompt, assume_yes=False):
    if assume_yes:
        return True
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def gh_run(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


# --- the requirements file -------------------------------------------------------------------


def table_bounds(lines, heading):
    """(first, last) line indexes of the first table after a heading."""
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    first = next(i for i in range(start, len(lines)) if lines[i].startswith("|"))
    last = first
    while last + 1 < len(lines) and lines[last + 1].startswith("|"):
        last += 1
    return first, last


def add_row(text, heading, row_id, cells):
    """Put a row into a requirements table: replace the template's empty row for the same ID, or append.
    Refuses an ID that already holds content: IDs are never reused (requirements file)."""
    lines = text.split("\n")
    first, last = table_bounds(lines, heading)
    row = "| " + " | ".join([row_id, *cells]) + " |"
    for i in range(first + 2, last + 1):
        existing = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if existing[0].strip("~") == row_id:
            if any(c and c not in ("must", "U1") for c in existing[1:]):
                raise SystemExit(f"{row_id} is already used; IDs are never reused. Pick a new one.")
            lines[i] = row
            return "\n".join(lines)
    lines.insert(last + 1, row)
    return "\n".join(lines)


def bump_version(text, change, when):
    lines = text.split("\n")
    first, last = table_bounds(lines, "| Version")
    previous = lines[last].strip().strip("|").split("|")[0].strip()
    major, minor = previous.split(".")
    lines.insert(last + 1, f"| {major}.{int(minor) + 1}     | {when} | Owner, with arsys | {change} |")
    return "\n".join(lines)


def propose_requirements_change(config, edit, title, branch, body):
    """Open a PR, as the Owner, changing project_requirements.md: in a scratch worktree, never a checkout in use."""
    checkout = runner.home(config) / "system"
    with tempfile.TemporaryDirectory() as d:
        wt = Path(d, "wt")
        with run.locked(checkout):
            subprocess.run(["git", "fetch", "--quiet", "origin", "main"], cwd=checkout, check=True)
            subprocess.run(["git", "worktree", "add", "--quiet", "-b", branch, str(wt), "origin/main"], cwd=checkout, check=True)
        try:
            path = wt / "project_requirements.md"
            path.write_text(edit(path.read_text()))
            subprocess.run(["git", "commit", "--quiet", "-am", title], cwd=wt, check=True)
            subprocess.run(["git", "push", "--quiet", "-u", "origin", branch], cwd=wt, check=True, capture_output=True)
        finally:
            with run.locked(checkout):
                subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=checkout, capture_output=True)
                subprocess.run(["git", "branch", "-D", branch], cwd=checkout, capture_output=True)
    gh = post.GitHub(config["identity"]["repo"])
    pr = gh.api("POST", f"repos/{gh.repo}/pulls", {"title": title, "head": branch, "base": "main", "body": body})
    return pr["html_url"]


# --- the dashboard ---------------------------------------------------------------------------


def serve(config, port):
    # Per tab. None, not 0.0: monotonic time starts near zero on a freshly booted machine.
    pages = {"/": lambda: views.html_page(data.collect(config)), "/progress": lambda: views.progress_page(data.progress(config))}
    cache = {path: {"at": None, "page": ""} for path in pages}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/favicon.svg":
                body, kind = views.ICON_SVG.encode(), "image/svg+xml"
            elif self.path in ("/index.html", *pages):
                c = cache["/" if self.path == "/index.html" else self.path]
                if c["at"] is None or time.monotonic() - c["at"] > 30:  # reading never slows the agents: at most one refresh per 30 s
                    c["page"], c["at"] = pages["/" if self.path == "/index.html" else self.path](), time.monotonic()
                body, kind = c["page"].encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if kind.startswith("text/html") else "max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # read-only: actions are the CLI's, with confirmation
            self.send_error(405)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Dashboard: http://127.0.0.1:{port}  (this Mac only; Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# --- command line ------------------------------------------------------------------------------


def parser():
    p = argparse.ArgumentParser(prog="arsys", description=__doc__.splitlines()[0])
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("progress")
    sub.add_parser("decisions")
    c = sub.add_parser("answer")
    c.add_argument("n", type=int)
    c.add_argument("text")
    sub.add_parser("approve").add_argument("pr", type=int)
    sub.add_parser("merge").add_argument("pr", type=int)
    sub.add_parser("pause")
    sub.add_parser("resume")
    goal = sub.add_parser("goal").add_subparsers(dest="action", required=True).add_parser("add")
    goal.add_argument("id")
    goal.add_argument("goal")
    goal.add_argument("outcome")
    goal.add_argument("--deadline", default="")
    req = sub.add_parser("req").add_subparsers(dest="action", required=True).add_parser("add")
    req.add_argument("id")
    req.add_argument("text")
    req.add_argument("--priority", default="must", choices=["must", "should", "could"])
    req.add_argument("--use-case", default="")
    req.add_argument("--hint", default="")
    sub.add_parser("merges")
    sub.add_parser("audit").add_argument("kind", nargs="?", choices=["product", "system", "all"], default="all")
    sub.add_parser("serve").add_argument("--port", type=int, default=8765)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    config = runner.load_all()
    identity = config["identity"]
    gh = post.GitHub(identity["repo"])
    repo = identity["repo"]
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    if args.command == "audit":
        from system.dispatcher import audit, dispatch
        lines, _ = dispatch.audit_pass(audit.KINDS if args.kind == "all" else (args.kind,), announce=False)
        print("\n".join(lines))
    elif args.command == "status":
        print(views.text(data.collect(config)))
    elif args.command == "progress":
        print(views.progress_text(data.progress(config)))
    elif args.command == "decisions":
        snap = data.github_state.snapshot(gh, identity)
        found = data.decisions(gh, snap, identity)
        for x in found:
            print(f"#{x['number']} {x['title']} ({'answered' if x['answered'] else 'asked by the ' + (x['asker'] or '?')})\n"
                  f"{x['url']}\n\n{x['question']}\n\n" + "-" * 60)
        if not found:
            print("No decisions waiting on you.")
    elif args.command == "answer":
        labels = {x["name"] for x in gh.api("GET", f"repos/{repo}/issues/{args.n}")["labels"]}
        if "needs-decision:owner" not in labels:
            raise SystemExit(f"#{args.n} isn't waiting on a decision of yours.")
        if not confirm(f"Post your answer to #{args.n} as you: {args.text!r}?", args.yes):
            return 1
        print(gh.api("POST", f"repos/{repo}/issues/{args.n}/comments", {"body": args.text})["html_url"])
        print("The asker is started on the Dispatcher's next poll.")
    elif args.command == "approve":
        url = f"https://github.com/{repo}/pull/{args.pr}/files"
        print(f"Approve from your second account (Review changes → Approve): {url}")
        subprocess.run(["open", url], capture_output=True)
    elif args.command == "merge":
        if not confirm(f"Merge PR #{args.pr} into main? This can't be undone without a revert.", args.yes):
            return 1
        print(gh_run("pr", "merge", str(args.pr), "--merge", "--repo", repo) or f"#{args.pr} merged.")
    elif args.command in ("pause", "resume"):
        thrown, why = killswitch.paused(gh)
        want = args.command == "pause"
        if thrown == want:
            print(f"Already {'paused' if want else 'running'}: {why}")
            return 0
        what = "Throw the kill switch: no agent run or Dispatcher pass starts" if want else "Clear the kill switch: runs may start again"
        if not confirm(f"{what}?", args.yes):
            return 1
        gh_run("variable", "set", killswitch.VARIABLE, "--body", "true" if want else "false", "--repo", repo)
        print(f"{killswitch.VARIABLE} = {'true' if want else 'false'}")
    elif args.command in ("goal", "req"):
        if not re.fullmatch(r"[A-Z]+\d+", args.id):
            raise SystemExit("An ID looks like G1 or F3.")
        if args.command == "goal":
            heading, cells, what = "## 2. Goals", [args.goal, args.outcome, args.deadline], f"goal {args.id}: {args.goal}"
        else:
            heading, what = "## 4. Functional requirements", f"requirement {args.id}: {args.text}"
            cells = [args.text, args.priority, args.use_case, args.hint]
        edit = lambda t: bump_version(add_row(t, heading, args.id, cells), f"Added {what}", today)
        if not confirm(f"Open a PR adding {what}?", args.yes):
            return 1
        branch = f"owner/{args.command}-{args.id.lower()}-{int(time.time())}"
        body = (f"Adds {what}, proposed by the Owner with `arsys`.\n\n"
                "After merge: the Coordinator opens the goal issue and its Definition item (blueprint §4.1–4.2).")
        print(propose_requirements_change(config, edit, f"Add {what}", branch, body))
        print("Approve it from your second account, then merge it.")
    elif args.command == "merges":
        for m in data.merges_by_owner_account(gh, identity):
            print(f"#{m['number']} {m['title']} — {views.local(m['mergedAt'], identity['time_zone'])}  {m['url']}")
    elif args.command == "serve":
        serve(config, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
