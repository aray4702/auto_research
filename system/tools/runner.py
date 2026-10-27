#!/usr/bin/env python3
"""The runner: how agent runs start on this Mac, as system/runner.toml defines it.

  runner.py check            exit 1 unless the installed launchd jobs and the applied checkout match main
  runner.py apply            update the applied checkout to origin/main and install the launchd jobs
  runner.py settings ROLE    print the Claude Code settings a ROLE run gets

The applied checkout (<home>/system) is a clone the schedules run from, never the Owner's checkout.
Each run's Claude Code settings are built here from runner.toml, caps.toml and, once the product
has one, the product's toolchain.toml. Standard library only.
"""

import argparse
import json
import os
import plistlib
import pwd
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCH_AGENTS = Path("~/Library/LaunchAgents").expanduser()


def load(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_all(root=ROOT):
    toolchain = root / "toolchain.toml"
    return {
        "runner": load(root / "system" / "runner.toml"),
        "caps": load(root / "system" / "caps.toml"),
        "identity": load(root / "system" / "identity.toml"),
        "toolchain": load(toolchain) if toolchain.exists() else {},
    }


def home(config):
    return Path(config["runner"]["runner"]["home"]).expanduser()


# --- one run --------------------------------------------------------------------------------


def role_settings(config, role):
    """The Claude Code settings for one role's runs: the only settings a restricted run loads."""
    r, common = config["runner"]["roles"][role], config["runner"]["common"]
    allow = list(common["allow"]) + list(r["allow"])
    if r["product_tools"]:
        allow += config["toolchain"].get("agents", {}).get("allow", [])
    return {
        "permissions": {"allow": allow, "deny": list(common["deny"]) + list(r.get("deny", []))},
        "autoMemoryEnabled": False,
    }


def claude_command(config, role, task, settings_path):
    runner, r, caps = config["runner"]["runner"], config["runner"]["roles"][role], config["caps"]["roles"][role]
    return [
        runner["claude"], "-p", runner["prompt"].format(role=role, task=task), *runner["flags"],
        "--tools", ",".join(r["tools"]), "--settings", str(settings_path),
        "--model", r["model"], "--effort", r["effort"],
        "--max-turns", str(caps["max_turns"]), "--max-budget-usd", str(caps["max_budget_usd"]),
    ]


def run_env(config):
    """The environment every run starts with, and nothing else. USER is what lets Claude Code find
    the subscription login in the keychain; without it (or with LOGNAME also set) it signed in with
    a stray token that belongs to no organization."""
    return {"PATH": config["runner"]["runner"]["path"], "HOME": str(Path.home()), "USER": pwd.getpwuid(os.getuid()).pw_name}


def claude_login(config):
    """(authMethod, subscriptionType) that a run's environment signs Claude Code in with."""
    out = subprocess.run([config["runner"]["runner"]["claude"], "auth", "status"], env=run_env(config),
                         capture_output=True, text=True)
    try:
        status = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None, None
    return status.get("authMethod"), status.get("subscriptionType")


def login_problem(config, login):
    """Why runs mustn't start with this login, or None. Anything but the subscription could spend money."""
    want, (method, subscription) = config["runner"]["runner"]["subscription"], login
    if method == "claude.ai" and subscription == want:
        return None
    return (f"runs would sign Claude Code in with {method or 'nothing'} ({subscription or 'no subscription'}), "
            f"not the {want} subscription: the Owner runs `claude auth login` in Terminal")


# --- launchd ----------------------------------------------------------------------------------


def cli_link(config):
    """(where the Owner's CLI is linked, what it links to in the applied checkout)."""
    return Path(config["runner"]["runner"]["cli"]).expanduser(), home(config) / "system" / "system" / "cli" / "arsys.py"


def label(config, name):
    return f"{config['runner']['runner']['launchd_prefix']}.{name}"


def plists(config):
    """{label: plist bytes} for every schedule, rendered deterministically."""
    runner, checkout = config["runner"]["runner"], home(config) / "system"
    logs = Path(runner["logs"]).expanduser()
    out = {}
    for name, s in config["runner"]["schedules"].items():
        job = {
            "Label": label(config, name),
            "ProgramArguments": [runner["python"], "-I", str(checkout / s["command"][0]), *s["command"][1:]],
            "WorkingDirectory": str(checkout),
            "EnvironmentVariables": {"PATH": runner["path"], "HOME": str(Path.home())},
            "StandardOutPath": str(logs / f"{name}.log"),
            "StandardErrorPath": str(logs / f"{name}.err"),
            "ProcessType": "Background",
            # Keep the agent runs a pass starts alive when the pass itself exits.
            "AbandonProcessGroup": True,
            "Disabled": not s["enabled"],
        }
        if "interval_seconds" in s:
            job["StartInterval"] = s["interval_seconds"]
        else:
            job["StartCalendarInterval"] = {k.capitalize(): s[k] for k in ("hour", "minute") if k in s}
        out[job["Label"]] = plistlib.dumps(job, sort_keys=True)
    return out


def loaded(job_label):
    target = f"gui/{os.getuid()}/{job_label}"
    return subprocess.run(["launchctl", "print", target], capture_output=True).returncode == 0


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def problems(config, agents_dir=LAUNCH_AGENTS, is_loaded=loaded, applied_commit=None, main_commit=None, login=None, cli=None):
    out = []
    if login is not None and login_problem(config, login):
        out.append(login_problem(config, login))
    wanted = plists(config)
    for job_label, data in wanted.items():
        path = agents_dir / f"{job_label}.plist"
        if not path.exists():
            out.append(f"{path.name} is not installed")
        elif path.read_bytes() != data:
            out.append(f"{path.name} differs from runner.toml")
        enabled = not plistlib.loads(data)["Disabled"]
        if is_loaded(job_label) != enabled:
            out.append(f"{job_label} is {'not ' if enabled else ''}loaded, but runner.toml has it {'enabled' if enabled else 'disabled'}")
    if cli is not None:
        link, target = cli
        if not (link.is_symlink() and Path(os.readlink(link)) == target):
            out.append(f"{link} doesn't link to {target}")
    prefix = config["runner"]["runner"]["launchd_prefix"] + "."
    for path in sorted(agents_dir.glob(prefix + "*.plist")):
        if path.stem not in wanted:
            out.append(f"{path.name} is installed but not in runner.toml")
    if applied_commit != main_commit:
        out.append(f"the applied checkout is at {applied_commit or 'nothing'}, main is at {main_commit}")
    return out


def commits(config):
    """(applied checkout's commit, origin/main's commit)."""
    checkout = home(config) / "system"
    main = git("rev-parse", "origin/main", cwd=ROOT)
    applied = git("rev-parse", "HEAD", cwd=checkout) if (checkout / ".git").exists() else None
    return applied, main


def apply(config):
    checkout, done = home(config) / "system", []
    if not (checkout / ".git").exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "--quiet", f"https://github.com/{config['identity']['repo']}.git", str(checkout))
        done.append(f"cloned {config['identity']['repo']} into {checkout}")
    git("fetch", "--quiet", "origin", cwd=checkout)
    git("checkout", "--quiet", "--detach", "origin/main", cwd=checkout)
    done.append(f"applied checkout at {git('rev-parse', '--short', 'HEAD', cwd=checkout)}")
    Path(config["runner"]["runner"]["logs"]).expanduser().mkdir(parents=True, exist_ok=True)
    link, target = cli_link(config)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)
    done.append(f"linked {link} -> {target}")
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    for job_label, data in plists(config).items():
        path = LAUNCH_AGENTS / f"{job_label}.plist"
        if loaded(job_label):
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{job_label}"], capture_output=True)
        path.write_bytes(data)
        if not plistlib.loads(data)["Disabled"]:
            subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True)
            done.append(f"installed and loaded {path.name}")
        else:
            done.append(f"installed {path.name} (disabled)")
    return done


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "apply", "settings"])
    parser.add_argument("role", nargs="?")
    args = parser.parse_args(argv)
    config = load_all()
    if args.command == "settings":
        if args.role not in config["runner"]["roles"]:
            parser.error(f"settings needs one of: {', '.join(config['runner']['roles'])}")
        print(json.dumps(role_settings(config, args.role), indent=2))
        return 0
    if args.command == "apply":
        for line in apply(config):
            print(line)
    git("fetch", "--quiet", "origin", cwd=ROOT)
    applied, main_commit = commits(config)
    found = problems(config, applied_commit=applied, main_commit=main_commit, login=claude_login(config), cli=cli_link(config))
    print("FAIL runner" if found else "PASS runner: runs sign in with the subscription; installed jobs and the applied checkout match main")
    for p in found:
        print(f"  - {p}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
