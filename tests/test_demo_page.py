"""The demo's page: its source checks and its DOM contract (design/internal/g3-stage1.md §2a, 2a1).

What app.js does in a browser is for 3a to 3c to test; these need no server and no browser.
"""

import importlib.resources
import re
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

STATIC = importlib.resources.files("lmt.demo") / "static"  # as the handler reads them
FILES = ["index.html", "app.js", "app.css", "favicon.svg"]
URL_ATTRS = {"href", "src", "action", "formaction", "poster", "data"}
UNSAFE_JS = ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("]


class Elements(HTMLParser):
    """Every start tag, as (tag, attributes), in document order."""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def parse(html):
    parser = Elements()
    parser.feed(html)
    parser.close()
    return parser.tags


def by_id(tags, element_id):
    found = [(tag, attrs) for tag, attrs in tags if attrs.get("id") == element_id]
    assert len(found) == 1, f"#{element_id}: {len(found)} elements"
    return found[0]


def source_problems(html, js):
    """What breaks the page's source rules, as messages (none when it keeps them)."""
    problems = []
    for tag, attrs in parse(html):
        if tag == "script" and not attrs.get("src"):
            problems.append("a <script> without src")
        if "style" in attrs:
            problems.append(f"a style= attribute on <{tag}>")
        for name in URL_ATTRS & attrs.keys():
            url = (attrs[name] or "").strip()
            if url.startswith("//") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", url):
                problems.append(f"<{tag} {name}={url!r}> leaves the page's origin")
    problems += [f"app.js uses {name}" for name in UNSAFE_JS if name in js]
    return problems


class DemoPageTest(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.tags = parse(self.html)

    def test_files_are_in_the_package(self):
        for name in FILES:
            self.assertTrue((STATIC / name).is_file(), name)

    def test_source_keeps_the_rules(self):
        self.assertEqual(source_problems(self.html, self.js), [])

    def test_source_check_catches_each_fault(self):
        clean = '<script src="app.js"></script>'
        self.assertEqual(source_problems(clean, ""), [])
        for html in ["<script>alert(1)</script>", '<p style="color:red"></p>',
                     '<img src="https://example.com/x.png">', '<link href="//example.com/a.css">',
                     '<a href="javascript:void(0)"></a>']:
            self.assertTrue(source_problems(html, ""), html)
        for name in UNSAFE_JS:
            self.assertTrue(source_problems(clean, f"x.{name} = y"), name)

    def test_dom_contract(self):
        tag, attrs = by_id(self.tags, "error")
        self.assertEqual(tag, "p")
        self.assertIn("hidden", attrs)
        self.assertEqual(attrs.get("role"), "alert")

        tag, attrs = by_id(self.tags, "audio")
        self.assertEqual(tag, "audio")
        self.assertEqual(attrs.get("src"), "audio.wav")
        self.assertIn("controls", attrs)

        tag, attrs = by_id(self.tags, "track")
        self.assertEqual(tag, "track")
        self.assertEqual(attrs.get("kind"), "subtitles")
        self.assertEqual(attrs.get("src"), "transcript.vtt")
        self.assertIn("default", attrs)

        self.assertEqual(by_id(self.tags, "speakers")[0], "ul")
        self.assertEqual(by_id(self.tags, "segments")[0], "ol")

    def test_links_and_script(self):
        links = {(a.get("rel"), a.get("href")) for t, a in self.tags if t == "link"}
        self.assertIn(("icon", "favicon.svg"), links)
        self.assertIn(("stylesheet", "app.css"), links)
        scripts = [a for t, a in self.tags if t == "script"]
        self.assertEqual(scripts, [{"src": "app.js"}])

    def test_favicon_is_xml(self):
        root = ET.fromstring((STATIC / "favicon.svg").read_bytes())
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")


if __name__ == "__main__":
    unittest.main()
