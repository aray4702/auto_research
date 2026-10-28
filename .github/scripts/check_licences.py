#!/usr/bin/env python3
"""G1-15's licence check: every installed distribution and bundled shared library against toolchain.toml.

Usage: python3 -I .github/scripts/check_licences.py --site DIR --platform {linux-x86_64,macos-arm64}
                                                    [--toolchain toolchain.toml] [--inventory]

DIR is an install's site-packages. A distribution's licence must pass toolchain.toml's
`[licences] distributions`; a bundled library's, `distributions` plus `bundled`
(design/internal/g1-stage1.md §1b). Standard library only, so any Python 3.11 runs it.
"""

import argparse
import fnmatch
import os
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

PLATFORMS = ["linux-x86_64", "macos-arm64"]
# Trove licence classifiers that name one SPDX licence. "BSD License" names none, so it needs an override.
CLASSIFIERS = {
    "MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
    "Historical Permission Notice and Disclaimer (HPND)": "HPND",
}
LIBRARY = re.compile(r"\.so$|\.so\.|\.dylib$")
EXTENSION_MODULE = re.compile(r"\.cpython-311-x86_64-linux-gnu\.so$|\.cpython-311-darwin\.so$|\.abi3\.so$")
TOKEN = re.compile(r"\s*(\(|\)|[A-Za-z0-9.+:-]+)")


class SpdxError(ValueError):
    pass


def parse_spdx(text):
    """An SPDX expression as a tree: a string, ("WITH", id, exception), or ("AND" | "OR", left, right)."""
    tokens, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise SpdxError(f"can't read {text[pos:]!r}")
        tokens.append(m.group(1))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    if not tokens:
        raise SpdxError("empty")

    def op(i, name):
        return i < len(tokens) and tokens[i].upper() == name

    def binary(i, name, operand):
        left, i = operand(i)
        while op(i, name):
            right, i = operand(i + 1)
            left = (name, left, right)
        return left, i

    def atom(i):
        if i >= len(tokens):
            raise SpdxError("ends early")
        if tokens[i] == "(":
            tree, i = binary(i + 1, "OR", lambda j: binary(j, "AND", atom))
            if i >= len(tokens) or tokens[i] != ")":
                raise SpdxError("unclosed parenthesis")
            return tree, i + 1
        if tokens[i] == ")" or tokens[i].upper() in ("AND", "OR", "WITH"):
            raise SpdxError(f"unexpected {tokens[i]!r}")
        ident, i = tokens[i], i + 1
        if op(i, "WITH"):
            if i + 1 >= len(tokens) or tokens[i + 1] in "()":
                raise SpdxError("WITH needs an exception")
            return ("WITH", ident, tokens[i + 1]), i + 2
        return ident, i

    tree, i = binary(0, "OR", lambda j: binary(j, "AND", atom))
    if i != len(tokens):
        raise SpdxError(f"unexpected {tokens[i]!r}")
    return tree


def allowed(tree, allow):
    """Whether the expression passes: OR on either branch, AND on both, `X WITH E` only if listed whole."""
    allow = {a.lower() for a in allow}
    if isinstance(tree, str):
        return tree.lower() in allow
    if tree[0] == "WITH":
        return f"{tree[1]} WITH {tree[2]}".lower() in allow
    left, right = allowed(tree[1], allow), allowed(tree[2], allow)
    return (left or right) if tree[0] == "OR" else (left and right)


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def load_config(path):
    licences = tomllib.loads(Path(path).read_text()).get("licences", {})
    return {
        "distributions": licences.get("distributions", []),
        "bundled": licences.get("bundled", []),
        "override": licences.get("override", []),
        "library": licences.get("library", []),
    }


def distribution_licence(dist, config):
    """(licence, source) for a distribution, or (None, why) when its metadata gives no clear SPDX licence."""
    name, version = normalize(dist.metadata["Name"]), dist.version
    for o in config["override"]:
        if normalize(o["distribution"]) == name and o["version"] == version:
            return o["licence"], f"override ({o['source']})"
    expression = dist.metadata.get("License-Expression")
    if expression:
        return expression.strip(), "License-Expression"
    named = [c.split("::")[-1].strip() for c in dist.metadata.get_all("Classifier") or []
             if c.startswith("License ::") and c.count("::") >= 2 and c.strip() != "License :: OSI Approved"]
    if named and all(c in CLASSIFIERS for c in named):
        return " AND ".join(dict.fromkeys(CLASSIFIERS[c] for c in named)), "classifier"
    licence = (dist.metadata.get("License") or "").strip()
    if licence and "\n" not in licence:
        try:
            parse_spdx(licence)
            return licence, "License"
        except SpdxError:
            pass
    why = f"classifier {', '.join(named)!r}" if named else f"License {licence[:40]!r}" if licence else "nothing"
    return None, f"no clear SPDX licence ({why}); needs an override at {version}"


def libraries(dist):
    """The bundled shared libraries in a distribution's RECORD, relative to site-packages."""
    found = []
    for f in dist.files or []:
        path = str(f).replace("\\", "/")
        name = path.rsplit("/", 1)[-1]
        if path.startswith("..") or not LIBRARY.search(name) or EXTENSION_MODULE.search(name):
            continue
        found.append(path)
    return sorted(found)


def check(site, platform, config):
    """Rows (status, kind, name, version, licence, source) and warnings."""
    rows, warnings, used = [], [], set()
    allow_dist = config["distributions"]
    allow_lib = config["distributions"] + config["bundled"]
    dists = {normalize(d.metadata["Name"]): d for d in metadata.distributions(path=[str(site)])}
    for key in sorted(dists):
        dist = dists[key]
        licence, source = distribution_licence(dist, config)
        ok = licence is not None and passes(licence, allow_dist)
        rows.append(("PASS" if ok else "FAIL", "distribution", dist.metadata["Name"], dist.version,
                     licence or "?", source))
        for path in libraries(dist):
            entry = next((i for i, e in enumerate(config["library"])
                          if normalize(e["distribution"]) == key and platform in e.get("platforms", [])
                          and fnmatch.fnmatch(path, e["files"])), None)
            if entry is None:
                rows.append(("FAIL", "library", path, dist.version, "?",
                             f"no [[licences.library]] entry for {platform}"))
                continue
            used.add(entry)
            e = config["library"][entry]
            ok = passes(e["licence"], allow_lib)
            rows.append(("PASS" if ok else "FAIL", "library", path, dist.version, e["licence"],
                         f"{e['library']} ({e['source']})"))
    for i, e in enumerate(config["library"]):
        if i not in used and platform in e.get("platforms", []):
            warnings.append(f"[[licences.library]] {e['distribution']} {e['files']!r} matches nothing on {platform}")
    return rows, warnings


def passes(licence, allow):
    try:
        return allowed(parse_spdx(licence), allow)
    except SpdxError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", required=True)
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--toolchain", default="toolchain.toml")
    parser.add_argument("--inventory", action="store_true", help="print a Markdown table and exit 0")
    args = parser.parse_args(argv)
    if not Path(args.site).is_dir():
        print(f"FAIL --site {args.site} is not a directory")
        return 1
    rows, warnings = check(args.site, args.platform, load_config(args.toolchain))
    if args.inventory:
        lines = ["| Result | Kind | Name | Version | Licence | Source |", "| --- | --- | --- | --- | --- | --- |"]
        lines += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows]
    else:
        lines = [" ".join(row) for row in rows]
    lines += [f"warning: {w}" for w in warnings]
    failed = sum(row[0] == "FAIL" for row in rows)
    lines.append(f"{len(rows)} checked on {args.platform}, {failed} failed")
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"### Licences ({args.platform})\n\n```\n{report}\n```\n")
    return 0 if args.inventory or not failed else 1


if __name__ == "__main__":
    sys.exit(main())
